"""ScholarReach — Streamlit dashboard (entry point).

Run:  streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when launched via `streamlit run app.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from db import session as dbsession
from db.models import Asset, Email, Opportunity, Professor
from modules import (
    config_loader,
    discovery,
    email_gen,
    gmail_client,
    ingest,
    parser,
    prof_research,
    scheduler,
    scoring,
    summary_gen,
    tracker,
)

st.set_page_config(page_title="ScholarReach", page_icon="🎓", layout="wide")


@st.cache_resource
def _bootstrap():
    config_loader.ensure_dirs()
    dbsession.init_engine(str(config_loader.abspath("database")))
    return True


_bootstrap()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _status_badge(status: str) -> str:
    colors = {
        "draft_created": "🟡", "awaiting_review": "🟠", "approved": "🔵",
        "scheduled": "🟣", "sent": "🟢", "failed": "🔴", "cancelled": "⚪",
    }
    return f"{colors.get(status, '⚪')} {status}"


def _resolved_attachments(session):
    paths = ingest.asset_paths(session)
    return paths


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_pipeline():
    st.header("📋 Pipeline")
    with dbsession.new_session() as s:
        emails = s.query(Email).order_by(Email.created_at.desc()).all()
        statuses = ["draft_created", "awaiting_review", "approved", "scheduled",
                    "sent", "failed", "cancelled"]
        cols = st.columns(len(statuses))
        for col, status in zip(cols, statuses):
            n = sum(1 for e in emails if e.status == status)
            col.metric(_status_badge(status), n)

        st.divider()
        if not emails:
            st.info("No emails yet. Add an opportunity or run prospecting.")
            return

        for e in emails:
            prof = e.professor
            opp = e.opportunity
            label = f"{_status_badge(e.status)} — {e.subject or '(no subject)'} → {prof.name if prof else '?'}"
            with st.expander(label):
                gate = e.quality_gate_report or {}
                st.caption(f"Quality gate: {'✅ passed' if e.quality_gate_passed else '❌ failed'}")
                if gate.get("checks"):
                    for name, c in gate["checks"].items():
                        st.write(f"{'✅' if c['passed'] else '❌'} **{name}** — {c['detail']}")
                new_body = st.text_area("Body", e.body or "", key=f"body_{e.id}", height=240)
                new_subj = st.text_input("Subject", e.subject or "", key=f"subj_{e.id}")

                c1, c2, c3, c4 = st.columns(4)
                if c1.button("💾 Save edits", key=f"save_{e.id}"):
                    e.subject, e.body = new_subj, new_body
                    # Re-run quality gate after manual edit.
                    report = _rerun_gate(s, e)
                    e.quality_gate_passed = report["passed"]
                    e.quality_gate_report = report
                    s.commit()
                    st.rerun()
                if c2.button("🔁 Regenerate", key=f"regen_{e.id}"):
                    email_gen.regenerate(s, e)
                    s.commit()
                    st.success("Regenerated.")
                    st.rerun()
                if e.status == "awaiting_review" and e.quality_gate_passed:
                    if c3.button("✅ Approve", key=f"appr_{e.id}"):
                        tracker.transition(s, e, "approved", {"by": "dashboard"})
                        s.commit()
                        st.rerun()
                if e.status not in ("sent", "cancelled"):
                    if c4.button("✖ Cancel", key=f"cancel_{e.id}"):
                        tracker.transition(s, e, "cancelled", {"by": "dashboard"})
                        s.commit()
                        st.rerun()

                _draft_and_schedule_controls(s, e, opp, prof)


def _rerun_gate(s, e):
    from modules import quality_gate
    prof = e.professor or Professor(name="")
    return quality_gate.run(e.body or "", e.subject or "", prof,
                            [k for k in (e.attachments or []) if k != "summary_pdf"],
                            _resolved_attachments(s))


def _draft_and_schedule_controls(s, e, opp, prof):
    st.markdown("---")
    cols = st.columns(3)
    if cols[0].button("📄 Generate summary PDF", key=f"pdf_{e.id}"):
        try:
            path = summary_gen.generate_summary_pdf(s, opp, prof, email_id=e.id)
            e.summary_pdf_path = path
            s.commit()
            st.success(f"PDF: {path}")
        except Exception as exc:
            st.error(f"PDF failed: {exc}")
    if e.status == "approved":
        if cols[1].button("📨 Create Gmail draft", key=f"gdraft_{e.id}"):
            try:
                did = gmail_client.create_draft(s, e, _resolved_attachments(s))
                s.commit()
                st.success(f"Gmail draft created: {did}")
            except Exception as exc:
                st.error(f"Draft failed: {exc}")
        if cols[2].button("⏰ Schedule send", key=f"sched_{e.id}"):
            try:
                when = scheduler.schedule_send(s, e)
                s.commit()
                st.success(f"Scheduled for {when} UTC")
            except Exception as exc:
                st.error(f"Schedule failed: {exc}")


def page_opportunities():
    st.header("🎯 Opportunities")
    with dbsession.new_session() as s:
        active = (
            s.query(Opportunity)
            .filter(Opportunity.pipeline_status != "archived_not_funded")
            .order_by(Opportunity.fit_score.desc().nullslast())
            .all()
        )
        rows = [{
            "id": o.id, "type": o.opportunity_type, "title": o.position_title,
            "university": o.university, "country": o.country,
            "funding": o.funding_status, "fit": o.fit_score,
            "status": o.pipeline_status, "deadline": o.deadline,
        } for o in active]
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No active opportunities yet.")

        for o in active:
            with st.expander(f"#{o.id} {o.position_title or o.lab_name or 'Speculative'} — fit {o.fit_score}"):
                st.write(f"**University:** {o.university} · {o.city} · {o.country}")
                st.write(f"**Professor:** {o.professor_name} <{o.professor_email or 'no email'}>")
                st.write(f"**Funding:** {o.funding_status} — {o.funding_evidence}")
                st.write(f"**Fields:** {o.research_fields}")
                if o.score_breakdown:
                    st.json(o.score_breakdown)

                c1, c2, c3 = st.columns(3)
                if o.pipeline_status == "funding_unknown":
                    if c1.button("✅ Confirm funded", key=f"fund_ok_{o.id}"):
                        parser.confirm_funding(s, o, funded=True, evidence="dashboard confirm")
                        s.commit(); st.rerun()
                    if c2.button("🗄 Archive (not funded)", key=f"fund_no_{o.id}"):
                        parser.confirm_funding(s, o, funded=False)
                        s.commit(); st.rerun()
                if c3.button("🔬 Research + score + draft", key=f"pipe_{o.id}"):
                    _run_pipeline(s, o)
                    s.commit(); st.rerun()

        archived = (
            s.query(Opportunity)
            .filter(Opportunity.pipeline_status == "archived_not_funded").all()
        )
        with st.expander(f"🗄 Archived (not fully funded) — {len(archived)}"):
            for o in archived:
                st.write(f"#{o.id} {o.position_title or o.lab_name} — {o.funding_status}: {o.funding_evidence}")


def _run_pipeline(s, o: Opportunity):
    """Research professor -> score -> draft email for one funded opportunity."""
    if o.funding_status != "funded":
        st.warning("Only fully funded opportunities proceed past research.")
        return
    prof = None
    if o.professor_name:
        try:
            prof = prof_research.research_professor(
                s, o.professor_name, affiliation=o.university or "",
                profile_url=o.professor_profile_url, email=o.professor_email,
            )
            o.professor_id = prof.id
        except Exception as exc:
            st.error(f"Professor research failed: {exc}")
            return
    result = scoring.score_opportunity(o, prof)
    o.fit_score = result["total"]
    o.score_breakdown = result["breakdown"]
    o.pipeline_status = "scored"
    try:
        email_gen.generate_email(s, o, prof)
        o.pipeline_status = "email_ready"
        st.success(f"Pipeline complete — fit {result['total']}. See Pipeline page.")
    except Exception as exc:
        st.error(f"Email generation failed: {exc}")


def page_add_opportunity():
    st.header("➕ Add Opportunity")
    tab_manual, tab_search = st.tabs(["Paste LinkedIn/URL", "Advertised search (Tavily)"])

    with tab_manual:
        raw = st.text_area("Paste the LinkedIn post text or posting content", height=220)
        url = st.text_input("Source URL (optional)")
        if st.button("Parse & store"):
            if not raw.strip() and not url.strip():
                st.warning("Provide text or a URL.")
            else:
                with dbsession.new_session() as s:
                    text = raw.strip() or url
                    o = parser.classify_and_store(
                        s, text, source_type="linkedin_manual", source_url=url or None)
                    s.commit()
                    msg = {
                        "archived_not_funded": "Archived — not fully funded. Never emailed.",
                        "funding_unknown": "Funding unclear — surfaced for your decision in Opportunities.",
                        "needs_email": "Funded but professor email missing — see Opportunities.",
                        "parsed": "Stored as a funded opportunity. Run research from Opportunities.",
                    }.get(o.pipeline_status, o.pipeline_status)
                    st.success(f"#{o.id}: {msg}")

    with tab_search:
        st.caption("Requires TAVILY_API_KEY. Searches job boards + university pages.")
        if st.button("Run advertised search"):
            hits = discovery.search_advertised()
            if not hits:
                st.warning("No results (is TAVILY_API_KEY set?).")
            for h in hits[:20]:
                st.write(f"- [{h.get('title')}]({h.get('url')}) — _{h.get('query')}_")
                if st.button("Parse this", key=f"parse_{h.get('url')}"):
                    with dbsession.new_session() as s:
                        o = parser.classify_and_store(
                            s, h.get("content", h.get("title", "")),
                            source_type="job_board", source_url=h.get("url"))
                        s.commit()
                        st.success(f"Stored #{o.id} ({o.pipeline_status})")


def page_prospecting():
    st.header("🔭 Prospecting (speculative outreach)")
    cfg = config_loader.config()
    col1, col2 = st.columns(2)
    field = col1.selectbox("Field", cfg.get("target_fields", []))
    country = col2.selectbox("Country", cfg.get("default_funded_countries", []))

    if st.button("Run lab/professor discovery"):
        res = discovery.prospect_field_country(field, country)
        if not res["country_funded"]:
            st.error(f"{country} is not a default-funded PhD country; speculative outreach blocked (Rule 0).")
            return
        st.session_state["prospects"] = res
        st.success(f"Found {len(res['authors'])} active researchers and {len(res['labs'])} lab pages.")

    res = st.session_state.get("prospects")
    if res:
        st.subheader("Active researchers")
        for a in res["authors"]:
            affs = ", ".join(a.get("affiliations") or [])
            with st.expander(f"{a.get('name')} — {affs} (h-index {a.get('hIndex')})"):
                if st.button("Research + score as prospect", key=f"prospect_{a.get('authorId')}"):
                    with dbsession.new_session() as s:
                        _create_prospect(s, a, field, country)
                        s.commit()
                        st.rerun()
        st.subheader("Lab pages")
        for lab in res["labs"]:
            ev = discovery.funding_likelihood_evidence(lab.get("content", ""))
            st.write(f"- [{lab.get('title')}]({lab.get('url')}) — funding signals: {ev or 'none found'}")


def _create_prospect(s, author, field, country):
    name = author.get("name")
    affs = author.get("affiliations") or []
    university = affs[0] if affs else ""
    existing = discovery.is_duplicate_professor(s, None, name, university)
    if existing:
        st.info(f"{name} already in database (deduped).")
        return
    prof = prof_research.research_professor(s, name, affiliation=university)
    opp = Opportunity(
        source_type="prospecting", opportunity_type="speculative",
        university=university, country=country, professor_name=name,
        professor_id=prof.id, funding_status="funded",
        funding_evidence=f"{country}: PhDs funded by default (salaried/stipend system).",
        research_fields=[field], pipeline_status="prospect",
    )
    s.add(opp); s.flush()
    result = scoring.score_opportunity(opp, prof)
    opp.fit_score = result["total"]
    opp.score_breakdown = result["breakdown"]
    st.success(f"Prospect {name}: fit {result['total']}. Generate an email from Opportunities.")


def page_professors():
    st.header("👩‍🔬 Professors")
    with dbsession.new_session() as s:
        profs = s.query(Professor).order_by(Professor.last_researched_at.desc().nullslast()).all()
        if not profs:
            st.info("No professors researched yet.")
            return
        for p in profs:
            with st.expander(f"{p.name} — {p.university or ''} <{p.email or 'no email'}>"):
                st.write(f"**Themes:** {p.research_themes}")
                st.write(f"**Gap:** {p.identified_gap}")
                st.write(f"**Angle:** {p.proposed_angle}")
                st.write("**Verified recent papers:**")
                for paper in (p.recent_papers or []):
                    st.write(f"- {paper['title']} ({paper.get('year')}, {paper.get('venue')})")


def page_settings():
    st.header("⚙️ Settings")
    cfg = config_loader.config()

    st.subheader("Documents")
    with dbsession.new_session() as s:
        for kind in ingest.KINDS:
            a = ingest.get_asset(s, kind)
            up = st.file_uploader(f"{kind.upper()}", type=["pdf", "txt"], key=f"up_{kind}")
            if up is not None:
                tmp = config_loader.abspath("uploads") / up.name
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(up.getbuffer())
                asset = ingest.save_upload(s, kind, tmp, up.name)
                s.commit()
                if asset.warning:
                    st.warning(asset.warning)
                else:
                    st.success(f"{kind} stored ({asset.char_count} chars).")
            elif a:
                st.caption(f"current: {Path(a.file_path).name} ({a.char_count} chars)"
                           + (f" ⚠ {a.warning}" if a.warning else ""))

    st.divider()
    st.subheader("Send mode")
    send_on = cfg.get("approved_send_mode", False)
    if send_on:
        st.error("⚠ SEND MODE IS ON — approved emails can be sent.")
    else:
        st.info("Draft-only mode (safe default).")
    new_send = st.toggle("Enable approved_send_mode", value=send_on)
    new_cap = st.number_input("Daily send cap", 1, 50, value=cfg.get("daily_send_cap", 10))
    if st.button("Save settings"):
        cfg["approved_send_mode"] = bool(new_send)
        cfg["daily_send_cap"] = int(new_cap)
        config_loader.save_config(cfg)
        st.success("Saved.")

    st.divider()
    st.subheader("Gmail OAuth")
    st.write("Authorised ✅" if gmail_client.is_authorised() else "Not authorised ❌")
    st.caption(f"Credentials expected at: {config_loader.abspath('gmail_credentials')}")


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
PAGES = {
    "Pipeline": page_pipeline,
    "Opportunities": page_opportunities,
    "Add Opportunity": page_add_opportunity,
    "Prospecting": page_prospecting,
    "Professors": page_professors,
    "Settings": page_settings,
}

st.sidebar.title("🎓 ScholarReach")
st.sidebar.caption("Human-in-the-loop PhD outreach assistant for Raj Kumar Sah")
choice = st.sidebar.radio("Navigate", list(PAGES.keys()))
st.sidebar.divider()
st.sidebar.caption("Draft-first. Nothing is sent without explicit approval.")
PAGES[choice]()
