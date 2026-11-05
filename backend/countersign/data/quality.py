"""Data-quality checks over the loaded sources: schema, completeness, uniqueness, freshness,
reconciliation and transformation rules. Each check returns a result the UI and the tests can read."""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from countersign.data.sources.batch import SourceBatch
from countersign.domain.resources import Environment, Kind, Resource, State

Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    category: str  # schema | completeness | uniqueness | freshness | reconciliation | transformation
    status: Status
    observed: str
    expected: str
    detail: str = ""


def lag_hours(as_of: dt.datetime, day: dt.date) -> float:
    """Hours from the end of a daily point's day to the as-of time; a day that ends after it counts as 0."""
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=dt.UTC)
    return max(0.0, (as_of - end).total_seconds() / 3600)


def sync_check(synced_at: Sequence[dt.datetime], now: dt.datetime, hours: float) -> CheckResult:
    """The pipeline's freshness, judged now: the oldest current source's last sync against the service level, so
    a sync that stopped shows as a failing check instead of a pass recorded the last time it ran."""
    if not synced_at:
        return CheckResult("freshness.sync", "freshness", "fail", "never synced", f"<= {hours:.0f} h")
    age_h = (now - min(synced_at)).total_seconds() / 3600
    return CheckResult(
        "freshness.sync",
        "freshness",
        "pass" if age_h <= hours else "fail",
        f"{age_h:.1f} h since the last sync",
        f"<= {hours:.0f} h",
    )


def run_checks(
    batches: Sequence[SourceBatch], as_of: dt.datetime, *, metrics_hours: float, billing_hours: float
) -> list[CheckResult]:
    resources = [r for b in batches for r in b.resources]
    costs = [c for b in batches for c in b.costs]
    results: list[CheckResult] = []

    # schema: what each source refused, as a share of what it read
    for b in batches:
        rate = len(b.quarantined) / max(1, b.records_read)
        status: Status = "pass" if not b.quarantined else ("warn" if rate <= 0.02 else "fail")
        results.append(
            CheckResult(
                f"schema.{b.source}.quarantine",
                "schema",
                status,
                f"{len(b.quarantined)} of {b.records_read}",
                "<= 2% quarantined",
                "; ".join(sorted({q.reason.split(":")[0] for q in b.quarantined})),
            )
        )

    # completeness: owners and environments
    owned = sum(1 for r in resources if r.owner)
    share = owned / max(1, len(resources))
    results.append(
        CheckResult(
            "completeness.owner",
            "completeness",
            "pass" if share == 1 else ("warn" if share >= 0.8 else "fail"),
            f"{owned}/{len(resources)} have an owner",
            ">= 80% (100% to pass)",
            ", ".join(sorted(r.ref for r in resources if not r.owner))[:400],
        )
    )
    unknown_env = [r.ref for r in resources if r.environment is Environment.UNKNOWN]
    results.append(
        CheckResult(
            "completeness.environment",
            "completeness",
            "pass" if not unknown_env else "warn",
            f"{len(unknown_env)} without an environment",
            "0",
            ", ".join(sorted(unknown_env))[:400],
        )
    )
    vms_without_metrics = [
        r.ref
        for r in resources
        if r.kind is Kind.VM and r.state is State.RUNNING and not _has_cpu(batches, r)
    ]
    results.append(
        CheckResult(
            "completeness.metrics",
            "completeness",
            "pass" if not vms_without_metrics else "warn",
            f"{len(vms_without_metrics)} running VMs without metrics",
            "0",
            ", ".join(vms_without_metrics)[:400],
        )
    )

    # uniqueness: canonical ids across all sources, after each adapter's own deduplication
    dupes = [rid for rid, n in Counter(r.id.lower() for r in resources).items() if n > 1]
    refs = [ref for ref, n in Counter(r.ref for r in resources).items() if n > 1]
    dedup_warnings = sum(1 for b in batches for w in b.warnings if w.rule == "uniqueness")
    results.append(
        CheckResult(
            "uniqueness.resource_id",
            "uniqueness",
            "fail" if dupes or refs else ("warn" if dedup_warnings else "pass"),
            f"{len(dupes) + len(refs)} duplicates; {dedup_warnings} removed at the source",
            "0 duplicates",
            ", ".join(dupes + refs)[:400],
        )
    )

    # freshness of the data itself: how far the newest complete day of utilization and of billing trails the
    # sources' as-of time. The pipeline's own freshness, the time since the last sync, is `sync_check`, judged
    # when the checks are read, because a result stored at ingest cannot age.
    newest_cpu = max((pt.day for b in batches for pts in b.cpu.values() for pt in pts), default=None)
    newest_cost = max((c.day for c in costs), default=None)
    for check_id, newest, hours in (
        ("freshness.metrics", newest_cpu, metrics_hours),
        ("freshness.billing", newest_cost, billing_hours),
    ):
        lag_h = math.inf if newest is None else lag_hours(as_of, newest)
        results.append(
            CheckResult(
                check_id,
                "freshness",
                "pass" if lag_h <= hours else "fail",
                "no data" if newest is None else f"newest day {newest}, {lag_h:.0f} h before the as-of time",
                f"<= {hours:.0f} h",
            )
        )

    # reconciliation: inventory against billing
    keys = {r.billing_key for r in resources}
    unmatched = sorted({c.resource_key for c in costs if c.resource_key and c.resource_key not in keys})
    results.append(
        CheckResult(
            "reconciliation.billing_to_inventory",
            "reconciliation",
            "pass" if not unmatched else "warn",
            f"{len(unmatched)} billed resources missing from inventory",
            "0",
            ", ".join(unmatched)[:400],
        )
    )
    last_day = max((c.day for c in costs), default=as_of.date())
    compute_last_day = {
        c.resource_key
        for c in costs
        if c.service_category == "Compute" and c.day == last_day and c.billed_cost > 0
    }
    running_unbilled = [
        r.ref
        for r in resources
        if r.kind is Kind.VM and r.state is State.RUNNING and r.billing_key not in compute_last_day
    ]
    results.append(
        CheckResult(
            "reconciliation.running_vms_billed",
            "reconciliation",
            "pass" if not running_unbilled else "warn",
            f"{len(running_unbilled)} running VMs without compute charges on {last_day}",
            "0",
            ", ".join(running_unbilled)[:400],
        )
    )
    stopped_billed = [
        r.ref
        for r in resources
        if r.kind is Kind.VM and r.state is State.STOPPED and r.billing_key in compute_last_day
    ]
    results.append(
        CheckResult(
            "reconciliation.stopped_vms_unbilled",
            "reconciliation",
            "pass" if not stopped_billed else "warn",
            f"{len(stopped_billed)} stopped VMs billed for compute on {last_day}",
            "0",
            ", ".join(stopped_billed),
        )
    )
    currencies = sorted({c.currency for c in costs})
    results.append(
        CheckResult(
            "reconciliation.single_currency",
            "reconciliation",
            "pass" if currencies == ["USD"] else "fail",
            ", ".join(currencies) or "none",
            "USD",
        )
    )

    # transformation rules applied while loading
    transformed = [w for b in batches for w in b.warnings if w.rule == "transformation"]
    results.append(
        CheckResult(
            "transformation.normalized_values",
            "transformation",
            "pass" if not transformed else "warn",
            f"{len(transformed)} values normalized",
            "0",
            "; ".join(sorted({w.detail for w in transformed}))[:400],
        )
    )
    flagged = sorted(r.ref for r in resources if "suspicious_content" in r.flags)
    redacted = sorted(r.ref for r in resources if "secret_redacted" in r.flags)
    results.append(
        CheckResult(
            "transformation.untrusted_text",
            "transformation",
            "pass" if not flagged and not redacted else "warn",
            f"{len(flagged)} flagged for injection, {len(redacted)} with secrets masked",
            "0",
            f"flagged: {', '.join(flagged)}; masked: {', '.join(redacted)}",
        )
    )
    return results


def _has_cpu(batches: Sequence[SourceBatch], resource: Resource) -> bool:
    return any(resource.id in b.cpu and b.cpu[resource.id] for b in batches)
