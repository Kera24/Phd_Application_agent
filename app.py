"""ScholarReach dashboard (Streamlit) — talks to the FastAPI backend only.

Run the backend first:   uvicorn api.main:app --port 8001
Then the dashboard:       streamlit run app.py

Set SCHOLARREACH_API to point at a non-default backend URL. The default backend
port is 8001 (8000 is commonly occupied by other local apps).
"""
from __future__ import annotations

import os

import requests
import streamlit as st

API = os.environ.get("SCHOLARREACH_API", "http://localhost:8001")

st.set_page_config(page_title="ScholarReach", page_icon="🎓", layout="wide")

# Light polish (Streamlit's styling ceiling is low, but this tightens it up).
st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; max-width: 1200px; }
      [data-testid="stMetricValue"] { font-size: 1.6rem; }
      .stButton > button { border-radius: 8px; }
      [data-testid="stExpander"] { border-radius: 10px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def api_get(path: str):
    try:
        r = requests.get(f"{API}{path}", timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"GET {path} failed: {exc}")
        return None


def api_post(path: str, json=None):
    try:
        r = requests.post(f"{API}{path}", json=json or {}, timeout=600)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"POST {path} failed: {exc}")
        return None


def api_put(path: str, json=None):
    try:
        r = requests.put(f"{API}{path}", json=json or {}, timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"PUT {path} failed: {exc}")
        return None


def api_post_file(path: str, kind, uploaded_file):
    """POST a multipart file upload (Streamlit UploadedFile) to the backend.

    `kind` is sent as a form field when provided (e.g. /assets); pass None for
    endpoints that take only the file (e.g. /opportunities/ingest-file).
    """
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
        data = {"kind": kind} if kind is not None else None
        r = requests.post(f"{API}{path}", data=data, files=files, timeout=600)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Upload to {path} failed: {exc}")
        return None


def api_delete(path: str):
    try:
        r = requests.delete(f"{API}{path}", timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"DELETE {path} failed: {exc}")
        return None


def _backend_ok() -> bool:
    return api_get("/healthz") is not None


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
KANBAN = ["draft_created", "awaiting_review", "approved", "scheduled", "sent",
          "failed", "cancelled"]


def page_pipeline():
    st.header("📋 Pipeline")
    data = api_get("/pipeline")
    if not data:
        return
    emails = data["emails"]
    cols = st.columns(len(KANBAN))
    for col, status in zip(cols, KANBAN):
        col.metric(status, sum(1 for e in emails if e["status"] == status))
    st.divider()
    for e in emails:
        tags = []
        if e.get("is_followup"):
            tags.append("🔁 follow-up")
        if e.get("reply_received"):
            tags.append("📨 replied")
        tag_str = ("  ·  " + " ".join(tags)) if tags else ""
        with st.expander(f"[{e['status']}] {e['subject']} → {e['professor']}{tag_str}"):
            st.caption(f"Quality gate: {'✅' if e['quality_gate_passed'] else '❌'}")
            report = e.get("quality_gate_report") or {}
            for name, c in (report.get("checks") or {}).items():
                st.write(f"{'✅' if c.get('passed') else '❌'} **{name}** — {c.get('detail')}")
            st.text_area("Body", e.get("body") or "", height=200, key=f"b_{e['id']}",
                         disabled=True)
            if e.get("scheduled_send_at_utc"):
                st.info(f"Scheduled: {e['scheduled_send_at_utc']} UTC")
            # Reply tracking for sent first-contact emails (manual / Gmail-optional).
            if e["status"] == "sent" and not e.get("is_followup"):
                if e.get("followup_due_date"):
                    st.caption(f"Follow-up due: {e['followup_due_date']} "
                               f"(status: {e.get('followup_status')})")
                if e.get("reply_received"):
                    st.success("Reply received — no follow-up will be drafted.")
                elif st.button("📨 Mark replied", key=f"reply_{e['id']}"):
                    if api_post(f"/emails/{e['id']}/reply"):
                        st.rerun()


def _opp_detail_view(oid):
    d = api_get(f"/opportunities/{oid}/detail")
    if not d:
        return
    o = d["opportunity"]
    st.markdown(f"#### {o.get('title') or o.get('professor_name') or ('#' + str(oid))}")
    st.write(f"**University:** {o.get('university') or '—'}  ·  **Country:** "
             f"{o.get('country') or '—'}  ·  **Fit:** {o.get('fit_score')}  ·  "
             f"**Status:** {d['application']['status']}")
    st.write(f"**Funding:** {o.get('funding_status')} — {o.get('funding_evidence') or ''}")
    st.write(f"**Professor:** {o.get('professor_name') or '—'} "
             f"<{o.get('professor_email') or ''}>  ·  **Deadline:** {o.get('deadline') or '—'}")
    if o.get("application_link"):
        st.markdown(f"[Application link]({o['application_link']})")
    b = d.get("research_brief")
    if b:
        with st.expander("🔬 Research brief"):
            st.write(f"**Gap:** {b.get('chosen_gap')}")
            st.write(f"**Question:** {b.get('research_question')}")
            st.write(f"**Approach:** {b.get('proposed_approach')}")
    if d.get("emails"):
        with st.expander(f"✉ Emails ({len(d['emails'])})"):
            for e in d["emails"]:
                fu = " (follow-up)" if e.get("is_followup") else ""
                st.write(f"- #{e['id']} [{e['status']}] {e.get('subject') or ''}{fu}")
    if d.get("documents"):
        with st.expander(f"📄 Generated documents ({len(d['documents'])})"):
            for doc in d["documents"]:
                st.write(f"- **{doc['kind']}** — {doc['title']}")
    if d.get("timeline"):
        with st.expander("🕑 Timeline"):
            for ev in d["timeline"]:
                st.write(f"- {(ev.get('at') or '')[:19]} · {ev['event']}")


def page_opportunities():
    st.header("🎯 Opportunities")
    data = api_get("/opportunities")
    if not data:
        return
    opps = data["opportunities"]
    if not opps:
        st.info("No opportunities yet. Add or discover some first.")
        return

    # --- search / filter / sort ---
    f1, f2, f3 = st.columns([2, 1, 1])
    q = f1.text_input("Search", "", placeholder="title, professor, university…")
    countries = sorted({o["country"] for o in opps if o.get("country")})
    country = f2.selectbox("Country", ["All"] + countries)
    fundings = sorted({o["funding_status"] for o in opps if o.get("funding_status")})
    funding = f3.selectbox("Funding", ["All"] + fundings)
    g1, g2 = st.columns([1, 1])
    show_archived = g1.checkbox("Show archived", False)
    sort_by = g2.selectbox("Sort by", ["Fit (high→low)", "Deadline (soonest)", "Newest"])

    def _match(o):
        if not show_archived and o["pipeline_status"] == "archived_not_funded":
            return False
        if country != "All" and o.get("country") != country:
            return False
        if funding != "All" and o.get("funding_status") != funding:
            return False
        if q:
            hay = " ".join(str(o.get(k) or "") for k in
                           ("title", "professor_name", "university")).lower()
            if q.lower() not in hay:
                return False
        return True

    rows = [o for o in opps if _match(o)]
    if sort_by.startswith("Fit"):
        rows.sort(key=lambda o: (o.get("fit_score") or -1), reverse=True)
    elif sort_by.startswith("Deadline"):
        rows.sort(key=lambda o: (o.get("deadline") is None, o.get("deadline") or ""))
    else:
        rows.sort(key=lambda o: o["id"], reverse=True)

    st.caption(f"{len(rows)} of {len(opps)} opportunities")
    if rows:
        st.dataframe([{k: o.get(k) for k in ("id", "type", "title", "university",
                       "country", "funding_status", "fit_score", "pipeline_status")}
                      for o in rows], use_container_width=True)

    labels = {o["id"]: f"#{o['id']} {o.get('title') or o.get('professor_name') or ''}"
              for o in rows}
    if not labels:
        return

    st.divider()
    st.subheader("Details")
    sel = st.selectbox("View an opportunity", list(labels.keys()),
                       format_func=lambda i: labels[i])
    _opp_detail_view(sel)

    st.divider()
    st.subheader("Bulk actions")
    pick = st.multiselect("Select opportunities", list(labels.keys()),
                          format_func=lambda i: labels[i])
    if pick:
        b1, b2 = st.columns(2)
        new_status = b1.selectbox("Set application status",
                                  ["interested", "preparing", "applied", "submitted",
                                   "interview", "offer", "rejected", "declined"])
        if b1.button("Apply status"):
            for i in pick:
                api_put(f"/applications/{i}", {"status": new_status})
            st.success(f"Updated {len(pick)}.")
            st.rerun()
        if b2.button(f"🗑 Delete {len(pick)} selected", type="secondary"):
            for i in pick:
                api_delete(f"/opportunities/{i}")
            st.success(f"Deleted {len(pick)}.")
            st.rerun()


def _show_run_result(res):
    """Render the outcome of a pipeline run (shared by text + file intake)."""
    if not res:
        return
    extr = res.get("extraction")
    if extr:
        st.caption(f"Extracted via **{extr['method']}** ({extr['char_count']} chars).")
    if res.get("status") == "awaiting_approval":
        st.success(f"Draft ready for approval (thread {res['thread_id']}). "
                   "See the Approvals page.")
    else:
        st.info("Run completed. Opportunity archived or parked "
                "(not fully funded / below fit threshold / needs review). "
                "Check Opportunities & Pipeline.")


def page_add():
    st.header("➕ Add Opportunity")
    tab_text, tab_file = st.tabs(["📝 Paste text", "🖼 Upload image / PDF"])

    with tab_text:
        st.caption("Paste a LinkedIn post / posting text. It runs the full graph; "
                   "fully funded ones reach the approval queue, others are archived.")
        raw = st.text_area("Posting text", height=220)
        if st.button("Run pipeline"):
            if not raw.strip():
                st.warning("Paste some text first.")
            else:
                _show_run_result(api_post("/runs", {"linkedin_inputs": [raw]}))

    with tab_file:
        st.caption("Upload a screenshot/photo of a posting or a PDF flyer. Text PDFs "
                   "are read directly; images and scanned PDFs are transcribed with "
                   "Claude vision (needs Anthropic credits). Then the same pipeline runs.")
        up = st.file_uploader("Posting image or PDF",
                              type=["png", "jpg", "jpeg", "webp", "gif", "pdf"],
                              key="ingest_file")
        if up is not None and st.button("Extract & run pipeline"):
            with st.spinner("Extracting text and running the pipeline…"):
                _show_run_result(api_post_file("/opportunities/ingest-file", None, up))


def page_prospecting():
    st.header("🔭 Prospecting (speculative outreach)")
    st.caption("Seed professors to prospect (funded-by-default countries). "
               "Each becomes a speculative opportunity run through the graph.")
    name = st.text_input("Professor name")
    university = st.text_input("University")
    country = st.selectbox("Country", ["Germany", "Switzerland", "Netherlands",
                                       "Sweden", "United Kingdom", "United States",
                                       "Australia"])
    field = st.text_input("Field", "Medical Imaging")
    if st.button("Prospect this professor"):
        seed = {"name": name, "university": university, "country": country, "field": field}
        res = api_post("/runs", {"professor_list": [seed]})
        if res:
            st.success(f"Run {res['thread_id']}: {res['status']}. "
                       "Approvable drafts appear under Approvals.")


def page_professors():
    st.header("👩‍🔬 Professors")
    data = api_get("/professors")
    if not data:
        return
    for p in data["professors"]:
        with st.expander(f"{p['name']} — {p['university'] or ''} <{p['email'] or 'no email'}>"):
            st.write(f"**Themes:** {p['themes']}")
            st.write(f"**Gap:** {p['gap']}")
            st.write(f"**Angle:** {p['angle']}")
            st.write("**Verified papers:**")
            for paper in (p["papers"] or []):
                st.write(f"- {paper['title']} ({paper.get('year')}, {paper.get('venue')}) "
                         f"[{paper.get('url')}]({paper.get('url')})")


def page_approvals():
    st.header("✅ Approvals — Human-in-the-loop queue")
    data = api_get("/approvals")
    if not data:
        return
    pending = data["pending"]
    if not pending:
        st.info("No drafts awaiting approval.")
        return
    for item in pending:
        kind = item.get("kind", "interrupt")
        if kind == "followup":
            _render_followup_approval(item)
        else:
            _render_interrupt_approval(item)


def _render_interrupt_approval(item):
    tid = item["thread_id"]
    intr = item["interrupt"]
    with st.expander(f"✉ {intr['subject']} → {intr.get('professor_email')} "
                     f"(fit {intr.get('fit_score')}) — thread {tid}", expanded=True):
        prof = intr.get("professor", {})
        st.write(f"**Professor:** {prof.get('name')}")
        st.write(f"**Identified gap:** {prof.get('gap')}")
        st.write("**Verified papers:**")
        for paper in (prof.get("papers") or []):
            st.write(f"- {paper['title']} ({paper.get('year')})")
        report = intr.get("quality_report") or {}
        st.caption(f"Quality gate: {'✅ passed' if report.get('passed') else '❌'}")
        subject = st.text_input("Subject", intr["subject"], key=f"s_{tid}")
        body = st.text_area("Body", intr["body"], height=240, key=f"bd_{tid}")
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ Approve", key=f"ap_{tid}"):
            edited = subject != intr["subject"] or body != intr["body"]
            payload = {"action": "approve"}
            if edited:
                payload = {"action": "edit", "edits": {"subject": subject, "body": body}}
            res = api_post(f"/approvals/{tid}/resume", payload)
            if res:
                st.success(f"{res['status']}")
                st.rerun()
        if c2.button("✖ Reject", key=f"rj_{tid}"):
            reason = st.session_state.get(f"rsn_{tid}", "")
            res = api_post(f"/approvals/{tid}/resume",
                           {"action": "reject", "reason": reason})
            if res:
                st.warning("Rejected.")
                st.rerun()
        c3.text_input("Reject reason", key=f"rsn_{tid}")


def _render_followup_approval(item):
    intr = item["interrupt"]
    eid = item["email_id"]
    prof = intr.get("professor", {})
    with st.expander(f"🔁 [follow-up] {intr['subject']} → {intr.get('professor_email')} "
                     f"(re: email #{intr.get('parent_email_id')})", expanded=True):
        st.caption("Follow-up to an unanswered first-contact email. Draft-only; "
                   "never sent without your approval.")
        report = intr.get("quality_report") or {}
        st.caption(f"Quality gate: {'✅ passed' if report.get('passed') else '❌'}")
        subject = st.text_input("Subject", intr["subject"], key=f"fs_{eid}")
        body = st.text_area("Body", intr["body"], height=200, key=f"fb_{eid}")
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ Approve", key=f"fap_{eid}"):
            edited = subject != intr["subject"] or body != intr["body"]
            payload = {"action": "approve"}
            if edited:
                payload = {"action": "edit", "edits": {"subject": subject, "body": body}}
            res = api_post(f"/emails/{eid}/decision", payload)
            if res:
                st.success(f"{res.get('status')}"
                           + (f" — scheduled {res['scheduled']}" if res.get("scheduled") else ""))
                st.rerun()
        if c2.button("✖ Reject", key=f"frj_{eid}"):
            res = api_post(f"/emails/{eid}/decision",
                           {"action": "reject", "reason": st.session_state.get(f"frsn_{eid}", "")})
            if res:
                st.warning("Rejected.")
                st.rerun()
        c3.text_input("Reject reason", key=f"frsn_{eid}")


def page_analytics():
    st.header("📊 Analytics")
    data = api_get("/analytics")
    if not data:
        return
    sends = data["sends"]
    totals = data["totals"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("First-contact sent", sends["first_contact_sent"])
    c2.metric("Replies", sends["replies"])
    c3.metric("Reply rate", f"{sends['reply_rate_pct']}%")
    avg = sends["avg_response_hours"]
    c4.metric("Avg response", f"{avg}h" if avg is not None else "—")

    st.divider()
    st.subheader("Follow-up funnel")
    fu = data["followup_funnel"]
    f1, f2, f3 = st.columns(3)
    f1.metric("Drafted", fu["drafted"])
    f2.metric("Awaiting review", fu["awaiting_review"])
    f3.metric("Sent", fu["sent"])

    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("By country")
        if data["by_country"]:
            st.bar_chart(data["by_country"])
        else:
            st.caption("No data yet.")
    with col_b:
        st.subheader("By field")
        if data["by_field"]:
            st.bar_chart(data["by_field"])
        else:
            st.caption("No data yet.")

    st.divider()
    st.subheader("Emails by status")
    st.bar_chart(data["status_counts"] or {})


def page_settings():
    st.header("⚙️ Settings")
    s = api_get("/settings")
    if not s:
        return
    if s["approved_send_mode"]:
        st.error("⚠ SEND MODE IS ON — approved emails will be scheduled & sent.")
    else:
        st.info("Draft-only mode (safe default).")
    send = st.toggle("approved_send_mode", value=s["approved_send_mode"])
    cap = st.number_input("Daily send cap", 1, 50, value=s["daily_send_cap"])
    threshold = st.number_input("Fit-score threshold", 0, 100, value=s["fit_score_threshold"])

    st.divider()
    st.subheader("Follow-ups & reply detection")
    fu_enabled = st.toggle("Follow-ups enabled", value=s.get("followup_enabled", True))
    fu_days = st.number_input("Follow up after (business days)", 1, 60,
                              value=s.get("followup_after_business_days", 10))
    reply_enabled = st.toggle("Reply detection enabled", value=s.get("reply_detection_enabled", True))
    st.caption("Reply detection reads Gmail threads via the gmail.readonly scope. "
               "Enabling it requires a one-time Gmail re-authorise. Without Gmail, mark "
               "replies manually on the Pipeline page.")

    if st.button("Save"):
        api_put("/settings", {"approved_send_mode": send, "daily_send_cap": int(cap),
                              "fit_score_threshold": int(threshold),
                              "followup_enabled": fu_enabled,
                              "followup_after_business_days": int(fu_days),
                              "reply_detection_enabled": reply_enabled})
        st.success("Saved.")
        st.rerun()

    if st.button("🔁 Scan for replies & due follow-ups now"):
        res = api_post("/followups/scan")
        if res:
            st.success(f"Replies detected: {res['replies_detected']} · "
                       f"Follow-ups drafted: {res['followups_created']} "
                       "(see Approvals).")

    st.divider()
    st.subheader("Gmail")
    st.write("Connected ✅" if s["gmail_authorised"] else "Not connected ❌")
    if not s["gmail_authorised"]:
        if st.button("Connect Gmail"):
            res = api_get("/gmail/authorize")
            if res and res.get("auth_url"):
                st.session_state["gmail_auth_url"] = res["auth_url"]
        if st.session_state.get("gmail_auth_url"):
            st.link_button("Open Google consent screen", st.session_state["gmail_auth_url"])
            st.caption("Approve in the new tab, then return here and refresh. "
                       "(Requires the backend's Google OAuth env vars — see DEPLOY.md.)")


def page_documents():
    st.header("📎 Documents")
    st.caption("Upload the documents your outreach emails attach (CV, transcript, "
               "base research summary, statement of purpose). One file per kind; "
               "re-uploading replaces the previous one. Text is extracted for the "
               "quality gate; image-only/scanned PDFs will warn.")
    KIND_LABELS = {
        "cv": "CV / résumé",
        "transcript": "Transcript",
        "summary": "Base research summary",
        "sop": "Statement of purpose (SOP)",
        "recommendation": "Recommendation letter",
        "english_test": "English test (IELTS/TOEFL) certificate",
        "degree_certificate": "Degree certificate / diploma",
        "research_proposal": "Research proposal",
        "writing_sample": "Writing sample",
        "portfolio": "Portfolio",
        "passport": "Passport / ID",
        "photo": "Passport photo",
    }
    existing = {a["kind"]: a for a in (api_get("/assets") or {}).get("assets", [])}
    for kind, label in KIND_LABELS.items():
        st.subheader(label)
        cur = existing.get(kind)
        if cur:
            line = f"Current: **{cur['file_name']}** ({cur['char_count']} chars extracted)"
            st.success(line)
            if cur.get("warning"):
                st.warning(cur["warning"])
        else:
            st.caption("Not uploaded yet.")
        up = st.file_uploader(
            f"Upload {label}",
            type=["pdf", "txt", "md", "doc", "docx", "png", "jpg", "jpeg"],
            key=f"upl_{kind}")
        if up is not None and st.button(f"Save {label}", key=f"save_{kind}"):
            res = api_post_file("/assets", kind, up)
            if res:
                msg = f"Uploaded {res['file_name']} ({res['char_count']} chars)."
                if res.get("warning"):
                    st.warning(res["warning"])
                st.success(msg)
                st.rerun()
        st.divider()


def page_profile():
    st.header("🧑‍🎓 Profile")
    st.caption("Your application details — used to auto-fill application forms and to "
               "personalise outreach. Stored in your database (RLS on). Sensitive "
               "fields are optional; fill only what you're comfortable with.")
    data = api_get("/profile") or {}
    ov = data.get("overrides", {}) or {}
    contact = ov.get("contact", {}) or {}
    addr = ov.get("address", {}) or {}
    scores = ov.get("test_scores", {}) or {}
    referees = ov.get("referees", []) or []

    with st.form("profile_form"):
        st.subheader("Personal")
        name = st.text_input("Full name", ov.get("name", ""))
        c1, c2 = st.columns(2)
        dob = c1.text_input("Date of birth (YYYY-MM-DD)", ov.get("date_of_birth", ""))
        gender = c2.text_input("Gender", ov.get("gender", ""))
        nationality = c1.text_input("Nationality", ov.get("nationality", ""))
        location = c2.text_input("Location (City, Country)", ov.get("location", ""))

        st.subheader("Contact")
        e1, e2 = st.columns(2)
        email = e1.text_input("Email", contact.get("email", ""))
        phone = e2.text_input("Phone", contact.get("phone", ""))
        linkedin = e1.text_input("LinkedIn", contact.get("linkedin", ""))
        github = e2.text_input("GitHub", contact.get("github", ""))
        scholar = e1.text_input("Google Scholar", contact.get("scholar", ""))
        orcid = e2.text_input("ORCID", contact.get("orcid", ""))
        website = e1.text_input("Website", contact.get("website", ""))

        st.subheader("Address (optional)")
        a1, a2 = st.columns(2)
        line1 = a1.text_input("Street address", addr.get("line1", ""))
        city = a2.text_input("City", addr.get("city", ""))
        state = a1.text_input("State / Province", addr.get("state", ""))
        postcode = a2.text_input("Postcode / ZIP", addr.get("postcode", ""))
        country = a1.text_input("Country", addr.get("country", ""))

        st.subheader("Academic")
        gpa = st.text_input("GPA / grade", ov.get("gpa", ""))
        languages = st.text_input("Languages (comma-separated)",
                                  ", ".join(ov.get("languages", []) or []))
        s1, s2, s3 = st.columns(3)
        ielts = s1.text_input("IELTS", scores.get("ielts", ""))
        toefl = s2.text_input("TOEFL", scores.get("toefl", ""))
        gre = s3.text_input("GRE", scores.get("gre", ""))
        gmat = s1.text_input("GMAT", scores.get("gmat", ""))
        duolingo = s2.text_input("Duolingo", scores.get("duolingo", ""))

        st.subheader("Sensitive (optional)")
        passport = st.text_input("Passport number", ov.get("passport_number", ""),
                                 type="password")

        st.subheader("Referees")
        st.caption("Most applications ask for 2–3. Leave blank to skip.")
        ref_inputs = []
        for i in range(3):
            r = referees[i] if i < len(referees) else {}
            st.markdown(f"**Referee {i + 1}**")
            r1, r2 = st.columns(2)
            rn = r1.text_input("Name", r.get("name", ""), key=f"rn{i}")
            re_ = r2.text_input("Email", r.get("email", ""), key=f"re{i}")
            ri = r1.text_input("Institution", r.get("institution", ""), key=f"ri{i}")
            rr = r2.text_input("Relationship", r.get("relationship", ""), key=f"rr{i}")
            ref_inputs.append({"name": rn, "email": re_,
                               "institution": ri, "relationship": rr})

        submitted = st.form_submit_button("💾 Save profile")

    if submitted:
        payload = {
            "name": name, "date_of_birth": dob, "gender": gender,
            "nationality": nationality, "location": location,
            "passport_number": passport, "gpa": gpa,
            "languages": [s.strip() for s in languages.split(",") if s.strip()],
            "contact": {"email": email, "phone": phone, "linkedin": linkedin,
                        "github": github, "scholar": scholar, "orcid": orcid,
                        "website": website},
            "address": {"line1": line1, "city": city, "state": state,
                        "postcode": postcode, "country": country},
            "test_scores": {"ielts": ielts, "toefl": toefl, "gre": gre,
                            "gmat": gmat, "duolingo": duolingo},
            "referees": [r for r in ref_inputs if r.get("name") or r.get("email")],
        }
        if api_put("/profile", payload):
            st.success("Profile saved.")


def page_apply():
    st.header("📝 Application Assist")
    st.caption("Generate a fill-plan for an opportunity's application form, then run "
               "the local Playwright script to fill it in your own browser — you "
               "review and click submit yourself. Nothing is submitted automatically.")
    opps = (api_get("/opportunities") or {}).get("opportunities", [])
    if not opps:
        st.info("No opportunities yet. Add one first.")
        return
    options = {f"#{o['id']} {o.get('title') or o.get('professor_name') or '—'}": o
               for o in opps}
    label = st.selectbox("Opportunity", list(options.keys()))
    o = options[label]
    url_override = st.text_input("Application URL", o.get("application_link") or "",
                                 help="Defaults to the opportunity's application link.")
    if st.button("Generate fill-plan"):
        body = {"url": url_override} if url_override.strip() else {}
        res = api_post(f"/opportunities/{o['id']}/fill-plan", body)
        if res:
            st.session_state["fill_plan"] = res

    res = st.session_state.get("fill_plan")
    if res and res.get("opportunity_id") == o["id"]:
        st.caption(f"Source: {res['url']} · {res['field_count']} fields · method: {res['method']}")
        rows = [{"Field": p["label"], "Selector": p.get("name") or p.get("id"),
                 "Type": p["type"], "Value": p["value"],
                 "You fill": "✏️" if p["needs_human"] else "", "Source": p["source"]}
                for p in res["plan"]]
        if rows:
            st.dataframe(rows, use_container_width=True)
        else:
            st.warning("No fillable form fields found on that page (it may be a portal "
                       "that loads its form via JavaScript or requires login).")
        st.subheader("Fill it in your browser")
        st.caption("One-time local setup: `pip install -r requirements-local.txt` then "
                   "`playwright install chromium`.")
        st.markdown("**Option A — simple filler** (best for plain static forms):")
        st.code(f"python scripts/fill_application.py --api {API} "
                f"--opportunity {o['id']} --cv path/to/your_cv.pdf", language="bash")
        st.markdown("**Option B — agentic browser** (adapts to JS / multi-step portals; "
                    "needs Anthropic credits):")
        st.code(f"python scripts/agent_fill.py --api {API} "
                f"--opportunity {o['id']} --cv path/to/your_cv.pdf", language="bash")
        st.caption("Both fill the form then pause for you to review and submit — never auto-submit. "
                   "Agentic setup: pip install -r requirements-local.txt && playwright install chromium.")


def page_discover():
    st.header("🔎 Discover (proactive search)")
    st.caption("Search the web for funded PhD postings in your target fields/regions. "
               "Review the candidates, then run any of them through the pipeline — "
               "nothing is processed until you click Run.")
    c1, c2, c3 = st.columns([2, 2, 1])
    field = c1.text_input("Field (optional)", "")
    country = c2.text_input("Country (optional)", "")
    n = c3.number_input("Per query", 1, 10, 5)
    if st.button("🔎 Discover"):
        with st.spinner("Searching…"):
            res = api_post("/discover", {"field": field or None,
                                         "country": country or None,
                                         "max_per_query": int(n)})
        if res is not None:
            st.session_state["discover_res"] = res

    res = st.session_state.get("discover_res")
    if res:
        if not res.get("tavily_enabled"):
            st.warning("TAVILY_API_KEY is not set on the backend, so web search is "
                       "disabled and no candidates can be found. Set it in Render → Environment.")
        cands = res.get("candidates", [])
        st.caption(f"{len(cands)} candidate(s).")
        for i, c in enumerate(cands):
            with st.expander(c.get("title") or c["url"]):
                st.markdown(f"[{c['url']}]({c['url']})")
                if c.get("funding_signals"):
                    st.caption("Funding signals: " + ", ".join(c["funding_signals"]))
                st.write(c.get("snippet") or "")
                st.caption(f"query: {c.get('query')}")
                if st.button("Run this through the pipeline", key=f"runc_{i}"):
                    with st.spinner("Fetching page and running pipeline…"):
                        _show_run_result(api_post("/discover/run", {"url": c["url"]}))


def page_deep_research():
    st.header("🔬 Deep Research & Documents")
    st.caption("Deep-dive a professor's work, identify a grounded gap + research "
               "question + proposed approach, then generate tailored documents "
               "(outreach email, SOP, cover/motivation letter, research proposal). "
               "Needs Anthropic credits for real depth.")
    opps = (api_get("/opportunities") or {}).get("opportunities", [])
    if not opps:
        st.info("No opportunities yet. Add one first.")
        return
    options = {f"#{o['id']} {o.get('title') or o.get('professor_name') or '—'}": o
               for o in opps}
    label = st.selectbox("Opportunity", list(options.keys()))
    oid = options[label]["id"]

    c_scout, c_all = st.columns(2)
    if c_scout.button("🔬 Scout this lab (deep)"):
        with st.spinner("Scouting (papers + web search: site, Scholar, talks, news)…"):
            res = api_post(f"/opportunities/{oid}/deep-research", {})
        st.session_state[f"brief_{oid}"] = res.get("brief") if res else None
    if c_all.button("🚀 Scout & draft everything"):
        with st.spinner("Scouting, then drafting all documents…"):
            res = api_post(f"/opportunities/{oid}/deep-research", {})
            st.session_state[f"brief_{oid}"] = res.get("brief") if res else None
            api_post(f"/opportunities/{oid}/documents",
                     {"kinds": ["email", "sop", "cover", "proposal"]})
        st.success("Dossier + all documents ready below.")

    brief = st.session_state.get(f"brief_{oid}")
    if brief is None:
        got = api_get(f"/opportunities/{oid}/deep-research")
        brief = got.get("brief") if got else None

    if not brief:
        st.info("No dossier yet — scout the lab above.")
        return

    if brief.get("method") == "keyless":
        st.warning("Generated without an LLM — add Anthropic credits for real depth "
                   "(web search needs an Anthropic key).")
    elif not (brief.get("sources_used") or {}).get("web_searched"):
        st.caption("ℹ️ Crawl-only dossier (live web search was off/unavailable).")

    # Full markdown dossier (the comprehensive document).
    if brief.get("dossier_md"):
        st.markdown(brief["dossier_md"])
        st.link_button("⬇ Download dossier PDF",
                       f"{API}/opportunities/{oid}/deep-research/pdf")
    else:
        # Fallback rendering for older briefs without dossier_md.
        st.subheader("Research brief")
        st.write(f"**Gap:** {brief.get('chosen_gap')}")
        st.write(f"**Research question:** {brief.get('research_question')}")
        st.write(f"**Proposed approach:** {brief.get('proposed_approach')}")

    with st.expander("Structured proposal (problem → approaches → gap → extension)"):
        if brief.get("problem_statement"):
            st.write(f"**Problem statement:** {brief['problem_statement']}")
        if brief.get("current_approaches"):
            st.write("**Current approaches:**")
            for a in brief["current_approaches"]:
                ref = f" _(ref: {a.get('citation')})_" if a.get("citation") else ""
                st.write(f"- {a.get('approach')}{ref}")
        if brief.get("the_gap"):
            st.write(f"**The gap:** {brief['the_gap']}")
        if brief.get("research_question"):
            st.write(f"**Research question:** {brief['research_question']}")
        if brief.get("proposed_approach"):
            st.write(f"**Proposed approach:** {brief['proposed_approach']}")
        if brief.get("proposed_extension"):
            st.write(f"**Taking it further:** {brief['proposed_extension']}")
        if brief.get("pitch"):
            st.info(f"**Pitch:** {brief['pitch']}")

    if brief.get("future_directions"):
        with st.expander("Stated future directions"):
            for d in brief["future_directions"]:
                st.write(f"- {d}")
    if brief.get("talks"):
        with st.expander("Recent talks & activity"):
            for t in brief["talks"]:
                st.write(f"- {t}")
    if brief.get("candidate_gaps"):
        with st.expander("Candidate gaps considered"):
            for g in brief["candidate_gaps"]:
                st.write(f"- {g.get('gap')}")
    if brief.get("sources"):
        with st.expander("Web sources"):
            for srx in brief["sources"]:
                st.write(f"- [{srx.get('title') or srx.get('url')}]({srx.get('url')})")

    st.divider()
    st.subheader("Generate documents")
    labels = {"email": "Outreach email", "sop": "SOP",
              "cover": "Cover / Motivation letter", "proposal": "Research proposal"}
    kinds = st.multiselect("Documents", list(labels.keys()),
                           default=list(labels.keys()),
                           format_func=lambda k: labels[k])
    if st.button("✍️ Generate selected"):
        with st.spinner("Writing documents…"):
            api_post(f"/opportunities/{oid}/documents", {"kinds": kinds})
        st.rerun()

    for d in (api_get(f"/opportunities/{oid}/documents") or {}).get("documents", []):
        with st.expander(d["title"]):
            content = st.text_area("Content", d["content"], height=320, key=f"doc_{d['id']}")
            cc1, cc2 = st.columns(2)
            if cc1.button("💾 Save edits", key=f"save_{d['id']}"):
                if api_put(f"/documents/{d['id']}", {"content": content}):
                    st.success("Saved.")
            cc2.link_button("⬇ Download PDF", f"{API}/documents/{d['id']}/pdf")


def page_tracker():
    st.header("🗓 Application Tracker")
    st.caption("Track each application's status and deadlines. Sorted by nearest deadline.")
    data = api_get("/applications")
    if not data:
        return
    apps = data["applications"]
    statuses = data["statuses"]
    if not apps:
        st.info("No active opportunities yet. Add or discover some first.")
        return
    soon = [a for a in apps if a["days_left"] is not None and 0 <= a["days_left"] <= 14]
    overdue = [a for a in apps if a["days_left"] is not None and a["days_left"] < 0]
    m1, m2, m3 = st.columns(3)
    m1.metric("Tracked", len(apps))
    m2.metric("Due ≤14 days", len(soon))
    m3.metric("Overdue", len(overdue))
    st.divider()

    for a in apps:
        dl = a["days_left"]
        badge = ""
        if dl is not None:
            badge = (f"🔴 {abs(dl)}d overdue" if dl < 0
                     else f"🟠 {dl}d left" if dl <= 14 else f"🟢 {dl}d left")
        title = a["title"] or a["professor_name"] or f"#{a['opportunity_id']}"
        oid = a["opportunity_id"]
        with st.expander(f"{title} — {a['university'] or ''}   {badge}  ·  [{a['status']}]"):
            c1, c2 = st.columns(2)
            idx = statuses.index(a["status"]) if a["status"] in statuses else 0
            new_status = c1.selectbox("Status", statuses, index=idx, key=f"st_{oid}")
            c2.write(f"**Deadline:** {a['deadline'] or '—'}"
                     + (f" · fit {a['fit_score']}" if a.get("fit_score") is not None else ""))
            if a.get("required_documents"):
                st.caption("Required documents: " + ", ".join(a["required_documents"]))
            notes = st.text_area("Notes", a.get("notes") or "", key=f"nt_{oid}", height=80)
            if st.button("💾 Save", key=f"sv_{oid}"):
                if api_put(f"/applications/{oid}", {"status": new_status, "notes": notes}):
                    st.success("Saved.")
                    st.rerun()
    st.caption("Documents on file: " + (", ".join(data.get("documents_on_file", [])) or "none"))


def page_home():
    st.header("🏠 ScholarReach")
    st.caption("Your PhD outreach + application assistant.")
    s = api_get("/status")
    if not s:
        return
    c = s.get("counts", {})
    m1, m2, m3 = st.columns(3)
    m1.metric("Opportunities", c.get("opportunities", 0))
    m2.metric("Emails", c.get("emails", 0))
    m3.metric("Professors", c.get("professors", 0))

    st.divider()
    st.subheader("Setup checklist")

    def row(ok, label, hint):
        st.markdown(f"{'✅' if ok else '⬜'} **{label}**"
                    + ("" if ok else f" — {hint}"))

    row(s.get("postgres"), "Database (Supabase Postgres)", "backend not on Postgres")
    row(s.get("profile_complete"), "Profile (name + email)", "fill in the Profile page")
    row(bool(s.get("documents")), "Documents uploaded", "upload your CV/SOP on Documents")
    row(s.get("llm_configured"),
        f"LLM key ({s.get('llm_provider') or 'none'})",
        "set OPENAI_API_KEY or ANTHROPIC_API_KEY in Render for AI features")
    row(s.get("gmail_connected"), "Gmail connected", "connect on the Settings page")
    row(s.get("tavily_enabled"), "Web search (Tavily)", "set TAVILY_API_KEY to enable Discover")
    row(s.get("cron_token_set"), "Scheduled tasks", "set CRON_TOKEN + GitHub Actions secrets")
    row(s.get("public_base_url_set"), "Public base URL", "set PUBLIC_BASE_URL (needed for Gmail OAuth)")
    if s.get("documents"):
        st.caption("Documents on file: " + ", ".join(s["documents"]))


# --------------------------------------------------------------------------- #
# Router — grouped navigation (st.navigation)
# --------------------------------------------------------------------------- #
st.sidebar.title("🎓 ScholarReach")
st.sidebar.caption("Draft-first — nothing sends without your approval.")
if not _backend_ok():
    st.sidebar.error(f"Backend unreachable at {API}.")

_nav = st.navigation({
    "Overview": [
        st.Page(page_home, title="Home", icon="🏠", default=True),
        st.Page(page_pipeline, title="Pipeline", icon="📋"),
        st.Page(page_analytics, title="Analytics", icon="📊"),
    ],
    "Opportunities": [
        st.Page(page_opportunities, title="Opportunities", icon="🎯"),
        st.Page(page_add, title="Add", icon="➕"),
        st.Page(page_discover, title="Discover", icon="🔎"),
        st.Page(page_prospecting, title="Prospecting", icon="🔭"),
        st.Page(page_tracker, title="Tracker", icon="🗓"),
    ],
    "Research & Apply": [
        st.Page(page_professors, title="Professors", icon="👩‍🔬"),
        st.Page(page_deep_research, title="Deep Research", icon="🔬"),
        st.Page(page_apply, title="Apply", icon="📝"),
        st.Page(page_approvals, title="Approvals", icon="✅"),
    ],
    "Setup": [
        st.Page(page_profile, title="Profile", icon="🧑‍🎓"),
        st.Page(page_documents, title="Documents", icon="📎"),
        st.Page(page_settings, title="Settings", icon="⚙️"),
    ],
})
_nav.run()
