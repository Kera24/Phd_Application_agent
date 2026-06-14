"""Opportunity detail aggregation + cascade delete."""
import pytest
from fastapi.testclient import TestClient

from db.models import (
    ApplicationTracking, Email, GeneratedDocument, Opportunity, PipelineEvent,
    ResearchBrief,
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from api.main import app
    with TestClient(app) as c:
        yield c


def _seed_full(client):
    from api.main import dbsession
    with dbsession.session_scope() as s:
        o = Opportunity(source_type="web", opportunity_type="advertised",
                        position_title="PhD X", university="Uni",
                        professor_name="Jane Doe")
        s.add(o); s.flush()
        e = Email(opportunity_id=o.id, subject="Hi", status="draft_created")
        s.add(e); s.flush()
        s.add(PipelineEvent(email_id=e.id, event="email_drafted", detail={}))
        s.add(GeneratedDocument(opportunity_id=o.id, kind="sop", title="SOP", content="..."))
        s.add(ResearchBrief(opportunity_id=o.id, data={"chosen_gap": "g"}))
        s.add(ApplicationTracking(opportunity_id=o.id, status="applied"))
        s.flush()
        return o.id


def test_opportunity_detail(client):
    oid = _seed_full(client)
    r = client.get(f"/opportunities/{oid}/detail")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["opportunity"]["title"] == "PhD X"
    assert d["application"]["status"] == "applied"
    assert d["research_brief"]["chosen_gap"] == "g"
    assert len(d["emails"]) == 1
    assert d["documents"][0]["kind"] == "sop"
    assert any(ev["event"] == "email_drafted" for ev in d["timeline"])


def test_delete_opportunity_cascades(client):
    oid = _seed_full(client)
    r = client.delete(f"/opportunities/{oid}")
    assert r.status_code == 200, r.text
    # gone, and no leftover dependent rows / errors
    assert client.get(f"/opportunities/{oid}/detail").status_code == 404
    from api.main import dbsession
    with dbsession.session_scope() as s:
        assert s.query(Email).filter_by(opportunity_id=oid).count() == 0
        assert s.query(GeneratedDocument).filter_by(opportunity_id=oid).count() == 0
        assert s.query(ResearchBrief).filter_by(opportunity_id=oid).count() == 0
        assert s.query(ApplicationTracking).filter_by(opportunity_id=oid).count() == 0


def test_detail_unknown(client):
    assert client.get("/opportunities/99999/detail").status_code == 404
