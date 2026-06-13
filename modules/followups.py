"""Phase 3 — reply detection + follow-up draft generation.

A single follow-up is drafted for a first-contact email that was sent, has had
no reply after N business days, and has not already been followed up. Follow-ups
are draft-first and flow through the existing Human Approval queue; nothing here
ever sends. Reply detection is Gmail-optional (graceful skip when unauthorised)
and complemented by a manual "mark replied" toggle in the dashboard.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from db.models import Email, Followup
from modules import config_loader, gmail_client, tracker


# --- business-day arithmetic -------------------------------------------------

def business_days_after(start: dt.date, n: int) -> dt.date:
    """Return the date `n` business days (Mon–Fri) after `start`."""
    d = start
    added = 0
    while added < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon .. 4=Fri
            added += 1
    return d


def followup_due_date(sent: dt.date) -> dt.date:
    cfg = config_loader.config().get("followup", {})
    return business_days_after(sent, int(cfg.get("after_business_days", 10)))


def _followup_cfg() -> dict:
    return config_loader.config().get("followup", {}) or {}


# --- reply detection ---------------------------------------------------------

def mark_replied(session: Session, email: Email, *, by: str = "manual") -> None:
    """Flag an email (and any pending follow-up) as having received a reply."""
    if email.reply_received:
        return
    email.reply_received = True
    email.reply_received_at = dt.datetime.now(dt.timezone.utc)
    email.followup_status = "replied"
    for fu in session.query(Followup).filter_by(email_id=email.id).all():
        fu.reply_received = True
        fu.followup_status = "replied"
    tracker.log_event(session, email.id, "reply_detected", {"by": by})


def detect_replies(session: Session) -> int:
    """Scan sent first-contact emails for inbound Gmail replies. Returns count flagged.

    No-op (returns 0) when reply detection is disabled or Gmail is unavailable.
    """
    if not config_loader.config().get("reply_detection", {}).get("enabled"):
        return 0
    candidates = (
        session.query(Email)
        .filter(Email.status == "sent",
                Email.is_followup == False,  # noqa: E712
                Email.reply_received == False,  # noqa: E712
                Email.gmail_thread_id.isnot(None))
        .all()
    )
    flagged = 0
    for email in candidates:
        if gmail_client.fetch_thread_replies(email.gmail_thread_id):
            mark_replied(session, email, by="gmail")
            flagged += 1
    return flagged


# --- follow-up generation ----------------------------------------------------

def _followup_count(session: Session, original: Email) -> int:
    return (
        session.query(Email)
        .filter(Email.parent_email_id == original.id, Email.is_followup == True)  # noqa: E712
        .count()
    )


def due_followups(session: Session, *, today: Optional[dt.date] = None) -> list[Email]:
    """First-contact emails eligible for a follow-up draft right now."""
    today = today or dt.date.today()
    max_followups = int(_followup_cfg().get("max_followups", 1))
    candidates = (
        session.query(Email)
        .filter(Email.status == "sent",
                Email.is_followup == False,  # noqa: E712
                Email.reply_received == False,  # noqa: E712
                Email.followup_due_date.isnot(None),
                Email.followup_due_date <= today,
                Email.followup_status.in_(("not_needed", "due")))
        .all()
    )
    return [e for e in candidates if _followup_count(session, e) < max_followups]


def generate_due_followups(session: Session, *, today: Optional[dt.date] = None) -> int:
    """Draft follow-ups for all due emails. Returns the number created."""
    if not _followup_cfg().get("enabled", True):
        return 0
    from modules import email_gen  # deferred: email_gen imports many modules
    created = 0
    for original in due_followups(session, today=today):
        followup_email = email_gen.generate_followup(session, original)
        original.followup_status = "drafted"
        session.add(Followup(
            email_id=original.id,
            followup_email_id=followup_email.id,
            sent_date=original.sent_date,
            followup_due_date=original.followup_due_date,
            reply_received=False,
            followup_status="drafted",
        ))
        tracker.log_event(session, original.id, "followup_drafted",
                          {"followup_email_id": followup_email.id})
        created += 1
    session.flush()
    return created


def run_scan(session: Session, *, today: Optional[dt.date] = None) -> dict:
    """Single entry point for the background job and the manual scan endpoint."""
    replies = detect_replies(session)
    followups = generate_due_followups(session, today=today)
    return {"replies_detected": replies, "followups_created": followups}


def scan_job() -> dict:
    """Module-level wrapper for the APScheduler job (opens its own session).

    Must stay importable as `modules.followups:scan_job` so the persistent
    jobstore can serialise the reference.
    """
    from db import session as dbsession
    with dbsession.session_scope() as s:
        return run_scan(s)
