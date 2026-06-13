"""LangChain tools — the only sanctioned way nodes touch the outside world.

Every tool:
  * has typed (Pydantic) args via the @tool signature,
  * returns a plain dict with an explicit `ok` flag — never raises silently,
  * is independently unit-testable.

DB-touching tools open their own session (init_engine must have run). The
gmail_send_tool is hard-gated and additionally guarded at the Scheduler node.
"""
from __future__ import annotations

import re
from typing import Optional

from langchain_core.tools import tool

from db import session as dbsession
from db.models import Email, Opportunity, Professor
from modules import (
    config_loader,
    discovery,
    gmail_client,
    http_cache,
    ingest,
    paper_apis,
    scheduler as sched_mod,
    summary_gen,
    timezones,
)


def _ok(**kw):
    return {"ok": True, **kw}


def _err(msg, **kw):
    return {"ok": False, "error": str(msg), **kw}


# --- Search ------------------------------------------------------------------

@tool
def web_search_tool(query: str, max_results: int = 8) -> dict:
    """Search the web via Tavily. Returns {ok, results:[{title,url,content}]}."""
    try:
        return _ok(results=discovery.tavily_search(query, max_results=max_results))
    except Exception as exc:
        return _err(exc, results=[])


@tool
def professor_profile_search_tool(name: str, affiliation: str = "") -> dict:
    """Find a professor's profile/email via a targeted Tavily query."""
    try:
        q = f'{name} {affiliation} faculty profile email'.strip()
        results = discovery.tavily_search(q, max_results=5)
        email = None
        for r in results:
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", r.get("content", ""))
            if m:
                email = m.group(0)
                break
        return _ok(results=results, email=email)
    except Exception as exc:
        return _err(exc, results=[], email=None)


@tool
def semantic_scholar_tool(name: str, affiliation: str = "", limit: int = 5) -> dict:
    """Retrieve a researcher's most recent papers from Semantic Scholar."""
    try:
        return _ok(papers=paper_apis.semantic_scholar_recent(name, affiliation, limit),
                   source_api="semantic_scholar")
    except Exception as exc:
        return _err(exc, papers=[])


@tool
def arxiv_tool(name: str, limit: int = 5) -> dict:
    """Retrieve a researcher's most recent papers from arXiv (secondary source)."""
    try:
        return _ok(papers=paper_apis.arxiv_recent(name, limit), source_api="arxiv")
    except Exception as exc:
        return _err(exc, papers=[])


@tool
def dblp_tool(name: str, limit: int = 5) -> dict:
    """Retrieve a researcher's most recent papers from DBLP (fallback source)."""
    try:
        return _ok(papers=paper_apis.dblp_recent(name, limit), source_api="dblp")
    except Exception as exc:
        return _err(exc, papers=[])


@tool
def university_page_reader_tool(url: str) -> dict:
    """Fetch a university/lab page (robots-aware, cached) and return readable text."""
    try:
        html = http_cache.get(url)
        if html is None:
            return _err("fetch disallowed or failed", text="")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return _ok(text=text[:8000], url=url)
    except Exception as exc:
        return _err(exc, text="")


@tool
def funding_evidence_tool(text: str) -> dict:
    """Detect funding-scheme signals (ERC/DFG/EPSRC/SNSF/ARC/DAAD/MSCA...) in page text."""
    try:
        signals = discovery.funding_likelihood_evidence(text)
        return _ok(signals=signals, likely_funded=bool(signals))
    except Exception as exc:
        return _err(exc, signals=[])


# --- Documents / PDF ---------------------------------------------------------

@tool
def document_reader_tool(file_path: str) -> dict:
    """Extract text from a PDF (pypdf). Warns if extraction looks scanned."""
    try:
        text = ingest.extract_text(file_path)
        warning = None if len(text) >= ingest.MIN_CHARS else (
            f"Only {len(text)} chars extracted (<{ingest.MIN_CHARS}); may be scanned."
        )
        return _ok(text=text, char_count=len(text), warning=warning)
    except Exception as exc:
        return _err(exc, text="")


@tool
def pdf_generator_tool(html: str, out_path: str) -> dict:
    """Render HTML to a single-page PDF (WeasyPrint). Fails if it overflows."""
    try:
        path = summary_gen.render_html_to_pdf(html, out_path)
        return _ok(pdf_path=path)
    except Exception as exc:
        return _err(exc)


# --- Gmail (draft always allowed; send hard-gated) ---------------------------

@tool
def gmail_draft_tool(to: str, subject: str, body: str,
                     attachment_files: Optional[list] = None) -> dict:
    """Create a Gmail draft (default, always-permitted path)."""
    try:
        did = gmail_client.create_draft_from_fields(to, subject, body, attachment_files or [])
        return _ok(draft_id=did)
    except Exception as exc:
        return _err(exc)


@tool
def gmail_send_tool(to: str, subject: str, body: str,
                    attachment_files: Optional[list] = None) -> dict:
    """Send an email. HARD-GATED: refuses unless approved_send_mode is true.

    This tool must only be reached after the Human Approval interrupt and the
    Scheduler node's dedupe/approval re-checks.
    """
    try:
        if not config_loader.config().get("approved_send_mode"):
            return _err("approved_send_mode is false — send refused.")
        mid = gmail_client.send_from_fields(to, subject, body, attachment_files or [])
        return _ok(message_id=mid)
    except Exception as exc:
        return _err(exc)


# --- Timezone / scheduling ---------------------------------------------------

@tool
def timezone_resolver_tool(university: str = "", city: str = "",
                           state: str = "", country: str = "") -> dict:
    """Resolve a professor's IANA timezone (university->city->IANA; US-state aware)."""
    try:
        r = timezones.resolve(university=university, city=city, state=state, country=country)
        return _ok(zone=r.zone, flagged=r.flagged, basis=r.basis)
    except Exception as exc:
        return _err(exc, zone="UTC", flagged=True)


@tool
def scheduler_tool(university: str = "", city: str = "", country: str = "") -> dict:
    """Compute the next valid send time (Mon-Thu 08:00-09:00 local) in UTC."""
    try:
        import datetime as dt
        from zoneinfo import ZoneInfo
        tz = timezones.resolve(university=university, city=city, country=country)
        now_local = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo(tz.zone))
        send_local = sched_mod.next_send_time(now_local)
        return _ok(send_at_utc=send_local.astimezone(dt.timezone.utc).isoformat(),
                   professor_tz=tz.zone, tz_flagged=tz.flagged)
    except Exception as exc:
        return _err(exc)


# --- Database / dedupe -------------------------------------------------------

@tool
def duplicate_check_tool(professor_email: str = "", professor_name: str = "",
                         university: str = "", title: str = "") -> dict:
    """Check whether a professor or opportunity already exists (Rule 5)."""
    try:
        with dbsession.session_scope() as s:
            prof = discovery.is_duplicate_professor(
                s, professor_email or None, professor_name, university)
            opp_dup = discovery.is_duplicate_opportunity(
                s, university or None, professor_email or None, title or None)
            return _ok(professor_exists=prof is not None,
                       professor_id=prof.id if prof else None,
                       opportunity_exists=opp_dup)
    except Exception as exc:
        return _err(exc)


@tool
def database_tool(operation: str, table: str, payload: Optional[dict] = None,
                  row_id: Optional[int] = None) -> dict:
    """Minimal CRUD passthrough for opportunities/professors/emails.

    operation: get|insert|update. Most node DB work uses agent.repo directly;
    this exists for completeness and ad-hoc dashboard/debug use.
    """
    models = {"opportunities": Opportunity, "professors": Professor, "emails": Email}
    model = models.get(table)
    if model is None:
        return _err(f"unknown table {table!r}")
    try:
        with dbsession.session_scope() as s:
            if operation == "get":
                obj = s.get(model, row_id)
                return _ok(found=obj is not None,
                           row={c.name: getattr(obj, c.name) for c in model.__table__.columns} if obj else None)
            if operation == "insert":
                obj = model(**(payload or {}))
                s.add(obj); s.flush()
                return _ok(id=obj.id)
            if operation == "update":
                obj = s.get(model, row_id)
                if not obj:
                    return _err("row not found")
                for k, v in (payload or {}).items():
                    setattr(obj, k, v)
                return _ok(id=obj.id)
            return _err(f"unknown operation {operation!r}")
    except Exception as exc:
        return _err(exc)


ALL_TOOLS = [
    web_search_tool, professor_profile_search_tool, semantic_scholar_tool,
    arxiv_tool, dblp_tool, university_page_reader_tool, funding_evidence_tool,
    document_reader_tool, pdf_generator_tool, gmail_draft_tool, gmail_send_tool,
    timezone_resolver_tool, scheduler_tool, duplicate_check_tool, database_tool,
]
