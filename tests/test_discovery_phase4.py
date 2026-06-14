"""Phase 4 — proactive discovery (review-only). Tavily mocked; hermetic."""
import pytest
from fastapi.testclient import TestClient

from db.models import Opportunity
from modules import discovery

HITS = [
    {"url": "https://uni.edu/phd1", "title": "Funded PhD in Medical Imaging",
     "content": "Fully funded PhD position. DFG funded. Prof X."},
    {"url": "https://uni.edu/phd2", "title": "PhD Computer Vision",
     "content": "Stipend provided."},
]


def test_discover_candidates_and_dedupe(db, monkeypatch):
    monkeypatch.setattr(discovery, "tavily_search", lambda q, max_results=5: HITS)
    with db.session_scope() as s:
        cands = discovery.discover_candidates(s, field="Medical Imaging", country="Germany")
        urls = {c["url"] for c in cands}
        assert urls == {"https://uni.edu/phd1", "https://uni.edu/phd2"}
        # import one, then it should be filtered out next time
        s.add(Opportunity(source_type="web", opportunity_type="advertised",
                          source_url="https://uni.edu/phd1"))
        s.flush()
        again = {c["url"] for c in discovery.discover_candidates(
            s, field="Medical Imaging", country="Germany")}
        assert again == {"https://uni.edu/phd2"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(discovery, "tavily_search", lambda q, max_results=5: HITS)
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_discover_endpoint(client):
    r = client.post("/discover", json={"field": "Medical Imaging", "country": "Germany"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 2
    assert body["candidates"][0]["url"].startswith("https://uni.edu/")


def test_discover_run(client, monkeypatch):
    monkeypatch.setattr(discovery, "fetch_page_text",
                        lambda url, **kw: "Fully funded PhD. Prof Jane Doe (j@uni.edu). Medical imaging.")
    r = client.post("/discover/run", json={"url": "https://uni.edu/phd1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "thread_id" in body
    assert body["source_url"] == "https://uni.edu/phd1"


def test_discover_run_unfetchable(client, monkeypatch):
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **kw: "")
    r = client.post("/discover/run", json={"url": "https://uni.edu/blocked"})
    assert r.status_code == 422
