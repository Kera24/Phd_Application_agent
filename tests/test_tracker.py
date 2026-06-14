"""Application + deadline tracker endpoints."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from db.models import Opportunity


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app
    with TestClient(app) as c:
        yield c


def _seed(client):
    from api.main import dbsession
    today = dt.date.today()
    with dbsession.session_scope() as s:
        far = Opportunity(source_type="web", opportunity_type="advertised",
                          position_title="Far PhD", deadline=today + dt.timedelta(days=30))
        near = Opportunity(source_type="web", opportunity_type="advertised",
                           position_title="Near PhD", deadline=today + dt.timedelta(days=3))
        archived = Opportunity(source_type="web", opportunity_type="advertised",
                               position_title="Archived", pipeline_status="archived_not_funded")
        s.add_all([far, near, archived]); s.flush()
        return near.id


def test_applications_list_sorted_and_excludes_archived(client):
    near_id = _seed(client)
    r = client.get("/applications")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = [a["title"] for a in body["applications"]]
    assert "Archived" not in titles               # archived excluded
    assert titles[0] == "Near PhD"                 # nearest deadline first
    assert body["applications"][0]["opportunity_id"] == near_id
    assert body["applications"][0]["status"] == "interested"   # default
    assert "interested" in body["statuses"]


def test_update_application_status(client):
    near_id = _seed(client)
    r = client.put(f"/applications/{near_id}", json={"status": "applied", "notes": "sent"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    # reflected in the list
    r = client.get("/applications")
    near = next(a for a in r.json()["applications"] if a["opportunity_id"] == near_id)
    assert near["status"] == "applied" and near["notes"] == "sent"


def test_update_application_bad_status(client):
    near_id = _seed(client)
    r = client.put(f"/applications/{near_id}", json={"status": "nonsense"})
    assert r.status_code == 400


def test_update_application_unknown(client):
    r = client.put("/applications/99999", json={"status": "applied"})
    assert r.status_code == 404
