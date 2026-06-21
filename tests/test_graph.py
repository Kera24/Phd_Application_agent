"""LangGraph integration + safety tests.

No network, no real API keys, no real sends. The LLM is disabled, Semantic
Scholar is canned, scoring/email are stubbed to deterministic passing values so
the graph reaches the Human Approval interrupt, and Gmail send is wired to fail
if it is ever called.
"""
import pytest
from langgraph.types import Command

from db.models import Approval, Email, Opportunity
from modules import email_gen, gmail_client, llm, paper_apis, scoring, tracker

CANNED = [
    {"title": "Explainable DL for Mammography", "year": 2024, "venue": "MICCAI",
     "url": "http://x/1", "abstract": "ViT mammography; scanner robustness open."},
]


@pytest.fixture()
def graph_env(db, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(paper_apis, "recent_papers",
                        lambda *a, **k: (CANNED, "semantic_scholar"))
    # High fit so we pass the threshold gate.
    monkeypatch.setattr(scoring, "score_opportunity",
                        lambda opp, prof=None: {"total": 88,
                                                "breakdown": {"research_alignment": {"score": 9, "weight": 30, "weighted": 27, "rationale": "x"}},
                                                "highlight": True})

    # A quality-gate-passing email draft.
    def fake_generate(s, opp, prof):
        e = Email(opportunity_id=opp.id, professor_id=prof.id if prof else None,
                  subject="PhD application medical imaging",
                  body='Body citing "Explainable DL for Mammography".',
                  attachments=["cv"], quality_gate_passed=True,
                  quality_gate_report={"passed": True, "checks": {}},
                  status="draft_created")
        s.add(e); s.flush()
        tracker.transition(s, e, "awaiting_review", {"r": "stub"}); s.flush()
        return e
    monkeypatch.setattr(email_gen, "generate_email", fake_generate)

    # Never allow a real send during tests.
    def boom(*a, **k):
        raise AssertionError("send_from_fields must never be called in tests")
    monkeypatch.setattr(gmail_client, "send_from_fields", boom)
    monkeypatch.setattr(gmail_client, "is_authorised", lambda: False)

    from agent.graph import build_graph
    from agent.checkpointer import build_checkpointer
    return build_graph(build_checkpointer("memory"))


FUNDED_POST = ("Fully funded PhD (full stipend) in Medical Imaging at TU Munich. "
               "Prof. Maria Schmidt (maria.schmidt@tum.de). Deadline 2026-09-01.")


def test_interrupt_fires_before_any_send(graph_env):
    g = graph_env
    cfg = {"configurable": {"thread_id": "tA"}, "recursion_limit": 80}
    res = g.invoke({"linkedin_inputs": [FUNDED_POST]}, cfg)
    assert "__interrupt__" in res, "graph must pause at Human Approval"
    payload = res["__interrupt__"][0].value
    assert payload["subject"]
    assert payload["fit_score"] == 88
    # Paused at approval, nothing sent (send stub would have raised).
    assert g.get_state(cfg).next  # graph is paused, not finished


def test_reject_completes_without_send(graph_env):
    g = graph_env
    cfg = {"configurable": {"thread_id": "tB"}, "recursion_limit": 80}
    g.invoke({"linkedin_inputs": [FUNDED_POST]}, cfg)
    g.invoke(Command(resume={"action": "reject", "reason": "not a fit"}), cfg)
    from db import session as dbsession
    with dbsession.session_scope() as s:
        email = s.query(Email).first()
        assert email.status == "cancelled"
        appr = s.query(Approval).first()
        assert appr.decision == "rejected"


def test_approve_without_send_mode_does_not_schedule(graph_env):
    g = graph_env
    cfg = {"configurable": {"thread_id": "tC"}, "recursion_limit": 80}
    g.invoke({"linkedin_inputs": [FUNDED_POST]}, cfg)
    # approved_send_mode defaults to false -> approved but never scheduled/sent.
    g.invoke(Command(resume={"action": "approve"}), cfg)
    from db import session as dbsession
    with dbsession.session_scope() as s:
        email = s.query(Email).first()
        assert email.status == "approved"
        assert email.scheduled_send_at_utc is None  # not scheduled


def test_approve_with_send_mode_schedules_but_does_not_send(graph_env, monkeypatch):
    from modules import config_loader
    base = config_loader.config()
    base["approved_send_mode"] = True
    monkeypatch.setattr(config_loader, "config", lambda: base)

    g = graph_env
    cfg = {"configurable": {"thread_id": "tS"}, "recursion_limit": 80}
    g.invoke({"linkedin_inputs": [FUNDED_POST]}, cfg)
    g.invoke(Command(resume={"action": "approve"}), cfg)
    from db import session as dbsession
    from db.models import ScheduledEmail
    with dbsession.session_scope() as s:
        email = s.query(Email).first()
        assert email.status == "scheduled"
        assert email.scheduled_send_at_utc is not None
        sched = s.query(ScheduledEmail).first()
        assert sched is not None and sched.status == "queued"
        # send stub would have raised — reaching here proves nothing was sent.


def test_non_funded_post_archived_no_interrupt(graph_env):
    g = graph_env
    cfg = {"configurable": {"thread_id": "tD"}, "recursion_limit": 80}
    res = g.invoke(
        {"linkedin_inputs": ["Self-funded PhD, no stipend. Prof X (x@uni.edu)."]}, cfg)
    assert "__interrupt__" not in res
    from db import session as dbsession
    with dbsession.session_scope() as s:
        opp = s.query(Opportunity).first()
        assert opp.pipeline_status == "archived_not_funded"
        assert s.query(Email).count() == 0


def test_reactive_non_funded_post_drafts_anyway(graph_env):
    """run_mode='reactive' bypasses the funding gate; an explicit submission
    even with no funding signal should reach the Human Approval interrupt
    (i.e. produce a draft for the user to review in Approvals)."""
    g = graph_env
    cfg = {"configurable": {"thread_id": "tE"}, "recursion_limit": 80}
    res = g.invoke(
        {"linkedin_inputs": ["Self-funded PhD, no stipend. Prof X (x@uni.edu)."],
         "run_mode": "reactive"},
        cfg)
    assert "__interrupt__" in res, "reactive mode must reach the approval interrupt"
    payload = res["__interrupt__"][0].value
    assert payload["subject"] and payload["body"]
    # Funding signal is still parsed & persisted on the Opportunity so the
    # dashboard can flag it, even though the gate did not block the draft.
    from db import session as dbsession
    with dbsession.session_scope() as s:
        opp = s.query(Opportunity).first()
        assert opp.funding_status in ("self-funded", "unfunded")


def test_safety_scheduler_only_reachable_via_approval(graph_env):
    """Structural: the scheduler (the only send-adjacent node) has approval as
    its sole predecessor, so no path reaches sending without Human Approval."""
    g = graph_env
    drawable = g.get_graph()
    preds = {e.source for e in drawable.edges if e.target == "scheduler"}
    assert preds == {"approval"}, f"scheduler predecessors must be just approval, got {preds}"
    # And scheduler routes back only to approval (no direct END/send bypass).
    succs = {e.target for e in drawable.edges if e.source == "scheduler"}
    assert succs == {"approval"}
