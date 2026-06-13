"""Fit scoring with two rubrics selected by opportunity_type.

Each criterion is scored 0-10 (with rationale) and combined into a weighted
0-100 score. Deadline feasibility for advertised positions is computed
deterministically; the rest come from a single Claude call (with a heuristic
fallback when no API key is set).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from db.models import Opportunity, Professor
from modules import config_loader, llm

ADVERTISED_RUBRIC = {
    "research_alignment": 30,
    "field_match": 20,
    "supervisor_alignment": 20,
    "international_eligibility": 10,
    "country_preference": 10,
    "deadline_feasibility": 10,
}

SPECULATIVE_RUBRIC = {
    "research_alignment": 30,
    "supervisor_alignment": 25,        # recency + relevance
    "funding_likelihood": 20,
    "lab_activity": 10,
    "international_track_record": 5,
    "country_preference": 10,
}

HIGHLIGHT_THRESHOLD = 70


def deadline_feasibility_score(deadline: Optional[dt.date],
                               today: Optional[dt.date] = None) -> int:
    """>=3 weeks = 10, 1-3 weeks = 6, <1 week = 3, passed = 0, none = 5 (neutral)."""
    if deadline is None:
        return 5
    today = today or dt.date.today()
    days = (deadline - today).days
    if days < 0:
        return 0
    if days < 7:
        return 3
    if days < 21:
        return 6
    return 10


def _country_pref_score(country: Optional[str]) -> int:
    if not country:
        return 5
    targets = [c.lower() for c in config_loader.config().get("target_countries", [])]
    return 10 if country.strip().lower() in targets else 4


def _scoring_prompt(opp: Opportunity, prof: Optional[Professor],
                    criteria: list[str], profile: dict) -> str:
    papers = "; ".join(p["title"] for p in (prof.recent_papers or [])) if prof else ""
    proj = "; ".join(f"{p['title']}: {p['detail']}" for p in profile.get("research_projects", []))
    return (
        "Score each criterion 0-10 for this PhD opportunity given the applicant.\n"
        f"Applicant projects: {proj}\n"
        f"Applicant target fields: {', '.join(profile.get('target_fields', []))}\n\n"
        f"Opportunity: title={opp.position_title!r}, university={opp.university!r}, "
        f"country={opp.country!r}, fields={opp.research_fields}, "
        f"funding={opp.funding_status}, funding_evidence={opp.funding_evidence!r}, "
        f"eligibility={opp.eligibility_notes!r}, international_eligible={opp.international_eligible}\n"
        f"Supervisor recent papers: {papers}\n\n"
        f"Return JSON: each key in {criteria} -> {{\"score\": 0-10, \"rationale\": \"...\"}}."
    )


def _heuristic_scores(opp: Opportunity, prof: Optional[Professor],
                      criteria: list[str], profile: dict) -> dict:
    """Keyword-overlap fallback so scoring runs without an API key."""
    fields_text = " ".join(opp.research_fields or []).lower() + " " + (opp.position_title or "").lower()
    kw = []
    for p in profile.get("research_projects", []):
        kw += [k.lower() for k in p.get("match_keywords", [])]
    overlap = sum(1 for k in set(kw) if k in fields_text)
    base = min(10, 3 + overlap * 2)
    out = {}
    for c in criteria:
        out[c] = {"score": base, "rationale": f"Heuristic: {overlap} keyword overlaps (no LLM key)."}
    return out


def score_opportunity(opp: Opportunity, prof: Optional[Professor] = None) -> dict[str, Any]:
    """Return {'total': int, 'breakdown': {criterion: {score, rationale, weight, weighted}}}."""
    profile = config_loader.profile()
    rubric = ADVERTISED_RUBRIC if opp.opportunity_type == "advertised" else SPECULATIVE_RUBRIC
    criteria = list(rubric.keys())

    if llm.available():
        try:
            scores = llm.complete_json(_scoring_prompt(opp, prof, criteria, profile))
        except Exception:
            scores = _heuristic_scores(opp, prof, criteria, profile)
    else:
        scores = _heuristic_scores(opp, prof, criteria, profile)

    # Deterministic overrides.
    if "deadline_feasibility" in rubric:
        scores["deadline_feasibility"] = {
            "score": deadline_feasibility_score(opp.deadline),
            "rationale": f"Deadline {opp.deadline}.",
        }
    scores.setdefault("country_preference", {})
    scores["country_preference"] = {
        "score": _country_pref_score(opp.country),
        "rationale": f"Country: {opp.country}.",
    }

    breakdown = {}
    total = 0.0
    for crit, weight in rubric.items():
        entry = scores.get(crit, {"score": 5, "rationale": "missing"})
        s = max(0, min(10, int(entry.get("score", 5))))
        weighted = s / 10 * weight
        total += weighted
        breakdown[crit] = {
            "score": s,
            "weight": weight,
            "weighted": round(weighted, 1),
            "rationale": entry.get("rationale", ""),
        }
    return {"total": round(total), "breakdown": breakdown,
            "highlight": round(total) >= HIGHLIGHT_THRESHOLD}
