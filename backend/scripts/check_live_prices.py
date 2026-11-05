"""Contract check against the public Azure Retail Prices API, run nightly.

Every Azure price the snapshot covers is quoted through the real code path (`PriceCatalog` with the live
fetcher). Two things must hold: the live answer passes the catalog's trust rules (exactly one meter, USD, a
non-negative price), so the quote's source is `live`; and the live price is within 25% of the committed snapshot.
The first failing means the API changed shape and the fetcher needs work; the second means the snapshot is stale
and should be refreshed with `scripts/snapshot_azure_prices.py`. The API is public: no key, no cost.

    uv run python scripts/check_live_prices.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from countersign.data.pricing import PriceCatalog, azure_fetcher
from countersign.domain.resources import Environment, Kind, Provider, Resource, State

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TOLERANCE = 0.25


def resource(kind: str, sku: str, region: str) -> Resource:
    return Resource(
        id=f"contract-{kind}-{sku}-{region}",
        ref=f"contract-{kind}",
        provider=Provider.AZURE,
        kind=Kind(kind),
        name="contract",
        account="contract",
        region=region,
        environment=Environment.DEV,
        team=None,
        owner=None,
        tags={},
        state=State.RUNNING,
        native_state="running",
        sku=sku,
        size_gb=None,
        created_at=None,
        source="contract-check",
        source_ref="scripts/check_live_prices.py",
    )


def main() -> int:
    snapshot = json.loads((FIXTURES / "pricing" / "azure_retail_prices_snapshot.json").read_text())
    live = PriceCatalog(FIXTURES, fetch=azure_fetcher(timeout=15))
    offline = PriceCatalog(FIXTURES)
    problems = []
    for item in snapshot["items"]:
        r = resource(item["kind"], item["key"], item["armRegionName"])
        got = live.quote(r)
        was = offline.quote(r, live=False)
        drift = (
            abs(got.unit_price_usd - was.unit_price_usd) / was.unit_price_usd if was.unit_price_usd else 0.0
        )
        ok = got.source == "live" and drift <= TOLERANCE
        label = f"{item['kind']:9} {item['key'] or '(default)':32} {item['armRegionName']:10}"
        print(
            f"{'ok  ' if ok else 'FAIL'} {label} source={got.source:8} {got.unit_price_usd:<10g} snapshot {was.unit_price_usd:<10g} drift {drift:.1%}"
        )
        if not ok:
            problems.append(label)
    print(f"{len(snapshot['items']) - len(problems)}/{len(snapshot['items'])} prices pass")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
