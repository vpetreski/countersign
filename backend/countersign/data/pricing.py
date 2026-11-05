"""Prices: the public Azure Retail Prices API live, the committed snapshots as the fallback.

AWS on-demand prices come from a snapshot of the official Price List Bulk API files (the us-east-1 EC2 offer
is ~300 MB, too large to fetch per call). Azure prices are fetched live from the public Retail Prices API with
the same filters the snapshot was taken with; when the API is slow, rate-limited or down, the snapshot answers
and the quote says so. Every quote carries its source, its age and the reference it came from.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from countersign.domain.resources import Kind, PriceQuote, Provider, Resource
from countersign.domain.rules import HOURS_PER_MONTH, RIGHTSIZE_TO

AZURE_API = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"


class PriceUnavailableError(LookupError):
    pass


@dataclass(frozen=True)
class _Row:
    unit_price: float
    unit: str  # hour | month | gb-month
    reference: str
    as_of: dt.datetime
    query: str | None = None


Fetcher = Callable[[str], list[dict[str, Any]]]


def azure_fetcher(timeout: float) -> Fetcher:
    def fetch(odata_filter: str) -> list[dict[str, Any]]:
        resp = httpx.get(
            AZURE_API, params={"api-version": AZURE_API_VERSION, "$filter": odata_filter}, timeout=timeout
        )
        resp.raise_for_status()
        items: list[dict[str, Any]] = resp.json().get("Items", [])
        return items

    return fetch


class PriceCatalog:
    def __init__(
        self, fixtures_dir: Path, fetch: Fetcher | None = None, now: Callable[[], dt.datetime] | None = None
    ) -> None:
        self._fetch = fetch
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._aws: dict[tuple[str, str], _Row] = {}
        self._azure: dict[tuple[str, str, str], _Row] = {}
        self._live_cache: dict[tuple[str, str, str], tuple[_Row, dt.datetime]] = {}
        self.snapshot_as_of: dict[str, dt.datetime] = {}
        self._load(fixtures_dir / "pricing")

    def _load(self, folder: Path) -> None:
        aws = json.loads((folder / "aws_price_list_snapshot.json").read_text())
        files = {f["offer"]: f for f in aws["files"]}
        aws_as_of = _ts(aws["captured_at"])
        self.snapshot_as_of["aws"] = aws_as_of
        units = {"Hrs": "hour", "GB-Mo": "gb-month", "GB-month": "gb-month"}
        for item in aws["items"]:
            offer = "AmazonVPC" if item["kind"] == "public_ip" else "AmazonEC2"
            ref = f"{files[offer]['url']} SKU {item['sku']} ({item['description']})"
            self._aws[(item["kind"], item["key"])] = _Row(
                item["price_usd"], units[item["unit"]], ref, aws_as_of
            )
        az = json.loads((folder / "azure_retail_prices_snapshot.json").read_text())
        az_as_of = _ts(az["captured_at"])
        self.snapshot_as_of["azure"] = az_as_of
        for item in az["items"]:
            unit = "hour" if item["unitOfMeasure"] == "1 Hour" else "month"
            ref = f"{AZURE_API} meter {item['meterId']} ({item['productName']} / {item['meterName']})"
            self._azure[(item["kind"], item["key"], item["armRegionName"])] = _Row(
                item["retailPrice"], unit, ref, az_as_of, item["query"]
            )

    # ------------------------------------------------------------------ public API

    def quote(self, resource: Resource, sku: str | None = None, live: bool = True) -> PriceQuote:
        """Monthly run-rate of `resource` at `sku` (default: its current SKU)."""
        sku = sku or resource.sku
        if resource.provider is Provider.AWS:
            return self._quote_aws(resource, sku)
        return self._quote_azure(resource, sku, live)

    def _quote_aws(self, resource: Resource, sku: str) -> PriceQuote:
        kind = {Kind.VM: "instance", Kind.DISK: "volume", Kind.PUBLIC_IP: "public_ip"}[resource.kind]
        key = "in-use" if resource.kind is Kind.PUBLIC_IP else sku
        row = self._aws.get((kind, key))
        if row is None or resource.region != "us-east-1":
            raise PriceUnavailableError(f"no AWS price for {kind} {sku} in {resource.region}")
        monthly = (
            row.unit_price * (resource.size_gb or 0)
            if row.unit == "gb-month"
            else row.unit_price * HOURS_PER_MONTH
        )
        return PriceQuote(
            provider=resource.provider,
            kind=resource.kind,
            sku=sku,
            region=resource.region,
            unit=row.unit,
            unit_price_usd=row.unit_price,
            monthly_usd=round(monthly, 2),
            source="snapshot",
            as_of=row.as_of,
            reference=row.reference,
        )

    def _quote_azure(self, resource: Resource, sku: str, live: bool) -> PriceQuote:
        kind = {Kind.VM: "vm", Kind.DISK: "disk", Kind.PUBLIC_IP: "public_ip"}[resource.kind]
        key = (kind, sku, resource.region)
        row = self._azure.get(key)
        if row is None:
            raise PriceUnavailableError(f"no Azure price for {kind} {sku} in {resource.region}")
        source = "snapshot"
        if live and self._fetch is not None and row.query:
            cached = self._live_cache.get(key)
            if cached and self._now() - cached[1] < dt.timedelta(hours=24):
                row, source = cached[0], "cache"
            else:
                fresh = self._live(row)
                if fresh is not None:
                    self._live_cache[key] = (fresh, self._now())
                    row, source = fresh, "live"
        monthly = row.unit_price * HOURS_PER_MONTH if row.unit == "hour" else row.unit_price
        return PriceQuote(
            provider=resource.provider,
            kind=resource.kind,
            sku=sku,
            region=resource.region,
            unit=row.unit,
            unit_price_usd=row.unit_price,
            monthly_usd=round(monthly, 2),
            source=source,
            as_of=row.as_of,
            reference=row.reference,
        )

    def _live(self, row: _Row) -> _Row | None:
        if self._fetch is None or row.query is None:
            return None
        try:
            items = self._fetch(row.query)
        except httpx.HTTPError, ValueError:
            return None
        if len(items) != 1:  # the filters select exactly one meter; anything else is not trusted
            return None
        item = items[0]
        price = item.get("retailPrice")
        if not isinstance(price, int | float) or price < 0 or item.get("currencyCode") != "USD":
            return None
        ref = f"{AZURE_API} meter {item.get('meterId')} ({item.get('productName')} / {item.get('meterName')})"
        return _Row(float(price), row.unit, ref, self._now(), row.query)

    def rightsize_target(self, resource: Resource) -> str | None:
        return RIGHTSIZE_TO.get((resource.provider, resource.sku))


def _ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
