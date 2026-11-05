"""Snapshot the AWS on-demand prices this project prices against, from the official Price List Bulk API.

The Bulk API files are public. The us-east-1 EC2 offer is about 300 MB as CSV, far too large for a live
tool call, so this script streams it once and keeps only the rows the demo needs (Linux, shared tenancy,
on-demand compute; EBS storage) plus the small VPC offer for public IPv4. Every row keeps its SKU, rate code
and description, and the snapshot records the offer version, publication date and the file's SHA-256.

    uv run python scripts/snapshot_aws_prices.py
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

import httpx

BASE = "https://pricing.us-east-1.amazonaws.com"
REGION = "us-east-1"
INSTANCE_TYPES = {"m5.xlarge", "m6i.xlarge", "m6i.large", "c6i.xlarge", "t3.medium"}
VOLUME_USAGE = {"EBS:VolumeUsage.gp3": "gp3", "EBS:VolumeUsage.gp2": "gp2", "EBS:VolumeUsage.st1": "st1"}
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "pricing" / "aws_price_list_snapshot.json"


def current_url(client: httpx.Client, offer: str) -> tuple[str, str]:
    index = client.get(f"{BASE}/offers/v1.0/aws/{offer}/current/region_index.json").raise_for_status().json()
    region = index["regions"][REGION]
    return BASE + region["currentVersionUrl"].replace("index.json", "index.csv"), index["publicationDate"]


def main() -> None:
    rows: list[dict] = []
    files: list[dict] = []
    with httpx.Client(timeout=600, follow_redirects=True) as client:
        for offer in ("AmazonEC2", "AmazonVPC"):
            url, published = current_url(client, offer)
            digest = hashlib.sha256()
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                lines = (line for line in _lines(resp, digest))
                preamble = [next(lines) for _ in range(5)]
                reader = csv.DictReader(lines)
                for rec in reader:
                    if rec.get("TermType") != "OnDemand":
                        continue
                    usage = rec.get("usageType", "")
                    if offer == "AmazonEC2" and rec.get("Instance Type") in INSTANCE_TYPES:
                        if (
                            rec.get("Operating System") == "Linux"
                            and rec.get("Tenancy") == "Shared"
                            and rec.get("Pre Installed S/W") == "NA"
                            and rec.get("CapacityStatus") == "Used"
                            and rec.get("License Model") == "No License required"
                            and usage.startswith("BoxUsage:")
                        ):
                            rows.append(_row("instance", rec["Instance Type"], rec, url))
                    elif offer == "AmazonEC2" and usage in VOLUME_USAGE:
                        rows.append(_row("volume", VOLUME_USAGE[usage], rec, url))
                    elif offer == "AmazonVPC" and usage.endswith("PublicIPv4:InUseAddress"):
                        rows.append(_row("public_ip", "in-use", rec, url))
                    elif offer == "AmazonVPC" and usage.endswith("PublicIPv4:IdleAddress"):
                        rows.append(_row("public_ip", "idle", rec, url))
            files.append(
                {
                    "offer": offer,
                    "url": url,
                    "publication_date": published,
                    "sha256": digest.hexdigest(),
                    "preamble": [p.strip() for p in preamble],
                }
            )
    snapshot = {
        "source": "AWS Price List Bulk API",
        "region": REGION,
        "captured_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "files": files,
        "items": sorted(rows, key=lambda r: (r["kind"], r["key"])),
    }
    OUT.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
    print(f"{len(rows)} rows -> {OUT}")


def _lines(resp: httpx.Response, digest: hashlib._Hash):
    buffer = ""
    for chunk in resp.iter_bytes():
        digest.update(chunk)
        buffer += chunk.decode("utf-8")
        *complete, buffer = buffer.split("\n")
        yield from (line + "\n" for line in complete)
    if buffer:
        yield buffer


def _row(kind: str, key: str, rec: dict, url: str) -> dict:
    return {
        "kind": kind,
        "key": key,
        "sku": rec["SKU"],
        "rate_code": rec["RateCode"],
        "usage_type": rec["usageType"],
        "unit": rec["Unit"],
        "price_usd": float(rec["PricePerUnit"]),
        "description": rec["PriceDescription"],
        "effective_date": rec.get("EffectiveDate"),
        "source_url": url,
    }


if __name__ == "__main__":
    main()
