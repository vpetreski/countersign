"""Pure rules shared by the data layer and the analyzer: units, statistics and the protection vocabulary.
No I/O and no imports from the outer layers, so the domain stays the innermost ring."""

from __future__ import annotations

import math
from collections.abc import Mapping

from countersign.domain.resources import Provider

HOURS_PER_MONTH = 730  # FinOps convention for monthly run-rate estimates

PROTECTED_TAGS: Mapping[str, frozenset[str]] = {
    "lifecycle": frozenset({"do-not-delete", "keep", "retain", "protected"}),
    "compliance": frozenset({"legal-hold", "regulatory-hold", "litigation-hold"}),
}

# One size down within the same capability class, among the SKUs this demo prices.
RIGHTSIZE_TO: Mapping[tuple[Provider, str], str] = {
    (Provider.AWS, "m5.xlarge"): "m6i.large",
    (Provider.AWS, "m6i.xlarge"): "m6i.large",
    (Provider.AZURE, "Standard_D4s_v5"): "Standard_D2s_v5",
}


def protected_by(tags: Mapping[str, str]) -> str | None:
    for key, values in PROTECTED_TAGS.items():
        if tags.get(key, "").lower() in values:
            return f"{key}={tags[key]}"
    return None


def monthly_from_hourly(hourly: float) -> float:
    return round(hourly * HOURS_PER_MONTH, 2)


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile, the definition FinOps tools document for utilization thresholds."""
    if not values:
        raise ValueError("percentile of an empty series")
    if not 0 < q <= 1:
        raise ValueError(f"quantile out of range: {q}")
    ordered = sorted(values)
    rank = min(len(ordered), max(1, math.ceil(q * len(ordered) - 1e-9)))
    return ordered[rank - 1]
