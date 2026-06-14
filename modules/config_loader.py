"""Load and persist the three YAML configs. Single source of truth for paths."""
from __future__ import annotations

import copy
import functools
from pathlib import Path
from typing import Any

import yaml

# Project root = parent of the `modules/` package directory.
ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _load(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.lru_cache(maxsize=1)
def _profile_base() -> dict[str, Any]:
    return _load("profile.yaml")


def _deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` into `base` (dicts merge, scalars/lists replace)."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v not in (None, ""):
            out[k] = v
    return out


def applicant_overrides() -> dict[str, Any]:
    """The DB-stored applicant profile (empty if none / DB not ready)."""
    try:
        from db import session as dbsession
        from db.models import ApplicantProfile
        with dbsession.session_scope() as s:
            row = s.get(ApplicantProfile, 1)
            return dict(row.data) if row and row.data else {}
    except Exception:
        return {}  # engine not initialised yet, or table absent — fall back to YAML


def profile() -> dict[str, Any]:
    """Candidate profile = YAML base with the DB applicant record merged over it."""
    return _deep_merge(copy.deepcopy(_profile_base()), applicant_overrides())


def save_applicant(data: dict[str, Any]) -> dict[str, Any]:
    """Persist the editable applicant profile (single DB row)."""
    from db import session as dbsession
    from db.models import ApplicantProfile
    with dbsession.session_scope() as s:
        row = s.get(ApplicantProfile, 1)
        if row is None:
            s.add(ApplicantProfile(id=1, data=data))
        else:
            row.data = data
        s.flush()
    return data


def config() -> dict[str, Any]:
    # Not cached: Settings page edits it at runtime.
    return _load("config.yaml")


def email_template() -> dict[str, Any]:
    return _load("email_template.yaml")


def email_templates() -> dict[str, Any]:
    """Plural file used by the LangGraph system (same structure)."""
    return _load("email_templates.yaml")


def save_config(data: dict[str, Any]) -> None:
    with open(CONFIG_DIR / "config.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def abspath(rel_or_key: str) -> Path:
    """Resolve a path from config.paths (by key) or a relative path to ROOT."""
    cfg = config()
    paths = cfg.get("paths", {})
    target = paths.get(rel_or_key, rel_or_key)
    p = Path(target)
    return p if p.is_absolute() else (ROOT / p)


def ensure_dirs() -> None:
    cfg = config()
    for key in ("cache", "pdfs", "uploads"):
        abspath(key).mkdir(parents=True, exist_ok=True)
    abspath("database").parent.mkdir(parents=True, exist_ok=True)
