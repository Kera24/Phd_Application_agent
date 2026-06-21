"""LangGraph state schemas.

OutreachState is the top-level, checkpointer-persisted state. Dict-valued
channels that several parallel fan-out branches write to use a merge reducer so
concurrent updates combine instead of clobbering each other.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Reducer: shallow-merge two dicts (right wins on key conflict)."""
    out = dict(left or {})
    out.update(right or {})
    return out


class OutreachState(TypedDict, total=False):
    # --- static inputs -------------------------------------------------------
    user_profile: dict
    uploaded_documents: dict            # {cv|transcript|base_summary|sop: {path, text}}
    email_templates: dict
    target_fields: list
    target_countries: list
    professor_list: list                # optional manual seed list
    linkedin_inputs: list               # manually pasted posts/URLs
    run_mode: str                       # 'reactive' | 'proactive'

    # --- pipeline data (keyed by opportunity_id where applicable) ------------
    discovered_opportunities: Annotated[list, operator.add]
    parsed_opportunities: Annotated[list, operator.add]
    funding_decisions: Annotated[dict, merge_dicts]
    professor_research: Annotated[dict, merge_dicts]
    research_gaps: Annotated[dict, merge_dicts]
    fit_scores: Annotated[dict, merge_dicts]
    tailored_research_summaries: Annotated[dict, merge_dicts]
    email_drafts: Annotated[dict, merge_dicts]
    approval_status: Annotated[dict, merge_dicts]
    scheduled_emails: Annotated[dict, merge_dicts]
    send_logs: Annotated[list, operator.add]
    tracker_status: Annotated[dict, merge_dicts]
    errors: Annotated[list, operator.add]

    # --- control -------------------------------------------------------------
    current_email_id: int               # email currently at the approval interrupt


class OppState(TypedDict, total=False):
    """Per-opportunity working state for the fan-out subgraph.

    Keys overlap with OutreachState so the subgraph's outputs merge cleanly into
    the parent via the parent's reducers.
    """
    opportunity: dict                   # raw discovered item: {raw_text, source_type, source_url, opportunity_type, seed}
    user_profile: dict
    uploaded_documents: dict
    email_templates: dict
    run_mode: str                       # propagated from OutreachState; 'reactive' bypasses gates

    opportunity_id: int
    route: str                          # terminal/branch marker
    parsed_opportunities: Annotated[list, operator.add]
    funding_decisions: Annotated[dict, merge_dicts]
    professor_research: Annotated[dict, merge_dicts]
    research_gaps: Annotated[dict, merge_dicts]
    fit_scores: Annotated[dict, merge_dicts]
    tailored_research_summaries: Annotated[dict, merge_dicts]
    email_drafts: Annotated[dict, merge_dicts]
    tracker_status: Annotated[dict, merge_dicts]
    errors: Annotated[list, operator.add]
