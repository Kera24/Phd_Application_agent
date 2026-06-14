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

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from langgraph.types import Command
from pydantic import BaseModel

from db import session as dbsession
from db.models import (
    Asset, Email, GeneratedDocument, Opportunity, Professor, ResearchBrief,
)
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


class FillPlanRequest(BaseModel):
    url: Optional[str] = None         # override the opportunity's application_link


class DiscoverRequest(BaseModel):
    field: Optional[str] = None
    country: Optional[str] = None
    max_per_query: int = 5


class DiscoverRunRequest(BaseModel):
    url: str


class DocumentsRequest(BaseModel):
    kinds: list[str] = ["email", "sop", "cover", "proposal"]


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


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


@app.post("/opportunities/ingest-file")
def ingest_file(file: UploadFile = File(...)) -> dict:
    """Extract a posting from an uploaded image or PDF, then run the full pipeline.

    Text PDFs are read with pypdf (free, no LLM). Images and scanned/short PDFs
    are transcribed with Claude vision. The extracted text is fed into the same
    graph as /runs, so the funding gate / research / email steps are unchanged.
    """
    import os
    import tempfile
    from modules import ingest, vision_extract

    fname = file.filename or "upload"
    suffix = Path(fname).suffix.lower()
    if suffix not in vision_extract.SUPPORTED_SUFFIXES:
        raise HTTPException(
            400, f"Unsupported file type {suffix!r}; use one of "
                 f"{', '.join(vision_extract.SUPPORTED_SUFFIXES)}.")
    data = file.file.read()

    text = ""
    method = None
    # Text-PDF fast path: pypdf, no LLM required.
    if suffix == ".pdf":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            text = ingest.extract_text(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if len(text) >= ingest.MIN_CHARS:
            method = "pdf_text"
        else:
            text = ""  # too little extracted -> likely scanned; fall through to vision

    # Vision path: images, or scanned/short PDFs.
    if not text:
        try:
            text = vision_extract.transcribe_file(data, fname)
            method = "vision"
        except vision_extract.VisionUnavailable as exc:
            raise HTTPException(422, f"Could not extract text from {fname!r}: {exc}")

    if not text.strip():
        raise HTTPException(422, f"No text could be extracted from {fname!r}.")

    result = start_run(RunRequest(linkedin_inputs=[text]))
    result["extraction"] = {"method": method, "char_count": len(text)}
    return result


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


# --- durable cron tick ------------------------------------------------------
@app.post("/cron/tick")
def cron_tick(x_cron_token: Optional[str] = Header(None)) -> dict:
    """Durable heartbeat for a host that sleeps (Render free tier).

    An external scheduler (cron-job.org / GitHub Actions / Supabase pg_cron)
    POSTs here on an interval. This both wakes the instance and does the
    time-sensitive work: deliver any due scheduled sends, then run the reply /
    follow-up scan. Both steps are idempotent, so duplicate ticks are harmless.

    Protected by the CRON_TOKEN env var when set: callers must send a matching
    `X-Cron-Token` header.
    """
    import os
    expected = os.environ.get("CRON_TOKEN")
    if expected and x_cron_token != expected:
        raise HTTPException(401, "invalid cron token")
    from modules import followups, scheduler as sched_mod
    with dbsession.session_scope() as s:
        sends = sched_mod.dispatch_due_sends(s)
    with dbsession.session_scope() as s:
        scan = followups.run_scan(s)
    return {"sends": sends, "scan": scan}


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


@app.post("/opportunities/{opp_id}/fill-plan")
def fill_plan(opp_id: int, req: FillPlanRequest) -> dict:
    """Build an assisted application fill-plan for an opportunity's form.

    Fetches the application page (robots-aware, cached), extracts its form fields,
    and maps the candidate profile onto them. The plan is consumed locally by
    scripts/fill_application.py (Playwright) — nothing is submitted here.
    """
    from modules import app_filler, config_loader, http_cache, llm
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, opp_id)
        if opp is None:
            raise HTTPException(404, "unknown opportunity")
        url = req.url or opp.application_link
        if not url:
            raise HTTPException(400, "no application_link on this opportunity; pass a url")
        html = http_cache.get(url)
        if html is None:
            raise HTTPException(422, f"could not fetch {url!r} (blocked by robots.txt or failed)")
        fields = app_filler.extract_form_fields(html)
        plan = app_filler.build_fill_plan(opp, config_loader.profile(), fields)
        return {"opportunity_id": opp_id, "url": url, "field_count": len(fields),
                "method": "llm" if llm.available() else "heuristic", "plan": plan}


# --- deep research + generated documents ------------------------------------
@app.post("/opportunities/{opp_id}/deep-research")
def deep_research(opp_id: int) -> dict:
    """Run the deep-research workflow for an opportunity; persist + return the brief."""
    from modules import deep_research as dr
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, opp_id)
        if opp is None:
            raise HTTPException(404, "unknown opportunity")
        brief = dr.run_deep_research(s, opp)
        return {"opportunity_id": opp_id, "brief": brief}


@app.get("/opportunities/{opp_id}/deep-research")
def get_deep_research(opp_id: int) -> dict:
    with dbsession.session_scope() as s:
        row = (s.query(ResearchBrief).filter_by(opportunity_id=opp_id)
               .order_by(ResearchBrief.id.desc()).first())
        return {"opportunity_id": opp_id, "brief": row.data if row else None}


@app.post("/opportunities/{opp_id}/documents")
def generate_documents_ep(opp_id: int, req: DocumentsRequest) -> dict:
    from modules import documents
    with dbsession.session_scope() as s:
        opp = s.get(Opportunity, opp_id)
        if opp is None:
            raise HTTPException(404, "unknown opportunity")
        docs = documents.generate_documents(s, opp, req.kinds)
        return {"opportunity_id": opp_id, "documents": docs}


@app.get("/opportunities/{opp_id}/documents")
def list_documents(opp_id: int) -> dict:
    with dbsession.session_scope() as s:
        rows = (s.query(GeneratedDocument).filter_by(opportunity_id=opp_id)
                .order_by(GeneratedDocument.kind).all())
        return {"documents": [{"id": d.id, "kind": d.kind, "title": d.title,
                               "content": d.content} for d in rows]}


@app.put("/documents/{doc_id}")
def update_document(doc_id: int, req: DocumentUpdate) -> dict:
    with dbsession.session_scope() as s:
        doc = s.get(GeneratedDocument, doc_id)
        if doc is None:
            raise HTTPException(404, "unknown document")
        if req.title is not None:
            doc.title = req.title
        if req.content is not None:
            doc.content = req.content
        s.flush()
        return {"id": doc.id, "kind": doc.kind, "title": doc.title, "content": doc.content}


@app.get("/documents/{doc_id}/pdf")
def document_pdf(doc_id: int):
    """Render a generated document to PDF and stream it."""
    import os
    import tempfile
    from fastapi.responses import FileResponse
    from modules import documents
    with dbsession.session_scope() as s:
        doc = s.get(GeneratedDocument, doc_id)
        if doc is None:
            raise HTTPException(404, "unknown document")
        title, content, kind = doc.title, doc.content, doc.kind
    out = os.path.join(tempfile.gettempdir(), f"doc_{doc_id}.pdf")
    try:
        documents.render_document_pdf(title, content, out)
    except Exception as exc:
        raise HTTPException(500, f"PDF render failed (WeasyPrint system libs?): {exc}")
    return FileResponse(out, media_type="application/pdf", filename=f"{kind}_{doc_id}.pdf")


@app.post("/discover")
def discover(req: DiscoverRequest) -> dict:
    """Proactively search for funded-PhD postings (review-only — no DB writes)."""
    import os
    from modules import discovery
    with dbsession.session_scope() as s:
        candidates = discovery.discover_candidates(
            s, field=req.field, country=req.country, max_per_query=req.max_per_query)
    return {"candidates": candidates, "count": len(candidates),
            "tavily_enabled": bool(os.environ.get("TAVILY_API_KEY"))}


@app.post("/discover/run")
def discover_run(req: DiscoverRunRequest) -> dict:
    """Fetch a chosen discovered posting and run it through the pipeline."""
    from modules import discovery
    text = discovery.fetch_page_text(req.url)
    if not text:
        raise HTTPException(422, f"could not fetch {req.url!r} (blocked or empty)")
    result = start_run(RunRequest(linkedin_inputs=[text]))
    result["source_url"] = req.url
    return result


@app.get("/professors")
def professors() -> dict:
    with dbsession.session_scope() as s:
        profs = s.query(Professor).all()
        return {"professors": [{
            "id": p.id, "name": p.name, "email": p.email, "university": p.university,
            "themes": p.research_themes, "gap": p.identified_gap,
            "angle": p.proposed_angle, "papers": p.recent_papers,
        } for p in profs]}


# --- applicant profile (student details for applications) -------------------
@app.get("/profile")
def get_profile() -> dict:
    """Effective candidate profile (YAML base + DB applicant overrides merged)."""
    return {"profile": config_loader.profile(),
            "overrides": config_loader.applicant_overrides()}


@app.put("/profile")
def put_profile(data: dict) -> dict:
    """Replace the editable applicant profile (the fields the Profile page manages)."""
    saved = config_loader.save_applicant(data)
    return {"saved": saved, "profile": config_loader.profile()}


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


def _gmail_redirect_uri() -> str:
    import os
    base = os.environ.get("PUBLIC_BASE_URL")
    if not base:
        raise HTTPException(400, "PUBLIC_BASE_URL is not set on the backend "
                                 "(needed for the Gmail OAuth redirect).")
    return base.rstrip("/") + "/gmail/callback"


@app.get("/gmail/authorize")
def gmail_authorize() -> dict:
    """Start the hosted web OAuth flow: return Google's consent URL to open."""
    try:
        url, _state = gmail_client.build_auth_url(_gmail_redirect_uri())
        return {"auth_url": url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"could not start Gmail OAuth: {exc}")


@app.get("/gmail/callback")
def gmail_callback(code: str = "", state: str = "", error: str = ""):
    """Google redirects here after consent; exchange the code and store the token."""
    from fastapi.responses import HTMLResponse
    if error:
        return HTMLResponse(f"<h3>Gmail authorization failed: {error}</h3>")
    try:
        gmail_client.complete_auth(code, _gmail_redirect_uri())
        return HTMLResponse(
            "<h3>✅ Gmail connected.</h3><p>You can close this tab and return to "
            "ScholarReach (refresh the Settings page).</p>")
    except Exception as exc:
        return HTMLResponse(f"<h3>Gmail connection failed:</h3><pre>{exc}</pre>")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "postgres": dbsession.is_postgres()}
