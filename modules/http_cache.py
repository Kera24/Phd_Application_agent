"""Polite, cached HTTP GET with robots.txt respect and rate limiting.

Non-Negotiable Rule 6: respect robots.txt and rate limits; cache aggressively.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser as robotparser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from modules import config_loader

_last_request_ts = 0.0
_robots_cache: dict[str, robotparser.RobotFileParser] = {}


def _cache_path(url: str) -> Path:
    cache_dir = config_loader.abspath("cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"http_{key}.json"


def _allowed_by_robots(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots can't be fetched, be conservative but not blocking.
            rp = None
        _robots_cache[base] = rp  # type: ignore[assignment]
    if rp is None:
        return True
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


def get(url: str, *, force_refresh: bool = False) -> Optional[str]:
    """Fetch a URL respecting cache TTL, rate limit, and robots.txt.

    Returns response text or None when fetching is disallowed/failed.
    """
    global _last_request_ts
    cfg = config_loader.config()
    http = cfg.get("http", {})
    user_agent = http.get("user_agent", "ScholarReach/1.0")
    ttl_hours = float(http.get("cache_ttl_hours", 168))
    rate_limit = float(http.get("rate_limit_seconds", 1.0))

    cache_file = _cache_path(url)
    if not force_refresh and cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < ttl_hours:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))["text"]
            except Exception:
                pass

    if http.get("respect_robots_txt", True) and not _allowed_by_robots(url, user_agent):
        return None

    # Rate limit.
    elapsed = time.time() - _last_request_ts
    if elapsed < rate_limit:
        time.sleep(rate_limit - elapsed)
    _last_request_ts = time.time()

    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=20)
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return None

    try:
        cache_file.write_text(
            json.dumps({"url": url, "text": text}), encoding="utf-8"
        )
    except Exception:
        pass
    return text
