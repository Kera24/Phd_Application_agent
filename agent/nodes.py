"""Graph node implementations.

Each node is a pure-ish function: (state) -> partial state update. The heavy
domain logic is delegated to the reused pipeline modules; nodes add routing,
persistence of the new tables, and state mapping.

Subgraph nodes operate on OppState (one opportunity). Main-graph nodes operate
on OutreachState. The Human Approval node uses LangGraph interrupt().
"""
from __future__ import annotations

import datetime as dt

from langgraph.types import interrupt

from db import session as dbsession
from db.models import Email, Opportunity, Professor
from modules import (
    config_loader,
    email_gen,
    gmail_client,
    parser,
    prof_research,
    scheduler as sched_mod,
    scoring,
    summary_gen,
    tracker,
)
from agent import repo

PIPELINE_KEYS = (
    "parsed_opportunities", "funding_decisions", "professor_research",
    "research_gaps", "fit_scores", "tailored_research_summaries",
    "email_drafts", "tracker_status", "errors",
)


# ===========================================================================
# Subgraph nodes (per opportunity)
# ===========================================================================
def parse_node(state: dict) -> dict:
    """Parse a discovered item into an Opportunity + apply nothing yet (gate is next)."""
    item = state["opportunity"]
    with dbsession.session_scope() as s:
        if item.get("raw_text"):
            opp = parser.classify_and_store(
                s, item["raw_text"],
                source_type=item.get("source_type", "linkedin_manual"),
                source_url=item.get("source_url"),
                opportunity_type=item.get("opportunity_type", "advertised"),
            )
        else:
            # Speculative seed (from Lab Prospector): funded-by-default country.
            seed = item.get("seed", {})
            opp = Opportunity(
                source_type="prospecting", opportunity_type="speculative",
                university=seed.get("university"), country=seed.get("country"),
                professor_name=seed.get("name"),
                funding_status="funded",
                funding_evidence=f"{seed.get('country')}: PhDs funded by default.",
                research_fields=[seed.get("field")] if seed.get("field") else [],
                pipeline_status="parsed",
            )
            s.add(opp); s.flush()
        oid = opp.id
        parsed = {
            "id": oid, "title": opp.position_title, "university": opp.university,
            "country": opp.country, "professor_name": opp.professor_name,
            "professor_email": opp.professor_email,
            "funding_status": opp.funding_status, "pipeline_status": opp.pipeline_status,
            "opportunity_type": opp.opportunity_type,
        }
    return {
        "opportunity_id": oid,
        "parsed_opportunities": [parsed],
        "funding_decisions": {str(oid): {"status": opp.funding_status,
                                         "pipeline": opp.pipeline_status}},
        "tracker_status": {str(oid): opp.pipeline_status},
    }


def funding_route(state: dict) -> str:
    """Conditional edge after parse — Rule 0. Routes in Python from the gate."""
    status = state["parsed_opportunities"][-1]["pipeline_status"]
    if status == "archived_not_funded":
        return "archived"
    if status in ("funding_unknown", "needs_email"):
        return "needs_review"
    return "research"  # funded + has email


def research_node(state: dict) -> dict:
    """Professor research with citation verification (retry-once is inside)."""
    oid = state["opportunity_id"]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        try:
            prof = prof_research.research_professor(
                s, opp.professor_name or "", affiliation=opp.university or "",
                profile_url=opp.professor_profile_url, email=opp.professor_email,
            )
            opp.professor_id = prof.id
            source_api = "semantic_scholar" if prof.recent_papers else "none"
            pub_ids = repo.store_publications(s, prof, prof.recent_papers or [], source_api)
            repo.set_tracker(s, opp, "researched")
            research = {
                "professor_id": prof.id, "name": prof.name,
                "themes": prof.research_themes, "papers": prof.recent_papers,
                "publication_ids": pub_ids,
                "gap": prof.identified_gap, "angle": prof.proposed_angle,
            }
            return {"professor_research": {str(oid): research},
                    "tracker_status": {str(oid): "researched"}}
        except prof_research.CitationVerificationError as exc:
            repo.set_tracker(s, opp, "needs_review")
            return {"route": "needs_review",
                    "tracker_status": {str(oid): "needs_review"},
                    "errors": [{"opportunity_id": oid, "node": "research", "error": str(exc)}]}


def research_route(state: dict) -> str:
    return "needs_review" if state.get("route") == "needs_review" else "gap"


def gap_node(state: dict) -> dict:
    """Persist the research gap, grounded in specific stored publications."""
    oid = state["opportunity_id"]
    research = state["professor_research"][str(oid)]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        prof = s.get(Professor, research["professor_id"])
        gap = research.get("gap") or ""
        angle = research.get("angle") or ""
        gap_id = repo.store_gap(s, opp, prof, gap, angle, research.get("publication_ids", []))
    return {"research_gaps": {str(oid): {"gap_id": gap_id, "gap": gap, "angle": angle,
                                         "source_publication_ids": research.get("publication_ids", [])}}}


def score_node(state: dict) -> dict:
    oid = state["opportunity_id"]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        prof = s.get(Professor, opp.professor_id) if opp.professor_id else None
        result = scoring.score_opportunity(opp, prof)
        opp.fit_score = result["total"]
        opp.score_breakdown = result["breakdown"]
        repo.set_tracker(s, opp, "scored")
    return {"fit_scores": {str(oid): result}, "tracker_status": {str(oid): "scored"}}


def score_route(state: dict) -> str:
    oid = state["opportunity_id"]
    total = state["fit_scores"][str(oid)]["total"]
    threshold = config_loader.config().get("fit_score_threshold", 50)
    return "summary" if total >= threshold else "parked"


def summary_node(state: dict) -> dict:
    oid = state["opportunity_id"]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        prof = s.get(Professor, opp.professor_id) if opp.professor_id else None
        try:
            path = summary_gen.generate_summary_pdf(s, opp, prof)
            return {"tailored_research_summaries": {str(oid): path}}
        except summary_gen.SummaryOverflowError as exc:
            repo.set_tracker(s, opp, "needs_review")
            return {"route": "needs_review",
                    "tracker_status": {str(oid): "needs_review"},
                    "errors": [{"opportunity_id": oid, "node": "summary", "error": str(exc)}]}
        except Exception as exc:
            # e.g. WeasyPrint/GTK not installed locally — don't kill the batch.
            return {"tailored_research_summaries": {str(oid): None},
                    "errors": [{"opportunity_id": oid, "node": "summary",
                                "error": f"pdf skipped: {exc}"}]}


def summary_route(state: dict) -> str:
    return "needs_review" if state.get("route") == "needs_review" else "email_writer"


def email_writer_node(state: dict) -> dict:
    """Draft (or re-draft on retry) the email; quality gate runs inside generate_email."""
    oid = state["opportunity_id"]
    retries = state.get("email_retries", 0)
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        prof = s.get(Professor, opp.professor_id) if opp.professor_id else None
        summary_path = state.get("tailored_research_summaries", {}).get(str(oid))
        # Find an existing draft to regenerate, else create fresh.
        existing = (s.query(Email).filter_by(opportunity_id=oid)
                    .order_by(Email.id.desc()).first())
        if existing and retries > 0 and existing.status not in ("sent", "cancelled"):
            email = email_gen.regenerate(s, existing)
        else:
            email = email_gen.generate_email(s, opp, prof)
        if summary_path:
            email.summary_pdf_path = summary_path
        s.flush()
        draft = {
            "email_id": email.id, "subject": email.subject, "body": email.body,
            "attachments": email.attachments, "passed": email.quality_gate_passed,
            "quality_report": email.quality_gate_report,
            "summary_pdf_path": email.summary_pdf_path,
        }
    return {"email_drafts": {str(oid): draft}}


def quality_route(state: dict) -> str:
    """Conditional edge: pass -> gmail_draft; fail -> retry once -> needs_review."""
    oid = state["opportunity_id"]
    draft = state["email_drafts"][str(oid)]
    if draft.get("passed"):
        return "gmail_draft"
    if state.get("email_retries", 0) < 1:
        return "retry"
    return "needs_review"


def bump_retry_node(state: dict) -> dict:
    return {"email_retries": state.get("email_retries", 0) + 1}


def quality_needs_review_node(state: dict) -> dict:
    oid = state["opportunity_id"]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        repo.set_tracker(s, opp, "needs_review")
    return {"tracker_status": {str(oid): "needs_review"}}


def gmail_draft_node(state: dict) -> dict:
    """Create a Gmail draft when possible; otherwise leave it for approval time.

    Either way the email stays in 'awaiting_review' for the Human Approval node.
    """
    oid = state["opportunity_id"]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        repo.set_tracker(s, opp, "awaiting_approval")
        email = (s.query(Email).filter_by(opportunity_id=oid)
                 .order_by(Email.id.desc()).first())
        draft_id = None
        if email and gmail_client.is_authorised():
            try:
                from modules import ingest
                paths = ingest.asset_paths(s)
                if email.summary_pdf_path:
                    paths = dict(paths, summary_pdf=email.summary_pdf_path)
                draft_id = gmail_client.create_draft(s, email, paths)
            except Exception as exc:
                repo.log(s, email.id, "gmail_draft_deferred", {"error": str(exc)})
    update = {"tracker_status": {str(oid): "awaiting_approval"}}
    if draft_id:
        update["email_drafts"] = {str(oid): {**state["email_drafts"][str(oid)],
                                             "gmail_draft_id": draft_id}}
    return update


def archived_node(state: dict) -> dict:
    oid = state["opportunity_id"]
    return {"tracker_status": {str(oid): "archived_not_funded"}}


def needs_review_node(state: dict) -> dict:
    oid = state["opportunity_id"]
    return {"tracker_status": {str(oid): "needs_review"}}


def parked_node(state: dict) -> dict:
    oid = state["opportunity_id"]
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, oid)
        repo.set_tracker(s, opp, "parked")
    return {"tracker_status": {str(oid): "parked"}}


# ===========================================================================
# Main-graph nodes
# ===========================================================================
def intake_node(state: dict) -> dict:
    """Load profile/templates/config; surface any document warnings."""
    update = {
        "user_profile": state.get("user_profile") or config_loader.profile(),
        "email_templates": state.get("email_templates") or config_loader.email_templates(),
        "target_fields": state.get("target_fields") or config_loader.config().get("target_fields", []),
        "target_countries": state.get("target_countries") or config_loader.config().get("target_countries", []),
    }
    return update


def discover_node(state: dict) -> dict:
    """Assemble discovered opportunities from manual inputs + optional seeds.

    Proactive web search / prospecting is exposed via the dashboard + tools; the
    graph processes whatever discovered items are present in state.
    """
    discovered = list(state.get("discovered_opportunities", []))
    for raw in state.get("linkedin_inputs", []) or []:
        discovered.append({"raw_text": raw, "source_type": "linkedin_manual",
                           "opportunity_type": "advertised"})
    for seed in state.get("professor_list", []) or []:
        discovered.append({"seed": seed, "opportunity_type": "speculative"})
    return {"discovered_opportunities": discovered}


def approval_node(state: dict) -> dict:
    """Human Approval interrupt — pauses the graph for Raj's decision.

    No path to the Scheduler bypasses this node.
    """
    with dbsession.session_scope() as s:
        email = (s.query(Email).filter_by(status="awaiting_review")
                 .order_by(Email.id).first())
        if email is None:
            return {"current_email_id": 0}
        opp = email.opportunity
        prof = email.professor
        payload = {
            "email_id": email.id,
            "subject": email.subject,
            "body": email.body,
            "attachments": email.attachments,
            "quality_report": email.quality_gate_report,
            "fit_score": opp.fit_score if opp else None,
            "score_breakdown": opp.score_breakdown if opp else None,
            "professor": {
                "name": prof.name if prof else None,
                "papers": (prof.recent_papers or []) if prof else [],
                "gap": prof.identified_gap if prof else None,
            },
            "professor_email": prof.email if prof else None,
            "summary_pdf_path": email.summary_pdf_path,
        }
        eid = email.id

    # ---- graph pauses here; resumes with the dashboard decision ----
    decision = interrupt(payload)
    action = (decision or {}).get("action", "reject")
    edits = (decision or {}).get("edits") or {}
    reason = (decision or {}).get("reason", "")
    by = (decision or {}).get("by", "dashboard")

    from langgraph.config import get_config
    thread_id = (get_config().get("configurable", {}) or {}).get("thread_id", "")

    with dbsession.session_scope() as s:
        email = s.get(Email, eid)
        if action == "approve":
            tracker.transition(s, email, "approved", {"by": by})
            repo.record_approval(s, email, thread_id, "approved", decided_by=by, reason=reason)
            status = "approved"
        elif action == "edit":
            if "subject" in edits:
                email.subject = edits["subject"]
            if "body" in edits:
                email.body = edits["body"]
            # Re-run the quality gate after a manual edit.
            from modules import quality_gate, ingest
            report = quality_gate.run(
                email.body or "", email.subject or "",
                email.professor or Professor(name=""),
                [k for k in (email.attachments or []) if k != "summary_pdf"],
                ingest.asset_paths(s))
            email.quality_gate_passed = report["passed"]
            email.quality_gate_report = report
            repo.record_approval(s, email, thread_id, "edited", decided_by=by,
                                 edits=edits, reason=reason)
            status = "edited"  # stays awaiting_review -> presented again
        else:  # reject
            tracker.transition(s, email, "cancelled", {"by": by, "reason": reason})
            repo.record_approval(s, email, thread_id, "rejected", decided_by=by, reason=reason)
            status = "rejected"
    return {"current_email_id": eid, "approval_status": {str(eid): status}}


def route_after_approval(state: dict) -> str:
    eid = state.get("current_email_id")
    if not eid:
        return "finalize"
    status = state.get("approval_status", {}).get(str(eid))
    if status == "approved" and config_loader.config().get("approved_send_mode"):
        return "scheduler"
    return "approval"  # edited/rejected/approved-without-send -> handle next


def scheduler_node(state: dict) -> dict:
    """Schedule an approved email (gated). Creates the Gmail draft if not yet made."""
    eid = state["current_email_id"]
    with dbsession.session_scope() as s:
        email = s.get(Email, eid)
        try:
            send_utc = sched_mod.schedule_send(s, email)
            tz = sched_mod.timezones.resolve(
                university=(email.professor.university if email.professor else "") or "",
                city=email.opportunity.city if email.opportunity else "",
                country=email.opportunity.country if email.opportunity else "")
            repo.record_scheduled(s, email, send_utc, tz.zone, tz.flagged)
            sched = {"send_at_utc": send_utc.isoformat(), "professor_tz": tz.zone,
                     "tz_flagged": tz.flagged}
        except Exception as exc:
            tracker.transition(s, email, "failed", {"error": str(exc)})
            return {"scheduled_emails": {str(eid): {"error": str(exc)}},
                    "errors": [{"email_id": eid, "node": "scheduler", "error": str(exc)}]}
    return {"scheduled_emails": {str(eid): sched}}


def finalize_node(state: dict) -> dict:
    return {"send_logs": [{"event": "run_complete",
                           "at": dt.datetime.now(dt.timezone.utc).isoformat()}]}
