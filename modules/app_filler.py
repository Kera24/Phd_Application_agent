"""Assisted application-form filling — the *plan* half (server-safe).

Given an application page URL, extract its form fields and build a fill-plan
mapping the candidate's profile (ground truth) to those fields. This module does
NOT drive a browser — the actual filling is done locally by
`scripts/fill_application.py` (Playwright), which keeps the human at the browser
to review and submit (never auto-submit).

Rules mirrored from the rest of the app: only profile facts are used; anything
not derivable from the profile is left blank and flagged `needs_human` so the
applicant fills it themselves. No fabrication.
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Optional

from modules import config_loader, llm

# Input types that are not user-fillable form fields.
_SKIP_INPUT_TYPES = {"hidden", "submit", "button", "image", "reset"}

def _addr(pr: dict, key: str) -> str:
    return (pr.get("address") or {}).get(key, "") or ""


def _score(pr: dict, key: str) -> str:
    return str((pr.get("test_scores") or {}).get(key, "") or "")


def _country_from_location(pr: dict) -> str:
    return pr.get("location", "").split(",")[-1].strip() if pr.get("location") else ""


# (label/name keyword(s)) -> function(profile) -> value. First match wins, so
# order from most specific to least.
_FIELD_HINTS: list[tuple[tuple[str, ...], Any]] = [
    (("first name", "given name", "forename"),
     lambda pr: (pr.get("name", "").split()[0] if pr.get("name") else "")),
    (("last name", "surname", "family name"),
     lambda pr: (pr.get("name", "").split()[-1] if pr.get("name") else "")),
    (("full name", "your name", "name"), lambda pr: pr.get("name", "")),
    (("e-mail", "email"), lambda pr: pr.get("contact", {}).get("email", "")),
    (("phone", "mobile", "telephone", "tel"), lambda pr: pr.get("contact", {}).get("phone", "")),
    (("linkedin",), lambda pr: pr.get("contact", {}).get("linkedin", "")),
    (("github",), lambda pr: pr.get("contact", {}).get("github", "")),
    (("orcid",), lambda pr: pr.get("contact", {}).get("orcid", "")),
    (("scholar",), lambda pr: pr.get("contact", {}).get("scholar", "")),
    (("website", "homepage", "personal page", "web page"),
     lambda pr: pr.get("contact", {}).get("website", "")),
    (("date of birth", "dob", "birth date", "birthdate"), lambda pr: pr.get("date_of_birth", "")),
    (("gender", "sex"), lambda pr: pr.get("gender", "")),
    (("nationality", "citizenship"), lambda pr: pr.get("nationality", "") or _country_from_location(pr)),
    (("passport",), lambda pr: pr.get("passport_number", "")),
    (("postcode", "postal code", "zip"), lambda pr: _addr(pr, "postcode")),
    (("street", "address line", "address1", "address"),
     lambda pr: _addr(pr, "line1") or pr.get("location", "")),
    (("state", "province"), lambda pr: _addr(pr, "state")),
    (("country",), lambda pr: _addr(pr, "country") or pr.get("nationality", "") or _country_from_location(pr)),
    (("city", "town"), lambda pr: _addr(pr, "city") or (pr.get("location", "").split(",")[0].strip() if pr.get("location") else "")),
    (("location",), lambda pr: pr.get("location", "")),
    (("cgpa", "gpa", "grade point", "grade average", "class of degree"),
     lambda pr: str(pr.get("gpa", "") or "")),
    (("ielts",), lambda pr: _score(pr, "ielts")),
    (("toefl",), lambda pr: _score(pr, "toefl")),
    (("duolingo",), lambda pr: _score(pr, "duolingo")),
    (("gre",), lambda pr: _score(pr, "gre")),
    (("gmat",), lambda pr: _score(pr, "gmat")),
    (("languages", "language"), lambda pr: ", ".join(pr.get("languages", []) or [])),
]


class _FormParser(HTMLParser):
    """Collect fillable form controls + their labels from an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[dict] = []
        self.labels: dict[str, str] = {}         # for-id -> label text
        self._label_for: Optional[str] = None
        self._label_buf: list[str] = []
        self._select: Optional[dict] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self._label_for = a.get("for")
            self._label_buf = []
        elif tag == "input":
            t = (a.get("type") or "text").lower()
            if t in _SKIP_INPUT_TYPES:
                return
            self.fields.append({"tag": "input", "type": t, "name": a.get("name"),
                                "id": a.get("id"), "placeholder": a.get("placeholder"),
                                "required": "required" in a, "options": None})
        elif tag == "textarea":
            self.fields.append({"tag": "textarea", "type": "textarea", "name": a.get("name"),
                                "id": a.get("id"), "placeholder": a.get("placeholder"),
                                "required": "required" in a, "options": None})
        elif tag == "select":
            self._select = {"tag": "select", "type": "select", "name": a.get("name"),
                            "id": a.get("id"), "placeholder": None,
                            "required": "required" in a, "_opts": []}
        elif tag == "option" and self._select is not None:
            self._select["_opts"].append(a.get("value"))

    def handle_data(self, data):
        if self._label_for is not None:
            self._label_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "label":
            if self._label_for:
                self.labels[self._label_for] = " ".join("".join(self._label_buf).split())
            self._label_for = None
            self._label_buf = []
        elif tag == "select" and self._select is not None:
            self._select["options"] = [o for o in self._select.pop("_opts", []) if o]
            self.fields.append(self._select)
            self._select = None


def extract_form_fields(html: str) -> list[dict]:
    """Return the fillable form controls on the page, each with a resolved label."""
    parser = _FormParser()
    parser.feed(html or "")
    out = []
    for f in parser.fields:
        if not (f.get("name") or f.get("id")):
            continue  # un-targetable
        f["label"] = parser.labels.get(f.get("id") or "") or f.get("placeholder") or f.get("name")
        out.append(f)
    return out


def _heuristic_value(label: str, profile: dict) -> str:
    low = (label or "").lower()
    # Referee / recommender fields: map the first referee on record.
    if any(k in low for k in ("referee", "reference", "recommender")):
        refs = profile.get("referees") or []
        if not refs:
            return ""
        r = refs[0]
        if "email" in low:
            return r.get("email", "")
        if any(k in low for k in ("institution", "organisation", "organization",
                                  "affiliation", "university", "company")):
            return r.get("institution", "")
        return r.get("name", "")
    for keywords, getter in _FIELD_HINTS:
        if any(k in low for k in keywords):
            val = getter(profile) or ""
            if val:
                return val
    return ""


def _plan_item(f: dict, value: str, *, document: Optional[str] = None,
               source: str = "manual") -> dict:
    return {
        "name": f.get("name"),
        "id": f.get("id"),
        "label": f.get("label"),
        "type": f.get("type"),
        "value": value,
        "document": document,                 # for file inputs: which Asset kind
        "needs_human": not value and document is None,
        "source": source,                     # profile | upload | manual | llm
        "options": f.get("options"),
    }


def _heuristic_plan(profile: dict, fields: list[dict]) -> list[dict]:
    plan = []
    for f in fields:
        if f.get("type") == "file":
            plan.append(_plan_item(f, "", document="cv", source="upload"))
            continue
        val = _heuristic_value(f.get("label") or "", profile)
        plan.append(_plan_item(f, val, source="profile" if val else "manual"))
    return plan


def _llm_plan(opp, profile: dict, fields: list[dict]) -> list[dict]:
    """Map fields to values with the LLM, constrained to profile facts only."""
    import json
    descriptors = [{"key": (f.get("name") or f.get("id")), "label": f.get("label"),
                    "type": f.get("type"), "options": f.get("options")} for f in fields]
    prompt = (
        f"APPLICANT PROFILE (the only source of truth):\n{json.dumps(profile, default=str)[:4000]}\n\n"
        f"FORM FIELDS:\n{json.dumps(descriptors)[:4000]}\n\n"
        "For each field, return the value to enter, using ONLY facts present in the "
        "profile. If a field is not answerable from the profile (e.g. an essay, a "
        "field requiring a document upload, or anything not in the profile), return "
        'an empty string. Never invent or infer facts. Return JSON: '
        '{"values": {"<key>": "<value>", ...}}.'
    )
    data = llm.complete_json(prompt, system="You fill application forms strictly from a given profile. Never fabricate.")
    values = (data or {}).get("values", {}) or {}
    plan = []
    for f in fields:
        key = f.get("name") or f.get("id")
        if f.get("type") == "file":
            plan.append(_plan_item(f, "", document="cv", source="upload"))
            continue
        val = (values.get(key) or "").strip() if isinstance(values.get(key), str) else ""
        plan.append(_plan_item(f, val, source="llm" if val else "manual"))
    return plan


def build_fill_plan(opp, profile: dict, fields: list[dict]) -> list[dict]:
    """Build the field->value plan. LLM-enhanced, with a heuristic fallback."""
    if llm.available():
        try:
            return _llm_plan(opp, profile, fields)
        except Exception:
            return _heuristic_plan(profile, fields)
    return _heuristic_plan(profile, fields)
