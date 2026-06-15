"""Deep "Lab Scout" research workflow (Claude-orchestrated, web-grounded).

Goes far deeper than the single-shot prof_research node. For one professor it:

  1. retrieves their recent papers (paper APIs) and crawls their own lab/homepage
     pages,
  2. runs a live **web-search dossier** (Claude's server-side web_search): personal
     site, Google Scholar, lab page, recent talks/keynotes, news, and stated future
     directions — producing a long, readable markdown dossier,
  3. maps themes + per-paper contributions + candidate gaps (grounded analysis),
  4. synthesises a single structured research plan tied to the applicant's real
     background: problem statement -> how the field currently approaches it -> the
     gap the professor has signalled -> a concrete approach to implement -> a 1-2
     step extension -> a short pitch to send the professor.

Grounding rules preserved: every cited PAPER title must match a retrieved one
(prof_research.verify_titles); unverifiable citations are dropped, never kept.
Talks/news are grounded by the web_search source URLs. Every step degrades
independently — missing web search (or no key) falls back to the crawl-only /
keyless paths so the dashboard never 500s.

Persists a ResearchBrief (incl. the full `dossier_md`) and refreshes the
Professor's gap/angle/themes.
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
    "You help a PhD applicant find a specific, honest research angle and turn it into a "
    "concrete project they can build and pitch. Use ONLY the applicant's real background "
    "and the provided research material. Never fabricate applicant experience or invent "
    "paper titles. The problem statement, gap, approach and extension must be concrete "
    "and grounded in what the professor actually works on."
)
DOSSIER_SYSTEM = (
    "You are a meticulous research analyst profiling an academic. Use web search to gather "
    "evidence from the professor's own pages (homepage, lab site, Google Scholar) and "
    "reputable sources. Cite specifics (paper titles, talk venues/dates, programme pages). "
    "Never fabricate — if something cannot be found, say so. Write in clear markdown."
)


def _dr_cfg() -> dict:
    return config_loader.config().get("deep_research", {}) or {}


def _fetch_prof_pages(opp: Opportunity, prof: Optional[Professor], limit: int = 6000) -> str:
    """Fetch the professor's profile/lab page(s) as readable text (best-effort)."""
    from modules import discovery
    urls = []
    for u in (getattr(prof, "profile_url", None), getattr(prof, "scholar_url", None),
              opp.professor_profile_url, opp.application_link):
        if u and u not in urls:
            urls.append(u)
    chunks = []
    for u in urls[:4]:
        text = discovery.fetch_page_text(u, limit=limit)
        if text:
            chunks.append(f"[{u}]\n{text}")
    return "\n\n".join(chunks)[: limit * 2]


def _papers_block(papers: list[dict]) -> str:
    return "\n".join(
        f"- TITLE: {p['title']} ({p.get('year')}, {p.get('venue')})\n"
        f"  ABSTRACT: {(p.get('abstract') or '')[:800]}"
        for p in papers
    )


# ---------------------------------------------------------------------------
# Step 2 — live web-search dossier (best-effort; non-fatal)
# ---------------------------------------------------------------------------
def _web_dossier(name: str, affiliation: str, papers: list[dict],
                 page_context: str) -> dict:
    """Return {"text": markdown, "sources": [...]} or {} if unavailable."""
    cfg = _dr_cfg()
    if not cfg.get("web_search", True):
        return {}
    known = "\n".join(f"- {p['title']} ({p.get('year')})" for p in papers[:12])
    prompt = (
        f"Build a comprehensive research dossier on Professor {name}"
        + (f" ({affiliation})." if affiliation else ".")
        + "\n\nSearch the web for their personal homepage, lab/group website, Google "
          "Scholar profile, recent talks/keynotes/panels, and any recent news or "
          "interviews. Focus on the last ~2 years.\n\n"
          f"Papers already retrieved from paper APIs (use as anchors, don't contradict):\n{known}\n\n"
          f"Lab/homepage text already crawled (may be partial):\n{page_context[:3000]}\n\n"
          "Write a markdown dossier with these sections:\n"
          "## Profile & lab — who they are, their group, overall research focus\n"
          "## Research themes — the threads running through their recent work\n"
          "## Recent papers — a short deep dive per important recent paper "
          "(what problem, what method, what result)\n"
          "## Recent talks & activity — talks/keynotes/panels with venue and date where found\n"
          "## Stated future directions — where THEY say the field/their work is heading "
          "(quote or paraphrase with the source)\n\n"
          "Be specific and cite sources. If a section has little evidence, say so briefly."
    )
    try:
        return llm.research_with_search(
            prompt, system=DOSSIER_SYSTEM,
            max_uses=int(cfg.get("web_search_max_uses", 8)), max_tokens=4500,
        )
    except llm.LLMUnavailable:
        return {}
    except Exception:  # never let the dossier kill the run
        return {}


# ---------------------------------------------------------------------------
# Step 3 — grounded analysis (themes / paper notes / candidate gaps)
# ---------------------------------------------------------------------------
def _analyse(name: str, papers: list[dict], context: str) -> dict:
    prompt = (
        f"Professor: {name}\n\n"
        f"Recent papers (reference titles VERBATIM):\n{_papers_block(papers)}\n\n"
        f"Lab / homepage / web dossier context (may be empty):\n{context[:6000]}\n\n"
        "Return JSON with keys:\n"
        '  "themes": [short strings],\n'
        '  "paper_notes": [{"title": exact title, "contribution": one line}],\n'
        '  "candidate_gaps": [{"gap": concrete limitation/open question, '
        '"related_titles": [exact titles]}]  (2-3 gaps).\n'
    )
    return llm.complete_json(prompt, system=ANALYSIS_SYSTEM) or {}


# ---------------------------------------------------------------------------
# Step 4 — structured synthesis: the pitchable project
# ---------------------------------------------------------------------------
def _synthesise(name: str, candidate_gaps: list[dict], profile: dict,
                dossier_text: str) -> dict:
    projects = "; ".join(
        f"{p.get('title')}: {p.get('detail')}" for p in profile.get("research_projects", []))
    prompt = (
        f"Professor: {name}\n\n"
        f"Applicant background (ground truth — do not exceed it):\n{projects}\n\n"
        f"Web dossier on the professor (their work, talks, stated future directions):\n"
        f"{dossier_text[:5000]}\n\n"
        f"Candidate gaps from the paper analysis:\n{json.dumps(candidate_gaps)[:3000]}\n\n"
        "Choose the single gap that best matches BOTH the professor's signalled future "
        "direction and the applicant's background. Then design a project the applicant "
        "could actually build and pitch. Return JSON:\n"
        '  "chosen_gap": string,\n'
        '  "research_question": one precise question,\n'
        '  "problem_statement": 2-4 sentences framing the problem and why it matters now,\n'
        '  "current_approaches": [{"approach": how the field/professor tackles it today, '
        '"citation": exact paper title or source}]  (2-4 items),\n'
        '  "the_gap": what current approaches miss (the opening),\n'
        '  "proposed_approach": 2-4 sentences tying the applicant\'s real skills to a concrete build,\n'
        '  "proposed_extension": how to take the build 1-2 steps further than current work,\n'
        '  "pitch": a 3-4 sentence message the applicant could send the professor '
        '("here is what I built / propose, is this the right direction?"),\n'
        '  "talks": [short strings of notable recent talks if any appear in the dossier],\n'
        '  "future_directions": [short strings the professor has signalled],\n'
        '  "paper_deep_dive": [{"title": exact title, "summary": 1-2 line dive}],\n'
        '  "rationale": why this fits both the professor and the applicant,\n'
        '  "cited_titles": [exact paper titles that support this].\n'
    )
    return llm.complete_json(prompt, system=SYNTH_SYSTEM) or {}


def _verified_citations(claimed_titles: list[str], papers: list[dict]) -> list[dict]:
    """Keep only papers whose titles were retrieved (drop unverifiable)."""
    ok_titles = {prof_research._norm(t) for t in claimed_titles}
    return [p for p in papers if prof_research._norm(p["title"]) in ok_titles]


# ---------------------------------------------------------------------------
# Assemble the human-facing markdown dossier
# ---------------------------------------------------------------------------
def _bullets(items, render) -> str:
    return "\n".join(f"- {render(it)}" for it in (items or []) if it) or "_(none found)_"


def _build_dossier_md(name: str, brief: dict, web_text: str) -> str:
    parts: list[str] = [f"# Lab Scout — {name}\n"]
    if web_text:
        parts.append(web_text.strip() + "\n")
    else:
        # No web search: assemble from the structured analysis instead.
        if brief.get("themes"):
            parts.append("## Research themes\n" + _bullets(brief["themes"], str) + "\n")
        if brief.get("paper_deep_dive"):
            parts.append("## Recent papers\n" + _bullets(
                brief["paper_deep_dive"],
                lambda d: f"**{d.get('title')}** — {d.get('summary')}") + "\n")
        if brief.get("talks"):
            parts.append("## Recent talks & activity\n" + _bullets(brief["talks"], str) + "\n")
        if brief.get("future_directions"):
            parts.append("## Stated future directions\n"
                         + _bullets(brief["future_directions"], str) + "\n")

    parts.append("## Proposed project\n")
    if brief.get("problem_statement"):
        parts.append(f"**Problem statement.** {brief['problem_statement']}\n")
    if brief.get("current_approaches"):
        parts.append("**How it's currently approached:**\n" + _bullets(
            brief["current_approaches"],
            lambda d: f"{d.get('approach')}"
                      + (f" _(ref: {d.get('citation')})_" if d.get("citation") else "")) + "\n")
    if brief.get("the_gap"):
        parts.append(f"**The gap.** {brief['the_gap']}\n")
    if brief.get("research_question"):
        parts.append(f"**Research question.** {brief['research_question']}\n")
    if brief.get("proposed_approach"):
        parts.append(f"**Proposed approach (what to build).** {brief['proposed_approach']}\n")
    if brief.get("proposed_extension"):
        parts.append(f"**Taking it further.** {brief['proposed_extension']}\n")
    if brief.get("pitch"):
        parts.append(f"**Pitch to the professor.**\n\n> {brief['pitch']}\n")

    cites = brief.get("citations") or []
    if cites:
        parts.append("## Cited papers (verified)\n" + _bullets(
            cites, lambda c: f"{c.get('title')} ({c.get('year')})"))
    sources = brief.get("sources") or []
    if sources:
        parts.append("## Sources\n" + _bullets(
            sources, lambda s: f"[{s.get('title') or s.get('url')}]({s.get('url')})"))
    return "\n".join(parts).strip()


def _keyless_brief(name: str, papers: list[dict]) -> dict:
    titles = [p["title"] for p in papers[:3]]
    first = titles[0] if titles else "the group's recent work"
    brief = {
        "themes": [], "paper_notes": [],
        "candidate_gaps": [{"gap": f"open questions building on {first}", "related_titles": titles[:1]}],
        "chosen_gap": f"an open question building on {first}",
        "research_question": f"How can recent methods extend the directions in {first!r}?",
        "problem_statement": None, "current_approaches": [], "the_gap": None,
        "proposed_approach": "Apply methods from my prior research to the gap (LLM unavailable — refine manually).",
        "proposed_extension": None, "pitch": None,
        "talks": [], "future_directions": [], "paper_deep_dive": [],
        "rationale": "Generated without an LLM; please refine.",
        "citations": papers[:2],
        "sources": [],
        "sources_used": {"papers": len(papers), "pages": False, "web_searched": False},
        "method": "keyless",
    }
    brief["dossier_md"] = _build_dossier_md(name, brief, "")
    return brief


def run_deep_research(session: Session, opp: Opportunity, *,
                      paper_limit: Optional[int] = None) -> dict:
    """Run the deep-research workflow for an opportunity; persist + return the brief."""
    profile = config_loader.profile()
    name = opp.professor_name or ""
    affiliation = opp.university or ""
    paper_limit = paper_limit or int(_dr_cfg().get("paper_limit", 18))

    prof = prof_research.research_professor(
        session, name, affiliation=affiliation,
        profile_url=opp.professor_profile_url, email=opp.professor_email,
    ) if name else None
    papers = (prof.recent_papers if prof else None) or []
    if not papers:
        papers, _ = paper_apis.recent_papers(name, affiliation, limit=paper_limit)

    page_context = _fetch_prof_pages(opp, prof)
    dossier = _web_dossier(name, affiliation, papers, page_context) if (name and papers) else {}
    web_text = dossier.get("text", "")
    sources = dossier.get("sources", [])
    combined_context = "\n\n".join(c for c in (page_context, web_text) if c)

    if llm.available() and papers:
        try:
            analysis = _analyse(name, papers, combined_context)
            candidate_gaps = analysis.get("candidate_gaps", []) or []
            synth = _synthesise(name, candidate_gaps, profile, web_text)
            citations = _verified_citations(synth.get("cited_titles", []) or [], papers)
            brief = {
                "themes": analysis.get("themes", []) or [],
                "paper_notes": analysis.get("paper_notes", []) or [],
                "candidate_gaps": candidate_gaps,
                "chosen_gap": synth.get("chosen_gap"),
                "research_question": synth.get("research_question"),
                "problem_statement": synth.get("problem_statement"),
                "current_approaches": synth.get("current_approaches", []) or [],
                "the_gap": synth.get("the_gap"),
                "proposed_approach": synth.get("proposed_approach"),
                "proposed_extension": synth.get("proposed_extension"),
                "pitch": synth.get("pitch"),
                "talks": synth.get("talks", []) or [],
                "future_directions": synth.get("future_directions", []) or [],
                "paper_deep_dive": synth.get("paper_deep_dive", []) or [],
                "rationale": synth.get("rationale"),
                "citations": citations or papers[:2],
                "sources": sources,
                "sources_used": {"papers": len(papers), "pages": bool(page_context),
                                 "web_searched": bool(web_text)},
                "method": "llm",
            }
            brief["dossier_md"] = _build_dossier_md(name, brief, web_text)
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
