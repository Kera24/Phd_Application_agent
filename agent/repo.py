"""DB persistence helpers used by graph nodes.

Keeps SQLAlchemy usage out of the node logic and centralises the
publications / research_gaps / approvals / scheduled_emails writes that the new
schema adds on top of the reused pipeline modules.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from db.models import (
    Approval,
    Email,
    Opportunity,
    PipelineEvent,
    Professor,
    Publication,
    ResearchGap,
    ScheduledEmail,
)


def log(session: Session, email_id: Optional[int], event: str, detail: dict | None = None) -> None:
    session.add(PipelineEvent(email_id=email_id, event=event, detail=detail or {}))


def set_tracker(session: Session, opp: Opportunity, status: str) -> None:
    """Record a pipeline status transition on the opportunity + audit log."""
    opp.pipeline_status = status
    log(session, None, f"opp:{opp.id}:{status}", {"opportunity_id": opp.id})


def store_publications(session: Session, professor: Professor,
                       papers: list[dict], source_api: str) -> list[int]:
    """Persist verbatim paper records (Rules 3 & 4). Returns publication ids."""
    # Replace prior records for this professor to keep them current.
    for old in session.query(Publication).filter_by(professor_id=professor.id).all():
        session.delete(old)
    ids = []
    for p in papers:
        pub = Publication(
            professor_id=professor.id,
            title=p.get("title", ""),
            year=p.get("year"),
            venue=p.get("venue"),
            abstract=p.get("abstract"),
            source_url=p.get("url"),
            source_api=source_api,
        )
        session.add(pub)
        session.flush()
        ids.append(pub.id)
    return ids


def store_gap(session: Session, opp: Opportunity, professor: Professor,
              gap: str, angle: str, source_pub_ids: list[int]) -> int:
    rg = ResearchGap(
        opportunity_id=opp.id, professor_id=professor.id,
        gap=gap, proposed_angle=angle, source_publication_ids=source_pub_ids,
    )
    session.add(rg)
    session.flush()
    return rg.id


def record_approval(session: Session, email: Email, thread_id: str, decision: str,
                    *, decided_by: str = "dashboard", edits: dict | None = None,
                    reason: str = "") -> None:
    session.add(Approval(
        email_id=email.id, thread_id=thread_id, decision=decision,
        decided_by=decided_by, edits=edits, reason=reason,
    ))


def record_scheduled(session: Session, email: Email, send_at_utc: dt.datetime,
                     professor_tz: str, tz_flagged: bool, job_id: str = "") -> None:
    session.add(ScheduledEmail(
        email_id=email.id, send_at_utc=send_at_utc, professor_tz=professor_tz,
        tz_flagged=tz_flagged, job_id=job_id, status="queued",
    ))
