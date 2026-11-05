"""The production guard: development or short secrets never start in production, and a Vercel production
deployment counts as production whatever COUNTERSIGN_ENV says."""

from __future__ import annotations

import pytest

from countersign.api.app import create_app
from countersign.settings import Settings

pytestmark = pytest.mark.unit

STRONG = {name: f"{name}-" + "x" * 40 for name in ("jwt_secret", "capability_secret", "cron_secret")}


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, database_url="sqlite://", live_prices=False, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _no_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("COUNTERSIGN_ENV", raising=False)


def test_production_refuses_development_and_short_secrets() -> None:
    with pytest.raises(RuntimeError, match="development secrets") as err:
        settings(env="production").assert_production_safe()
    for name in ("COUNTERSIGN_JWT_SECRET", "COUNTERSIGN_CAPABILITY_SECRET", "COUNTERSIGN_CRON_SECRET"):
        assert name in str(err.value)
    with pytest.raises(RuntimeError, match="COUNTERSIGN_CRON_SECRET"):
        settings(env="production", **{**STRONG, "cron_secret": "short"}).assert_production_safe()
    settings(env="production", **STRONG).assert_production_safe()


@pytest.mark.parametrize("countersign_env", [None, "development", "test"])
def test_a_vercel_production_deployment_is_production_whatever_countersign_env_says(
    monkeypatch: pytest.MonkeyPatch, countersign_env: str | None
) -> None:
    monkeypatch.setenv("VERCEL_ENV", "production")
    if countersign_env:
        monkeypatch.setenv("COUNTERSIGN_ENV", countersign_env)
    s = settings()
    assert s.env == "production"
    with pytest.raises(RuntimeError, match="development secrets"):
        s.assert_production_safe()
    with pytest.raises(RuntimeError, match="development secrets"):
        create_app(s)  # the app refuses to start, not just the check
    settings(**STRONG).assert_production_safe()


@pytest.mark.parametrize("vercel_env", ["preview", "development"])
def test_other_vercel_environments_keep_the_configured_environment(
    monkeypatch: pytest.MonkeyPatch, vercel_env: str
) -> None:
    monkeypatch.setenv("VERCEL_ENV", vercel_env)
    s = settings()
    assert s.env == "development"
    s.assert_production_safe()
