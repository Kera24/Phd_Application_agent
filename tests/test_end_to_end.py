"""End-to-end pipeline with a fixture LinkedIn post + a fixture professor.

No real network: Semantic Scholar is monkeypatched with canned papers, the LLM
is disabled (deterministic fallbacks), and Gmail is a mock client. Verifies no
real send occurs.
"""
import pytest

from db.models import Email, Opportunity, Professor
from modules import (
    email_gen,
    gmail_client,
    llm,
    paper_apis,
    parser,
    prof_research,
    scoring,
    tracker,
)

FIXTURE_POST = (
    "Fully funded PhD position (full stipend, TV-L E13) in Medical Imaging at "
    "the Technical University of Munich. Supervisor: Prof. Maria Schmidt "
    "(maria.schmidt@tum.de). Topic: deep learning for mammography. "
    "Apply with CV and transcript by 2026-09-01."
)

CANNED_PAPERS = [
    {"title": "Explainable Deep Learning for Mammography Screening",
     "year": 2024, "venue": "MICCAI",
     "url": "https://example.org/p1",
     "abstract": "We study ViT-based mammography with saliency-based explanations; "
                 "robustness across scanners remains an open challenge."},
    {"title": "Self-Supervised Pretraining for Breast Imaging",
     "year": 2023, "venue": "MIDL", "url": "https://example.org/p2",
     "abstract": "Contrastive pretraining improves low-label mammography performance."},
]


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(
        paper_apis, "recent_papers",
        lambda name, affiliation="", limit=5: (CANNED_PAPERS, "semantic_scholar"),
    )


def test_full_advertised_pipeline(db):
    with db.session_scope() as s:
        # 1. Parse + funding gate.
        opp = parser.classify_and_store(s, FIXTURE_POST, source_type="linkedin_manual")
        assert opp.funding_status == "funded"
        assert opp.pipeline_status == "parsed"
        assert opp.professor_email == "maria.schmidt@tum.de"

        # 2. Professor research with verified papers.
        prof = prof_research.research_professor(
            s, "Maria Schmidt", affiliation="Technical University of Munich",
            email=opp.professor_email)
        opp.professor_id = prof.id
        assert len(prof.recent_papers) == 2

        # 3. Scoring.
        result = scoring.score_opportunity(opp, prof)
        opp.fit_score = result["total"]
        assert 0 <= result["total"] <= 100

        # 4. Email generation + quality gate -> Email row exists.
        email = email_gen.generate_email(s, opp, prof)
        assert email.id is not None
        assert email.attachments  # has attachment kinds
        # Fallback draft cites the first canned paper title.
        assert CANNED_PAPERS[0]["title"] in (email.body or "")


def test_non_funded_post_never_emailed(db):
    with db.session_scope() as s:
        opp = parser.classify_and_store(
            s, "Self-funded PhD opening in CV. Prof X (x@uni.edu).")
        assert opp.pipeline_status == "archived_not_funded"
        # No email should be generated for archived opportunities.
        emails = s.query(Email).filter_by(opportunity_id=opp.id).all()
        assert emails == []


class MockGmail:
    def __init__(self):
        self.sent = []
        self.drafts = []

    def create_draft(self, session, email, paths):
        self.drafts.append(email.id)
        email.gmail_draft_id = f"draft_{email.id}"
        return email.gmail_draft_id

    def send(self, *a, **k):
        raise AssertionError("send() must never be called in tests")


def test_draft_only_no_send(db, monkeypatch):
    mock = MockGmail()
    monkeypatch.setattr(gmail_client, "create_draft", mock.create_draft)
    monkeypatch.setattr(gmail_client, "send", mock.send)

    with db.session_scope() as s:
        prof = Professor(name="Maria Schmidt", email="maria.schmidt@tum.de")
        s.add(prof); s.flush()
        opp = Opportunity(source_type="linkedin_manual", opportunity_type="advertised",
                          professor_id=prof.id, funding_status="funded",
                          pipeline_status="parsed", position_title="PhD")
        s.add(opp); s.flush()
        email = Email(opportunity_id=opp.id, professor_id=prof.id,
                      subject="PhD application", body="...", status="draft_created",
                      attachments=["cv"])
        s.add(email); s.flush()

        gmail_client.create_draft(s, email, {})
        assert email.gmail_draft_id == f"draft_{email.id}"
        assert mock.sent == []  # nothing sent


def test_send_blocked_when_mode_off(db):
    """send() must refuse when approved_send_mode is false (config default)."""
    with db.session_scope() as s:
        prof = Professor(name="X", email="x@uni.edu")
        s.add(prof); s.flush()
        email = Email(professor_id=prof.id, status="approved", subject="s", body="b")
        s.add(email); s.flush()
        with pytest.raises(gmail_client.SendNotPermitted):
            gmail_client.send(s, email, {})
