"""Emit the Supabase/PostgreSQL migration SQL from the SQLAlchemy models.

Usage:
    python -m scripts.export_schema        # writes migrations/001_init.sql
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from db.models import Base  # noqa: E402

HEADER = """\
-- ScholarReach schema -- Supabase/PostgreSQL
-- Generated from db/models.py by scripts/export_schema.py. Apply via the
-- Supabase SQL editor or:  psql $DATABASE_URL -f migrations/001_init.sql
-- LangGraph checkpoint tables are created separately by PostgresSaver.setup().
"""


def main() -> None:
    dialect = postgresql.dialect()
    parts = [HEADER]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
        parts.append(ddl + ";\n")
    out = ROOT / "migrations" / "001_init.sql"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out} ({len(Base.metadata.sorted_tables)} tables)")


if __name__ == "__main__":
    main()
