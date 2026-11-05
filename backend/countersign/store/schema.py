"""Relational schema (SQLAlchemy 2.0). Postgres in production, SQLite locally and in fast tests;
the CI suite runs against both."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = "0001"


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute as configuration
        dict[str, Any]: JSON,
        list[Any]: JSON,
        dt.datetime: DateTime(timezone=True),
    }


# ---------------------------------------------------------------- ingested data (shared, read-only to runs)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    checksum: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[dt.datetime]
    data_as_of: Mapped[dt.datetime]
    records_read: Mapped[int]
    quarantined: Mapped[int]
    warnings: Mapped[int]
    current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ResourceRow(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(400), primary_key=True)
    ref: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(Text)
    account: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(32))
    environment: Mapped[str] = mapped_column(String(16), index=True)
    team: Mapped[str | None] = mapped_column(String(64), index=True)
    owner: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[dict[str, Any]]
    state: Mapped[str] = mapped_column(String(16))
    native_state: Mapped[str] = mapped_column(String(32))
    sku: Mapped[str] = mapped_column(String(64))
    size_gb: Mapped[int | None]
    created_at: Mapped[dt.datetime | None]
    detached_at: Mapped[dt.datetime | None]
    attached_to: Mapped[str | None] = mapped_column(String(200))
    flags: Mapped[list[Any]]
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(Text)
    billing_key: Mapped[str] = mapped_column(String(400), index=True)


class CpuPointRow(Base):
    __tablename__ = "cpu_points"
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    avg: Mapped[float] = mapped_column(Float)
    max: Mapped[float] = mapped_column(Float)


class CostLineRow(Base):
    __tablename__ = "cost_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_key: Mapped[str | None] = mapped_column(String(400), index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    service_category: Mapped[str] = mapped_column(String(64))
    service_name: Mapped[str] = mapped_column(String(128))
    charge_category: Mapped[str] = mapped_column(String(32))
    billed_cost: Mapped[float] = mapped_column(Float)
    effective_cost: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    sku: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(32))


class QuarantineRow(Base):
    __tablename__ = "quarantine"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    raw: Mapped[str] = mapped_column(Text)


class DataWarningRow(Base):
    __tablename__ = "data_warnings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(Text)
    rule: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text)


class QualityResultRow(Base):
    __tablename__ = "quality_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(String(80), unique=True)
    category: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(8))
    observed: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[dt.datetime]


class PriceCacheRow(Base):
    __tablename__ = "price_cache"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    unit_price: Mapped[float]
    unit: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime]


# ---------------------------------------------------------------- tenancy, runs, approvals, effects, audit


class WorkspaceRow(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[dt.datetime]
    expires_at: Mapped[dt.datetime] = mapped_column(index=True)


class ResourceOverrideRow(Base):
    """A workspace's view of a resource after its approved changes: the shared estate stays untouched."""

    __tablename__ = "resource_overrides"
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    resource_id: Mapped[str] = mapped_column(String(400), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    native_state: Mapped[str] = mapped_column(String(32))
    sku: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[dt.datetime]
    run_id: Mapped[str] = mapped_column(String(40))
    snapshot_ref: Mapped[str | None] = mapped_column(String(64))


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime]
    updated_at: Mapped[dt.datetime]
    state: Mapped[dict[str, Any]]
    plan_hash: Mapped[str | None] = mapped_column(String(64))
    # A lease taken before each tick (compare-and-set), so two concurrent advances never both call the model.
    lease_owner: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[dt.datetime | None]


class RunEventRow(Base):
    __tablename__ = "run_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[dt.datetime]
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]]


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    approver: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[dt.datetime]
    decisions: Mapped[dict[str, Any]]
    __table_args__ = (
        UniqueConstraint("run_id", "plan_hash", "approver", name="uq_one_decision_per_approver"),
    )


class ExecutionRow(Base):
    """One side effect in the sandbox cloud, keyed by an idempotency key: replays return the stored result."""

    __tablename__ = "executions"
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any]]
    created_at: Mapped[dt.datetime]
    updated_at: Mapped[dt.datetime]


class AuditRow(Base):
    """Append-only and hash-chained per workspace: editing or deleting any entry breaks verification."""

    __tablename__ = "audit_log"
    workspace_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[dt.datetime]
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(400))
    payload: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    route: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int]
    response: Mapped[dict[str, Any]]
    created_at: Mapped[dt.datetime]


class RateLimitRow(Base):
    __tablename__ = "rate_limits"
    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[dt.datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class MetaRow(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


Index("ix_cost_lines_resource_day", CostLineRow.resource_key, CostLineRow.day)
