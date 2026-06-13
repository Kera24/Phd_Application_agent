"""Tailored one-page research summary -> PDF (WeasyPrint).

Background section stays factually identical to the base summary; only the
"future research direction" paragraph adapts to the professor's work, citing
1-2 verified papers + the identified gap + proposed approach. Same citation
verification as Section 4. Single page enforced (fail if content overflows).
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Optional

from db.models import Opportunity, Professor
from modules import config_loader, ingest, llm, prof_research

_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 1.6cm; }}
body {{ font-family: 'Georgia', serif; font-size: 10.5pt; line-height: 1.4; color: #111; }}
h1 {{ font-size: 16pt; margin: 0 0 2px 0; }}
.contact {{ font-size: 9pt; color: #444; margin-bottom: 10px; }}
h2 {{ font-size: 11pt; border-bottom: 1px solid #999; padding-bottom: 2px; margin: 12px 0 6px; }}
p {{ margin: 4px 0; text-align: justify; }}
.refs {{ font-size: 8.5pt; color: #333; }}
</style></head><body>
<h1>{name}</h1>
<div class="contact">{contact}</div>
<h2>Background</h2>
{background}
<h2>Future Research Direction — {target}</h2>
{future}
<h2>Referenced Work</h2>
<div class="refs">{refs}</div>
</body></html>"""


class SummaryOverflowError(RuntimeError):
    pass


def _contact_line(profile: dict) -> str:
    c = profile.get("contact", {})
    bits = [profile.get("location", "")]
    for k in ("email", "phone", "linkedin", "github", "scholar"):
        if c.get(k):
            bits.append(c[k])
    return " · ".join(b for b in bits if b)


def _future_paragraph(opp, prof, profile, base_text) -> tuple[str, list[dict]]:
    """LLM-written future-direction paragraph citing verified papers; verified."""
    cited_papers = (prof.recent_papers or [])[:2] if prof else []
    if not (prof and llm.available()):
        gap = (prof.identified_gap if prof else "") or "open questions in this area"
        angle = (prof.proposed_angle if prof else "") or "methods from my prior research"
        para = (
            f"Working with {prof.name if prof else 'your group'}, I would focus on {gap}. "
            f"Drawing on my background, I would pursue {angle}."
        )
        return para, cited_papers

    titles = "; ".join(f'"{p["title"]}"' for p in cited_papers)
    prompt = (
        f"Write ONE paragraph (<=90 words) describing the future research direction "
        f"{profile['name']} would pursue with Professor {prof.name}. "
        f"Cite 1-2 of these EXACT paper titles: {titles}. "
        f"Ground it in this gap: {prof.identified_gap}. "
        f"Angle: {prof.proposed_angle}. "
        f"Do not restate the background. Do not invent titles.\n"
        f"Base summary (for tone/consistency, do not contradict):\n{base_text[:1500]}\n\n"
        'Return JSON: {"paragraph": "...", "cited_titles": [exact titles]}.'
    )
    data = llm.complete_json(prompt)
    para = data.get("paragraph", "")
    claimed = data.get("cited_titles", [])
    ok, bad = prof_research.verify_titles(claimed, prof.recent_papers or [])
    if not ok:
        raise prof_research.CitationVerificationError(
            f"Summary cited unverifiable titles: {bad}"
        )
    cited = [p for p in (prof.recent_papers or []) if p["title"] in claimed] or cited_papers
    return para, cited


def generate_summary_pdf(session, opp: Opportunity, prof: Optional[Professor],
                         email_id: Optional[int] = None) -> str:
    """Render the tailored summary to a PDF and return its path."""
    profile = config_loader.profile()
    base = ingest.get_asset(session, "summary")
    base_text = base.extracted_text if base else ""

    # Background stays identical to the base summary.
    if base_text:
        background_html = "".join(
            f"<p>{html.escape(p.strip())}</p>" for p in base_text.split("\n\n") if p.strip()
        )
    else:
        proj = "; ".join(p["detail"].strip() for p in profile.get("research_projects", []))
        background_html = f"<p>{html.escape(proj)}</p>"

    future_para, cited = _future_paragraph(opp, prof, profile, base_text)
    refs = "<br>".join(
        f"{html.escape(p['title'])} ({p.get('year')}, {html.escape(p.get('venue') or '')})"
        for p in cited
    ) or "—"

    doc_html = _TEMPLATE.format(
        name=html.escape(profile["name"]),
        contact=html.escape(_contact_line(profile)),
        background=background_html,
        target=html.escape(opp.lab_name or opp.university or prof.name if prof else "Target Lab"),
        future=f"<p>{html.escape(future_para)}</p>",
        refs=refs,
    )

    pdf_dir = config_loader.abspath("pdfs")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"e{email_id}" if email_id else f"o{opp.id}"
    out_path = pdf_dir / f"summary_{suffix}.pdf"

    _render_pdf(doc_html, out_path)
    return str(out_path)


def render_html_to_pdf(doc_html: str, out_path: str) -> str:
    """Public, session-free renderer used by the pdf_generator_tool."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _render_pdf(doc_html, p)
    return str(p)


def _render_pdf(doc_html: str, out_path: Path) -> None:
    """Render and enforce a single page."""
    from weasyprint import HTML  # imported lazily; heavy dependency

    document = HTML(string=doc_html).render()
    if len(document.pages) > 1:
        raise SummaryOverflowError(
            f"Tailored summary overflows to {len(document.pages)} pages; must be one. "
            "Trim the base summary or future paragraph."
        )
    document.write_pdf(str(out_path))
