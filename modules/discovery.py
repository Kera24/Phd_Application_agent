"""Opportunity discovery in three modes.

A. Manual (reactive)  — handled by the dashboard textarea -> parser.classify_and_store.
B. Advertised-position search — Tavily queries over job boards / university pages.
C. Proactive lab prospecting — independent lab/professor discovery for speculative
   outreach; only in countries where PhDs are funded by default (Rule 0).

Dedup against existing opportunities/professors by (university + professor_email)
or fuzzy title match before inserting.
"""
from __future__ import annotations

import os
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from db.models import Opportunity, Professor
from modules import config_loader, paper_apis


# --- Tavily wrapper ----------------------------------------------------------

def tavily_search(query: str, max_results: int = 8) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        resp = client.search(query=query, max_results=max_results,
                             search_depth="advanced")
        return resp.get("results", [])
    except Exception:
        return []


# --- Dedup -------------------------------------------------------------------

def is_duplicate_opportunity(session: Session, university: Optional[str],
                             professor_email: Optional[str],
                             title: Optional[str]) -> bool:
    if professor_email:
        if session.query(Opportunity).filter(
            Opportunity.professor_email == professor_email
        ).first():
            return True
    candidates = session.query(Opportunity)
    if university:
        candidates = candidates.filter(Opportunity.university == university)
    for opp in candidates.all():
        if title and opp.position_title and fuzz.token_sort_ratio(title, opp.position_title) > 88:
            return True
    return False


def is_duplicate_professor(session: Session, email: Optional[str],
                           name: str, university: Optional[str]) -> Optional[Professor]:
    if email:
        prof = session.query(Professor).filter_by(email=email).first()
        if prof:
            return prof
    for prof in session.query(Professor).all():
        if fuzz.token_sort_ratio(prof.name, name) > 90 and (
            not university or not prof.university
            or fuzz.partial_ratio(prof.university, university) > 80
        ):
            return prof
    return None


# --- Mode B: advertised-position search -------------------------------------

def advertised_queries() -> list[str]:
    cfg = config_loader.config()
    fields = cfg.get("target_fields", [])
    countries = cfg.get("target_countries", [])
    keywords = cfg.get("advertised_search_keywords", [])
    boards = cfg.get("advertised_job_boards", [])
    queries = []
    for field in fields:
        for country in countries:
            kw = keywords[0] if keywords else "fully funded PhD"
            queries.append(f'{kw} {field} {country}')
    # A couple of board-scoped queries per field.
    for field in fields[:4]:
        for board in boards[:2]:
            queries.append(f'site:{board} PhD {field}')
    return queries


def search_advertised(max_per_query: int = 5) -> list[dict]:
    """Return raw Tavily hits across advertised queries (caller parses each)."""
    seen_urls = set()
    hits = []
    for q in advertised_queries():
        for r in tavily_search(q, max_results=max_per_query):
            url = r.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                hits.append({"query": q, **r})
    return hits


# --- Mode C: proactive lab prospecting --------------------------------------

def prospect_field_country(field: str, country: str,
                           max_results: int = 6) -> dict:
    """Discover labs/professors for one field x country.

    Returns {'country_funded': bool, 'labs': [tavily hits], 'authors': [s2 authors]}.
    Only proceeds for countries funded by default (Rule 0 for speculative).
    """
    cfg = config_loader.config()
    funded_countries = [c.lower() for c in cfg.get("default_funded_countries", [])]
    country_funded = country.strip().lower() in funded_countries

    labs, authors = [], []
    if country_funded:
        for template in (
            f'{field} lab {country} university',
            f'{field} research group {country}',
        ):
            labs.extend(tavily_search(template, max_results=max_results))
        # Active authors in the field (recent, productive).
        authors = paper_apis.search_authors(f"{field}", limit=max_results)
    return {"country_funded": country_funded, "labs": labs, "authors": authors}


def funding_likelihood_evidence(text: str) -> list[str]:
    """Scan text for known funding-scheme signals (ERC/DFG/EPSRC/SNSF/ARC...)."""
    cfg = config_loader.config()
    signals = cfg.get("funding_scheme_signals", [])
    found = []
    tl = (text or "").lower()
    for s in signals:
        if s.lower() in tl:
            found.append(s)
    return found
