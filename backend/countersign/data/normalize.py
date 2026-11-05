"""Transformation rules shared by every source: tags, environments, owners and timestamps."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping

from countersign.domain.resources import Environment
from countersign.domain.rules import (
    HOURS_PER_MONTH,
    PROTECTED_TAGS,
    monthly_from_hourly,
    percentile,
    protected_by,
)
from countersign.security.screening import screen

ENV_KEYS = ("env", "environment", "stage", "tier")
TEAM_KEYS = ("team", "squad", "owner-team")
OWNER_KEYS = ("owner", "owner-email", "contact")
_ENV_VALUES = {
    Environment.PROD: {"prod", "production", "prd", "live"},
    Environment.STAGING: {"staging", "stage", "stg", "preprod", "pre-prod"},
    Environment.DEV: {"dev", "development", "devel"},
    Environment.TEST: {"test", "testing", "qa", "uat"},
    Environment.SANDBOX: {"sandbox", "sbx", "playground", "lab"},
}


def normalize_tags(raw: Mapping[str, str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Lowercase keys (Azure tag names are case-insensitive; AWS's are not, so a collision keeps the first),
    trim values, mask secrets and flag injection attempts. Returns (tags, flags)."""
    tags: dict[str, str] = {}
    flags: set[str] = set()
    for key, value in raw.items():
        k = key.strip().lower()
        if k in tags:
            flags.add("tag_key_collision")
            continue
        screened = screen(str(value).strip())
        if screened.secrets:
            flags.add("secret_redacted")
        if screened.injection:
            flags.add("suspicious_content")
        tags[k] = screened.text
    return tags, tuple(sorted(flags))


def environment(tags: Mapping[str, str]) -> Environment:
    for key in ENV_KEYS:
        value = tags.get(key, "").strip().lower()
        for env, aliases in _ENV_VALUES.items():
            if value in aliases:
                return env
    return Environment.UNKNOWN


def first_tag(tags: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if tags.get(key):
            return tags[key]
    return None


_NAIVE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")


def parse_timestamp(value: object) -> tuple[dt.datetime, bool]:
    """Parse ISO 8601. Returns (aware UTC datetime, was_normalized). Naive values are read as UTC and
    reported, because FOCUS requires an explicit offset. Raises ValueError for anything else."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"not a timestamp: {value!r}")
    text = value.strip()
    normalized = False
    if _NAIVE.match(text):
        text = text.replace(" ", "T") + "+00:00"
        normalized = True
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value!r}")
    return parsed.astimezone(dt.UTC), normalized


__all__ = [
    "HOURS_PER_MONTH",
    "PROTECTED_TAGS",
    "environment",
    "first_tag",
    "monthly_from_hourly",
    "normalize_tags",
    "parse_timestamp",
    "percentile",
    "protected_by",
]
