"""Runtime configuration. Every value has a safe default so the app runs keyless and offline."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COUNTERSIGN_", env_file=".env", extra="ignore", populate_by_name=True
    )

    env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        default=f"sqlite:///{BACKEND_ROOT / 'countersign.sqlite3'}", validation_alias="DATABASE_URL"
    )
    fixtures_dir: Path = BACKEND_ROOT / "fixtures"

    # Secrets. Development defaults are deliberately obvious; production refuses to start with them.
    jwt_secret: SecretStr = SecretStr("dev-only-jwt-secret-change-me-0000000000")
    capability_secret: SecretStr = SecretStr("dev-only-capability-secret-change-me-000")
    cron_secret: SecretStr = SecretStr("dev-only-cron-secret")

    # The model. No key means the offline model: this project never spends money unless a key is set.
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model_provider: Literal["auto", "offline", "claude"] = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # Adaptive thinking counts against max_tokens, so a turn needs room to think and then call tools.
    model_max_tokens: int = Field(default=16_000, ge=1024, le=128_000)

    # Agent budgets (per run).
    max_model_turns: int = 16
    max_tool_calls: int = 60
    max_input_tokens: int = 400_000
    max_wall_seconds: int = 600
    tool_timeout_seconds: float = 4.0
    model_timeout_seconds: float = 90.0
    max_plan_revisions: int = 3

    # Data freshness service levels, in hours.
    inventory_freshness_hours: float = 30.0
    metrics_freshness_hours: float = 54.0
    prices_freshness_hours: float = 48.0

    # Live prices: the public Azure Retail Prices API. Off in tests; the snapshot is the fallback.
    live_prices: bool = True
    live_price_timeout_seconds: float = 3.0

    # Demo workspaces and abuse limits.
    workspace_ttl_hours: int = 72
    rate_limit_runs_per_hour: int = 30
    rate_limit_workspaces_per_hour: int = 60
    token_ttl_hours: int = 12

    @field_validator("database_url")
    @classmethod
    def _driver(cls, url: str) -> str:
        # Neon and most hosts hand out postgres:// URLs; SQLAlchemy needs the psycopg 3 driver named.
        if url.startswith("postgres://"):
            url = "postgresql://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url

    @property
    def model_mode(self) -> Literal["offline", "claude"]:
        if self.model_provider == "claude" or (self.model_provider == "auto" and self.anthropic_api_key):
            return "claude"
        return "offline"

    def assert_production_safe(self) -> None:
        if self.env != "production":
            return
        weak = [
            name
            for name, value in (
                ("COUNTERSIGN_JWT_SECRET", self.jwt_secret),
                ("COUNTERSIGN_CAPABILITY_SECRET", self.capability_secret),
                ("COUNTERSIGN_CRON_SECRET", self.cron_secret),
            )
            if value.get_secret_value().startswith("dev-only") or len(value.get_secret_value()) < 32
        ]
        if weak:
            raise RuntimeError(f"refusing to start in production with development secrets: {', '.join(weak)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
