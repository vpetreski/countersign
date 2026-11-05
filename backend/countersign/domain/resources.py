"""The canonical model every source is normalized into. Pure data: no I/O, no framework."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    AWS = "aws"
    AZURE = "azure"


class Kind(StrEnum):
    VM = "vm"
    DISK = "disk"
    PUBLIC_IP = "public_ip"


class Environment(StrEnum):
    PROD = "prod"
    STAGING = "staging"
    DEV = "dev"
    TEST = "test"
    SANDBOX = "sandbox"
    UNKNOWN = "unknown"


class State(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"  # AWS stopped, Azure deallocated: no compute charge either way
    ATTACHED = "attached"
    UNATTACHED = "unattached"
    RESERVED = "reserved"  # Azure: attached to a deallocated VM
    ASSOCIATED = "associated"
    UNASSOCIATED = "unassociated"
    DELETED = "deleted"
    RELEASED = "released"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Resource(Frozen):
    """One cloud resource after normalization. `id` is the provider's full id (ARN or ARM id)."""

    id: str
    ref: str = Field(description="Short human id: i-…, vol-…, eipalloc-… or the Azure resource name")
    provider: Provider
    kind: Kind
    name: str
    account: str
    region: str
    environment: Environment
    team: str | None
    owner: str | None
    tags: dict[str, str] = Field(description="Normalized: lowercase keys, secrets masked at ingest")
    state: State
    native_state: str = Field(description="The provider's own state value, kept for provenance")
    sku: str
    size_gb: int | None = None
    created_at: dt.datetime | None = Field(description="None where the provider's API has no creation time")
    detached_at: dt.datetime | None = None
    attached_to: str | None = None
    flags: tuple[str, ...] = ()
    source: str
    source_ref: str = Field(description="file#json-pointer of the record this came from")

    @property
    def billing_key(self) -> str:
        """The id FOCUS exports use for this resource, compared case-insensitively."""
        if self.provider is Provider.AWS:
            kind = {Kind.VM: "instance", Kind.DISK: "volume", Kind.PUBLIC_IP: "elastic-ip"}[self.kind]
            return f"arn:aws:ec2:{self.region}:{self.account}:{kind}/{self.ref}".lower()
        return self.id.lower()


class CpuPoint(Frozen):
    day: dt.date
    avg: float
    max: float


class UtilizationSummary(Frozen):
    resource_id: str
    window_days: int
    expected_days: int
    datapoints: int
    coverage: float
    avg: float | None
    p95_daily_avg: float | None
    max: float | None
    last_datapoint: dt.date | None


class CostLine(Frozen):
    resource_key: str | None
    provider: Provider
    day: dt.date
    service_category: str
    service_name: str
    charge_category: str
    billed_cost: float
    effective_cost: float
    currency: str
    sku: str | None
    region: str | None


class PriceQuote(Frozen):
    provider: Provider
    kind: Kind
    sku: str
    region: str
    unit: str  # "hour" or "month"
    unit_price_usd: float
    monthly_usd: float
    source: str  # live | cache | snapshot
    as_of: dt.datetime
    reference: str


class QuarantinedRecord(Frozen):
    source: str
    source_ref: str
    reason: str
    raw: str


class DataWarning(Frozen):
    source: str
    source_ref: str
    rule: str
    detail: str
