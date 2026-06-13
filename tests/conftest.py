"""Pytest fixtures: isolated DB + path setup. No network, no real API keys."""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import session as dbsession  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_from_real_db(monkeypatch):
    # Tests must be hermetic: never let a configured Supabase/Postgres URL
    # leak in — always use the per-test SQLite file.
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture()
def db(tmp_path, _isolate_from_real_db):
    db_path = tmp_path / "test.db"
    dbsession.init_engine(str(db_path))
    return dbsession
