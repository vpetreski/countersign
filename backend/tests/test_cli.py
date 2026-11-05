from __future__ import annotations

import json
from pathlib import Path

import pytest

from countersign import cli
from countersign.settings import get_settings

pytestmark = pytest.mark.integration


def test_seed_then_ingest_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.sqlite3'}")
    monkeypatch.setenv("COUNTERSIGN_LIVE_PRICES", "false")
    get_settings.cache_clear()
    try:
        assert cli.main(["seed"]) == 0
        assert "changed=True resources=149" in capsys.readouterr().out
        assert cli.main(["ingest"]) == 0
        assert "changed=False" in capsys.readouterr().out
        assert cli.main(["openapi"]) == 0
        doc = json.loads(capsys.readouterr().out)
        assert "/api/runs/{run_id}/decision" in doc["paths"]
    finally:
        get_settings.cache_clear()


def test_the_published_openapi_document_is_current(capsys: pytest.CaptureFixture[str]) -> None:
    """docs/openapi.json is what the API serves; a route or schema change without regenerating it fails here
    (regenerate with `uv run countersign openapi > ../docs/openapi.json`)."""
    assert cli.main(["openapi"]) == 0
    served = json.loads(capsys.readouterr().out)
    published = json.loads((Path(__file__).resolve().parents[2] / "docs" / "openapi.json").read_text())
    assert served == published
