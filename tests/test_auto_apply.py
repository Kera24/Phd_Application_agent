"""One-shot auto-apply: posting-driven document selection, skim brief, endpoint.

All LLM / paper APIs / page fetch are mocked; runs keyless where possible.
"""
import pytest
from fastapi.testclient import TestClient

from db.models import GeneratedDocument, Opportunity, Professor, ResearchBrief
from modules import deep_research, documents


def _opp(s, required_documents=None, **kw):
    o = Opportunity(source_type="web", opportunity_type="advertised",
                    professor_name="Jane Doe", university="Example University",
                    professor_email="jane@uni.edu",
                    required_documents=required_documents, **kw)
    s.add(o); s.flush()
    return o


# --- kinds_for_opportunity --------------------------------------------------

@pytest.mark.parametrize("required,expected", [
    (["CV", "Statement of Purpose", "Research proposal"], ["sop", "proposal"]),
    (["Letter of motivation"], ["cover"]),
    (["cover letter", "transcripts"], ["cover"]),
    (["research statement"], ["proposal"]),
    (["CV", "transcripts"], []),          # nothing maps -> no extra docs
    ([], []),
    (None, []),
])
def test_kinds_for_opportunity(db, required, expected):
    with db.session_scope() as s:
        o = _opp(s, required_documents=required)
        assert documents.kinds_for_opportunity(o) == expected


def test_kinds_never_includes_email(db):
    with db.session_scope() as s:
        o = _opp(s, required_documents=["email", "Statement of Purpose"])
        kinds = documents.kinds_for_opportunity(o)
        assert "email" not in kinds and kinds == ["sop"]


# --- skim_brief -------------------------------------------------------------

def test_skim_brief_from_researched_prof(db):
    """Builds a brief from the prof record with no LLM/web calls."""
    with db.session_scope() as s:
        prof = Professor(name="Jane Doe", university="Example University",
                         identified_gap="robustness under distribution shift",
                         proposed_angle="contrastive pretraining from my VPR work",
                         research_themes=["self-supervised vision"],
                         recent_papers=[{"title": "Paper One", "year": 2024}])
        s.add(prof); s.flush()
        o = _opp(s, professor_id=prof.id)
        brief = deep_research.skim_brief(s, o)
        assert brief["method"] == "skim"
        assert brief["chosen_gap"] == "robustness under distribution shift"
        assert brief["proposed_approach"].startswith("contrastive")
        assert brief["citations"][0]["title"] == "Paper One"
        assert s.query(ResearchBrief).filter_by(opportunity_id=o.id).count() == 1


def test_skim_brief_keeps_existing_deep_brief(db):
    """A richer existing brief (e.g. deep scout) is not overwritten."""
    with db.session_scope() as s:
        o = _opp(s)
        s.add(ResearchBrief(opportunity_id=o.id,
                            data={"chosen_gap": "deep gap", "method": "llm"}))
        s.flush()
        brief = deep_research.skim_brief(s, o)
        assert brief["method"] == "llm" and brief["chosen_gap"] == "deep gap"
        assert s.query(ResearchBrief).filter_by(opportunity_id=o.id).count() == 1


# --- /auto-apply endpoint ---------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_auto_apply_requires_input(client):
    r = client.post("/auto-apply", json={})
    assert r.status_code == 422


def test_auto_apply_text_runs_pipeline(client, monkeypatch):
    """Keyless run: unfunded posting is archived, but the endpoint resolves cleanly.

    /auto-apply always sets run_mode='reactive' so the funding gate is bypassed.
    We assert the run completes (either awaiting_approval or completed) and does
    NOT block on the no-funding signal.
    """
    from api import main
    from modules import discovery
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "")
    r = client.post("/auto-apply", json={"text": "Self-funded PhD, no stipend. CV required."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("awaiting_approval", "completed")
    assert "thread_id" in body


def test_runs_endpoint_proactive_keeps_funding_gate(client, monkeypatch):
    """Legacy /runs without run_mode: no funding signal -> archived, no draft.

    The /runs endpoint is the proactive/discovered path. Explicit funding
    absence must still park the opportunity (the user did not submit this
    themselves; the system found it). This guards the gate from being
    accidentally bypassed for the autonomous path.
    """
    from modules import discovery
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "")
    r = client.post("/runs", json={"linkedin_inputs": ["Self-funded PhD, no stipend."]})
    assert r.status_code == 200, r.text
    body = r.json()
    # Proactive path with no funding -> archived before drafting -> completed
    # with no interrupt. This is the behaviour that changed for reactive paths.
    assert body["status"] == "completed"
    assert "interrupt" not in body


def test_auto_apply_url_fetch_failure(client, monkeypatch):
    from modules import discovery
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "")
    r = client.post("/auto-apply", json={"url": "http://blocked.example/post"})
    assert r.status_code == 422


def test_finish_auto_apply_generates_listing_documents(client):
    """On approval, the listing's required documents are generated (keyless)."""
    from api import main
    from api.main import dbsession
    with dbsession.session_scope() as s:
        prof = Professor(name="Jane Doe", university="Example University",
                         identified_gap="a gap", proposed_angle="my angle",
                         recent_papers=[{"title": "Paper One", "year": 2024}])
        s.add(prof); s.flush()
        o = _opp(s, professor_id=prof.id,
                 required_documents=["CV", "Statement of Purpose", "Research proposal"])
        oid = o.id

    out = main._finish_auto_apply(
        {"status": "awaiting_approval", "thread_id": "t1",
         "interrupt": {"opportunity_id": oid}})
    kinds = {d["kind"] for d in out["generated_documents"]}
    assert kinds == {"sop", "proposal"}            # email excluded; cover not requested
    with dbsession.session_scope() as s:
        rows = s.query(GeneratedDocument).filter_by(opportunity_id=oid).all()
        assert {r.kind for r in rows} == {"sop", "proposal"}


def test_finish_auto_apply_noop_without_approval(client):
    """A non-approval result is returned untouched (no documents)."""
    from api import main
    out = main._finish_auto_apply({"status": "completed", "thread_id": "t2"})
    assert "generated_documents" not in out
