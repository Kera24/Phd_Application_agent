"""Email-draft quality: 6-paragraph structure hits the 220-320 word range and
passes the quality gate. Covers both the LLM path (stubbed) and the
deterministic fallback (no LLM key).
"""
import pytest

from db.models import Opportunity, Professor
from modules import config_loader, email_gen, llm, quality_gate

PAPER = {"title": "Explainable DL for Mammography",
         "year": 2024, "venue": "MICCAI", "url": "http://x/1",
         "abstract": "ViT mammography; scanner robustness open."}


def _opp(opportunity_type="advertised"):
    return Opportunity(
        opportunity_type=opportunity_type,
        position_title="PhD in Medical Imaging",
        university="TU Munich",
        research_fields=["Medical Imaging"],
    )


def _prof():
    return Professor(
        name="Maria Schmidt",
        email="maria.schmidt@tum.de",
        research_themes=["medical imaging", "explainability"],
        recent_papers=[PAPER],
        identified_gap="how the model behaves across scanner vendors",
        proposed_angle="a vendor-agnostic saliency calibration method",
    )


@pytest.fixture(autouse=True)
def no_claims_judge(monkeypatch):
    """No LLM key => claims_traceable is skipped (returns True)."""
    monkeypatch.setattr(llm, "available", lambda: False)


def _check_body_in_range(body: str):
    n = len(body.split())
    assert 220 <= n <= 320, f"body {n} words outside 220-320 range"


def _gate_passes(body: str, prof: Professor, subject: str = "PhD application — Medical Imaging"):
    report = quality_gate.run(body, subject, prof, [], {})
    failed = [k for k, v in report["checks"].items() if not v["passed"]]
    assert not failed, f"quality gate failed: {failed}"


def test_fallback_advertised_meets_depth_and_passes_gate():
    """No LLM -> fallback draft should still land in the 220-320 range, cite
    the verified paper by exact title, and pass the deterministic gate."""
    profile = config_loader.profile()
    project = email_gen.select_project(_opp("advertised"), _prof(), profile)
    kinds = ["cv", "transcript", "summary_pdf"]
    d = email_gen._fallback_draft(_opp("advertised"), _prof(), project, profile, kinds)
    _check_body_in_range(d["body"])
    assert PAPER["title"] in d["body"]
    _gate_passes(d["body"], _prof(), d["subject"])


def test_fallback_speculative_meets_depth_and_passes_gate():
    """Speculative branch (no position title) -> fallback still hits range."""
    profile = config_loader.profile()
    project = email_gen.select_project(_opp("speculative"), _prof(), profile)
    kinds = ["cv", "summary_pdf"]
    d = email_gen._fallback_draft(_opp("speculative"), _prof(), project, profile, kinds)
    _check_body_in_range(d["body"])
    assert PAPER["title"] in d["body"]
    _gate_passes(d["body"], _prof(), d["subject"])


def test_draft_prompt_carries_paragraph_brief():
    """The LLM-facing prompt should show per-paragraph target words and the new
    6-paragraph structure so the model writes a deeper body."""
    profile = config_loader.profile()
    project = email_gen.select_project(_opp(), _prof(), profile)
    prompt = email_gen._draft_prompt(_opp(), _prof(), project, profile, ["cv", "transcript", "summary_pdf"])
    # New 220-320 range present.
    assert "220" in prompt and "320" in prompt
    # Per-paragraph brief present.
    assert "hook" in prompt.lower()
    assert "per-paragraph" in prompt.lower()
    # 6-paragraph structure: numbered 1..6.
    for i in range(1, 7):
        assert f"{i}. " in prompt, f"missing paragraph {i} in prompt"
    # The 6 paragraph names are signposted in the structure.
    assert "Ask" in prompt and "Attachments" in prompt
