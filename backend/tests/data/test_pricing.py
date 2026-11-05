from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest
import respx

from countersign.data.pricing import AZURE_API, PriceCatalog, PriceUnavailableError, azure_fetcher
from countersign.domain.resources import Environment, Kind, Provider, Resource, State
from tests.conftest import FIXTURES

pytestmark = pytest.mark.unit


def vm(provider: Provider, sku: str, region: str) -> Resource:
    return Resource(
        id=f"{provider}-vm",
        ref="vm-1",
        provider=provider,
        kind=Kind.VM,
        name="vm-1",
        account="a",
        region=region,
        environment=Environment.DEV,
        team=None,
        owner=None,
        tags={},
        state=State.RUNNING,
        native_state="running",
        sku=sku,
        created_at=None,
        source="test",
        source_ref="test",
    )


class Clock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 11, 1, 6, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now


def test_aws_quotes_come_from_the_snapshot_with_provenance() -> None:
    q = PriceCatalog(FIXTURES).quote(vm(Provider.AWS, "m5.xlarge", "us-east-1"))
    assert q.source == "snapshot"
    assert q.unit == "hour"
    assert q.monthly_usd == round(q.unit_price_usd * 730, 2)
    assert "AmazonEC2" in q.reference


def test_unknown_sku_or_region_is_unavailable_not_guessed() -> None:
    catalog = PriceCatalog(FIXTURES)
    with pytest.raises(PriceUnavailableError):
        catalog.quote(vm(Provider.AWS, "m5.xlarge", "eu-west-1"))
    with pytest.raises(PriceUnavailableError):
        catalog.quote(vm(Provider.AZURE, "Standard_X99", "eastus"))


def _azure_vm() -> Resource:
    return vm(Provider.AZURE, "Standard_D4s_v5", "eastus")


def test_azure_live_answer_is_used_and_cached_for_a_day() -> None:
    calls: list[str] = []
    clock = Clock()

    def fetch(f: str) -> list[dict[str, Any]]:
        calls.append(f)
        return [
            {"retailPrice": 0.2, "currencyCode": "USD", "meterId": "m", "productName": "p", "meterName": "n"}
        ]

    catalog = PriceCatalog(FIXTURES, fetch=fetch, now=clock)
    first = catalog.quote(_azure_vm())
    second = catalog.quote(_azure_vm())
    clock.now += dt.timedelta(hours=25)
    third = catalog.quote(_azure_vm())
    assert (first.source, second.source, third.source) == ("live", "cache", "live")
    assert first.monthly_usd == 146.0
    assert len(calls) == 2


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"retailPrice": 0.2, "currencyCode": "USD"}, {"retailPrice": 0.3, "currencyCode": "USD"}],
        [{"retailPrice": 0.2, "currencyCode": "EUR"}],
        [{"retailPrice": -1, "currencyCode": "USD"}],
        [{"retailPrice": "0.2", "currencyCode": "USD"}],
    ],
)
def test_untrustworthy_live_answers_fall_back_to_the_snapshot(items: list[dict[str, Any]]) -> None:
    catalog = PriceCatalog(FIXTURES, fetch=lambda _f: items)
    assert catalog.quote(_azure_vm()).source == "snapshot"


@respx.mock
def test_live_fetcher_speaks_the_retail_prices_api_and_survives_its_failures() -> None:
    route = respx.get(AZURE_API).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"Items": []})]
    )
    catalog = PriceCatalog(FIXTURES, fetch=azure_fetcher(timeout=1))
    assert catalog.quote(_azure_vm()).source == "snapshot"  # throttled
    assert catalog.quote(_azure_vm()).source == "snapshot"  # empty
    request = route.calls[0].request
    assert request.url.params["api-version"] == "2023-01-01-preview"
    assert "armSkuName eq 'Standard_D4s_v5'" in request.url.params["$filter"]


def test_live_lookup_can_be_switched_off() -> None:
    catalog = PriceCatalog(FIXTURES, fetch=lambda _f: pytest.fail("no network expected"))
    assert catalog.quote(_azure_vm(), live=False).source == "snapshot"


def test_rightsize_targets_are_one_size_down() -> None:
    catalog = PriceCatalog(FIXTURES)
    big = catalog.quote(vm(Provider.AWS, "m5.xlarge", "us-east-1"))
    target = catalog.rightsize_target(vm(Provider.AWS, "m5.xlarge", "us-east-1"))
    assert target == "m6i.large"
    small = catalog.quote(vm(Provider.AWS, "m5.xlarge", "us-east-1"), sku=target)
    assert small.monthly_usd < big.monthly_usd
