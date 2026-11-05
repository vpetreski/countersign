"""Snapshot the Azure Retail Prices API rows this project prices against.

The Azure Retail Prices API (https://prices.azure.com/api/retail/prices) is public and needs no credentials.
The live pricing tool queries it at run time; this snapshot is the committed fallback for when it is down,
slow or rate-limited, and it records exactly which meter each price came from.

    uv run python scripts/snapshot_azure_prices.py
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import httpx

API = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "pricing" / "azure_retail_prices_snapshot.json"

REGIONS = ["eastus", "westeurope"]
VM_SIZES = {
    # armSkuName: (productName, meterName) of the Linux on-demand meter
    "Standard_D2s_v5": ("Virtual Machines Dsv5 Series", "D2s v5"),
    "Standard_D4s_v5": ("Virtual Machines Dsv5 Series", "D4s v5"),
    "Standard_E4s_v5": ("Virtual Machines Esv5 Series", "E4s v5"),
    "Standard_B2ms": ("Virtual Machines BS Series", "B2ms"),
}
DISKS = {
    "P10": ("Premium SSD Managed Disks", "P10 LRS Disk"),
    "P20": ("Premium SSD Managed Disks", "P20 LRS Disk"),
    "E10": ("Standard SSD Managed Disks", "E10 LRS Disk"),
    "E20": ("Standard SSD Managed Disks", "E20 LRS Disk"),
}
PUBLIC_IP = ("IP Addresses", "Standard IPv4 Static Public IP")


def query(client: httpx.Client, flt: str) -> list[dict]:
    items: list[dict] = []
    url: str | None = API
    params: dict[str, str] | None = {"api-version": API_VERSION, "$filter": flt}
    while url:
        for attempt in range(5):
            resp = client.get(url, params=params)
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("retry-after", "10")))
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError(f"rate limited: {flt}")
        body = resp.json()
        items.extend(body.get("Items", []))
        url, params = body.get("NextPageLink"), None
    return items


def main() -> None:
    rows: list[dict] = []
    with httpx.Client(timeout=30) as client:
        for region in REGIONS:
            for arm_sku, (product, meter) in VM_SIZES.items():
                flt = (
                    f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' and armSkuName eq '{arm_sku}' "
                    f"and priceType eq 'Consumption' and productName eq '{product}' and meterName eq '{meter}'"
                )
                rows += [{"kind": "vm", "key": arm_sku, "query": flt, **i} for i in query(client, flt)]
            for tier, (product, meter) in DISKS.items():
                flt = (
                    f"serviceName eq 'Storage' and armRegionName eq '{region}' and priceType eq 'Consumption' "
                    f"and productName eq '{product}' and meterName eq '{meter}'"
                )
                rows += [{"kind": "disk", "key": tier, "query": flt, **i} for i in query(client, flt)]
            flt = (
                f"serviceName eq 'Virtual Network' and armRegionName eq '{region}' and priceType eq 'Consumption' "
                f"and productName eq '{PUBLIC_IP[0]}' and meterName eq '{PUBLIC_IP[1]}'"
            )
            rows += [{"kind": "public_ip", "key": "Standard", "query": flt, **i} for i in query(client, flt)]
    snapshot = {
        "source": API,
        "api_version": API_VERSION,
        "captured_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "note": "Linux on-demand (Consumption) meters; one row per (kind, key, region). Live tool queries use the same filters.",
        "items": sorted(rows, key=lambda r: (r["kind"], r["key"], r["armRegionName"])),
    }
    OUT.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
    print(f"{len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()
