"""Deterministic waste analysis. The arithmetic lives here, in code, not in the model: the agent chooses
scope and judgment, the analyzer computes utilization statistics and savings the same way every time."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from countersign.domain.resources import (
    CostLine,
    CpuPoint,
    Environment,
    Kind,
    PriceQuote,
    Resource,
    State,
    UtilizationSummary,
)
from countersign.domain.rules import HOURS_PER_MONTH, RIGHTSIZE_TO, percentile, protected_by


class Category(StrEnum):
    IDLE_VM = "idle_vm"
    OVERSIZED_VM = "oversized_vm"
    UNATTACHED_DISK = "unattached_disk"
    UNUSED_IP = "unused_ip"
    STATE_CONFLICT = "state_conflict"


class Operation(StrEnum):
    STOP_VM = "stop_vm"  # AWS StopInstances / Azure deallocate: reversible
    RESIZE_VM = "resize_vm"  # stop, change size, start: reversible
    DELETE_DISK = "delete_disk"  # snapshot first, then delete: recoverable from the snapshot
    RELEASE_IP = "release_ip"  # the address itself cannot be recovered


RECOMMENDED = {
    Category.IDLE_VM: Operation.STOP_VM,
    Category.OVERSIZED_VM: Operation.RESIZE_VM,
    Category.UNATTACHED_DISK: Operation.DELETE_DISK,
    Category.UNUSED_IP: Operation.RELEASE_IP,
}


@dataclass(frozen=True)
class Thresholds:
    idle_p95_cpu: float = 5.0  # p95 of daily average CPU, percent
    idle_max_cpu: float = 20.0  # highest daily maximum, percent
    rightsize_p95_cpu: float = 15.0
    rightsize_max_cpu: float = 40.0
    min_days: int = 14
    min_coverage: float = 0.8
    disk_grace_days: int = 7


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_id: str
    ref: str
    name: str
    provider: str
    kind: str
    environment: str
    team: str | None
    owner: str | None
    category: Category
    operation: Operation | None
    current_sku: str
    target_sku: str | None = None
    monthly_cost_usd: float | None
    est_monthly_savings_usd: float | None
    price_source: str | None
    price_reference: str | None
    utilization: UtilizationSummary | None = None
    unattached_days: int | None = None
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.operation is not None and not self.blockers


def summarize(
    resource: Resource, points: Sequence[CpuPoint], window_start: dt.date, window_end: dt.date
) -> UtilizationSummary:
    """Coverage counts the days the VM existed in the window; a VM created yesterday has one expected day."""
    start = max(window_start, resource.created_at.date()) if resource.created_at else window_start
    expected = max(0, (window_end - start).days + 1)
    inside = [p for p in points if window_start <= p.day <= window_end]
    if not inside:
        return UtilizationSummary(
            resource_id=resource.id,
            window_days=(window_end - window_start).days + 1,
            expected_days=expected,
            datapoints=0,
            coverage=0.0,
            avg=None,
            p95_daily_avg=None,
            max=None,
            last_datapoint=None,
        )
    return UtilizationSummary(
        resource_id=resource.id,
        window_days=(window_end - window_start).days + 1,
        expected_days=expected,
        datapoints=len(inside),
        coverage=round(min(1.0, len(inside) / max(1, expected)), 3),
        avg=round(sum(p.avg for p in inside) / len(inside), 2),
        p95_daily_avg=round(percentile([p.avg for p in inside], 0.95), 2),
        max=round(max(p.max for p in inside), 2),
        last_datapoint=inside[-1].day,
    )


PriceFn = Callable[[Resource, str | None], PriceQuote]


DEFAULT_THRESHOLDS = Thresholds()


def analyze(
    resources: Iterable[Resource],
    cpu: Mapping[str, Sequence[CpuPoint]],
    costs: Mapping[str, Sequence[CostLine]],
    *,
    price: PriceFn,
    as_of: dt.datetime,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    window_days: int = 31,
) -> list[Finding]:
    window_end = (as_of - dt.timedelta(days=1)).date()
    window_start = window_end - dt.timedelta(days=window_days - 1)
    findings: list[Finding] = []
    for r in resources:
        if r.state in (State.DELETED, State.RELEASED):
            continue
        lines = costs.get(r.billing_key, ())
        if r.kind is Kind.VM:
            findings.extend(
                _vm(
                    r, cpu.get(r.id, ()), lines, price=price, start=window_start, end=window_end, t=thresholds
                )
            )
        elif r.kind is Kind.DISK and r.state is State.UNATTACHED:
            findings.append(_disk(r, lines, price, as_of, thresholds))
        elif r.kind is Kind.PUBLIC_IP and r.state is State.UNASSOCIATED:
            findings.append(_ip(r, lines, price))
    return sorted(findings, key=lambda f: (-(f.est_monthly_savings_usd or 0), f.ref))


def _blockers(r: Resource) -> list[str]:
    out = []
    if r.environment is Environment.PROD:
        out.append("production")
    if (tag := protected_by(r.tags)) is not None:
        out.append(f"protected:{tag}")
    if "suspicious_content" in r.flags:
        out.append("suspicious_content")
    return out


def _warnings(r: Resource) -> list[str]:
    out = []
    if not r.owner:
        out.append("no_owner_to_notify")
    if r.environment is Environment.UNKNOWN:
        out.append("unknown_environment")
    if "secret_redacted" in r.flags:
        out.append("secret_redacted_from_tags")
    return out


def _quote(
    price: PriceFn, r: Resource, sku: str | None, lines: Sequence[CostLine]
) -> tuple[float | None, str | None, str | None]:
    """Monthly run-rate from the price list; if no price is available, from the billing history."""
    try:
        q = price(r, sku)
    except LookupError:
        if sku is not None and sku != r.sku:
            return None, None, None
        usage = [c for c in lines if c.charge_category == "Usage"]
        if not usage:
            return None, None, None
        days = {c.day for c in usage}
        daily = sum(c.billed_cost for c in usage) / max(1, len(days))
        return (
            round(daily * HOURS_PER_MONTH / 24, 2),
            "billing_history",
            "FOCUS export, average daily billed cost",
        )
    return q.monthly_usd, q.source, q.reference


def _vm(
    r: Resource,
    points: Sequence[CpuPoint],
    lines: Sequence[CostLine],
    *,
    price: PriceFn,
    start: dt.date,
    end: dt.date,
    t: Thresholds,
) -> list[Finding]:
    if r.state is State.STOPPED:
        compute_last_day = [
            c for c in lines if c.service_category == "Compute" and c.day == end and c.billed_cost > 0
        ]
        if not compute_last_day:
            return []
        return [
            Finding(
                resource_id=r.id,
                ref=r.ref,
                name=r.name,
                provider=r.provider,
                kind=r.kind,
                environment=r.environment,
                team=r.team,
                owner=r.owner,
                category=Category.STATE_CONFLICT,
                operation=None,
                current_sku=r.sku,
                monthly_cost_usd=None,
                est_monthly_savings_usd=None,
                price_source=None,
                price_reference=None,
                blockers=("conflicting_sources",),
                warnings=(f"inventory says {r.native_state}; billing shows compute on {end}",),
            )
        ]
    if r.state is not State.RUNNING:
        return []
    u = summarize(r, points, start, end)
    if u.p95_daily_avg is None or u.max is None:
        return []
    blockers = _blockers(r)
    data_blockers = []
    if u.expected_days < t.min_days:
        data_blockers.append("insufficient_history")
    elif u.coverage < t.min_coverage:
        data_blockers.append("insufficient_coverage")
    if u.p95_daily_avg <= t.idle_p95_cpu and u.max <= t.idle_max_cpu:
        monthly, source, ref = _quote(price, r, None, lines)
        return [
            Finding(
                resource_id=r.id,
                ref=r.ref,
                name=r.name,
                provider=r.provider,
                kind=r.kind,
                environment=r.environment,
                team=r.team,
                owner=r.owner,
                category=Category.IDLE_VM,
                operation=Operation.STOP_VM,
                current_sku=r.sku,
                monthly_cost_usd=monthly,
                est_monthly_savings_usd=monthly,
                price_source=source,
                price_reference=ref,
                utilization=u,
                blockers=tuple(blockers + data_blockers),
                warnings=tuple(_warnings(r)),
            )
        ]
    if u.p95_daily_avg <= t.rightsize_p95_cpu and u.max <= t.rightsize_max_cpu:
        target = RIGHTSIZE_TO.get((r.provider, r.sku))
        if target is None:
            return []
        monthly, source, ref = _quote(price, r, None, lines)
        target_monthly, _, _ = _quote(price, r, target, lines)
        savings = (
            round(monthly - target_monthly, 2) if monthly is not None and target_monthly is not None else None
        )
        return [
            Finding(
                resource_id=r.id,
                ref=r.ref,
                name=r.name,
                provider=r.provider,
                kind=r.kind,
                environment=r.environment,
                team=r.team,
                owner=r.owner,
                category=Category.OVERSIZED_VM,
                operation=Operation.RESIZE_VM,
                current_sku=r.sku,
                target_sku=target,
                monthly_cost_usd=monthly,
                est_monthly_savings_usd=savings,
                price_source=source,
                price_reference=ref,
                utilization=u,
                blockers=tuple(blockers + data_blockers),
                warnings=tuple(_warnings(r)),
            )
        ]
    return []


def _disk(
    r: Resource, lines: Sequence[CostLine], price: PriceFn, as_of: dt.datetime, t: Thresholds
) -> Finding:
    since = r.detached_at or r.created_at
    days = (as_of - since).days if since else None
    blockers = _blockers(r)
    if days is not None and days < t.disk_grace_days:
        blockers.append("grace_period")
    monthly, source, ref = _quote(price, r, None, lines)
    return Finding(
        resource_id=r.id,
        ref=r.ref,
        name=r.name,
        provider=r.provider,
        kind=r.kind,
        environment=r.environment,
        team=r.team,
        owner=r.owner,
        category=Category.UNATTACHED_DISK,
        operation=Operation.DELETE_DISK,
        current_sku=r.sku,
        monthly_cost_usd=monthly,
        est_monthly_savings_usd=monthly,
        price_source=source,
        price_reference=ref,
        unattached_days=days,
        blockers=tuple(blockers),
        warnings=tuple(_warnings(r)),
    )


def _ip(r: Resource, lines: Sequence[CostLine], price: PriceFn) -> Finding:
    monthly, source, ref = _quote(price, r, None, lines)
    return Finding(
        resource_id=r.id,
        ref=r.ref,
        name=r.name,
        provider=r.provider,
        kind=r.kind,
        environment=r.environment,
        team=r.team,
        owner=r.owner,
        category=Category.UNUSED_IP,
        operation=Operation.RELEASE_IP,
        current_sku=r.sku,
        monthly_cost_usd=monthly,
        est_monthly_savings_usd=monthly,
        price_source=source,
        price_reference=ref,
        blockers=tuple(_blockers(r)),
        warnings=tuple(_warnings(r)),
    )
