"""Agentic application filler (LOCAL, browser-use + Claude) — PoC.

A "browser harness": Claude drives a real browser step-by-step (read rendered
page -> decide action -> act -> observe -> repeat), so it adapts to forms it has
never seen, including JavaScript/multi-step portals. This is the ~80-90% path,
not a guarantee — CAPTCHA, logins and exotic widgets still need you.

Safety model (same as the rest of the app):
  * Runs on YOUR machine in a VISIBLE browser.
  * You handle login / CAPTCHA when they appear.
  * It is instructed to STOP at the final review and NEVER submit. (Soft guard —
    keep watching the headed browser; that's the real safeguard.)

Setup (one time):
    pip install -r requirements-local.txt
    playwright install chromium
    export ANTHROPIC_API_KEY=...        # needs credits; agentic browsing uses many calls

Usage:
    python scripts/agent_fill.py --api http://localhost:8001 --opportunity 12 \
        --cv cv.pdf --sop sop.pdf
    python scripts/agent_fill.py --api https://<backend> --url https://apply.x.edu/form \
        --cv cv.pdf
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import requests

DEFAULT_MODEL = "claude-sonnet-4-6"


def _fetch_json(url: str) -> dict:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_context(api: str, opp_id: int | None) -> tuple[dict, dict | None, str | None]:
    """Return (profile, opportunity, application_url) from the backend."""
    api = api.rstrip("/")
    profile = _fetch_json(f"{api}/profile").get("profile", {})
    opp = None
    url = None
    if opp_id is not None:
        opps = _fetch_json(f"{api}/opportunities").get("opportunities", [])
        opp = next((o for o in opps if o.get("id") == opp_id), None)
        if opp:
            url = opp.get("application_link")
    return profile, opp, url


def build_task(profile: dict, opp: dict | None, url: str, available_docs: dict[str, str]) -> str:
    """Build the agent instruction (pure function — unit tested)."""
    docs_lines = "\n".join(f"  - {kind}: {path}" for kind, path in available_docs.items()) or "  (none provided)"
    opp_ctx = ""
    if opp:
        opp_ctx = (f"\nOPPORTUNITY CONTEXT:\n"
                   f"  title: {opp.get('title')}\n  university: {opp.get('university')}\n"
                   f"  professor: {opp.get('professor_name')}\n")
    return (
        "You are filling out a PhD/graduate application form on behalf of the applicant.\n\n"
        f"APPLICATION URL: {url}\n"
        f"{opp_ctx}\n"
        "APPLICANT DATA (the ONLY source of truth — never invent or embellish):\n"
        f"{json.dumps(profile, indent=2, default=str)}\n\n"
        "DOCUMENTS available to upload (use the correct file for each upload field):\n"
        f"{docs_lines}\n\n"
        "INSTRUCTIONS:\n"
        "1. Go to the application URL and locate the application form.\n"
        "2. Fill every field you can map from the applicant data, accurately and only "
        "from that data. For fields not answerable from the data (essays, anything not "
        "provided), LEAVE THEM BLANK — do not fabricate.\n"
        "3. For document-upload fields, upload the matching file from the list above.\n"
        "4. Navigate multi-step/multi-page forms as needed.\n"
        "5. If you hit a login, account creation, payment, or CAPTCHA, STOP and tell the "
        "human to handle it, then continue once they say so.\n"
        "6. CRITICAL: do NOT click the final Submit/Send button. When the form is filled, "
        "STOP at the review/submit screen and report: what you filled, what you left for "
        "the human, and anything you were unsure about.\n"
    )


def _agent_llm(model):
    """Pick browser-use's OpenAI or Anthropic chat model from the env keys."""
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from browser_use import ChatOpenAI
        except ImportError:
            from browser_use.llm import ChatOpenAI
        return ChatOpenAI(model=model or "gpt-4o"), "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from browser_use import ChatAnthropic
        except ImportError:
            from browser_use.llm import ChatAnthropic
        return ChatAnthropic(model=model or "claude-sonnet-4-6"), "anthropic"
    return None, None


async def run_agent(task: str, model, file_paths: list[str], max_steps: int) -> int:
    try:
        from browser_use import Agent
    except ImportError:
        print("browser-use is not installed. Run:\n"
              "  pip install -r requirements-local.txt\n"
              "  playwright install chromium", file=sys.stderr)
        return 2
    llm, prov = _agent_llm(model)
    if llm is None:
        print("Set OPENAI_API_KEY or ANTHROPIC_API_KEY (with credits).", file=sys.stderr)
        return 2

    # NOTE: browser-use's API evolves quickly; if these kwargs error, check the
    # installed version's docs. We keep the call minimal for forward-compatibility.
    agent = Agent(task=task, llm=llm, available_file_paths=file_paths or None)
    print(f"Launching agent (provider={prov}, model={model or 'default'}). "
          "Watch the browser; handle login/CAPTCHA when asked.\n"
          ">>> It will NOT submit — it stops at the review screen for you.\n")
    await agent.run(max_steps=max_steps)
    print("\nAgent finished. Review the form in the browser and submit yourself if it looks correct.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Agentic (never auto-submit) application filler PoC.")
    ap.add_argument("--api", required=True, help="Backend base URL")
    ap.add_argument("--opportunity", type=int, default=None, help="Opportunity id (for context + URL)")
    ap.add_argument("--url", default=None, help="Application URL (overrides the opportunity's link)")
    ap.add_argument("--cv", default=None)
    ap.add_argument("--sop", default=None)
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--summary", default=None)
    ap.add_argument("--model", default=None,
                    help="Model id (default: gpt-4o for OpenAI, claude-sonnet-4-6 for Anthropic)")
    ap.add_argument("--max-steps", type=int, default=40)
    args = ap.parse_args()

    profile, opp, url = fetch_context(args.api, args.opportunity)
    url = args.url or url
    if not url:
        print("No application URL (pass --url or an --opportunity that has an application_link).",
              file=sys.stderr)
        return 1
    docs = {k: v for k, v in {"cv": args.cv, "sop": args.sop,
                              "transcript": args.transcript, "summary": args.summary}.items() if v}
    task = build_task(profile, opp, url, docs)
    return asyncio.run(run_agent(task, args.model, list(docs.values()), args.max_steps))


if __name__ == "__main__":
    raise SystemExit(main())
