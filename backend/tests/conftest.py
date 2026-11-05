"""Shared fixtures: settings that never touch the network, an in-memory database and the ingested estate."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from countersign.data.estate import ESTATE
from countersign.data.ingest import ingest
from countersign.settings import BACKEND_ROOT, Settings
from countersign.store.db import Database

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
