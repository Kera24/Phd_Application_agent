"""FastAPI backend.

Exposes the LangGraph runs, the Human-Approval interrupt queue, thread resume,
read models for the dashboard, settings, and Gmail OAuth status. The Streamlit
dashboard talks only to these endpoints — it never imports the graph directly.

Run:  uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from langgraph.types import Command
from pydantic import BaseModel

from db import session as dbsession
from db.models import Asset, Email, Opportunity, Professor
from modules import config_loader, gmail_client

# In-memory registry of paused threads: thread_id -> last interrupt payload.
# The authoritative state lives in the checkpointer; this is a convenience index
# rebuilt best-effort on startup.
PENDING: dict[str, dict] = {}
GRAPH = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_loader.ensure_dirs()
    dbsession.init_engine(str(config_loader.abspath("database")))
    global GRAPH
    from agent.graph import build_graph
    from agent.checkpointer import build_checkpointer
    GRAPH = build_graph(build_checkpointer())
    # Phase 3: start the recurring reply-scan / follow-up job (draft-only).
    try:
        from modules import scheduler as sched_mod
        sched_mod.register_followup_scan()
    except Exception:  # pragma: no cover - never block startup on the job
        pass
    yield


app = FastAPI(title="ScholarReach API", version="1.0", lifespan=lifespan)


# --- schemas ----------------------------------------------------------------
class RunRequest(BaseModel):
    linkedin_inputs: list[str] = []
    professor_list: list[dict] = []
    target_fields: Optional[list[str]] = None
    target_countries: Optional[list[str]] = None
    thread_id: Optional[str] = None


class ResumeRequest(BaseModel):
    action: str                       # approve | reject | edit
    edits: Optional[dict] = None
    reason: str = ""
    by: str = "dashboard"


class SettingsUpdate(BaseModel):
    approved_send_mode: Optional[bool] = None
    daily_send_cap: Optional[int] = None
    fit_score_threshold: Optional[int] = None
    followup_enabled: Optional[bool] = None
    followup_after_business_days: Optional[int] = None
    reply_detection_enabled: Optional[bool] = None


class DecisionRequest(BaseModel):
    action: str                       # approve | reject | edit
    edits: Optional[dict] = None
    reason: str = ""
    by: str = "dashboard"


def _run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}


def _interrupt_payload(result: dict) -> Optional[dict]:
    intr = result.get("__interrupt__")
    if intr:
        return intr[0].value
    return None


# --- runs -------------------------------------------------------------------
@app.post("/runs")
def start_run(req: RunRequest) -> dict:
    """Invoke the graph. Returns the first approval interrupt, if any."""
    thread_id = req.thread_id or f"run_{uuid.uuid4().hex[:12]}"
    state: dict[str, Any] = {
        "linkedin_inputs": req.linkedin_inputs,
        "professor_list": req.professor_list,
    }
    if req.target_fields is not None:
        state["target_fields"] = req.target_fields
    if req.target_countries is not None:
        state["target_countries"] = req.target_countries

    result = GRAPH.invoke(state, _run_config(thread_id))
    payload = _interrupt_payload(result)
    if payload:
        PENDING[thread_id] = payload
        return {"thread_id": thread_id, "status": "awaiting_approval", "interrupt": payload}
    return {"thread_id": thread_id, "status": "completed"}


# --- approvals --------------------------------------------------------------
def _followup_review_item(e: Email) -> dict:
    """Approval-queue item for a follow-up draft (DB-backed, not a graph thread)."""
    prof = e.professor
    parent = e.parent_email_id
    return {
        "kind": "followup",
        "email_id": e.id,
        "interrupt": {
            "email_id": e.id,
            "subject": e.subject,
            "body": e.body,
            "attachments": e.attachments or [],
            "quality_report": e.quality_gate_report,
            "professor": {"name": prof.name if prof else None},
            "professor_email": prof.email if prof else None,
            "parent_email_id": parent,
            "is_followup": True,
        },
    }


@app.get("/approvals")
def list_approvals() -> dict:
    """List items awaiting a human decision: graph interrupts + follow-up drafts."""
    items = [{"kind": "interrupt", "thread_id": tid, "interrupt": payload}
             for tid, payload in PENDING.items()]
    with dbsession.session_scope() as s:
        followups = (s.query(Email)
                     .filter(Email.is_followup == True,  # noqa: E712
                             Email.status == "awaiting_review")
                     .order_by(Email.id).all())
        items.extend(_followup_review_item(e) for e in followups)
    return {"pending": items}


@app.post("/approvals/{thread_id}/resume")
def resume(thread_id: str, req: ResumeRequest) -> dict:
    """Resume a paused thread with Raj's decision."""
    if GRAPH.get_state(_run_config(thread_id)) is None:
        raise HTTPException(404, "unknown thread")
    cmd = Command(resume={"action": req.action, "edits": req.edits,
                          "reason": req.reason, "by": req.by})
    result = GRAPH.invoke(cmd, _run_config(thread_id))
    payload = _interrupt_payload(result)
    if payload:
        PENDING[thread_id] = payload   # next draft awaiting approval on same run
        return {"thread_id": thread_id, "status": "awaiting_approval", "interrupt": payload}
    PENDING.pop(thread_id, None)
    return {"thread_id": thread_id, "status": "completed"}


# --- follow-up email decisions (DB-backed, reuse the approval queue) ---------
@app.post("/emails/{email_id}/decision")
def decide_email(email_id: int, req: DecisionRequest) -> dict:
    """Approve / edit / reject a follow-up draft. Mirrors the graph approval node.

    On approve AND approved_send_mode, schedules the send via the same gated path
    (scheduler.schedule_send requires status=='approved'); otherwise it stays an
    approved draft. No new send path is introduced.
    """
    from modules import quality_gate, ingest, scheduler as sched_mod, tracker
    from agent import repo
    with dbsession.session_scope() as s:
        email = s.get(Email, email_id)
        if email is None:
            raise HTTPException(404, "unknown email")
        if email.status != "awaiting_review":
            raise HTTPException(409, f"email status is {email.status!r}, not awaiting_review")

        if req.action == "approve":
            tracker.transition(s, email, "approved", {"by": req.by})
            repo.record_approval(s, email, "", "approved", decided_by=req.by, reason=req.reason)
            scheduled = None
            if config_loader.config().get("approved_send_mode"):
                try:
                    send_utc = sched_mod.schedule_send(s, email)
                    scheduled = send_utc.isoformat()
                except Exception as exc:
                    return {"email_id": email_id, "status": "approved",
                            "scheduled": None, "schedule_error": str(exc)}
            return {"email_id": email_id, "status": "approved", "scheduled": scheduled}

        if req.action == "edit":
            edits = req.edits or {}
            if "subject" in edits:
                email.subject = edits["subject"]
            if "body" in edits:
                email.body = edits["body"]
            fcfg = config_loader.config().get("followup", {})
            report = quality_gate.run(
                email.body or "", email.subject or "",
                email.professor or Professor(name=""), [], ingest.asset_paths(s),
                word_bounds=(fcfg.get("word_min", 40), fcfg.get("word_max", 90)),
                mode="followup")
            email.quality_gate_passed = report["passed"]
            email.quality_gate_report = report
            repo.record_approval(s, email, "", "edited", decided_by=req.by,
                                 edits=edits, reason=req.reason)
            return {"email_id": email_id, "status": "edited",
                    "quality_gate_passed": report["passed"]}

        # reject
        tracker.transition(s, email, "cancelled", {"by": req.by, "reason": req.reason})
        repo.record_approval(s, email, "", "rejected", decided_by=req.by, reason=req.reason)
        return {"email_id": email_id, "status": "rejected"}


@app.post("/emails/{email_id}/reply")
def mark_email_replied(email_id: int) -> dict:
    """Manually mark a sent email as having received a reply (Gmail-optional path)."""
    from modules import followups
    with dbsession.session_scope() as s:
        email = s.get(Email, email_id)
        if email is None:
            raise HTTPException(404, "unknown email")
        followups.mark_replied(s, email, by="manual")
    return {"email_id": email_id, "reply_received": True}


# --- follow-ups -------------------------------------------------------------
@app.post("/followups/scan")
def followups_scan() -> dict:
    """Run reply detection + due follow-up generation now. Returns counts."""
    from modules import followups
    with dbsession.session_scope() as s:
        return followups.run_scan(s)


@app.get("/followups")
def list_followups() -> dict:
    """All follow-up emails + the first-contact emails currently due for one."""
    from modules import followups
    with dbsession.session_scope() as s:
        fus = (s.query(Email).filter(Email.is_followup == True)  # noqa: E712
               .order_by(Email.created_at.desc()).all())
        due = followups.due_followups(s)
        return {
            "followups": [{
                "id": e.id, "subject": e.subject, "status": e.status,
                "parent_email_id": e.parent_email_id,
                "professor": e.professor.name if e.professor else None,
                "quality_gate_passed": e.quality_gate_passed,
            } for e in fus],
            "due": [{"id": e.id, "subject": e.subject,
                     "professor": e.professor.name if e.professor else None,
                     "followup_due_date": e.followup_due_date.isoformat() if e.followup_due_date else None}
                    for e in due],
        }


@app.get("/analytics")
def analytics() -> dict:
    from modules import analytics as analytics_mod
    with dbsession.session_scope() as s:
        return analytics_mod.compute_metrics(s)


# --- read models for the dashboard ------------------------------------------
def _email_row(e: Email) -> dict:
    return {
        "id": e.id, "subject": e.subject, "status": e.status,
        "quality_gate_passed": e.quality_gate_passed,
        "quality_gate_report": e.quality_gate_report,
        "professor": e.professor.name if e.professor else None,
        "professor_email": e.professor.email if e.professor else None,
        "opportunity_id": e.opportunity_id,
        "scheduled_send_at_utc": e.scheduled_send_at_utc.isoformat() if e.scheduled_send_at_utc else None,
        "body": e.body, "attachments": e.attachments,
        "summary_pdf_path": e.summary_pdf_path,
        "is_followup": e.is_followup,
        "reply_received": e.reply_received,
        "followup_status": e.followup_status,
        "followup_due_date": e.followup_due_date.isoformat() if e.followup_due_date else None,
    }


@app.get("/pipeline")
def pipeline() -> dict:
    with dbsession.session_scope() as s:
        emails = s.query(Email).order_by(Email.created_at.desc()).all()
        return {"emails": [_email_row(e) for e in emails]}


@app.get("/opportunities")
def opportunities() -> dict:
    with dbsession.session_scope() as s:
        opps = s.query(Opportunity).order_by(Opportunity.fit_score.desc().nullslast()).all()
        return {"opportunities": [{
            "id": o.id, "type": o.opportunity_type, "title": o.position_title,
            "university": o.university, "country": o.country,
            "funding_status": o.funding_status, "funding_evidence": o.funding_evidence,
            "pipeline_status": o.pipeline_status, "fit_score": o.fit_score,
            "score_breakdown": o.score_breakdown, "professor_name": o.professor_name,
            "professor_email": o.professor_email,
            "deadline": o.deadline.isoformat() if o.deadline else None,
            "research_fields": o.research_fields,
        } for o in opps]}


@app.get("/professors")
def professors() -> dict:
    with dbsession.session_scope() as s:
        profs = s.query(Professor).all()
        return {"professors": [{
            "id": p.id, "name": p.name, "email": p.email, "university": p.university,
            "themes": p.research_themes, "gap": p.identified_gap,
            "angle": p.proposed_angle, "papers": p.recent_papers,
        } for p in profs]}


# --- candidate documents (CV / transcript / base summary / SOP) -------------
@app.get("/assets")
def list_assets() -> dict:
    """Currently uploaded candidate documents, one per kind."""
    with dbsession.session_scope() as s:
        assets = s.query(Asset).order_by(Asset.kind).all()
        return {"assets": [{
            "kind": a.kind, "file_name": Path(a.file_path).name,
            "char_count": a.char_count, "warning": a.warning,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in assets]}


@app.post("/assets")
def upload_asset(kind: str = Form(...), file: UploadFile = File(...)) -> dict:
    """Upload/replace a candidate document. Extracts text and stores an Asset.

    `kind` must be one of ingest.KINDS (cv|transcript|summary|sop). The file is
    streamed to a temp path, then handed to ingest.save_upload (which copies it
    into data/uploads, extracts text, and replaces any prior asset of the kind).
    """
    import os
    import tempfile
    from modules import ingest

    if kind not in ingest.KINDS:
        raise HTTPException(400, f"kind must be one of {ingest.KINDS}")
    suffix = Path(file.filename or "").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name
    try:
        with dbsession.session_scope() as s:
            asset = ingest.save_upload(s, kind, tmp_path, original_name=file.filename)
            return {"kind": asset.kind, "file_name": Path(asset.file_path).name,
                    "char_count": asset.char_count, "warning": asset.warning}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# --- settings ---------------------------------------------------------------
@app.get("/settings")
def get_settings() -> dict:
    cfg = config_loader.config()
    return {"approved_send_mode": cfg.get("approved_send_mode"),
            "daily_send_cap": cfg.get("daily_send_cap"),
            "fit_score_threshold": cfg.get("fit_score_threshold"),
            "followup_enabled": cfg.get("followup", {}).get("enabled", True),
            "followup_after_business_days": cfg.get("followup", {}).get("after_business_days", 10),
            "reply_detection_enabled": cfg.get("reply_detection", {}).get("enabled", True),
            "gmail_authorised": gmail_client.is_authorised()}


@app.put("/settings")
def update_settings(req: SettingsUpdate) -> dict:
    cfg = config_loader.config()
    if req.approved_send_mode is not None:
        cfg["approved_send_mode"] = req.approved_send_mode
    if req.daily_send_cap is not None:
        cfg["daily_send_cap"] = req.daily_send_cap
    if req.fit_score_threshold is not None:
        cfg["fit_score_threshold"] = req.fit_score_threshold
    if req.followup_enabled is not None:
        cfg.setdefault("followup", {})["enabled"] = req.followup_enabled
    if req.followup_after_business_days is not None:
        cfg.setdefault("followup", {})["after_business_days"] = req.followup_after_business_days
    if req.reply_detection_enabled is not None:
        cfg.setdefault("reply_detection", {})["enabled"] = req.reply_detection_enabled
    config_loader.save_config(cfg)
    # Reflect follow-up enable/disable + cadence change into the live job.
    try:
        from modules import scheduler as sched_mod
        if cfg.get("followup", {}).get("enabled", True):
            sched_mod.register_followup_scan()
        else:
            sched_mod.get_scheduler().remove_job("followup_scan")
    except Exception:  # pragma: no cover - job may not exist
        pass
    return get_settings()


# --- gmail oauth ------------------------------------------------------------
@app.get("/gmail/status")
def gmail_status() -> dict:
    return {"authorised": gmail_client.is_authorised(),
            "credentials_path": str(config_loader.abspath("gmail_credentials"))}


@app.post("/gmail/authorize")
def gmail_authorize() -> dict:
    """Trigger the installed-app OAuth flow (opens a local consent window).

    For a desktop/local deployment this completes via google's local-server
    callback; the token is then cached for reuse.
    """
    try:
        gmail_client.get_service()
        return {"authorised": True}
    except Exception as exc:
        raise HTTPException(400, f"OAuth failed: {exc}")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "postgres": dbsession.is_postgres()}
