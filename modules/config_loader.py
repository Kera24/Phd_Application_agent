"""Load and persist the three YAML configs. Single source of truth for paths."""
from __future__ import annotations

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
def profile() -> dict[str, Any]:
    return _load("profile.yaml")


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
