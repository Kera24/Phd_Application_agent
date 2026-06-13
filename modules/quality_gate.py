"""Automated personalisation quality gate (Section 8).

Every check must pass before an email can move to 'awaiting_review'. Produces a
structured report shown in the dashboard.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from db.models import Professor
from modules import config_loader, llm


def _words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split()).rstrip(".")


def check_professor_name(body: str, prof: Professor) -> tuple[bool, str]:
    if not prof or not prof.name:
        return False, "No professor name on record."
    # Match surname at least.
    parts = [p for p in prof.name.split() if len(p) > 1]
    surname = parts[-1] if parts else prof.name
    ok = surname.lower() in (body or "").lower()
    return ok, f"Surname {surname!r} {'found' if ok else 'NOT found'} in body."


def check_citation(body: str, prof: Professor) -> tuple[bool, list[str]]:
    """At least one verified paper title (exact match against recent_papers)."""
    body_n = _norm(body)
    matched = [
        p["title"] for p in (prof.recent_papers or [])
        if _norm(p["title"]) and _norm(p["title"]) in body_n
    ]
    return (len(matched) >= 1, matched)


GAP_MARKERS = ["gap", "open question", "remains", "unclear", "limitation", "unaddressed",
               "has not", "yet to", "underexplored", "challenge"]
ANGLE_MARKERS = ["i could", "i would", "my work", "my experience", "i can contribute",
                 "building on", "i propose", "my background", "applying my", "i bring"]


def check_gap_and_angle(body: str) -> tuple[bool, bool]:
    bl = (body or "").lower()
    has_gap = any(m in bl for m in GAP_MARKERS)
    has_angle = any(m in bl for m in ANGLE_MARKERS)
    return has_gap, has_angle


def check_word_count(body: str, bounds: Optional[tuple[int, int]] = None) -> tuple[bool, int]:
    if bounds is None:
        qg = config_loader.config().get("quality_gate", {})
        bounds = (qg.get("body_word_min", 120), qg.get("body_word_max", 180))
    lo, hi = bounds
    n = _words(body)
    return (lo <= n <= hi, n)


def check_subject(subject: str) -> tuple[bool, int]:
    qg = config_loader.config().get("quality_gate", {})
    n = _words(subject)
    return (n <= qg.get("subject_max_words", 9), n)


def check_banned_phrases(body: str, subject: str = "") -> tuple[bool, list[str]]:
    qg = config_loader.config().get("quality_gate", {})
    banned = qg.get("banned_phrases", [])
    text = f"{subject} {body}".lower()
    hits = [b for b in banned if b.lower() in text]
    return (len(hits) == 0, hits)


def check_attachments(attachments: list[str], resolved: dict[str, str]) -> tuple[bool, list[str]]:
    """attachments is a list of kinds (cv/transcript/summary_pdf); resolved maps
    kind -> existing file path."""
    from pathlib import Path
    missing = []
    for kind in attachments or []:
        path = resolved.get(kind)
        if not path or not Path(path).exists():
            missing.append(kind)
    return (len(missing) == 0, missing)


JUDGE_SYSTEM = (
    "You are a strict fact-checker. You are given an applicant's verified profile "
    "and an outreach email. Identify any claim about the applicant in the email "
    "that is NOT supported by the profile. Be conservative: only flag genuine "
    "additions or contradictions, not rephrasings."
)


def check_claims_traceable(body: str) -> tuple[bool, list[str]]:
    """LLM-as-judge: all claims about Raj traceable to profile.yaml."""
    if not llm.available():
        return True, ["skipped: no LLM key (manual review advised)"]
    import json
    profile = config_loader.profile()
    prompt = (
        f"PROFILE (ground truth):\n{json.dumps(profile, default=str)[:4000]}\n\n"
        f"EMAIL BODY:\n{body}\n\n"
        'Return JSON {"unsupported_claims": [strings]}. Empty list if all claims '
        "are supported."
    )
    try:
        data = llm.complete_json(prompt, system=JUDGE_SYSTEM)
        bad = data.get("unsupported_claims", []) or []
        return (len(bad) == 0, bad)
    except Exception as exc:
        return False, [f"judge error: {exc}"]


def run(body: str, subject: str, prof: Professor,
        attachments: list[str], resolved_attachments: dict[str, str],
        *, word_bounds: Optional[tuple[int, int]] = None,
        mode: str = "standard") -> dict[str, Any]:
    """Run all checks; return a report. `passed` is the AND of all checks.

    `word_bounds` overrides the default body length range. `mode='followup'`
    runs the lighter gate for the short follow-up nudge: it keeps name / length /
    subject / banned-phrase / claim-traceability checks but drops the research
    checks (gap / angle / verified-citation) and attachment check, which apply to
    the research-led first-contact email, not a reminder.
    """
    name_ok, name_msg = check_professor_name(body, prof)
    wc_ok, wc = check_word_count(body, word_bounds)
    subj_ok, subj_words = check_subject(subject)
    banned_ok, banned_hits = check_banned_phrases(body, subject)
    claims_ok, bad_claims = check_claims_traceable(body)

    checks = {
        "professor_name": {"passed": name_ok, "detail": name_msg},
        "word_count": {"passed": wc_ok, "detail": {"words": wc}},
        "subject_length": {"passed": subj_ok, "detail": {"words": subj_words}},
        "no_banned_phrases": {"passed": banned_ok, "detail": {"hits": banned_hits}},
        "claims_traceable": {"passed": claims_ok, "detail": {"unsupported": bad_claims}},
    }
    if mode != "followup":
        cite_ok, cited = check_citation(body, prof)
        has_gap, has_angle = check_gap_and_angle(body)
        att_ok, att_missing = check_attachments(attachments, resolved_attachments)
        checks.update({
            "verified_citation": {"passed": cite_ok, "detail": {"matched": cited}},
            "gap_statement": {"passed": has_gap, "detail": "gap marker present" if has_gap else "no gap marker"},
            "angle_statement": {"passed": has_angle, "detail": "angle marker present" if has_angle else "no angle marker"},
            "attachments_resolved": {"passed": att_ok, "detail": {"missing": att_missing}},
        })
    passed = all(c["passed"] for c in checks.values())
    return {"passed": passed, "checks": checks}
