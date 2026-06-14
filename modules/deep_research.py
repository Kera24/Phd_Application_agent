"""Deep research workflow (Claude-orchestrated).

Goes deeper than the single-shot prof_research node: gathers the professor's
recent papers (paper APIs) AND fetches their lab/homepage/Scholar pages, then
runs a multi-step grounded synthesis:

  papers + page context
    -> themes + per-paper contributions
    -> candidate research gaps (each grounded in specific verified papers)
    -> pick the best gap for THIS applicant
    -> a precise research question + a proposed approach tied to the applicant's
       real background.

Grounding rules preserved: every cited paper title must match a retrieved one
(prof_research.verify_titles); unverifiable citations are dropped, never kept.
Persists a ResearchBrief and refreshes the Professor's gap/angle/themes.
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Opportunity, Professor, ResearchBrief
from modules import config_loader, llm, paper_apis, prof_research

ANALYSIS_SYSTEM = (
    "You analyse a professor's recent papers and lab pages to map their research. "
    "You may ONLY reference paper titles that appear verbatim in the provided list. "
    "Never invent or paraphrase a title. Ground every claim in the supplied abstracts/pages."
)
SYNTH_SYSTEM = (
    "You help a PhD applicant find a specific, honest research angle. Use ONLY the "
    "applicant's real background and the provided gaps. Never fabricate applicant "
    "experience. The research question and approach must be concrete and grounded."
)


def _fetch_prof_pages(opp: Opportunity, prof: Optional[Professor], limit: int = 6000) -> str:
    """Fetch the professor's profile/lab page(s) as readable text (best-effort)."""
    from modules import discovery
    urls = []
    for u in (getattr(prof, "profile_url", None), getattr(prof, "scholar_url", None),
              opp.professor_profile_url, opp.application_link):
        if u and u not in urls:
            urls.append(u)
    chunks = []
    for u in urls[:3]:
        text = discovery.fetch_page_text(u, limit=limit)
        if text:
            chunks.append(f"[{u}]\n{text}")
    return "\n\n".join(chunks)[: limit * 2]


def _papers_block(papers: list[dict]) -> str:
    return "\n".join(
        f"- TITLE: {p['title']} ({p.get('year')}, {p.get('venue')})\n"
        f"  ABSTRACT: {(p.get('abstract') or '')[:600]}"
        for p in papers
    )


def _analyse(name: str, papers: list[dict], page_context: str) -> dict:
    prompt = (
        f"Professor: {name}\n\n"
        f"Recent papers (reference titles VERBATIM):\n{_papers_block(papers)}\n\n"
        f"Lab / homepage context (may be empty):\n{page_context[:4000]}\n\n"
        "Return JSON with keys:\n"
        '  "themes": [short strings],\n'
        '  "paper_notes": [{"title": exact title, "contribution": one line}],\n'
        '  "candidate_gaps": [{"gap": concrete limitation/open question, '
        '"related_titles": [exact titles]}]  (2-3 gaps).\n'
    )
    return llm.complete_json(prompt, system=ANALYSIS_SYSTEM) or {}


def _synthesise(name: str, candidate_gaps: list[dict], profile: dict) -> dict:
    projects = "; ".join(
        f"{p.get('title')}: {p.get('detail')}" for p in profile.get("research_projects", []))
    prompt = (
        f"Professor: {name}\n\n"
        f"Applicant background (ground truth — do not exceed it):\n{projects}\n\n"
        f"Candidate gaps:\n{json.dumps(candidate_gaps)[:3000]}\n\n"
        "Choose the single gap best matched to the applicant's background, then return JSON:\n"
        '  "chosen_gap": string,\n'
        '  "research_question": one precise question,\n'
        '  "proposed_approach": 2-3 sentences tying the applicant\'s real skills to the gap,\n'
        '  "rationale": why this fits both the professor and the applicant,\n'
        '  "cited_titles": [exact titles from the gaps that support this].\n'
    )
    return llm.complete_json(prompt, system=SYNTH_SYSTEM) or {}


def _verified_citations(claimed_titles: list[str], papers: list[dict]) -> list[dict]:
    """Keep only papers whose titles were retrieved (drop unverifiable)."""
    ok_titles = {prof_research._norm(t) for t in claimed_titles}
    return [p for p in papers if prof_research._norm(p["title"]) in ok_titles]


def _keyless_brief(name: str, papers: list[dict]) -> dict:
    titles = [p["title"] for p in papers[:3]]
    first = titles[0] if titles else "the group's recent work"
    return {
        "themes": [], "paper_notes": [],
        "candidate_gaps": [{"gap": f"open questions building on {first}", "related_titles": titles[:1]}],
        "chosen_gap": f"an open question building on {first}",
        "research_question": f"How can recent methods extend the directions in {first!r}?",
        "proposed_approach": "Apply methods from my prior research to the gap (LLM unavailable — refine manually).",
        "rationale": "Generated without an LLM; please refine.",
        "citations": papers[:2],
        "sources_used": {"papers": len(papers), "pages": False},
        "method": "keyless",
    }


def run_deep_research(session: Session, opp: Opportunity, *,
                      paper_limit: int = 8) -> dict:
    """Run the deep-research workflow for an opportunity; persist + return the brief."""
    profile = config_loader.profile()
    name = opp.professor_name or ""
    affiliation = opp.university or ""

    prof = prof_research.research_professor(
        session, name, affiliation=affiliation,
        profile_url=opp.professor_profile_url, email=opp.professor_email,
    ) if name else None
    papers = (prof.recent_papers if prof else None) or []
    if not papers:
        papers, _ = paper_apis.recent_papers(name, affiliation, limit=paper_limit)

    page_context = _fetch_prof_pages(opp, prof)

    if llm.available() and papers:
        try:
            analysis = _analyse(name, papers, page_context)
            candidate_gaps = analysis.get("candidate_gaps", []) or []
            synth = _synthesise(name, candidate_gaps, profile)
            citations = _verified_citations(synth.get("cited_titles", []) or [], papers)
            brief = {
                "themes": analysis.get("themes", []) or [],
                "paper_notes": analysis.get("paper_notes", []) or [],
                "candidate_gaps": candidate_gaps,
                "chosen_gap": synth.get("chosen_gap"),
                "research_question": synth.get("research_question"),
                "proposed_approach": synth.get("proposed_approach"),
                "rationale": synth.get("rationale"),
                "citations": citations or papers[:2],
                "sources_used": {"papers": len(papers), "pages": bool(page_context)},
                "method": "llm",
            }
        except Exception as exc:
            brief = _keyless_brief(name, papers)
            brief["error"] = str(exc)
    else:
        brief = _keyless_brief(name, papers)

    # Refresh the professor record with the deepened findings.
    if prof:
        prof.research_themes = brief.get("themes") or prof.research_themes
        prof.identified_gap = brief.get("chosen_gap") or prof.identified_gap
        prof.proposed_angle = brief.get("proposed_approach") or prof.proposed_angle

    # Persist (replace any prior brief for this opportunity).
    for old in session.query(ResearchBrief).filter_by(opportunity_id=opp.id).all():
        session.delete(old)
    session.add(ResearchBrief(opportunity_id=opp.id,
                              professor_id=prof.id if prof else None, data=brief))
    session.flush()
    return brief
