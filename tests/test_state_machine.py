"""Email state-machine transitions + audit logging."""
import pytest

from db.models import Email, PipelineEvent
from modules import tracker


def test_valid_path(db):
    with db.session_scope() as s:
        e = Email(status="draft_created")
        s.add(e); s.flush()
        tracker.transition(s, e, "awaiting_review")
        tracker.transition(s, e, "approved")
        tracker.transition(s, e, "scheduled")
        tracker.transition(s, e, "sent")
        assert e.status == "sent"


def test_invalid_transition_blocked(db):
    with db.session_scope() as s:
        e = Email(status="draft_created")
        s.add(e); s.flush()
        with pytest.raises(tracker.InvalidTransition):
            tracker.transition(s, e, "sent")  # cannot skip to sent


def test_terminal_states_have_no_exit():
    assert tracker.TRANSITIONS["sent"] == set()
    assert tracker.TRANSITIONS["cancelled"] == set()


def test_side_exit_to_cancelled(db):
    with db.session_scope() as s:
        e = Email(status="approved")
        s.add(e); s.flush()
        tracker.transition(s, e, "cancelled")
        assert e.status == "cancelled"


def test_every_transition_is_logged(db):
    with db.session_scope() as s:
        e = Email(status="draft_created")
        s.add(e); s.flush()
        tracker.transition(s, e, "awaiting_review")
        tracker.transition(s, e, "approved")
        events = s.query(PipelineEvent).filter_by(email_id=e.id).all()
        assert any("draft_created->awaiting_review" in ev.event for ev in events)
        assert any("awaiting_review->approved" in ev.event for ev in events)
