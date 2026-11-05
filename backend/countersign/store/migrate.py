"""Schema management. `upgrade` brings a database to SCHEMA_VERSION: it drops the tables an earlier version
created and this one no longer has, creates any table that is missing and records the version. Every step is
idempotent, so it is safe to run on every deploy; the version gate in /api/health/ready refuses to serve a
database at another version."""

from __future__ import annotations

from sqlalchemy import text

from countersign.store.db import Database
from countersign.store.schema import DROPPED_TABLES, SCHEMA_VERSION


def upgrade(db: Database) -> str:
    with db.engine.begin() as conn:
        for table in DROPPED_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    db.create_all()
    return SCHEMA_VERSION
