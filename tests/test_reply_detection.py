"""Phase 3 — reply detection (Gmail-optional) + manual marking."""
import datetime as dt

from db.models import Email, Followup, Opportunity, Professor
from modules import followups, gmail_client


def _seed_sent(s, thread_id="t-1"):
    prof = Professor(name="Jane Doe", email="jane@uni.edu")
    s.add(prof); s.flush()
    opp = Opportunity(source_type="web", professor_name=prof.name, country="Germany")
    s.add(opp); s.flush()
    e = Email(opportunity_id=opp.id, professor_id=prof.id, subject="PhD application",
              body="hi", status="sent", gmail_thread_id=thread_id,
              sent_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc))
    s.add(e); s.flush()
    return e


def test_manual_mark_replied(db):
    with db.session_scope() as s:
        e = _seed_sent(s)
        followups.mark_replied(s, e, by="manual")
        assert e.reply_received is True
        assert e.reply_received_at is not None
        assert e.followup_status == "replied"


def test_detect_replies_flags_when_inbound(db, monkeypatch):
    with db.session_scope() as s:
        e = _seed_sent(s)
        eid = e.id
    # Pretend Gmail is authorised and the thread has an inbound reply.
    monkeypatch.setattr(gmail_client, "fetch_thread_replies",
                        lambda tid: [{"from": "jane@uni.edu", "date": "..."}])
    with db.session_scope() as s:
        flagged = followups.detect_replies(s)
        assert flagged == 1
        assert s.get(Email, eid).reply_received is True


def test_detect_replies_noop_without_inbound(db, monkeypatch):
    with db.session_scope() as s:
        _seed_sent(s)
    monkeypatch.setattr(gmail_client, "fetch_thread_replies", lambda tid: [])
    with db.session_scope() as s:
        assert followups.detect_replies(s) == 0


def test_detected_reply_cascades_to_followup_record(db, monkeypatch):
    with db.session_scope() as s:
        e = _seed_sent(s)
        s.add(Followup(email_id=e.id, followup_status="drafted"))
        s.flush()
    monkeypatch.setattr(gmail_client, "fetch_thread_replies",
                        lambda tid: [{"from": "jane@uni.edu"}])
    with db.session_scope() as s:
        followups.detect_replies(s)
        fu = s.query(Followup).one()
        assert fu.reply_received is True
        assert fu.followup_status == "replied"
