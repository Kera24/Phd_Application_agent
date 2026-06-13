"""Dedup by professor email / fuzzy name + title, plus follow-up send gating."""
import pytest

from db.models import Email, Opportunity, Professor
from modules import discovery, gmail_client


def test_duplicate_professor_by_email(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane Doe", email="jane@uni.edu", university="Uni X"))
        s.flush()
        assert discovery.is_duplicate_professor(s, "jane@uni.edu", "Jane Doe", "Uni X")


def test_duplicate_professor_by_fuzzy_name(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane A. Doe", university="Technical University of Munich"))
        s.flush()
        found = discovery.is_duplicate_professor(s, None, "Jane A Doe", "TU Munich")
        assert found is not None


def test_non_duplicate_professor(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane Doe", email="jane@uni.edu"))
        s.flush()
        assert discovery.is_duplicate_professor(s, None, "John Smith", "Other Uni") is None


def test_duplicate_opportunity_by_email(db):
    with db.session_scope() as s:
        s.add(Opportunity(source_type="web", professor_email="p@uni.edu", university="Uni"))
        s.flush()
        assert discovery.is_duplicate_opportunity(s, "Uni", "p@uni.edu", "Some PhD")


def test_duplicate_opportunity_by_fuzzy_title(db):
    with db.session_scope() as s:
        s.add(Opportunity(source_type="web", university="Uni",
                          position_title="PhD in Medical Imaging with Deep Learning"))
        s.flush()
        assert discovery.is_duplicate_opportunity(
            s, "Uni", None, "PhD in Deep Learning for Medical Imaging")


# --- Phase 3: follow-ups are a deliberate *second* email to the same prof ----

def _prof_with_sent(s):
    prof = Professor(name="Jane Doe", email="jane@uni.edu")
    s.add(prof); s.flush()
    first = Email(professor_id=prof.id, status="sent", is_followup=False)
    s.add(first); s.flush()
    return prof, first


def test_dedupe_blocks_duplicate_first_contact(db):
    with db.session_scope() as s:
        prof, _ = _prof_with_sent(s)
        dup = Email(professor_id=prof.id, status="approved", is_followup=False)
        s.add(dup); s.flush()
        assert gmail_client._dedupe_ok(s, prof, dup) is False


def test_dedupe_allows_followup(db):
    with db.session_scope() as s:
        prof, first = _prof_with_sent(s)
        fu = Email(professor_id=prof.id, status="approved", is_followup=True,
                   parent_email_id=first.id)
        s.add(fu); s.flush()
        assert gmail_client._dedupe_ok(s, prof, fu) is True


def test_dedupe_blocks_followup_after_reply(db):
    with db.session_scope() as s:
        prof, first = _prof_with_sent(s)
        first.reply_received = True
        s.flush()
        fu = Email(professor_id=prof.id, status="approved", is_followup=True,
                   parent_email_id=first.id)
        s.add(fu); s.flush()
        # Reply arrived — no point following up; send is blocked.
        assert gmail_client._dedupe_ok(s, prof, fu) is False


def test_approved_followup_not_sent_without_send_mode(db, monkeypatch):
    # approved_send_mode is false by default -> send must refuse before any API call.
    monkeypatch.setattr(gmail_client.config_loader, "config",
                        lambda: {"approved_send_mode": False})
    with db.session_scope() as s:
        prof, first = _prof_with_sent(s)
        fu = Email(professor_id=prof.id, status="approved", is_followup=True,
                   parent_email_id=first.id)
        s.add(fu); s.flush()
        with pytest.raises(gmail_client.SendNotPermitted):
            gmail_client.send(s, fu, {})
