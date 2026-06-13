"""Email drafting (two templates) + quality gate wiring.

Selects which of Raj's projects to foreground based on the lab's field, drafts
via Claude using email_template.yaml constraints, runs the quality gate, and
persists an Email (status 'awaiting_review' if the gate passes, else
'draft_created').
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Email, Opportunity, Professor
from modules import config_loader, ingest, llm, quality_gate, tracker

# Map a lab field keyword -> the profile project id to foreground.
FIELD_TO_PROJECT = {
    "medical": "medical_imaging_vindr",
    "mammo": "medical_imaging_vindr",
    "healthcare": "medical_imaging_vindr",
    "radiolog": "medical_imaging_vindr",
    "place recognition": "vpr_contrastive",
    "contrastive": "vpr_contrastive",
    "representation": "vpr_contrastive",
    "self-supervised": "vpr_contrastive",
    "interpretab": "sae_superposition",
    "mechanistic": "sae_superposition",
    "vision": "vpr_contrastive",
    "nlp": "rag_llm",
    "language model": "rag_llm",
    "llm": "rag_llm",
    "rag": "rag_llm",
    "retrieval": "rag_llm",
}


def select_project(opp: Opportunity, prof: Optional[Professor], profile: dict) -> dict:
    """Choose the single most relevant project to mention (Section 7 matching rule)."""
    haystack = " ".join(
        filter(None, [
            opp.position_title, opp.lab_name, opp.department,
            " ".join(opp.research_fields or []),
            " ".join(p["title"] for p in (prof.recent_papers or [])) if prof else "",
            " ".join(prof.research_themes or []) if prof else "",
        ])
    ).lower()

    projects = {p["id"]: p for p in profile.get("research_projects", [])}
    for keyword, proj_id in FIELD_TO_PROJECT.items():
        if keyword in haystack and proj_id in projects:
            return projects[proj_id]
    # Default: medical imaging if nothing matches (most decorated result), else first.
    return projects.get("medical_imaging_vindr") or next(iter(projects.values()))


def _attachment_kinds(opp: Opportunity) -> list[str]:
    tmpl = config_loader.email_template()["templates"]
    key = "advertised" if opp.opportunity_type == "advertised" else "speculative"
    kinds = list(tmpl[key]["default_attachments"])
    # Advertised: include transcript only if required_documents asks for it (already default).
    # Speculative: transcript only on request -> excluded by default (per spec).
    return kinds


def _draft_prompt(opp, prof, project, profile, kinds) -> str:
    tmpl = config_loader.email_template()
    key = "advertised" if opp.opportunity_type == "advertised" else "speculative"
    spec = tmpl["templates"][key]
    shared = tmpl["shared_constraints"]
    papers = "\n".join(
        f"- {p['title']} ({p.get('year')})" for p in (prof.recent_papers or [])
    ) if prof else ""
    structure = "\n".join(f"{i+1}. {s}" for i, s in enumerate(spec["structure"]))
    return (
        f"Write a {key} PhD outreach email body for {profile['name']} to "
        f"Professor {prof.name if prof else opp.professor_name}.\n\n"
        f"VERIFIED recent papers (cite >=1 by EXACT title):\n{papers}\n\n"
        f"Identified gap: {prof.identified_gap if prof else ''}\n"
        f"Proposed angle: {prof.proposed_angle if prof else ''}\n\n"
        f"Foreground ONLY this project (do not mention others):\n"
        f"  {project['title']}: {project['detail']}\n\n"
        f"Opportunity: title={opp.position_title!r}, university={opp.university!r}, "
        f"lab={opp.lab_name!r}, fields={opp.research_fields}.\n\n"
        f"Required structure:\n{structure}\n\n"
        f"Constraints: body {shared['body_word_min']}-{shared['body_word_max']} words; "
        f"subject <= {shared['subject_max_words']} words; tone {shared['tone']}; "
        f"forbid: {', '.join(shared['forbid'])}. {spec['subject_hint']}\n"
        f"End the body with an attachment line listing: {', '.join(kinds)}.\n"
        f"Only state facts about {profile['name']} from the foregrounded project.\n\n"
        'Return JSON: {"subject": "...", "body": "..."}.'
    )


def _fallback_draft(opp, prof, project, profile, kinds) -> dict:
    """Deterministic template fill when no LLM key is set (keeps pipeline runnable)."""
    paper = (prof.recent_papers[0]["title"] if prof and prof.recent_papers else "your recent work")
    name = prof.name if prof else (opp.professor_name or "Professor")
    gap = (prof.identified_gap if prof else "") or "an open question your group is exploring"
    angle = (prof.proposed_angle if prof else "") or "methods from my background"
    is_spec = opp.opportunity_type != "advertised"
    ask = (
        "I am seeking a fully funded PhD position and wondered whether you anticipate "
        "openings in the coming intake, or could advise on upcoming funded calls in your group."
        if is_spec else
        f"I would value the chance to be considered for the {opp.position_title or 'advertised PhD position'} "
        "and to discuss it on a short call."
    )
    body = (
        f"Dear Professor {name.split()[-1] if name else ''},\n\n"
        f"I read your paper \"{paper}\" with interest. I noticed that {gap} remains a gap "
        f"in this line of work. Building on my background, I could explore {angle}. "
        f"In my project {project['title']}, {project['detail'].strip()} "
        f"This work gives me directly relevant experience. {ask}\n\n"
        f"Attachments: {', '.join(kinds)}.\n\n"
        f"Kind regards,\n{profile['name']}"
    )
    subject = (
        f"Prospective PhD applicant — {(opp.research_fields or ['your group'])[0]}"
        if is_spec else f"PhD application — {opp.position_title or 'your group'}"
    )
    return {"subject": subject[:80], "body": body}


def generate_email(session: Session, opp: Opportunity,
                   prof: Optional[Professor]) -> Email:
    """Draft, quality-gate, and persist an Email for an opportunity."""
    profile = config_loader.profile()
    project = select_project(opp, prof, profile)
    kinds = _attachment_kinds(opp)

    if llm.available():
        try:
            draft = llm.complete_json(_draft_prompt(opp, prof, project, profile, kinds))
        except Exception:
            draft = _fallback_draft(opp, prof, project, profile, kinds)
    else:
        draft = _fallback_draft(opp, prof, project, profile, kinds)

    resolved = ingest.asset_paths(session)
    # Map template kinds to asset kinds: summary_pdf is produced per-email later.
    resolved_for_gate = dict(resolved)
    report = quality_gate.run(
        draft.get("body", ""), draft.get("subject", ""), prof or Professor(name=opp.professor_name or ""),
        [k for k in kinds if k != "summary_pdf"], resolved_for_gate,
    )

    email = Email(
        opportunity_id=opp.id,
        professor_id=prof.id if prof else None,
        subject=draft.get("subject"),
        body=draft.get("body"),
        attachments=kinds,
        quality_gate_passed=report["passed"],
        quality_gate_report=report,
        status="draft_created",
    )
    session.add(email)
    session.flush()
    tracker.log_event(session, email.id, "email_drafted",
                      {"opportunity_id": opp.id, "gate_passed": report["passed"]})
    if report["passed"]:
        tracker.transition(session, email, "awaiting_review",
                           {"reason": "quality gate passed"})
    session.flush()
    return email


def _followup_prompt(original: Email, prof, profile) -> str:
    tmpl = config_loader.email_templates().get("followup", {})
    cfg = config_loader.config().get("followup", {})
    name = prof.name if prof else (original.opportunity.professor_name if original.opportunity else "")
    paper = ""
    if prof and prof.recent_papers:
        paper = prof.recent_papers[0]["title"]
    structure = "\n".join(f"{i+1}. {s}" for i, s in enumerate(tmpl.get("structure", [])))
    return (
        f"Write a brief, polite follow-up to a previous PhD outreach email from "
        f"{profile['name']} to Professor {name}. The original email has had no reply.\n\n"
        f"Original subject: {original.subject!r}\n"
        f"A verified paper you may reference by exact title: {paper!r}\n\n"
        f"Required structure:\n{structure}\n\n"
        f"Constraints: body {cfg.get('word_min', 40)}-{cfg.get('word_max', 90)} words; "
        f"warm but not pushy; reference the earlier email; restate the interest in one line; "
        f"do NOT introduce any new claims about {profile['name']}; do NOT re-attach documents.\n"
        'Return JSON: {"subject": "...", "body": "..."}.'
    )


def _fallback_followup(original: Email, prof, profile) -> dict:
    """Deterministic follow-up when no LLM key is set."""
    name = prof.name if prof else (original.opportunity.professor_name if original.opportunity
                                   else "Professor")
    surname = name.split()[-1] if name else ""
    subject = original.subject or "my PhD application"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    body = (
        f"Dear Professor {surname},\n\n"
        f"I wanted to gently follow up on my earlier email about a PhD position in your group. "
        f"I remain very interested in the possibility of working with you and would be glad to "
        f"share any further materials that would help. I appreciate your time and understand how "
        f"busy you are.\n\n"
        f"Kind regards,\n{profile['name']}"
    )
    return {"subject": subject[:80], "body": body}


def generate_followup(session: Session, original: Email) -> Email:
    """Draft a single gated follow-up for an unanswered first-contact email."""
    profile = config_loader.profile()
    prof = original.professor
    cfg = config_loader.config().get("followup", {})

    if llm.available():
        try:
            draft = llm.complete_json(_followup_prompt(original, prof, profile))
        except Exception:
            draft = _fallback_followup(original, prof, profile)
    else:
        draft = _fallback_followup(original, prof, profile)

    subject = draft.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}".strip()

    report = quality_gate.run(
        draft.get("body", ""), subject,
        prof or Professor(name=(original.opportunity.professor_name if original.opportunity else "")),
        [], {},
        word_bounds=(cfg.get("word_min", 40), cfg.get("word_max", 90)),
        mode="followup",
    )

    email = Email(
        opportunity_id=original.opportunity_id,
        professor_id=original.professor_id,
        subject=subject,
        body=draft.get("body"),
        attachments=[],
        quality_gate_passed=report["passed"],
        quality_gate_report=report,
        status="draft_created",
        is_followup=True,
        parent_email_id=original.id,
        gmail_thread_id=original.gmail_thread_id,
    )
    session.add(email)
    session.flush()
    tracker.log_event(session, email.id, "followup_drafted",
                      {"parent_email_id": original.id, "gate_passed": report["passed"]})
    if report["passed"]:
        tracker.transition(session, email, "awaiting_review",
                           {"reason": "followup quality gate passed"})
    session.flush()
    return email


def regenerate(session: Session, email: Email) -> Email:
    """Re-draft an existing email (resets to draft_created then re-runs)."""
    opp = email.opportunity
    prof = email.professor
    if email.status not in ("sent", "cancelled"):
        # Move back to draft_created for a fresh attempt.
        if email.status == "awaiting_review":
            tracker.transition(session, email, "draft_created", {"reason": "regenerate"})
    new = generate_email(session, opp, prof)
    tracker.transition(session, email, "cancelled", {"reason": "superseded by regenerate",
                                                     "new_email_id": new.id})
    return new
