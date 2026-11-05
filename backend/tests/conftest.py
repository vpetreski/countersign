"""Shared fixtures: settings that never touch the network, an in-memory database, the ingested estate, and
harnesses for the runtime and the HTTP API."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from countersign.agent.runtime import Principal, Role, Runtime
from countersign.api.app import Services, create_app
from countersign.data.estate import ESTATE
from countersign.data.ingest import ingest
from countersign.data.pricing import PriceCatalog
from countersign.models.factory import make_model
from countersign.policy.engine import default_policy
from countersign.settings import BACKEND_ROOT, Settings
from countersign.store.db import Database
from countersign.store.schema import RunRow, WorkspaceRow

FIXTURES = BACKEND_ROOT / "fixtures"
GROUND_TRUTH = json.loads((FIXTURES / "ground_truth.json").read_text())
AS_OF = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 11, 1, 6, 0, tzinfo=dt.UTC)  # six hours after the data's as-of time


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"env": "test", "database_url": "sqlite://", "live_prices": False}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    database = Database("sqlite://")
    database.create_all()
    ESTATE.clear()
    yield database
    ESTATE.clear()
    database.engine.dispose()


@pytest.fixture
def ingested(db: Database, settings: Settings) -> Database:
    ingest(db, settings, now=NOW)
    return db


@pytest.fixture
def fixtures_copy(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, target)
    return target


# ---------------------------------------------------------------- runtime and API harness


class Clock:
    """A controllable UTC clock; starts six hours after the data's as-of time, like the default NOW."""

    def __init__(self, start: dt.datetime | None = None) -> None:
        self.now = start or dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += dt.timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


def principals(ws: str) -> dict[str, Principal]:
    return {
        "viewer": Principal(f"{ws}:viewer", ws, "viewer", Role.VIEWER, "Riley"),
        "operator": Principal(f"{ws}:operator", ws, "operator", Role.OPERATOR, "Sam"),
        "approver": Principal(f"{ws}:approver", ws, "approver", Role.APPROVER, "Alex"),
        "approver2": Principal(f"{ws}:approver2", ws, "approver2", Role.APPROVER, "Jordan"),
    }


@dataclass
class Harness:
    db: Database
    settings: Settings
    runtime: Runtime
    clock: Clock
    ws: str
    who: dict[str, Principal]

    def run(self, run_id: str) -> RunRow:
        with self.db.session() as s:
            row = s.get(RunRow, run_id)
            assert row is not None
            return row

    def start(
        self,
        request: str = "Find idle development VMs and unattached disks and propose savings.",
        faults: list[str] | None = None,
    ) -> str:
        return self.runtime.create(self.who["operator"], request, faults or [])

    def drive(self, run_id: str, until: set[str], limit: int = 60) -> RunRow:
        for _ in range(limit):
            row = self.run(run_id)
            if row.status in until:
                return row
            self.runtime.advance(self.who["operator"], run_id)
        return self.run(run_id)

    def plan(self, run_id: str) -> dict[str, Any]:
        plan = self.run(run_id).state["plan"]
        assert plan is not None
        return plan

    def approve_all(self, run_id: str, *approvers: str) -> None:
        plan = self.plan(run_id)
        decisions = {a["id"]: "approve" for a in plan["actions"]}
        for name in approvers or ("approver", "approver2"):
            if self.run(run_id).status != "awaiting_approval":
                break
            self.runtime.decide(self.who[name], run_id, plan["hash"], decisions, None)


WAITING = {"awaiting_approval", "awaiting_input"}
FINAL = {
    "completed",
    "completed_with_failures",
    "failed",
    "rejected",
    "cancelled",
    "budget_exhausted",
    "expired",
}


def make_harness(
    db: Database, clock: Clock, model_factory: Callable[..., Any] = make_model, **overrides: object
) -> Harness:
    settings = make_settings(**overrides)
    runtime = Runtime(
        db,
        settings,
        model_factory=model_factory,
        catalog=PriceCatalog(FIXTURES),
        policy=default_policy(),
        clock=clock,
        sleep=lambda _s: None,
    )
    runtime.executor._sleep = lambda _s: None
    ws = "ws_test"
    with db.session() as s:
        if s.get(WorkspaceRow, ws) is None:
            s.add(WorkspaceRow(id=ws, created_at=clock(), expires_at=clock() + dt.timedelta(days=3)))
    return Harness(db, settings, runtime, clock, ws, principals(ws))


@pytest.fixture
def harness(ingested: Database, clock: Clock) -> Harness:
    return make_harness(ingested, clock)


@dataclass
class Api:
    client: TestClient
    services: Services
    clock: Clock

    def workspace(self) -> dict[str, str]:
        resp = self.client.post("/api/workspaces")
        assert resp.status_code == 201, resp.text
        return dict(resp.json()["tokens"])

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def drive(self, run_id: str, token: str, until: set[str], limit: int = 60) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for _ in range(limit):
            body = self.client.get(f"/api/runs/{run_id}", headers=self.auth(token)).json()
            if body["status"] in until:
                return body
            resp = self.client.post(f"/api/runs/{run_id}/advance", headers=self.auth(token))
            assert resp.status_code == 200, resp.text
        return body


def make_api(clock: Clock, **overrides: object) -> Api:
    settings = make_settings(**overrides)
    services = Services(settings, clock=clock)
    services.runtime.sleep = lambda _s: None
    services.runtime.executor._sleep = lambda _s: None
    services.db.create_all()
    ESTATE.clear()
    ingest(services.db, settings, now=clock())
    return Api(TestClient(create_app(settings, services), raise_server_exceptions=False), services, clock)


@pytest.fixture
def api(clock: Clock) -> Iterator[Api]:
    a = make_api(clock)
    yield a
    ESTATE.clear()
    a.services.db.engine.dispose()
