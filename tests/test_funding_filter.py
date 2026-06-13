"""Rule 0 — fully-funded hard filter. Uses the heuristic parser (no LLM key)."""
import pytest

from modules import llm, parser


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)


def test_self_funded_is_archived(db):
    with db.session_scope() as s:
        o = parser.classify_and_store(
            s, "PhD position, self-funded applicants only. Prof. test@uni.edu")
        assert o.funding_status == "self-funded"
        assert o.pipeline_status == "archived_not_funded"


def test_unfunded_is_archived(db):
    with db.session_scope() as s:
        o = parser.classify_and_store(s, "PhD opening, no funding available.")
        assert o.pipeline_status == "archived_not_funded"


def test_unknown_is_surfaced(db):
    with db.session_scope() as s:
        o = parser.classify_and_store(s, "We seek a PhD student in computer vision.")
        assert o.funding_status == "unknown"
        assert o.pipeline_status == "funding_unknown"


def test_funded_with_email_proceeds(db):
    with db.session_scope() as s:
        o = parser.classify_and_store(
            s, "Fully funded PhD position (full stipend). Contact prof@uni.edu")
        assert o.funding_status == "funded"
        assert o.pipeline_status == "parsed"


def test_funded_without_email_needs_email(db):
    with db.session_scope() as s:
        o = parser.classify_and_store(s, "Fully funded PhD position with full stipend.")
        assert o.funding_status == "funded"
        assert o.pipeline_status == "needs_email"


def test_manual_confirm_funding(db):
    with db.session_scope() as s:
        o = parser.classify_and_store(s, "PhD in CV. prof@uni.edu")
        assert o.pipeline_status == "funding_unknown"
        parser.confirm_funding(s, o, funded=True, evidence="program page confirms stipend")
        assert o.funding_status == "funded"
        assert o.pipeline_status == "parsed"
