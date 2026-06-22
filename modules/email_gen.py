"""Email drafting (two templates) + quality gate wiring.

Selects which of Raj's projects to foreground based on the lab's field, drafts
via Claude using email_template.yaml constraints, runs the quality gate, and
persists an Email (status 'awaiting_review' if the gate passes, else
'draft_created').
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Email, Opportunity, Professor
from modules import config_loader, ingest, llm, quality_gate, tracker

# Per-paragraph target word counts. Sourced from email_template.yaml
# `_para_targets`; if the file is missing them we fall back to these values.
_DEFAULT_PARA_TARGETS = {
    "hook": 30, "gap": 50, "angle": 60, "evidence": 50, "ask": 40, "signoff": 15,
}

# Map a lab field keyword -> the profile project id to foreground.
FIELD_TO_PROJECT = {
    "medical": "medical_imaging_vindr",
    "mammo": "medical_imaging_vindr",
    "healthcare": "medical_imaging_vindr",
    "radiolog": "medical_imaging_vindr",
    "place recognition": "vpr_contrastive",
    "contrastive": "vpr_contrastive",
    "representation": "vpr_contrastive",
    "self-supervised": "vpr_contrastive",
    "interpretab": "sae_superposition",
    "mechanistic": "sae_superposition",
    "vision": "vpr_contrastive",
    "nlp": "rag_llm",
    "language model": "rag_llm",
    "llm": "rag_llm",
    "rag": "rag_llm",
    "retrieval": "rag_llm",
}


def select_project(opp: Opportunity, prof: Optional[Professor], profile: dict) -> dict:
    """Choose the single most relevant project to mention (Section 7 matching rule)."""
    haystack = " ".join(
        filter(None, [
            opp.position_title, opp.lab_name, opp.department,
            " ".join(opp.research_fields or []),
            " ".join(p["title"] for p in (prof.recent_papers or [])) if prof else "",
            " ".join(prof.research_themes or []) if prof else "",
        ])
    ).lower()

    projects = {p["id"]: p for p in profile.get("research_projects", [])}
    for keyword, proj_id in FIELD_TO_PROJECT.items():
        if keyword in haystack and proj_id in projects:
            return projects[proj_id]
    # Default: medical imaging if nothing matches (most decorated result), else first.
    return projects.get("medical_imaging_vindr") or next(iter(projects.values()))


def _attachment_kinds(opp: Opportunity) -> list[str]:
    tmpl = config_loader.email_template()["templates"]
    key = "advertised" if opp.opportunity_type == "advertised" else "speculative"
    kinds = list(tmpl[key]["default_attachments"])
    # Advertised: include transcript only if required_documents asks for it (already default).
    # Speculative: transcript only on request -> excluded by default (per spec).
    return kinds


def _para_targets() -> dict:
    """Per-paragraph target word counts from email_template.yaml, with defaults."""
    raw = config_loader.email_template().get("_para_targets") or {}
    merged = dict(_DEFAULT_PARA_TARGETS)
    merged.update({k: int(v) for k, v in raw.items() if isinstance(v, (int, float))})
    return merged


def _draft_prompt(opp, prof, project, profile, kinds) -> str:
    tmpl = config_loader.email_template()
    key = "advertised" if opp.opportunity_type == "advertised" else "speculative"
    spec = tmpl["templates"][key]
    shared = tmpl["shared_constraints"]
    papers = "\n".join(
        f"- {p['title']} ({p.get('year')})" for p in (prof.recent_papers or [])
    ) if prof else ""
    structure = "\n".join(f"{i+1}. {s}" for i, s in enumerate(spec["structure"]))
    targets = _para_targets()
    target_block = ", ".join(f"{k}={v}" for k, v in targets.items())
    return (
        f"Write a {key} PhD outreach email body for {profile['name']} to "
        f"Professor {prof.name if prof else opp.professor_name}.\n\n"
        f"VERIFIED recent papers (cite >=1 by EXACT title):\n{papers}\n\n"
        f"Identified gap (USE THIS VERBATIM, do not invent): "
        f"{prof.identified_gap if prof else ''}\n"
        f"Proposed angle (USE THIS VERBATIM, do not invent): "
        f"{prof.proposed_angle if prof else ''}\n\n"
        f"Foreground ONLY this project (do not mention others):\n"
        f"  {project['title']}: {project['detail']}\n\n"
        f"Opportunity: title={opp.position_title!r}, university={opp.university!r}, "
        f"lab={opp.lab_name!r}, fields={opp.research_fields}.\n\n"
        f"Required 6-paragraph structure (per-paragraph target words; "
        f"do not collapse paragraphs and do not exceed a target by >30 words):\n"
        f"{structure}\n\n"
        f"Per-paragraph word targets: {target_block}.\n\n"
        f"Global constraints: body {shared['body_word_min']}-{shared['body_word_max']} words; "
        f"subject <= {shared['subject_max_words']} words; tone {shared['tone']}; "
        f"forbid: {', '.join(shared['forbid'])}. {spec['subject_hint']}\n"
        f"End the body with an attachment line listing: {', '.join(kinds)}.\n"
        f"Only state facts about {profile['name']} from the foregrounded project. "
        f"If a fact is not in the project detail above, do NOT assert it.\n\n"
        'Return JSON: {"subject": "...", "body": "..."}.'
    )


def _fallback_draft(opp, prof, project, profile, kinds) -> dict:
    """Deterministic 6-paragraph template fill when no LLM key is set.

    Matches the prompt's per-paragraph structure (hook -> gap -> angle ->
    evidence -> ask -> signoff) and pads to land in the 220-320 word target.
    """
    paper = (prof.recent_papers[0]["title"] if prof and prof.recent_papers else "your recent work")
    name = prof.name if prof else (opp.professor_name or "Professor")
    surname = name.split()[-1] if name else ""
    gap = (prof.identified_gap if prof else "") or "an open question your group is exploring"
    angle = (prof.proposed_angle if prof else "") or "methods from my background"
    is_spec = opp.opportunity_type != "advertised"
    proj_title = project["title"]
    proj_detail = project["detail"].strip().rstrip(".")
    field = (opp.research_fields or ["your area"])[0]

    if is_spec:
        hook = (
            f"Dear Professor {surname},\n\n"
            f"I am writing as a prospective PhD applicant whose work sits close to "
            f"\"{paper}\" by your group. The combination of method and evaluation discipline "
            f"is exactly the space I have been building towards in my own work."
        )
        gap_para = (
            f"Your line of work in {field} leaves an opening where {gap}. "
            f"Bridging that gap is the angle I would most like to explore: {angle}, and I "
            f"can move on it quickly because the underlying engineering and evaluation setup "
            f"are already in place from my prior project."
        )
        angle_evidence = (
            f"My project \"{proj_title}\" approaches {field.lower()} with "
            f"{proj_detail}, and I have worked through end-to-end experiments on "
            f"the relevant dataset and method stack. This gives me a concrete starting "
            f"point — both the engineering setup and the evaluation discipline — to take "
            f"the proposed direction forward in your group rather than restarting the "
            f"tooling from scratch in the first year."
        )
        ask = (
            "I am seeking a fully funded PhD position and would value your guidance: do "
            "you anticipate openings in the coming intake, or could you advise on upcoming "
            "funded calls in your group (DAAD, MSCA, ERC, EPSRC, SNSF, ARC as relevant)? "
            "I would be glad to share further materials and can hold a 15-minute call at a "
            "time that suits your schedule, including early in your working week."
        )
        subject = f"Prospective PhD applicant — {field}"
    else:
        hook = (
            f"Dear Professor {surname},\n\n"
            f"I am applying for the {opp.position_title or 'PhD position'} in your group "
            f"at {opp.university or 'your university'}, drawn in particular by \"{paper}\". "
            f"The method and the dataset framing speak directly to the direction I have been "
            f"pushing in my own work."
        )
        gap_para = (
            f"That line of work leaves an opening where {gap} remains unaddressed. "
            f"I would like to address that direction in my own research, and your group is "
            f"one of the few places where the right combination of clinical data, compute, and "
            f"expertise to close it is already in place."
        )
        angle_evidence = (
            f"Specifically, {angle}, using methods I have already built and evaluated. "
            f"My project \"{proj_title}\" sits in this exact space: {proj_detail}. "
            f"I have carried the work from data preparation through evaluation, and the "
            f"results inform a clear next step that fits the direction of your group "
            f"rather than a generic application of the same model class."
        )
        ask = (
            f"I would value the chance to be considered for the {opp.position_title or 'position'} "
            f"and to discuss it on a 15-minute call during your working week "
            f"(Monday through Thursday, 08:00 to 09:00 local time), and can work around "
            f"your schedule if those windows are full."
        )
        subject = f"PhD application — {opp.position_title or field}"

    attachments = f"Attachments: {', '.join(kinds)}."
    body = (
        f"{hook}\n\n"
        f"{gap_para}\n\n"
        f"{angle_evidence}\n\n"
        f"{ask}\n\n"
        f"{attachments}\n\n"
        f"Kind regards,\n{profile['name']}"
    )
    return {"subject": subject[:80], "body": body}


def generate_email(session: Session, opp: Opportunity,
                   prof: Optional[Professor]) -> Email:
    """Draft, quality-gate, and persist an Email for an opportunity."""
    profile = config_loader.profile()
    project = select_project(opp, prof, profile)
    kinds = _attachment_kinds(opp)

    if llm.available():
        try:
            draft = llm.complete_json(_draft_prompt(opp, prof, project, profile, kinds))
        except Exception:
            draft = _fallback_draft(opp, prof, project, profile, kinds)
    else:
        draft = _fallback_draft(opp, prof, project, profile, kinds)

    resolved = ingest.asset_paths(session)
    # Map template kinds to asset kinds: summary_pdf is produced per-email later.
    resolved_for_gate = dict(resolved)
    report = quality_gate.run(
        draft.get("body", ""), draft.get("subject", ""), prof or Professor(name=opp.professor_name or ""),
        [k for k in kinds if k != "summary_pdf"], resolved_for_gate,
    )

    email = Email(
        opportunity_id=opp.id,
        professor_id=prof.id if prof else None,
        subject=draft.get("subject"),
        body=draft.get("body"),
        attachments=kinds,
        quality_gate_passed=report["passed"],
        quality_gate_report=report,
        status="draft_created",
    )
    session.add(email)
    session.flush()
    tracker.log_event(session, email.id, "email_drafted",
                      {"opportunity_id": opp.id, "gate_passed": report["passed"]})
    if report["passed"]:
        tracker.transition(session, email, "awaiting_review",
                           {"reason": "quality gate passed"})
    session.flush()
    return email


def _followup_prompt(original: Email, prof, profile) -> str:
    tmpl = config_loader.email_templates().get("followup", {})
    cfg = config_loader.config().get("followup", {})
    name = prof.name if prof else (original.opportunity.professor_name if original.opportunity else "")
    paper = ""
    if prof and prof.recent_papers:
        paper = prof.recent_papers[0]["title"]
    structure = "\n".join(f"{i+1}. {s}" for i, s in enumerate(tmpl.get("structure", [])))
    return (
        f"Write a brief, polite follow-up to a previous PhD outreach email from "
        f"{profile['name']} to Professor {name}. The original email has had no reply.\n\n"
        f"Original subject: {original.subject!r}\n"
        f"A verified paper you may reference by exact title: {paper!r}\n\n"
        f"Required structure:\n{structure}\n\n"
        f"Constraints: body {cfg.get('word_min', 40)}-{cfg.get('word_max', 90)} words; "
        f"warm but not pushy; reference the earlier email; restate the interest in one line; "
        f"do NOT introduce any new claims about {profile['name']}; do NOT re-attach documents.\n"
        'Return JSON: {"subject": "...", "body": "..."}.'
    )


def _fallback_followup(original: Email, prof, profile) -> dict:
    """Deterministic follow-up when no LLM key is set."""
    name = prof.name if prof else (original.opportunity.professor_name if original.opportunity
                                   else "Professor")
    surname = name.split()[-1] if name else ""
    subject = original.subject or "my PhD application"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    body = (
        f"Dear Professor {surname},\n\n"
        f"I wanted to gently follow up on my earlier email about a PhD position in your group. "
        f"I remain very interested in the possibility of working with you and would be glad to "
        f"share any further materials that would help. I appreciate your time and understand how "
        f"busy you are.\n\n"
        f"Kind regards,\n{profile['name']}"
    )
    return {"subject": subject[:80], "body": body}


def generate_followup(session: Session, original: Email) -> Email:
    """Draft a single gated follow-up for an unanswered first-contact email."""
    profile = config_loader.profile()
    prof = original.professor
    cfg = config_loader.config().get("followup", {})

    if llm.available():
        try:
            draft = llm.complete_json(_followup_prompt(original, prof, profile))
        except Exception:
            draft = _fallback_followup(original, prof, profile)
    else:
        draft = _fallback_followup(original, prof, profile)

    subject = draft.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}".strip()

    report = quality_gate.run(
        draft.get("body", ""), subject,
        prof or Professor(name=(original.opportunity.professor_name if original.opportunity else "")),
        [], {},
        word_bounds=(cfg.get("word_min", 40), cfg.get("word_max", 90)),
        mode="followup",
    )

    email = Email(
        opportunity_id=original.opportunity_id,
        professor_id=original.professor_id,
        subject=subject,
        body=draft.get("body"),
        attachments=[],
        quality_gate_passed=report["passed"],
        quality_gate_report=report,
        status="draft_created",
        is_followup=True,
        parent_email_id=original.id,
        gmail_thread_id=original.gmail_thread_id,
    )
    session.add(email)
    session.flush()
    tracker.log_event(session, email.id, "followup_drafted",
                      {"parent_email_id": original.id, "gate_passed": report["passed"]})
    if report["passed"]:
        tracker.transition(session, email, "awaiting_review",
                           {"reason": "followup quality gate passed"})
    session.flush()
    return email


def regenerate(session: Session, email: Email) -> Email:
    """Re-draft an existing email (resets to draft_created then re-runs)."""
    opp = email.opportunity
    prof = email.professor
    if email.status not in ("sent", "cancelled"):
        # Move back to draft_created for a fresh attempt.
        if email.status == "awaiting_review":
            tracker.transition(session, email, "draft_created", {"reason": "regenerate"})
    new = generate_email(session, opp, prof)
    tracker.transition(session, email, "cancelled", {"reason": "superseded by regenerate",
                                                     "new_email_id": new.id})
    return new
