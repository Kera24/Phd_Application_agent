"""Professor research: retrieve verified recent papers, then have Claude produce
themes + a grounded gap + a proposed angle — with strict citation verification.

Non-Negotiable Rule 3 / Section 4: every paper title in the LLM output must
exactly match a retrieved record. Reject + retry once, then fail loudly.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Professor
from modules import config_loader, llm, paper_apis


class CitationVerificationError(RuntimeError):
    pass


def _norm(title: str) -> str:
    return " ".join((title or "").lower().split()).rstrip(".")


def verify_titles(claimed: list[str], retrieved: list[dict]) -> tuple[bool, list[str]]:
    """Return (ok, unverified_titles)."""
    valid = {_norm(p["title"]) for p in retrieved}
    bad = [t for t in claimed if _norm(t) not in valid]
    return (len(bad) == 0, bad)


RESEARCH_SYSTEM = (
    "You analyse a professor's recent papers to help a PhD applicant write a "
    "specific, honest outreach email. You may ONLY reference paper titles that "
    "appear verbatim in the provided list. Never invent or paraphrase a title. "
    "Ground the gap and angle in the supplied abstracts."
)


def _research_prompt(name: str, papers: list[dict], profile: dict) -> str:
    paper_lines = "\n".join(
        f"- TITLE: {p['title']} ({p.get('year')}, {p.get('venue')})\n  ABSTRACT: {p.get('abstract', '')[:600]}"
        for p in papers
    )
    proj = "; ".join(
        f"{p['title']} — {p['detail']}" for p in profile.get("research_projects", [])
    )
    return (
        f"Professor: {name}\n\n"
        f"Applicant background: {proj}\n\n"
        f"Recent papers (use titles VERBATIM):\n{paper_lines}\n\n"
        "Return JSON with keys:\n"
        '  "relevant_papers": [titles] (2-3, copied exactly from the list),\n'
        '  "research_themes": [short strings],\n'
        '  "identified_gap": one concrete limitation/open question grounded in the abstracts,\n'
        '  "proposed_angle": one angle the applicant could contribute, tied to their real background.\n'
    )


def research_professor(
    session: Session,
    name: str,
    *,
    affiliation: str = "",
    profile_url: Optional[str] = None,
    scholar_url: Optional[str] = None,
    email: Optional[str] = None,
) -> Professor:
    """Run the full research pipeline and upsert the Professor (deduped by email)."""
    profile = config_loader.profile()
    papers, source = paper_apis.recent_papers(name, affiliation, limit=5)

    themes, gap, angle, relevant = [], None, None, []
    if papers and llm.available():
        relevant, themes, gap, angle = _analyse_with_verification(name, papers, profile)

    prof = _upsert(session, name, email, affiliation, profile_url, scholar_url)
    prof.recent_papers = papers
    prof.research_themes = themes
    prof.identified_gap = gap
    prof.proposed_angle = angle
    prof.last_researched_at = dt.datetime.now(dt.timezone.utc)
    session.flush()
    return prof


def _analyse_with_verification(name, papers, profile):
    """LLM analysis with one retry on citation failure, then fail loudly."""
    prompt = _research_prompt(name, papers, profile)
    last_bad: list[str] = []
    for attempt in range(2):
        retry_note = (
            f"\n\nYour previous answer cited titles not in the list: {last_bad}. "
            "Use ONLY exact titles from the list."
            if attempt and last_bad else ""
        )
        data = llm.complete_json(prompt + retry_note, system=RESEARCH_SYSTEM)
        relevant = data.get("relevant_papers", []) or []
        ok, bad = verify_titles(relevant, papers)
        if ok:
            return (
                relevant,
                data.get("research_themes", []) or [],
                data.get("identified_gap"),
                data.get("proposed_angle"),
            )
        last_bad = bad
    raise CitationVerificationError(
        f"Professor research for {name!r} cited unverifiable titles after retry: {last_bad}"
    )


def _upsert(session, name, email, affiliation, profile_url, scholar_url) -> Professor:
    """Dedupe by email when present, else by (name + university)."""
    prof = None
    if email:
        prof = session.query(Professor).filter_by(email=email).first()
    if prof is None:
        prof = (
            session.query(Professor)
            .filter(Professor.name == name, Professor.university == affiliation)
            .first()
        )
    if prof is None:
        prof = Professor(name=name, email=email, university=affiliation,
                         profile_url=profile_url, scholar_url=scholar_url)
        session.add(prof)
    else:
        prof.email = prof.email or email
        prof.university = prof.university or affiliation
        prof.profile_url = prof.profile_url or profile_url
        prof.scholar_url = prof.scholar_url or scholar_url
    session.flush()
    return prof
