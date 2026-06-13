"""Opportunity parsing via a single strict-JSON Claude call, plus the
fully-funded hard filter (Non-Negotiable Rule 0).

Missing fields -> null, never guessed. Dates -> ISO 8601.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import Opportunity
from modules import config_loader, llm

# Fields the LLM must populate (mirrors the opportunities table).
SCHEMA_FIELDS = [
    "position_title", "university", "country", "city", "department", "lab_name",
    "professor_name", "professor_email", "professor_profile_url",
    "deadline", "funding_status", "funding_evidence",
    "required_documents", "application_link", "research_fields",
    "eligibility_notes", "international_eligible",
]

PARSE_SYSTEM = (
    "You extract structured data from PhD opportunity postings. "
    "Rules: (1) Only use information present in the text. "
    "(2) Any field not stated must be null — never guess or infer. "
    "(3) Normalise dates to ISO 8601 (YYYY-MM-DD). "
    "(4) funding_status must be one of: funded, partial, unfunded, self-funded, unknown. "
    "Classify as 'funded' ONLY when the text indicates a full stipend/salary AND "
    "fees covered, or a salaried doctoral/research position. "
    "(5) funding_evidence: quote the exact phrase(s) that justify the funding_status."
)


def _parse_prompt(raw_text: str) -> str:
    fields = ", ".join(SCHEMA_FIELDS)
    return (
        f"Extract these fields as a JSON object: {fields}.\n"
        "research_fields and required_documents are arrays of strings. "
        "international_eligible is true/false/null. "
        "Return null for anything not explicitly stated.\n\n"
        f"POSTING TEXT:\n\"\"\"\n{raw_text.strip()}\n\"\"\""
    )


def _heuristic_parse(raw_text: str) -> dict[str, Any]:
    """Fallback used when no LLM key is configured (keeps the app runnable).

    Deliberately conservative: leaves most fields null and funding 'unknown'.
    """
    text_l = raw_text.lower()
    funding = "unknown"
    evidence = None
    funded_signals = ["fully funded", "full stipend", "salaried", "tv-l", "e13",
                      "tariff", "scholarship covers", "stipend of", "funded position"]
    partial_signals = ["partial funding", "partially funded", "tuition only"]
    unfunded_signals = ["self-funded", "self funded", "no funding", "unfunded"]
    for s in unfunded_signals:
        if s in text_l:
            funding, evidence = ("self-funded" if "self" in s else "unfunded"), s
            break
    else:
        for s in partial_signals:
            if s in text_l:
                funding, evidence = "partial", s
                break
        else:
            for s in funded_signals:
                if s in text_l:
                    funding, evidence = "funded", s
                    break

    import re
    email_m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw_text)
    return {
        **{f: None for f in SCHEMA_FIELDS},
        "funding_status": funding,
        "funding_evidence": evidence,
        "professor_email": email_m.group(0) if email_m else None,
        "research_fields": [],
        "required_documents": [],
    }


def parse_text(raw_text: str) -> dict[str, Any]:
    """Return a dict of extracted fields. Uses Claude when available."""
    if llm.available():
        cfg = config_loader.config().get("llm", {})
        data = llm.complete_json(
            _parse_prompt(raw_text),
            system=PARSE_SYSTEM,
            model=cfg.get("parser_model"),
        )
        # Ensure all schema fields are present.
        for f in SCHEMA_FIELDS:
            data.setdefault(f, None)
        return data
    return _heuristic_parse(raw_text)


def _to_date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def classify_and_store(
    session: Session,
    raw_text: str,
    *,
    source_type: str = "linkedin_manual",
    source_url: Optional[str] = None,
    opportunity_type: str = "advertised",
) -> Opportunity:
    """Parse, apply the funding hard filter, and persist an Opportunity.

    Pipeline status is set according to Rule 0:
      - partial/unfunded/self-funded -> archived_not_funded (terminal)
      - unknown -> funding_unknown (surfaced for manual decision)
      - funded -> parsed (continues), or needs_email if no professor email
    """
    parsed = parse_text(raw_text)

    fields = parsed.get("funding_status")
    funding = fields if fields in ("funded", "partial", "unfunded", "self-funded", "unknown") else "unknown"

    opp = Opportunity(
        source_url=source_url,
        source_type=source_type,
        opportunity_type=opportunity_type,
        position_title=parsed.get("position_title"),
        university=parsed.get("university"),
        country=parsed.get("country"),
        city=parsed.get("city"),
        department=parsed.get("department"),
        lab_name=parsed.get("lab_name"),
        professor_name=parsed.get("professor_name"),
        professor_email=parsed.get("professor_email"),
        professor_profile_url=parsed.get("professor_profile_url"),
        deadline=_to_date(parsed.get("deadline")),
        funding_status=funding,
        funding_evidence=parsed.get("funding_evidence"),
        required_documents=parsed.get("required_documents") or [],
        application_link=parsed.get("application_link"),
        research_fields=parsed.get("research_fields") or [],
        eligibility_notes=parsed.get("eligibility_notes"),
        international_eligible=parsed.get("international_eligible"),
        raw_text=raw_text,
    )

    opp.pipeline_status = _funding_gate_status(opp)
    session.add(opp)
    session.flush()
    return opp


def _funding_gate_status(opp: Opportunity) -> str:
    """Apply Rule 0 to decide the opportunity's entry pipeline status."""
    funding = opp.funding_status
    if funding in ("partial", "unfunded", "self-funded"):
        return "archived_not_funded"
    if funding == "unknown":
        return "funding_unknown"
    # funded:
    if not opp.professor_email:
        return "needs_email"
    return "parsed"


def confirm_funding(session: Session, opp: Opportunity, *, funded: bool,
                    evidence: str = "") -> Opportunity:
    """Manual resolution of a 'funding_unknown' opportunity from the dashboard."""
    if funded:
        opp.funding_status = "funded"
        opp.funding_evidence = (opp.funding_evidence or "") + f" | manual: {evidence}"
        opp.pipeline_status = "needs_email" if not opp.professor_email else "parsed"
    else:
        opp.funding_status = "unfunded"
        opp.pipeline_status = "archived_not_funded"
    session.flush()
    return opp
