"""Checkpointer factory.

Follows the database choice:
  * DATABASE_URL is Postgres  -> PostgresSaver (if langgraph-checkpoint-postgres installed)
  * otherwise                 -> SqliteSaver on a local file (persistent across restarts)
  * tests                     -> MemorySaver (pass kind='memory')
"""
from __future__ import annotations

import os
import sqlite3

from modules import config_loader


def build_checkpointer(kind: str | None = None):
    """Return a checkpointer instance appropriate for the environment."""
    url = os.environ.get("DATABASE_URL", "")
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    if url.startswith("postgres"):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection
            from psycopg.rows import dict_row

            # Long-lived connection (from_conn_string is a context manager whose
            # connection dies as soon as the manager is garbage-collected).
            conn = Connection.connect(url, autocommit=True, prepare_threshold=0,
                                      row_factory=dict_row)
            saver = PostgresSaver(conn)
            saver.setup()
            return saver
        except Exception:
            # Fall through to SQLite if the Postgres saver isn't available.
            pass

    from langgraph.checkpoint.sqlite import SqliteSaver
    path = config_loader.abspath("checkpoint_sqlite_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn)
