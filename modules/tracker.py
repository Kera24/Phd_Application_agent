"""Pipeline state machine + append-only audit logging for emails."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Email, PipelineEvent

# Allowed transitions for emails.status.
# Side exits to cancelled/failed are permitted from any non-terminal state.
TRANSITIONS = {
    "draft_created": {"awaiting_review", "cancelled", "failed"},
    "awaiting_review": {"approved", "draft_created", "cancelled", "failed"},  # regenerate -> draft_created
    "approved": {"scheduled", "awaiting_review", "cancelled", "failed"},
    "scheduled": {"sent", "cancelled", "failed"},
    "sent": set(),          # terminal
    "cancelled": set(),     # terminal
    "failed": {"draft_created", "cancelled"},  # allow retry
}

TERMINAL = {"sent", "cancelled"}


class InvalidTransition(ValueError):
    pass


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def log_event(session: Session, email_id: Optional[int], event: str,
              detail: Optional[dict] = None) -> None:
    session.add(PipelineEvent(email_id=email_id, event=event, detail=detail or {}))


def transition(session: Session, email: Email, target: str,
               detail: Optional[dict] = None) -> Email:
    """Move an email to a new status, enforcing the state machine and logging it."""
    current = email.status
    if current == target:
        return email
    if not can_transition(current, target):
        raise InvalidTransition(f"{current} -> {target} is not allowed")
    email.status = target
    log_event(session, email.id, f"status:{current}->{target}", detail)
    return email
