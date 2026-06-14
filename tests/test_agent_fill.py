"""Unit test for the agentic filler's task builder (no browser/LLM needed)."""
from scripts.agent_fill import build_task


def test_build_task_includes_data_docs_and_safety():
    profile = {"name": "Raj Kumar Sah", "contact": {"email": "raj@example.com"}}
    opp = {"title": "PhD in Medical Imaging", "university": "Example U",
           "professor_name": "Jane Doe"}
    task = build_task(profile, opp, "https://apply.example.edu/form",
                      {"cv": "/tmp/cv.pdf", "sop": "/tmp/sop.pdf"})

    # URL + applicant data present
    assert "https://apply.example.edu/form" in task
    assert "Raj Kumar Sah" in task
    assert "raj@example.com" in task
    # opportunity context + documents listed
    assert "PhD in Medical Imaging" in task
    assert "/tmp/cv.pdf" in task and "/tmp/sop.pdf" in task
    # safety + no-fabrication instructions present
    assert "not click the final Submit" in task or "do NOT click the final Submit" in task
    assert "LEAVE THEM BLANK" in task
    assert "CAPTCHA" in task


def test_build_task_handles_no_opp_no_docs():
    task = build_task({"name": "A"}, None, "https://x.test/apply", {})
    assert "https://x.test/apply" in task
    assert "(none provided)" in task
