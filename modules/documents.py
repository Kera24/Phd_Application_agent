"""Generate application documents from the deep-research brief + applicant profile.

Kinds: email | sop | cover | proposal. Every document is grounded: claims about
the applicant use ONLY the profile (no fabrication), and any paper is referenced
by an exact title from the brief's verified citations. Documents are editable and
can be rendered to (multi-page) PDF.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from db.models import GeneratedDocument, Opportunity, Professor, ResearchBrief
from modules import config_loader, llm

DOC_KINDS = ("email", "sop", "cover", "proposal")

# Map keywords found in a posting's required_documents -> a generated doc kind.
# Order matters: more specific phrases first. `email` is never produced here (the
# sendable outreach email is a pipeline Email, not a GeneratedDocument).
_REQUIRED_DOC_PATTERNS = (
    ("research proposal", "proposal"),
    ("research statement", "proposal"),
    ("research plan", "proposal"),
    ("proposal", "proposal"),
    ("statement of purpose", "sop"),
    ("personal statement", "sop"),
    ("sop", "sop"),
    ("letter of motivation", "cover"),
    ("motivation letter", "cover"),
    ("motivation", "cover"),
    ("cover letter", "cover"),
)


def kinds_for_opportunity(opp: Opportunity) -> list[str]:
    """Which application documents the listing asks for (excluding the email).

    Reads ``opp.required_documents`` (free-text strings parsed from the posting)
    and maps them to generated doc kinds. Returns a de-duplicated list preserving
    the canonical sop -> cover -> proposal order. Empty / unspecified -> ``[]``.
    """
    wanted: set[str] = set()
    for item in (opp.required_documents or []):
        text = str(item).lower()
        for keyword, kind in _REQUIRED_DOC_PATTERNS:
            if keyword in text:
                wanted.add(kind)
                break
    return [k for k in ("sop", "cover", "proposal") if k in wanted]

_TITLES = {
    "email": "Outreach email",
    "sop": "Statement of Purpose",
    "cover": "Cover / Motivation letter",
    "proposal": "Research proposal",
}

GEN_SYSTEM = (
    "You write PhD application documents. Use ONLY the applicant facts in the provided "
    "profile — never invent experience, skills, or results. Reference the professor's "
    "work only via the exact paper titles provided. Be specific, honest, and concise."
)

# Per-kind instruction + rough length.
_SPEC = {
    "email": "a 150-200 word outreach email. Reference >=1 cited paper by exact title, "
             "state the research gap, the research question, and your proposed approach, "
             "and end with a clear, modest ask for a conversation.",
    "sop": "a Statement of Purpose (~600-800 words, multiple paragraphs): motivation; your "
           "background (from the profile only); specific fit with this professor's work "
           "(the gap, question, approach, citing papers by exact title); why this "
           "university/program; and your goals.",
    "cover": "a cover / motivation letter (~400-500 words) for this specific position, "
             "connecting your background to the role and the professor's research direction.",
    "proposal": "a short research proposal (~600-800 words) with sections: Background, "
                "Research Gap, Research Question, Proposed Approach/Methodology, Expected "
                "Contribution, and References (list the cited papers by exact title).",
}


def _context(opp: Opportunity, prof: Optional[Professor], brief: dict, profile: dict) -> dict:
    cites = "; ".join(f'"{c.get("title")}" ({c.get("year")})'
                      for c in (brief.get("citations") or []))
    return {
        "name": profile.get("name", ""),
        "prof": (prof.name if prof else opp.professor_name) or "the professor",
        "university": opp.university or "",
        "position": opp.position_title or "the advertised position",
        "gap": brief.get("chosen_gap") or "",
        "question": brief.get("research_question") or "",
        "approach": brief.get("proposed_approach") or "",
        "cites": cites or "(none)",
        "profile_json": json.dumps(profile, default=str)[:3500],
    }


def _prompt(kind: str, ctx: dict) -> str:
    return (
        f"Write {_SPEC[kind]}\n\n"
        f"APPLICANT PROFILE (ground truth):\n{ctx['profile_json']}\n\n"
        f"PROFESSOR: {ctx['prof']} — {ctx['university']}\n"
        f"POSITION: {ctx['position']}\n"
        f"RESEARCH GAP: {ctx['gap']}\n"
        f"RESEARCH QUESTION: {ctx['question']}\n"
        f"PROPOSED APPROACH: {ctx['approach']}\n"
        f"PAPERS YOU MAY CITE (exact titles only): {ctx['cites']}\n\n"
        "Output only the document text (no preamble, no markdown headers unless natural)."
    )


def _fallback(kind: str, ctx: dict) -> str:
    return (
        f"[Draft generated without an LLM — please refine.]\n\n"
        f"Dear Professor {ctx['prof'].split()[-1] if ctx['prof'] else ''},\n\n"
        f"Research gap: {ctx['gap']}\n"
        f"Research question: {ctx['question']}\n"
        f"Proposed approach: {ctx['approach']}\n\n"
        f"Relevant work: {ctx['cites']}\n\n"
        f"Kind regards,\n{ctx['name']}"
    )


def _generate_one(kind: str, opp, prof, brief: dict, profile: dict) -> dict:
    ctx = _context(opp, prof, brief, profile)
    if llm.available():
        try:
            content = llm.complete(_prompt(kind, ctx), system=GEN_SYSTEM, max_tokens=2000)
        except Exception:
            content = _fallback(kind, ctx)
    else:
        content = _fallback(kind, ctx)
    title = f"{_TITLES[kind]} — {ctx['prof']}"
    return {"kind": kind, "title": title, "content": content.strip()}


def generate_documents(session: Session, opp: Opportunity, kinds) -> list[dict]:
    """Generate (and persist, replacing same-kind) the requested documents."""
    brief_row = (session.query(ResearchBrief).filter_by(opportunity_id=opp.id)
                 .order_by(ResearchBrief.id.desc()).first())
    brief = (brief_row.data if brief_row else {}) or {}
    prof = session.get(Professor, opp.professor_id) if opp.professor_id else None
    profile = config_loader.profile()

    out = []
    for kind in kinds:
        if kind not in DOC_KINDS:
            continue
        gen = _generate_one(kind, opp, prof, brief, profile)
        for old in session.query(GeneratedDocument).filter_by(
                opportunity_id=opp.id, kind=kind).all():
            session.delete(old)
        row = GeneratedDocument(opportunity_id=opp.id, kind=kind,
                                title=gen["title"], content=gen["content"])
        session.add(row)
        session.flush()
        out.append({"id": row.id, "kind": kind, "title": row.title, "content": row.content})
    return out


_PDF_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 2cm; }}
body {{ font-family: Georgia, serif; font-size: 11pt; line-height: 1.5; color: #111; }}
h1 {{ font-size: 15pt; margin: 0 0 14px; }}
p {{ margin: 0 0 10px; text-align: justify; white-space: pre-wrap; }}
</style></head><body><h1>{title}</h1>{body}</body></html>"""


def render_document_pdf(title: str, content: str, out_path: str) -> str:
    """Render a document to a (multi-page) PDF and return its path."""
    from weasyprint import HTML  # lazy heavy import
    body = "".join(f"<p>{html.escape(p.strip())}</p>"
                   for p in (content or "").split("\n\n") if p.strip())
    doc_html = _PDF_TEMPLATE.format(title=html.escape(title or "Document"), body=body)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=doc_html).write_pdf(str(p))
    return str(p)
