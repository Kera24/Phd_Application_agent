"""Phase 3 — outreach analytics computed from the emails / opportunities tables.

Pure read model: aggregates first-contact sends, reply rate, response time, the
follow-up funnel, and country/field breakdowns for the dashboard. No mutations.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from db.models import Email, Opportunity


def _avg_response_hours(emails: list[Email]) -> float | None:
    deltas = [
        (e.reply_received_at - e.sent_at).total_seconds() / 3600.0
        for e in emails
        if e.reply_received and e.reply_received_at and e.sent_at
    ]
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 1)


def compute_metrics(session: Session) -> dict[str, Any]:
    emails = session.query(Email).all()
    first_contact = [e for e in emails if not e.is_followup]
    followups = [e for e in emails if e.is_followup]

    sent_first = [e for e in first_contact if e.status == "sent"]
    replied = [e for e in sent_first if e.reply_received]

    status_counts = Counter(e.status for e in emails)

    # Country / field breakdown over first-contact emails (join via opportunity).
    opp_ids = {e.opportunity_id for e in first_contact if e.opportunity_id}
    opps = {o.id: o for o in session.query(Opportunity)
            .filter(Opportunity.id.in_(opp_ids)).all()} if opp_ids else {}
    by_country: Counter = Counter()
    by_field: Counter = Counter()
    for e in first_contact:
        opp = opps.get(e.opportunity_id)
        if not opp:
            continue
        if opp.country:
            by_country[opp.country] += 1
        for f in (opp.research_fields or []):
            by_field[f] += 1

    sent_n = len(sent_first)
    reply_rate = round(100.0 * len(replied) / sent_n, 1) if sent_n else 0.0

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "totals": {
            "emails": len(emails),
            "first_contact": len(first_contact),
            "followups": len(followups),
        },
        "status_counts": dict(status_counts),
        "sends": {
            "first_contact_sent": sent_n,
            "replies": len(replied),
            "reply_rate_pct": reply_rate,
            "avg_response_hours": _avg_response_hours(sent_first),
        },
        "followup_funnel": {
            "drafted": sum(1 for e in followups if e.status in
                           ("draft_created", "awaiting_review", "approved", "scheduled", "sent")),
            "awaiting_review": sum(1 for e in followups if e.status == "awaiting_review"),
            "sent": sum(1 for e in followups if e.status == "sent"),
        },
        "by_country": dict(by_country),
        "by_field": dict(by_field),
    }
