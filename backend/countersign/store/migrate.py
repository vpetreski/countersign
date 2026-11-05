"""Schema management. `upgrade` creates missing tables and records the schema version; the schema is
additive-only so far, and the version gate in /api/health/ready refuses to serve a mismatched database."""

from __future__ import annotations

from countersign.store.db import Database
from countersign.store.schema import SCHEMA_VERSION


def upgrade(db: Database) -> str:
    db.create_all()
    return SCHEMA_VERSION
