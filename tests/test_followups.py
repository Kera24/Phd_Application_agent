"""Phase 3 — follow-up due-date logic + generation gating."""
import datetime as dt

from db.models import Email, Followup, Opportunity, Professor
from modules import followups


def _seed_sent(s, *, due_offset_days, reply=False, prof=None):
    """Create a sent first-contact email with a follow-up due date offset from today."""
    prof = prof or Professor(name="Jane Doe", email="jane@uni.edu",
                             recent_papers=[{"title": "Deep Nets for X", "year": 2024}])
    s.add(prof); s.flush()
    opp = Opportunity(source_type="web", professor_name=prof.name, country="Germany",
                      research_fields=["Medical Imaging"])
    s.add(opp); s.flush()
    e = Email(opportunity_id=opp.id, professor_id=prof.id, subject="PhD application",
              body="hello", status="sent", is_followup=False, reply_received=reply,
              sent_date=dt.date.today() - dt.timedelta(days=30),
              followup_due_date=dt.date.today() + dt.timedelta(days=due_offset_days),
              gmail_thread_id="thread-1")
    s.add(e); s.flush()
    return e


def test_business_days_after_skips_weekend():
    # Fri 2026-06-12 + 1 business day = Mon 2026-06-15.
    friday = dt.date(2026, 6, 12)
    assert followups.business_days_after(friday, 1) == dt.date(2026, 6, 15)
    # +5 business days from Fri = next Fri.
    assert followups.business_days_after(friday, 5) == dt.date(2026, 6, 19)


def test_due_followup_generated_when_overdue(db):
    with db.session_scope() as s:
        original = _seed_sent(s, due_offset_days=-5)
        created = followups.generate_due_followups(s)
        assert created == 1
        fu = s.query(Email).filter_by(is_followup=True).one()
        assert fu.parent_email_id == original.id
        assert fu.subject.lower().startswith("re:")
        assert fu.status == "awaiting_review"          # passes the lighter gate
        assert s.query(Followup).count() == 1


def test_not_generated_when_not_due(db):
    with db.session_scope() as s:
        _seed_sent(s, due_offset_days=+5)              # due in the future
        assert followups.generate_due_followups(s) == 0


def test_not_generated_when_replied(db):
    with db.session_scope() as s:
        _seed_sent(s, due_offset_days=-5, reply=True)
        assert followups.generate_due_followups(s) == 0


def test_max_one_followup(db):
    with db.session_scope() as s:
        _seed_sent(s, due_offset_days=-5)
        assert followups.generate_due_followups(s) == 1
        # Second scan: original is now 'drafted', so no further follow-up.
        assert followups.generate_due_followups(s) == 0
        assert s.query(Email).filter_by(is_followup=True).count() == 1


def test_run_scan_counts(db):
    with db.session_scope() as s:
        _seed_sent(s, due_offset_days=-1)
        res = followups.run_scan(s)
        assert res["followups_created"] == 1
        assert res["replies_detected"] == 0            # no Gmail in tests
