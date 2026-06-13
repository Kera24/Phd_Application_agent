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


def page_opportunities():
    st.header("🎯 Opportunities")
    data = api_get("/opportunities")
    if not data:
        return
    opps = data["opportunities"]
    active = [o for o in opps if o["pipeline_status"] != "archived_not_funded"]
    archived = [o for o in opps if o["pipeline_status"] == "archived_not_funded"]
    if active:
        st.dataframe([{k: o[k] for k in ("id", "type", "title", "university",
                                         "country", "funding_status", "fit_score",
                                         "pipeline_status")} for o in active],
                     use_container_width=True)
    for o in active:
        hl = "⭐ " if (o.get("fit_score") or 0) >= 70 else ""
        with st.expander(f"{hl}#{o['id']} {o['title'] or o['professor_name']} — fit {o['fit_score']}"):
            st.write(f"**Funding:** {o['funding_status']} — {o['funding_evidence']}")
            st.write(f"**Professor:** {o['professor_name']} <{o['professor_email']}>")
            if o.get("score_breakdown"):
                st.json(o["score_breakdown"])
    with st.expander(f"🗄 Archived (not fully funded) — {len(archived)}"):
        for o in archived:
            st.write(f"#{o['id']} {o['title']} — {o['funding_status']}: {o['funding_evidence']}")


def page_add():
    st.header("➕ Add Opportunity")
    st.caption("Paste a LinkedIn post / posting text. It runs the full graph; "
               "fully funded ones reach the approval queue, others are archived.")
    raw = st.text_area("Posting text", height=220)
    if st.button("Run pipeline"):
        if not raw.strip():
            st.warning("Paste some text first.")
        else:
            res = api_post("/runs", {"linkedin_inputs": [raw]})
            if res:
                if res["status"] == "awaiting_approval":
                    st.success(f"Draft ready for approval (thread {res['thread_id']}). "
                               "See the Approvals page.")
                else:
                    st.info("Run completed. Opportunity archived or parked "
                            "(not fully funded / below fit threshold / needs review). "
                            "Check Opportunities & Pipeline.")


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
    st.write("Authorised ✅" if s["gmail_authorised"] else "Not authorised ❌")
    if st.button("Authorise Gmail"):
        api_post("/gmail/authorize")
        st.rerun()


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
PAGES = {
    "Pipeline": page_pipeline,
    "Opportunities": page_opportunities,
    "Add Opportunity": page_add,
    "Prospecting": page_prospecting,
    "Professors": page_professors,
    "Approvals": page_approvals,
    "Analytics": page_analytics,
    "Settings": page_settings,
}

st.sidebar.title("🎓 ScholarReach")
st.sidebar.caption("LangGraph PhD outreach agent — human-in-the-loop")
if not _backend_ok():
    st.sidebar.error(f"Backend unreachable at {API}.\nStart it:\n"
                     "uvicorn api.main:app --port 8001")
choice = st.sidebar.radio("Navigate", list(PAGES.keys()))
st.sidebar.divider()
st.sidebar.caption("Draft-first. Nothing is sent without explicit approval.")
PAGES[choice]()
