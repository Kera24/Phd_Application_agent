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


# ---------------------------------------------------------------------------
# Voice + research-fit workflow + role-type coverage (config-only tests).
# These guard the spec: skills.md must mention British English, the 5-step
# workflow, and the four position types the user enumerated (PhD,
# predoctoral, internship, RA). They fail loudly if anyone edits skills.md
# and accidentally drops a clause.
# ---------------------------------------------------------------------------


def test_skills_voice_section_mentions_british_english():
    """The Voice (applications) subsection must spell out British English."""
    block = config_loader.skills_section("Voice (applications)")
    assert block, "skills.md is missing Voice (applications) subsection"
    assert "British English" in block
    # Spot-check the canonical -ise / -isation spellings the spec demands.
    for word in ("organise", "modelling", "behaviour", "specialise", "optimisation"):
        assert word in block, f"Voice block missing British spelling: {word}"


def test_skills_voice_section_forbids_ai_phrases():
    block = config_loader.skills_section("Voice (applications)")
    assert "I am excited to apply" in block or "excited to apply" in block
    assert "great fit" in block or "strong candidate" in block


def test_skills_research_fit_workflow_section_exists_with_five_steps():
    """The 5-step research-fit workflow must be its own section so
    documents._prompt can reference it explicitly."""
    block = config_loader.skills_section("Research-fit workflow")
    assert block, "skills.md is missing the Research-fit workflow section"
    # Five ordered steps from the spec — phrasing should include all five
    # anchor verbs / nouns even if sentences are slightly reworded.
    assert "research fit" in block.lower()
    assert "strongest alignment" in block.lower() or "alignment" in block.lower()
    assert "gap" in block.lower() or "open question" in block.lower()
    assert "draft" in block.lower()
    assert "self-check" in block.lower() or "future researcher" in block.lower()


def test_skills_position_types_section_lists_all_four_roles():
    """Spec covers PhD, predoctoral, internship, research assistant."""
    block = config_loader.skills_section("Position types this playbook covers")
    assert block, "skills.md is missing the Position-types section"
    for role in ("PhD", "predoctoral", "internship", "research assistant"):
        assert role.lower() in block.lower(), f"Position-types missing: {role}"


def test_cover_and_sop_sections_reference_all_four_role_types():
    """The cover-letter and SOP sections must explicitly note that they apply
    to PhD, predoctoral, internship, and research-assistant roles."""
    cover = config_loader.skills_section("Cover / Motivation letter")
    sop = config_loader.skills_section("Statement of Purpose")
    for label, block in (("cover", cover), ("sop", sop)):
        for role in ("PhD", "predoctoral", "internship", "research assistant"):
            assert role.lower() in block.lower(), (
                f"{label} section does not mention role: {role}"
            )


def test_cover_and_sop_emphasise_gap_identification():
    """The mid-document paragraphs must name a gap + direction (not claim
    the gap is closed)."""
    cover = config_loader.skills_section("Cover / Motivation letter")
    sop = config_loader.skills_section("Statement of Purpose")
    for label, block in (("cover", cover), ("sop", sop)):
        assert "gap" in block.lower(), f"{label} section does not name a gap"
        # Must NOT claim the gap is closed — that's a paper claim, not an
        # application claim. The shared-voice rule says "gap + direction,
        # not gap + solution".
        assert "close the gap" not in block.lower(), (
            f"{label} section claims to close the gap; should only propose "
            "a direction."
        )


def test_documents_prompt_includes_research_fit_handshake():
    """modules/documents._prompt must inject the workflow handshake into
    every artefact kind so the model runs the 5-step prefix before writing."""
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
    for kind in ("sop", "cover", "proposal"):
        p = documents._prompt(kind, ctx)
        assert "Research-fit workflow" in p, (
            f"{kind} prompt missing the Research-fit workflow handshake"
        )
        # The handshake explicitly walks the model through all five workflow
        # steps in plain English. Verify each one is present.
        for step_phrase in (
            "lab's fit",                  # step 1: analyse fit
            "foreground one project",     # step 2: identify alignment
            "plausible gap",              # step 3: identify a realistic gap
            "draft",                      # step 4: draft
            "self-check",                 # step 5: future-researcher check
        ):
            assert step_phrase.lower() in p.lower(), (
                f"{kind} prompt missing workflow step: {step_phrase}"
            )


def test_profile_contains_bachelor_degree():
    """The Bachelor in Information Technology and Mathematical Innovation
    is a profile fact — the LLM should be able to reference it honestly."""
    profile = config_loader.profile()
    degrees = [e.get("degree", "") for e in profile.get("education", [])]
    assert any("Bachelor" in d and "Mathematical Innovation" in d for d in degrees), (
        "profile.yaml is missing the Bachelor degree used by the new spec"
    )