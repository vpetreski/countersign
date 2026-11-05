"""FOCUS 1.4 cost-and-usage exports (one CSV per provider)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from countersign.data.normalize import parse_timestamp
from countersign.data.sources.batch import SourceBatch
from countersign.domain.resources import CostLine, Provider

SOURCE = "billing"

# The 21 columns FOCUS 1.4 marks Mandatory (ProviderName and PublisherName were removed in 1.4).
MANDATORY = (
    "BilledCost",
    "BillingAccountId",
    "BillingAccountName",
    "BillingCurrency",
    "BillingPeriodEnd",
    "BillingPeriodStart",
    "ChargeCategory",
    "ChargeClass",
    "ChargeDescription",
    "ChargePeriodEnd",
    "ChargePeriodStart",
    "ContractedCost",
    "EffectiveCost",
    "HostProviderName",
    "InvoiceIssuerName",
    "ListCost",
    "PricingQuantity",
    "PricingUnit",
    "ServiceCategory",
    "ServiceName",
    "ServiceProviderName",
)
SERVICE_CATEGORIES = frozenset(
    {
        "AI and Machine Learning",
        "Analytics",
        "Business Applications",
        "Compute",
        "Databases",
        "Developer Tools",
        "Multicloud",
        "Identity",
        "Integration",
        "Internet of Things",
        "Management and Governance",
        "Media",
        "Migration",
        "Mobile",
        "Networking",
        "Security",
        "Storage",
        "Web",
        "Other",
    }
)
CHARGE_CATEGORIES = frozenset({"Usage", "Purchase", "Tax", "Credit", "Adjustment"})
PROVIDERS = {"AWS": Provider.AWS, "Microsoft": Provider.AZURE}


def load(root: Path) -> SourceBatch:
    batch = SourceBatch(SOURCE)
    for path in sorted((root / "sources" / "billing").glob("focus_*.csv")):
        _file(batch, root, path)
    return batch


def _file(batch: SourceBatch, root: Path, path: Path) -> None:
    rel = str(path.relative_to(root))
    text = batch.read(root, path).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in MANDATORY if c not in (reader.fieldnames or [])]
    if missing:
        batch.quarantine(
            rel, f"export lacks FOCUS mandatory columns: {', '.join(missing)}", reader.fieldnames
        )
        return
    seen: set[tuple[str, ...]] = set()
    for line, row in enumerate(reader, start=2):
        batch.records_read += 1
        ref = f"{rel}#L{line}"
        key = tuple(row.values())
        if key in seen:
            batch.warn(ref, "uniqueness", "exact duplicate of an earlier row; dropped")
            continue
        seen.add(key)
        column = "BilledCost"
        try:
            billed = float(row[column])
            column = "EffectiveCost"
            effective = float(row[column])
            column = "ChargePeriodStart"
            start, fixed = parse_timestamp(row[column])
            column = "ServiceProviderName"
            provider = PROVIDERS[row[column]]
        except (KeyError, ValueError) as exc:
            batch.quarantine(
                ref, f"invalid FOCUS row: {column} {row.get(column)!r} ({type(exc).__name__})", row
            )
            continue
        if fixed:
            batch.warn(ref, "transformation", "ChargePeriodStart had no UTC offset; read as UTC")
        currency = row["BillingCurrency"].strip()
        if currency != currency.upper():
            batch.warn(
                ref, "transformation", f"BillingCurrency {currency!r} normalized to {currency.upper()!r}"
            )
            currency = currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            batch.quarantine(ref, f"BillingCurrency is not an ISO 4217 code: {currency!r}", row)
            continue
        if row["ServiceCategory"] not in SERVICE_CATEGORIES or row["ChargeCategory"] not in CHARGE_CATEGORIES:
            batch.quarantine(ref, "ServiceCategory or ChargeCategory outside the FOCUS allowed values", row)
            continue
        if row["Tags"]:
            try:
                tags = json.loads(row["Tags"])
            except ValueError as exc:
                batch.warn(ref, "schema", f"Tags is not valid JSON: {exc}")
            else:
                if not isinstance(tags, dict):
                    batch.warn(ref, "schema", "Tags is not a JSON object")
        resource = row.get("ResourceId") or None
        batch.costs.append(
            CostLine(
                resource_key=resource.lower() if resource else None,
                provider=provider,
                day=start.date(),
                service_category=row["ServiceCategory"],
                service_name=row["ServiceName"],
                charge_category=row["ChargeCategory"],
                billed_cost=billed,
                effective_cost=effective,
                currency=currency,
                sku=row.get("SkuId") or None,
                region=row.get("RegionId") or None,
            )
        )
