"""Ingestion: load every source, validate, and replace the shared estate in one transaction.

Idempotent: when no source file changed (same checksums), only the fetch time moves forward, so a daily
scheduled sync keeps freshness green without rewriting data."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from countersign.data.quality import CheckResult, run_checks
from countersign.data.sources import aws, azure, focus
from countersign.data.sources.batch import SourceBatch
from countersign.domain.resources import Environment, Kind
from countersign.settings import Settings
from countersign.store.db import Database
from countersign.store.schema import (
    CostLineRow,
    CpuPointRow,
    DataWarningRow,
    MetaRow,
    QualityResultRow,
    QuarantineRow,
    ResourceRow,
    SourceSnapshot,
)


@dataclass(frozen=True)
class IngestReport:
    changed: bool
    resources: int
    cost_lines: int
    quarantined: int
    warnings: int
    checks: list[CheckResult]


def load_batches(settings: Settings) -> list[SourceBatch]:
    root = settings.fixtures_dir
    batches = [aws.load(root), azure.load(root), focus.load(root)]
    for batch in batches:
        inherit_tags(batch)
    return batches


def inherit_tags(batch: SourceBatch) -> None:
    """Attached disks often carry no tags of their own; allocate them to their VM's environment and owner
    (the usual FinOps allocation rule) and mark the inheritance so it is visible, never silent."""
    vms = {r.ref: r for r in batch.resources if r.kind is Kind.VM}
    for i, r in enumerate(batch.resources):
        vm = vms.get(r.attached_to or "")
        if r.kind is not Kind.DISK or vm is None:
            continue
        update: dict[str, object] = {}
        if r.environment is Environment.UNKNOWN and vm.environment is not Environment.UNKNOWN:
            update["environment"] = vm.environment
        if not r.team and vm.team:
            update["team"] = vm.team
        if not r.owner and vm.owner:
            update["owner"] = vm.owner
        if update:
            update["flags"] = tuple(sorted({*r.flags, "inherited_tags"}))
            batch.resources[i] = r.model_copy(update=update)


def data_as_of(settings: Settings) -> dt.datetime:
    manifest = json.loads((settings.fixtures_dir / "manifest.json").read_text())
    return dt.datetime.fromisoformat(manifest["as_of"].replace("Z", "+00:00"))


def ingest(db: Database, settings: Settings, now: dt.datetime | None = None) -> IngestReport:
    now = now or dt.datetime.now(dt.UTC)
    batches = load_batches(settings)
    as_of = data_as_of(settings)
    checks = run_checks(
        batches,
        as_of,
        metrics_hours=settings.metrics_freshness_hours,
        billing_hours=settings.billing_freshness_hours,
    )
    with db.session() as s:
        current = {
            row.source: row
            for row in s.scalars(select(SourceSnapshot).where(SourceSnapshot.current.is_(True)))
        }
        unchanged = len(current) == len(batches) and all(
            b.source in current and current[b.source].checksum == b.checksum for b in batches
        )
        if unchanged:
            s.execute(update(SourceSnapshot).where(SourceSnapshot.current.is_(True)).values(fetched_at=now))
            _write_checks(s, checks, now)
            return _report(False, batches, checks)

        for table in (CpuPointRow, CostLineRow, QuarantineRow, DataWarningRow, ResourceRow):
            s.execute(delete(table))
        s.execute(update(SourceSnapshot).values(current=False))
        for b in batches:
            s.add(
                SourceSnapshot(
                    source=b.source,
                    checksum=b.checksum,
                    fetched_at=now,
                    data_as_of=as_of,
                    records_read=b.records_read,
                    quarantined=len(b.quarantined),
                    warnings=len(b.warnings),
                    current=True,
                )
            )
        resources = [r for b in batches for r in b.resources]
        if resources:
            s.execute(
                insert(ResourceRow),
                [
                    {
                        **r.model_dump(mode="python", exclude={"flags"}),
                        "flags": list(r.flags),
                        "billing_key": r.billing_key,
                    }
                    for r in resources
                ],
            )
        points = [
            {"resource_id": rid, "day": p.day, "avg": p.avg, "max": p.max}
            for b in batches
            for rid, pts in b.cpu.items()
            for p in pts
        ]
        if points:
            s.execute(insert(CpuPointRow), points)
        costs = [c.model_dump(mode="python") for b in batches for c in b.costs]
        if costs:
            s.execute(insert(CostLineRow), costs)
        quarantined = [q.model_dump() for b in batches for q in b.quarantined]
        if quarantined:
            s.execute(insert(QuarantineRow), quarantined)
        warnings = [w.model_dump() for b in batches for w in b.warnings]
        if warnings:
            s.execute(insert(DataWarningRow), warnings)
        _write_checks(s, checks, now)
        s.merge(MetaRow(key="data_as_of", value=as_of.isoformat()))
    return _report(True, batches, checks)


def _write_checks(s: Session, checks: list[CheckResult], now: dt.datetime) -> None:
    s.execute(delete(QualityResultRow))
    s.execute(
        insert(QualityResultRow),
        [
            {
                "check_id": c.check_id,
                "category": c.category,
                "status": c.status,
                "observed": c.observed,
                "expected": c.expected,
                "detail": c.detail,
                "checked_at": now,
            }
            for c in checks
        ],
    )


def _report(changed: bool, batches: list[SourceBatch], checks: list[CheckResult]) -> IngestReport:
    return IngestReport(
        changed=changed,
        resources=sum(len(b.resources) for b in batches),
        cost_lines=sum(len(b.costs) for b in batches),
        quarantined=sum(len(b.quarantined) for b in batches),
        warnings=sum(len(b.warnings) for b in batches),
        checks=checks,
    )
