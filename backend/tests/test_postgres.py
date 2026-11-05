"""The workflow on PostgreSQL, where concurrency is real (CI's postgres job; skipped without a database).

SQLite in the other tests runs on one connection, so it cannot show two workers racing. Here two threads
advance the same run at the same moment: the compare-and-set lease lets exactly one call the model."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select, text

from countersign.agent.runtime import RunConflictError
from countersign.audit import chain
from countersign.data.estate import ESTATE
from countersign.data.ingest import ingest
from countersign.models.offline import ReferencePolicy
from countersign.store.db import Database
from countersign.store.schema import Base, ExecutionRow
from tests.conftest import FINAL, NOW, WAITING, Clock, make_harness, make_settings

URL = os.environ.get("COUNTERSIGN_TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not URL, reason="set COUNTERSIGN_TEST_DATABASE_URL to a disposable PostgreSQL database"
    ),
]


@pytest.fixture
def pg() -> Iterator[Database]:
    db = Database(make_settings(database_url=URL).database_url)
    assert db.is_postgres
    Base.metadata.drop_all(db.engine)
    db.create_all()
    ESTATE.clear()
    ingest(db, make_settings(), now=NOW)
    yield db
    ESTATE.clear()
    Base.metadata.drop_all(db.engine)
    db.engine.dispose()


def test_the_critical_workflow_on_postgres(pg: Database) -> None:
    h = make_harness(pg, Clock())
    run_id = h.start()
    assert h.drive(run_id, WAITING | FINAL).status == "awaiting_approval"
    h.approve_all(run_id)
    row = h.drive(run_id, FINAL)
    assert row.status == "completed"
    with pg.session() as s:
        effects = s.scalar(select(func.count()).select_from(ExecutionRow))
        verified = chain.verify(s, h.ws)
        tz = s.execute(
            text(
                "select data_type from information_schema.columns where table_name='runs' and column_name='created_at'"
            )
        ).scalar()
    assert effects == sum(len(a["steps"]) for a in row.state["plan"]["actions"])
    assert verified.valid and verified.entries > 5
    assert tz == "timestamp with time zone"


def test_two_workers_racing_on_one_run_call_the_model_once(pg: Database) -> None:
    """FM-13 with real concurrency: both threads pass the barrier together; the lease admits one."""
    calls: list[int] = []
    barrier = threading.Barrier(2)

    class Slow(ReferencePolicy):
        def complete(self, request: Any) -> Any:
            calls.append(1)
            return super().complete(request)

    h = make_harness(pg, Clock(), model_factory=lambda state, _s: Slow(seed=state.seed))
    run_id = h.start()
    h.runtime.advance(h.who["operator"], run_id)  # created -> planning (no model call)
    outcomes: list[str] = []

    def worker() -> None:
        barrier.wait()
        try:
            h.runtime.advance(h.who["operator"], run_id)
            outcomes.append("advanced")
        except RunConflictError:
            outcomes.append("refused")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) in (["advanced", "refused"], ["advanced", "advanced"])
    assert len(calls) == len([o for o in outcomes if o == "advanced"])  # never two calls for one tick
    assert h.run(run_id).version == 1 + len(calls)
