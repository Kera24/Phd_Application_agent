"""Assisted application filler (LOCAL, headed Playwright).

Runs on YOUR machine, not the server: it opens a real browser, applies the
fill-plan from the backend, uploads the document files you pass, then PAUSES so
you review everything and click submit yourself. It never submits for you.

Setup (one time):
    pip install -r requirements-local.txt
    playwright install chromium

Usage:
    python scripts/fill_application.py --api https://<backend> --opportunity 12 \
        --cv path/to/cv.pdf --sop path/to/sop.pdf

Or point at an arbitrary URL (bypassing the stored application_link):
    python scripts/fill_application.py --api http://localhost:8001 --opportunity 12 \
        --url https://apply.example.edu/form --cv cv.pdf
"""
from __future__ import annotations

import argparse
import sys

import requests


def _selector(item: dict) -> str | None:
    if item.get("id"):
        return f"#{item['id']}"
    if item.get("name"):
        return f"[name=\"{item['name']}\"]"
    return None


def fetch_plan(api: str, opp_id: int, url: str | None) -> dict:
    body = {"url": url} if url else {}
    r = requests.post(f"{api.rstrip('/')}/opportunities/{opp_id}/fill-plan",
                      json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def run(args) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run:\n"
              "  pip install -r requirements-local.txt\n"
              "  playwright install chromium", file=sys.stderr)
        return 2

    plan = fetch_plan(args.api, args.opportunity, args.url)
    url = plan["url"]
    items = plan.get("plan", [])
    doc_paths = {"cv": args.cv, "sop": args.sop,
                 "transcript": args.transcript, "summary": args.summary}

    print(f"Opening {url} ({plan.get('field_count')} fields, method {plan.get('method')})")
    filled, skipped = 0, []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")

        for item in items:
            sel = _selector(item)
            if not sel:
                continue
            try:
                if item.get("type") == "file":
                    path = doc_paths.get(item.get("document") or "")
                    if path:
                        page.set_input_files(sel, path)
                        filled += 1
                    else:
                        skipped.append(f"{item['label']} (no --{item.get('document')} provided)")
                    continue
                value = item.get("value") or ""
                if not value:
                    if item.get("needs_human"):
                        skipped.append(f"{item['label']} (you fill)")
                    continue
                if item.get("type") == "select":
                    page.select_option(sel, value)
                else:
                    page.fill(sel, value)
                filled += 1
            except Exception as exc:
                skipped.append(f"{item['label']} -> {exc}")

        print(f"\nFilled {filled} field(s).")
        if skipped:
            print("Left for you to complete / check:")
            for s in skipped:
                print(f"  - {s}")
        print("\n>>> Review every field in the browser, complete anything missing, "
              "and SUBMIT yourself. Nothing has been submitted.")
        if args.headless:
            print("(Ran headless — re-run without --headless to see/submit the form.)")
        else:
            try:
                input("Press Enter here to close the browser once you're done... ")
            except (EOFError, KeyboardInterrupt):
                pass
        browser.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Assisted (never auto-submit) application filler.")
    ap.add_argument("--api", required=True, help="Backend base URL (e.g. http://localhost:8001)")
    ap.add_argument("--opportunity", type=int, required=True, help="Opportunity id")
    ap.add_argument("--url", default=None, help="Override the application URL")
    ap.add_argument("--cv", default=None, help="Path to your CV file")
    ap.add_argument("--sop", default=None, help="Path to your statement of purpose")
    ap.add_argument("--transcript", default=None, help="Path to your transcript")
    ap.add_argument("--summary", default=None, help="Path to your research summary")
    ap.add_argument("--headless", action="store_true",
                    help="Run without a visible browser (no manual submit; for testing only)")
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
