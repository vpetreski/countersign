"""Engine and sessions. One small pool per process: on serverless the pooled (PgBouncer) URL does the
multiplexing, so each function instance keeps at most two connections."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from countersign.store.schema import SCHEMA_VERSION, Base, MetaRow


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url == "sqlite://":
                kwargs["poolclass"] = StaticPool
        else:
            kwargs |= {"pool_size": 2, "max_overflow": 0, "pool_pre_ping": True, "pool_recycle": 280}
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as s:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    def create_all(self) -> None:
        """Create every missing table and record the schema version (`migrate.upgrade` also drops retired ones)."""
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            s.merge(MetaRow(key="schema_version", value=SCHEMA_VERSION))

    def schema_version(self) -> str | None:
        try:
            with self.session() as s:
                row = s.scalar(select(MetaRow).where(MetaRow.key == "schema_version"))
                return row.value if row else None
        except Exception:
            return None


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
