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

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from db import session as dbsession
from db.models import Email, Opportunity, Professor
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
@app.get("/approvals")
def list_approvals() -> dict:
    """List paused threads awaiting a human decision."""
    return {"pending": [{"thread_id": tid, "interrupt": payload}
                        for tid, payload in PENDING.items()]}


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


# --- settings ---------------------------------------------------------------
@app.get("/settings")
def get_settings() -> dict:
    cfg = config_loader.config()
    return {"approved_send_mode": cfg.get("approved_send_mode"),
            "daily_send_cap": cfg.get("daily_send_cap"),
            "fit_score_threshold": cfg.get("fit_score_threshold"),
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
    config_loader.save_config(cfg)
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
