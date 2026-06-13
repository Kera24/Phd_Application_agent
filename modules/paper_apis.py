"""Verified paper retrieval: Semantic Scholar (primary), arXiv (secondary),
DBLP (fallback).

Non-Negotiable Rule 3: every paper cited must come from a verified API result
with title/year/venue stored. These functions return only what the APIs return;
nothing here is ever invented.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from modules import config_loader

S2_BASE = "https://api.semanticscholar.org/graph/v1"
ARXIV_BASE = "http://export.arxiv.org/api/query"
DBLP_BASE = "https://dblp.org/search/publ/api"

_last_call = 0.0


def _ua() -> str:
    return config_loader.config().get("http", {}).get("user_agent", "ScholarReach/1.0")


def _throttle() -> None:
    global _last_call
    rate = float(config_loader.config().get("http", {}).get("rate_limit_seconds", 1.0))
    elapsed = time.time() - _last_call
    if elapsed < rate:
        time.sleep(rate - elapsed)
    _last_call = time.time()


def _paper(title, year, venue, url, abstract) -> dict:
    return {
        "title": (title or "").strip(),
        "year": year,
        "venue": (venue or "").strip(),
        "url": url or "",
        "abstract": (abstract or "").strip(),
    }


# --- Semantic Scholar --------------------------------------------------------

def semantic_scholar_recent(name: str, affiliation: str = "", limit: int = 5) -> list[dict]:
    """Find the author and return their most recent papers."""
    _throttle()
    headers = {"User-Agent": _ua()}
    try:
        r = requests.get(
            f"{S2_BASE}/author/search",
            params={"query": name, "fields": "name,affiliations,paperCount"},
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        authors = r.json().get("data", [])
    except Exception:
        return []
    if not authors:
        return []

    # Prefer an author whose affiliation matches if we have one.
    author = authors[0]
    if affiliation:
        aff_l = affiliation.lower()
        for a in authors:
            affs = " ".join(a.get("affiliations") or []).lower()
            if aff_l[:12] in affs:
                author = a
                break

    _throttle()
    try:
        r = requests.get(
            f"{S2_BASE}/author/{author['authorId']}/papers",
            params={"fields": "title,year,venue,abstract,externalIds,url", "limit": 100},
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        papers = r.json().get("data", [])
    except Exception:
        return []

    papers = [p for p in papers if p.get("title")]
    papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    out = []
    for p in papers[:limit]:
        ext = p.get("externalIds") or {}
        url = p.get("url") or (
            f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv") else ""
        )
        out.append(_paper(p.get("title"), p.get("year"), p.get("venue"), url, p.get("abstract")))
    return out


# --- arXiv -------------------------------------------------------------------

def arxiv_recent(name: str, limit: int = 5) -> list[dict]:
    _throttle()
    try:
        r = requests.get(
            ARXIV_BASE,
            params={
                "search_query": f'au:"{name}"',
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": limit,
            },
            headers={"User-Agent": _ua()},
            timeout=20,
        )
        r.raise_for_status()
    except Exception:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []
    out = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        url = entry.findtext("a:id", default="", namespaces=ns) or ""
        abstract = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        out.append(_paper(title, year, "arXiv", url, abstract))
    return out


# --- DBLP --------------------------------------------------------------------

def dblp_recent(name: str, limit: int = 5) -> list[dict]:
    _throttle()
    try:
        r = requests.get(
            DBLP_BASE,
            params={"q": name, "format": "json", "h": 30},
            headers={"User-Agent": _ua()},
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    except Exception:
        return []
    rows = []
    for h in hits:
        info = h.get("info", {})
        year = int(info["year"]) if str(info.get("year", "")).isdigit() else None
        rows.append(
            _paper(info.get("title"), year, info.get("venue"), info.get("url") or info.get("ee"), "")
        )
    rows.sort(key=lambda p: p["year"] or 0, reverse=True)
    return rows[:limit]


def recent_papers(name: str, affiliation: str = "", limit: int = 5) -> tuple[list[dict], str]:
    """Cascade through providers. Returns (papers, source_used)."""
    papers = semantic_scholar_recent(name, affiliation, limit)
    if papers:
        return papers, "semantic_scholar"
    papers = arxiv_recent(name, limit)
    if papers:
        return papers, "arxiv"
    papers = dblp_recent(name, limit)
    if papers:
        return papers, "dblp"
    return [], "none"


def search_authors(query: str, limit: int = 10) -> list[dict]:
    """Used by prospecting: highly active authors in a field."""
    _throttle()
    try:
        r = requests.get(
            f"{S2_BASE}/author/search",
            params={"query": query, "fields": "name,affiliations,paperCount,hIndex", "limit": limit},
            headers={"User-Agent": _ua()},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception:
        return []
