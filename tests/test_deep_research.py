"""Deep research + document generation. LLM / paper APIs / page fetch mocked."""
import pytest
from fastapi.testclient import TestClient

from db.models import GeneratedDocument, Opportunity, ResearchBrief
from modules import deep_research, discovery, documents, llm, paper_apis, prof_research

PAPERS = [{"title": "Paper One", "year": 2024, "venue": "NeurIPS",
           "abstract": "A method for X.", "url": "http://x"}]


def _fake_cj(prompt, system=None, **kw):
    if "relevant_papers" in prompt:          # prof_research analysis call
        return {"relevant_papers": [], "research_themes": [],
                "identified_gap": None, "proposed_angle": None}
    if "candidate_gaps" in prompt and "Choose the single gap" not in prompt:  # _analyse
        return {"themes": ["theme1"], "paper_notes": [],
                "candidate_gaps": [{"gap": "gap1", "related_titles": ["Paper One"]}]}
    return {"chosen_gap": "gap1", "research_question": "q?",   # _synthesise
            "proposed_approach": "approach", "rationale": "fits",
            "problem_statement": "problem", "the_gap": "the gap",
            "current_approaches": [{"approach": "baseline", "citation": "Paper One"}],
            "proposed_extension": "extend it", "pitch": "here is my project",
            "talks": ["Keynote 2025"], "future_directions": ["scaling X"],
            "paper_deep_dive": [{"title": "Paper One", "summary": "does X"}],
            "cited_titles": ["Paper One"]}


def _opp(s):
    o = Opportunity(source_type="web", opportunity_type="advertised",
                    professor_name="Jane Doe", university="Example University",
                    professor_email="jane@uni.edu")
    s.add(o); s.flush()
    return o


def test_deep_research_keyless(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(paper_apis, "recent_papers", lambda *a, **k: (PAPERS, "semantic_scholar"))
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "")
    with db.session_scope() as s:
        o = _opp(s)
        brief = deep_research.run_deep_research(s, o)
        assert brief["method"] == "keyless"
        assert brief["chosen_gap"]
        assert s.query(ResearchBrief).filter_by(opportunity_id=o.id).count() == 1


def test_deep_research_llm_grounded(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(paper_apis, "recent_papers", lambda *a, **k: (PAPERS, "semantic_scholar"))
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "lab page text")
    monkeypatch.setattr(llm, "complete_json", _fake_cj)
    # No live web search in unit tests: degrade to the crawl-only path.
    def _no_web(*a, **k):
        raise llm.LLMUnavailable("no web in tests")
    monkeypatch.setattr(llm, "research_with_search", _no_web)
    with db.session_scope() as s:
        o = _opp(s)
        brief = deep_research.run_deep_research(s, o)
        assert brief["method"] == "llm"
        assert brief["chosen_gap"] == "gap1"
        assert brief["research_question"] == "q?"
        # citation grounded to a retrieved paper
        assert [c["title"] for c in brief["citations"]] == ["Paper One"]


def test_deep_research_web_search_dossier(db, monkeypatch):
    """Web-search dossier enriches the brief: dossier_md + structured proposal fields."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(paper_apis, "recent_papers", lambda *a, **k: (PAPERS, "semantic_scholar"))
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "lab page text")
    monkeypatch.setattr(llm, "complete_json", _fake_cj)
    monkeypatch.setattr(llm, "research_with_search", lambda *a, **k: {
        "text": "## Profile & lab\nGreat lab.\n## Stated future directions\nScaling X.",
        "sources": [{"url": "http://lab.edu", "title": "Lab site"}]})
    with db.session_scope() as s:
        o = _opp(s)
        brief = deep_research.run_deep_research(s, o)
        assert brief["method"] == "llm"
        assert brief["sources_used"]["web_searched"] is True
        assert "Great lab." in brief["dossier_md"]
        assert brief["problem_statement"] == "problem"
        assert brief["proposed_extension"] == "extend it"
        assert brief["pitch"] == "here is my project"
        assert brief["future_directions"] == ["scaling X"]
        assert brief["sources"] == [{"url": "http://lab.edu", "title": "Lab site"}]


def test_documents_generation_keyless(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.session_scope() as s:
        o = _opp(s)
        s.add(ResearchBrief(opportunity_id=o.id, data={
            "chosen_gap": "gap1", "research_question": "q?",
            "proposed_approach": "approach", "citations": PAPERS}))
        s.flush()
        docs = documents.generate_documents(s, o, ["email", "sop", "cover", "proposal"])
        assert {d["kind"] for d in docs} == {"email", "sop", "cover", "proposal"}
        assert all(d["content"] for d in docs)
        assert s.query(GeneratedDocument).filter_by(opportunity_id=o.id).count() == 4


def test_documents_generation_llm(db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(llm, "complete", lambda prompt, **k: "GENERATED DOC BODY")
    with db.session_scope() as s:
        o = _opp(s)
        s.add(ResearchBrief(opportunity_id=o.id, data={"chosen_gap": "g", "citations": []}))
        s.flush()
        docs = documents.generate_documents(s, o, ["sop"])
        assert docs[0]["content"] == "GENERATED DOC BODY"


# --- endpoints --------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(paper_apis, "recent_papers", lambda *a, **k: (PAPERS, "semantic_scholar"))
    monkeypatch.setattr(discovery, "fetch_page_text", lambda url, **k: "")
    from api.main import app
    with TestClient(app) as c:
        yield c


def _seed_opp(client):
    from api.main import dbsession
    with dbsession.session_scope() as s:
        o = _opp(s)
        return o.id


def test_deep_research_and_documents_endpoints(client):
    oid = _seed_opp(client)
    r = client.post(f"/opportunities/{oid}/deep-research")
    assert r.status_code == 200, r.text
    assert r.json()["brief"]["chosen_gap"]

    r = client.post(f"/opportunities/{oid}/documents", json={"kinds": ["email", "sop"]})
    assert r.status_code == 200, r.text
    docs = r.json()["documents"]
    assert {d["kind"] for d in docs} == {"email", "sop"}

    # edit one
    doc_id = docs[0]["id"]
    r = client.put(f"/documents/{doc_id}", json={"content": "edited"})
    assert r.status_code == 200 and r.json()["content"] == "edited"

    # list
    r = client.get(f"/opportunities/{oid}/documents")
    assert len(r.json()["documents"]) == 2
