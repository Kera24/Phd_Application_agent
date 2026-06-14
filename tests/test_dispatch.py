"""Phase 2 — durable dispatch of scheduled sends + the /cron/tick endpoint.

Gmail is never actually called (mocked / gated). Hermetic.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from db.models import Email, Professor
from modules import config_loader, gmail_client, scheduler as sched_mod, tracker


def _past():
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)


def _future():
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)


def _scheduled_email(s, when):
    prof = Professor(name="Jane Doe", email="jane@uni.edu")
    s.add(prof); s.flush()
    e = Email(professor_id=prof.id, status="scheduled",
              scheduled_send_at_utc=when, is_followup=False)
    s.add(e); s.flush()
    return e


def test_dispatch_sends_due(db, monkeypatch):
    monkeypatch.setattr(config_loader, "config", lambda: {"approved_send_mode": True})

    def fake_send(session, email, paths, *, max_retries=3, allowed_statuses=("approved",)):
        assert "scheduled" in allowed_statuses
        tracker.transition(session, email, "sent", {"message_id": "fake"})
        return "fake"

    monkeypatch.setattr(gmail_client, "send", fake_send)
    with db.session_scope() as s:
        e = _scheduled_email(s, _past())
        eid = e.id
        res = sched_mod.dispatch_due_sends(s)
        assert res["sent"] == 1
        assert s.get(Email, eid).status == "sent"


def test_dispatch_skips_future(db, monkeypatch):
    monkeypatch.setattr(config_loader, "config", lambda: {"approved_send_mode": True})
    called = []
    monkeypatch.setattr(gmail_client, "send", lambda *a, **k: called.append(1))
    with db.session_scope() as s:
        _scheduled_email(s, _future())
        res = sched_mod.dispatch_due_sends(s)
        assert res["sent"] == 0 and not called


def test_dispatch_noop_without_send_mode(db, monkeypatch):
    monkeypatch.setattr(config_loader, "config", lambda: {"approved_send_mode": False})

    def boom(*a, **k):
        raise AssertionError("send must not be called in draft-only mode")

    monkeypatch.setattr(gmail_client, "send", boom)
    with db.session_scope() as s:
        _scheduled_email(s, _past())
        res = sched_mod.dispatch_due_sends(s)
        assert res == {"sent": 0, "failed": 0, "skipped": 1}


# --- /cron/tick endpoint -----------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CRON_TOKEN", "secret123")
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_cron_tick_rejects_bad_token(client):
    r = client.post("/cron/tick", headers={"X-Cron-Token": "wrong"})
    assert r.status_code == 401


def test_cron_tick_runs_with_token(client):
    r = client.post("/cron/tick", headers={"X-Cron-Token": "secret123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sends" in body and "scan" in body
