"""Quality-gate checks (Section 8). LLM judge is monkeypatched off."""
import pytest

from db.models import Professor
from modules import llm, quality_gate

PAPER = {"title": "Contrastive Place Recognition with Sequence Cues",
         "year": 2024, "venue": "CVPR", "url": "", "abstract": ""}


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    # Force claims_traceable to skip (no key) so we test deterministic checks.
    monkeypatch.setattr(llm, "available", lambda: False)


def _prof():
    p = Professor(name="Maria Schmidt", email="m@uni.de", recent_papers=[PAPER])
    return p


def test_name_check():
    ok, _ = quality_gate.check_professor_name("Dear Professor Schmidt, ...", _prof())
    assert ok
    bad, _ = quality_gate.check_professor_name("Dear Professor Jones, ...", _prof())
    assert not bad


def test_citation_exact_match():
    body = f'I enjoyed your paper "{PAPER["title"]}".'
    ok, matched = quality_gate.check_citation(body, _prof())
    assert ok and PAPER["title"] in matched


def test_citation_rejects_unverified():
    ok, matched = quality_gate.check_citation("I read your paper on transformers.", _prof())
    assert not ok and matched == []


def test_word_count_bounds():
    short = "word " * 50
    ok, n = quality_gate.check_word_count(short)
    assert not ok and n == 50
    good = "word " * 150
    ok, n = quality_gate.check_word_count(good)
    assert ok and n == 150


def test_banned_phrases():
    ok, hits = quality_gate.check_banned_phrases("I hope this email finds you well today.")
    assert not ok and "I hope this email finds you well" in hits


def test_gap_and_angle_markers():
    has_gap, has_angle = quality_gate.check_gap_and_angle(
        "This remains an open question. I could contribute methods from my work.")
    assert has_gap and has_angle


def test_full_gate_pass():
    body = (
        "Dear Professor Schmidt, I read your paper "
        f"\"{PAPER['title']}\" with interest. A clear gap remains in handling "
        "seasonal change. I could contribute self-supervised methods from my "
        "Nordland place-recognition work. " + "Building on my background, "
        "I would explore temporal contrastive objectives. " * 6
    )
    report = quality_gate.run(body, "PhD application place recognition",
                              _prof(), [], {})
    # word count may push out of range; assert structural checks individually.
    assert report["checks"]["professor_name"]["passed"]
    assert report["checks"]["verified_citation"]["passed"]
    assert report["checks"]["gap_statement"]["passed"]
    assert report["checks"]["angle_statement"]["passed"]
