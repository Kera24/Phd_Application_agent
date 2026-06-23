"""config/skills.md loader: returns the markdown playbook and supports
section extraction. Sections are delimited by `## Heading` lines.
"""
import pytest

from modules import config_loader


def test_skills_returns_non_empty_markdown():
    text = config_loader.skills()
    assert isinstance(text, str) and len(text) > 500
    # Starts with the playbook header.
    assert text.lstrip().startswith("# ScholarReach writing playbook")


def test_skills_section_returns_expected_blocks():
    text = config_loader.skills()
    # Required sections for the artefacts the generators reference.
    for heading in [
        "Email (advertised + speculative)",
        "Cover / Motivation letter",
        "Statement of Purpose",
        "Research Proposal",
        "Follow-up email",
    ]:
        block = config_loader.skills_section(heading)
        assert block, f"skills.md is missing required section: {heading}"
        # Each block should reference its own length target.
        if heading == "Email (advertised + speculative)":
            assert "220" in block and "320" in block
        elif heading in ("Cover / Motivation letter", "Statement of Purpose",
                         "Research Proposal"):
            assert "800" in block and "900" in block
        elif heading == "Follow-up email":
            assert "40" in block and "90" in block


def test_skills_section_missing_returns_empty_string():
    assert config_loader.skills_section("Not a real section") == ""


def test_skills_section_ignores_subheadings():
    # `### Before you write` inside Email section must not be treated as a new
    # top-level section break.
    block = config_loader.skills_section("Email (advertised + speculative)")
    assert "Before you write" in block
    assert "## Email (advertised + speculative)" not in block  # the heading itself is excluded


def test_email_prompt_stitches_email_section():
    """The LLM-facing email prompt should embed the Email section of skills.md."""
    from db.models import Opportunity, Professor
    from modules import email_gen

    opp = Opportunity(opportunity_type="advertised",
                      position_title="PhD", university="X",
                      research_fields=["Medical Imaging"])
    prof = Professor(name="Maria Schmidt", research_themes=[],
                     recent_papers=[{"title": "X", "year": 2024}],
                     identified_gap="g", proposed_angle="a")
    profile = config_loader.profile()
    project = email_gen.select_project(opp, prof, profile)
    prompt = email_gen._draft_prompt(opp, prof, project, profile, ["cv"])
    assert "WRITING PLAYBOOK" in prompt
    assert "Tone calibration" in prompt or "Tone" in prompt


def test_documents_prompt_stitches_relevant_section():
    """documents._prompt should embed the relevant section of skills.md
    for each kind, plus the new 800-900 word target for SOP/cover/proposal."""
    from modules import documents

    ctx = {
        "name": "Raj",
        "prof": "Maria Schmidt",
        "university": "TU Munich",
        "position": "PhD",
        "gap": "scanner robustness",
        "question": "vendor-agnostic saliency",
        "approach": "calibrated saliency maps",
        "cites": '"Paper One" (2024)',
        "profile_json": "{}",
    }
    sop_prompt = documents._prompt("sop", ctx)
    cover_prompt = documents._prompt("cover", ctx)
    proposal_prompt = documents._prompt("proposal", ctx)
    email_prompt = documents._prompt("email", ctx)

    # Length bound present in each prompt.
    for p, kind in [(sop_prompt, "sop"), (cover_prompt, "cover"),
                    (proposal_prompt, "proposal")]:
        assert "800" in p and "900" in p, f"{kind} prompt missing 800-900"
    assert "150" in email_prompt and "200" in email_prompt

    # Skills section stitched in.
    assert "Statement of Purpose" in sop_prompt
    assert "Cover / Motivation letter" in cover_prompt
    assert "Research Proposal" in proposal_prompt
    assert "Email (advertised + speculative)" in email_prompt


def test_document_max_tokens_is_sufficient_for_target():
    """Per-kind max_tokens must be >= 2x the upper word bound so the LLM has
    room to output the full document without truncation. (1 word ≈ 1.3 tokens
    for English prose; 2x is a safe ceiling.)"""
    from modules import documents

    for kind, (lo, hi) in documents._KIND_LEN.items():
        max_t = documents._KIND_MAX_TOKENS[kind]
        assert max_t >= hi * 2, (
            f"{kind}: max_tokens={max_t} < 2x upper bound {hi*2}"
        )