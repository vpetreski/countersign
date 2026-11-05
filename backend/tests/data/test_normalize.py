from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from countersign.data.normalize import environment, first_tag, normalize_tags, parse_timestamp
from countersign.domain.resources import Environment
from countersign.domain.rules import monthly_from_hourly, percentile, protected_by

pytestmark = pytest.mark.unit


def test_tag_keys_are_lowercased_and_collisions_flagged() -> None:
    tags, flags = normalize_tags({"Environment": "Dev", "environment": "prod", " Team ": " data "})
    assert tags == {"environment": "Dev", "team": "data"}
    assert flags == ("tag_key_collision",)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Production", Environment.PROD),
        ("prd", Environment.PROD),
        ("development", Environment.DEV),
        ("STG", Environment.STAGING),
        ("qa", Environment.TEST),
        ("playground", Environment.SANDBOX),
        ("", Environment.UNKNOWN),
        ("blue", Environment.UNKNOWN),
    ],
)
def test_environment_aliases(value: str, expected: Environment) -> None:
    assert environment({"env": value}) is expected


def test_environment_key_precedence_and_first_tag() -> None:
    assert environment({"stage": "prod", "env": "dev"}) is Environment.DEV
    assert first_tag({"owner-email": "a@b.c", "contact": "x"}, ("owner", "owner-email", "contact")) == "a@b.c"
    assert first_tag({}, ("owner",)) is None


def test_protected_tags_are_case_insensitive_in_value() -> None:
    assert protected_by({"lifecycle": "Keep"}) == "lifecycle=Keep"
    assert protected_by({"compliance": "legal-hold"}) == "compliance=legal-hold"
    assert protected_by({"lifecycle": "temporary"}) is None


def test_timestamps_are_utc_and_naive_values_reported() -> None:
    assert parse_timestamp("2026-10-03T00:00:00Z") == (dt.datetime(2026, 10, 3, tzinfo=dt.UTC), False)
    assert parse_timestamp("2026-10-03 05:00:00") == (dt.datetime(2026, 10, 3, 5, tzinfo=dt.UTC), True)
    assert parse_timestamp("2026-10-03T05:00:00-05:00")[0] == dt.datetime(2026, 10, 3, 10, tzinfo=dt.UTC)
    for bad in ("not-a-date", "", None, 1730000000):
        with pytest.raises(ValueError, match=r"timestamp|isoformat"):
            parse_timestamp(bad)


def test_monthly_uses_the_730_hour_convention() -> None:
    assert monthly_from_hourly(0.192) == 140.16


def test_percentile_nearest_rank_examples() -> None:
    assert percentile([5, 1, 3, 2, 4], 0.95) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([7.5], 0.95) == 7.5
    with pytest.raises(ValueError, match="empty"):
        percentile([], 0.5)
    with pytest.raises(ValueError, match="range"):
        percentile([1], 0)


@given(
    st.lists(st.floats(min_value=0, max_value=100, allow_nan=False), min_size=1, max_size=60),
    st.floats(min_value=0.01, max_value=1),
)
def test_percentile_is_an_observed_value_with_at_least_q_below_or_equal(
    values: list[float], q: float
) -> None:
    p = percentile(values, q)
    assert p in values
    share = sum(v <= p for v in values) / len(values)
    assert share >= q - 1e-9
    assert min(values) <= p <= max(values)
