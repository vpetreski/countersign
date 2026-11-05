"""The estate as one workspace sees it: the shared, ingested data plus that workspace's own changes.

The ingested estate is immutable between syncs, so it is loaded once per process and keyed by the source
checksums; a workspace's approved changes live in `resource_overrides` and are laid over it on every read.
Nothing a run does can touch another workspace or the shared data.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from countersign.data.pricing import PriceCatalog, PriceUnavailableError
from countersign.data.quality import CheckResult, Status, sync_check
from countersign.domain.analysis import Finding, analyze
from countersign.domain.resources import (
    CostLine,
    CpuPoint,
    Environment,
    Kind,
    PriceQuote,
    Provider,
    Resource,
    State,
)
from countersign.store.schema import (
    CostLineRow,
    CpuPointRow,
    MetaRow,
    QualityResultRow,
    QuarantineRow,
    ResourceOverrideRow,
    ResourceRow,
    SourceSnapshot,
)

CHANGED_IN_WORKSPACE = "changed_in_workspace"


@dataclass(frozen=True)
class Snapshot:
    source: str
    checksum: str
    fetched_at: dt.datetime
    data_as_of: dt.datetime
    records_read: int
    quarantined: int
    warnings: int


@dataclass(frozen=True)
class BaseEstate:
    key: str
    resources: tuple[Resource, ...]
    cpu: dict[str, tuple[CpuPoint, ...]]
    costs: dict[str, tuple[CostLine, ...]]
    as_of: dt.datetime
    snapshots: tuple[Snapshot, ...]


@dataclass
class Estate:
    """A workspace's view. `resources` already carry the workspace's overrides."""

    base: BaseEstate
    resources: list[Resource]
    by_ref: dict[str, Resource] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_ref = {r.ref: r for r in self.resources}

    @property
    def as_of(self) -> dt.datetime:
        return self.base.as_of

    def resolve(self, ref_or_id: str) -> Resource | None:
        if ref_or_id in self.by_ref:
            return self.by_ref[ref_or_id]
        low = ref_or_id.lower()
        return next((r for r in self.resources if r.id.lower() == low or r.ref.lower() == low), None)

    def cpu_for(self, resource: Resource) -> tuple[CpuPoint, ...]:
        return self.base.cpu.get(resource.id, ())

    def costs_for(self, resource: Resource) -> tuple[CostLine, ...]:
        return self.base.costs.get(resource.billing_key, ())

    def findings(self, price: Callable[[Resource, str | None], PriceQuote]) -> list[Finding]:
        candidates = [r for r in self.resources if CHANGED_IN_WORKSPACE not in r.flags]
        return analyze(candidates, self.base.cpu, self.base.costs, price=price, as_of=self.as_of)

    def monthly_spend(self) -> dict[str, float]:
        """October billed cost by provider (actual, 744 hours), the dashboard's headline figure."""
        totals: dict[str, float] = defaultdict(float)
        for lines in self.base.costs.values():
            for c in lines:
                totals[c.provider] += c.billed_cost
        return {k: round(v, 2) for k, v in sorted(totals.items())}


class EstateCache:
    """Process-wide cache of the ingested estate, refreshed when a sync changes the source checksums."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._base: BaseEstate | None = None

    def clear(self) -> None:
        with self._lock:
            self._base = None

    def base(self, session: Session) -> BaseEstate:
        snapshots = _snapshots(session)
        key = "|".join(f"{s.source}:{s.checksum}" for s in snapshots)
        with self._lock:
            if self._base is not None and self._base.key == key:
                return self._base
        loaded = _load_base(session, key, snapshots)
        with self._lock:
            self._base = loaded
        return loaded

    def view(self, session: Session, workspace_id: str | None) -> Estate:
        base = self.base(session)
        resources = list(base.resources)
        if workspace_id is not None:
            overrides = {
                o.resource_id: o
                for o in session.scalars(
                    select(ResourceOverrideRow).where(ResourceOverrideRow.workspace_id == workspace_id)
                )
            }
            if overrides:
                resources = [_apply(r, overrides.get(r.id)) for r in resources]
        return Estate(base=base, resources=resources)


ESTATE = EstateCache()


def _apply(resource: Resource, override: ResourceOverrideRow | None) -> Resource:
    if override is None:
        return resource
    return resource.model_copy(
        update={
            "state": State(override.state),
            "native_state": override.native_state,
            "sku": override.sku,
            "flags": tuple(sorted({*resource.flags, CHANGED_IN_WORKSPACE})),
        }
    )


def _snapshots(session: Session) -> tuple[Snapshot, ...]:
    rows = session.scalars(
        select(SourceSnapshot).where(SourceSnapshot.current.is_(True)).order_by(SourceSnapshot.source)
    )
    return tuple(
        Snapshot(
            r.source,
            r.checksum,
            _utc(r.fetched_at),
            _utc(r.data_as_of),
            r.records_read,
            r.quarantined,
            r.warnings,
        )
        for r in rows
    )


def _load_base(session: Session, key: str, snapshots: tuple[Snapshot, ...]) -> BaseEstate:
    resources = tuple(
        _resource(row) for row in session.scalars(select(ResourceRow).order_by(ResourceRow.ref))
    )
    cpu: dict[str, list[CpuPoint]] = defaultdict(list)
    for p in session.scalars(select(CpuPointRow).order_by(CpuPointRow.resource_id, CpuPointRow.day)):
        cpu[p.resource_id].append(CpuPoint(day=p.day, avg=p.avg, max=p.max))
    costs: dict[str, list[CostLine]] = defaultdict(list)
    for c in session.scalars(select(CostLineRow).order_by(CostLineRow.day, CostLineRow.id)):
        costs[c.resource_key or ""].append(
            CostLine(
                resource_key=c.resource_key,
                provider=Provider(c.provider),
                day=c.day,
                service_category=c.service_category,
                service_name=c.service_name,
                charge_category=c.charge_category,
                billed_cost=c.billed_cost,
                effective_cost=c.effective_cost,
                currency=c.currency,
                sku=c.sku,
                region=c.region,
            )
        )
    as_of_row = session.get(MetaRow, "data_as_of")
    as_of = (
        _utc(dt.datetime.fromisoformat(as_of_row.value))
        if as_of_row
        else max((s.data_as_of for s in snapshots), default=dt.datetime.now(dt.UTC))
    )
    return BaseEstate(
        key=key,
        resources=resources,
        cpu={k: tuple(v) for k, v in cpu.items()},
        costs={k: tuple(v) for k, v in costs.items()},
        as_of=as_of,
        snapshots=snapshots,
    )


def _resource(row: ResourceRow) -> Resource:
    return Resource(
        id=row.id,
        ref=row.ref,
        provider=Provider(row.provider),
        kind=Kind(row.kind),
        name=row.name,
        account=row.account,
        region=row.region,
        environment=Environment(row.environment),
        team=row.team,
        owner=row.owner,
        tags={str(k): str(v) for k, v in row.tags.items()},
        state=State(row.state),
        native_state=row.native_state,
        sku=row.sku,
        size_gb=row.size_gb,
        created_at=_utc(row.created_at) if row.created_at else None,
        detached_at=_utc(row.detached_at) if row.detached_at else None,
        attached_to=row.attached_to,
        flags=tuple(str(f) for f in row.flags),
        source=row.source,
        source_ref=row.source_ref,
    )


def _utc(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes even for timezone-aware columns; every stored time is UTC."""
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def quality_results(session: Session) -> list[QualityResultRow]:
    return list(session.scalars(select(QualityResultRow).order_by(QualityResultRow.check_id)))


def data_checks(session: Session, now: dt.datetime, sync_hours: float) -> list[CheckResult]:
    """The checks stored at the last ingest, plus the sync check, computed now because a stored result
    cannot age: when the daily sync stops, the Data view and the dashboard show it."""
    stored = [
        CheckResult(r.check_id, r.category, cast("Status", r.status), r.observed, r.expected, r.detail)
        for r in quality_results(session)
    ]
    current = session.scalars(select(SourceSnapshot).where(SourceSnapshot.current.is_(True)))
    synced = [_utc(x.fetched_at) for x in current]
    return sorted([*stored, sync_check(synced, now, sync_hours)], key=lambda c: c.check_id)


def quarantined(session: Session) -> list[QuarantineRow]:
    return list(
        session.scalars(select(QuarantineRow).order_by(QuarantineRow.source, QuarantineRow.source_ref))
    )


def price_fn(
    catalog: PriceCatalog, *, live: bool, unavailable: Iterable[str] = ()
) -> Callable[[Resource, str | None], PriceQuote]:
    """A price function for the analyzer; `unavailable` names providers whose price source is down."""
    down = frozenset(unavailable)

    def price(resource: Resource, sku: str | None) -> PriceQuote:
        if resource.provider in down or "all" in down:
            raise PriceUnavailableError(f"price source for {resource.provider} unavailable")
        return catalog.quote(resource, sku, live=live)

    return price
