"""Fault injection. Each fault is deterministic (it fires a fixed number of times per run), so a failure the
demo shows is also a failure the test suite and the evaluation reproduce exactly."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Fault(StrEnum):
    PRICING_UNAVAILABLE = "pricing_unavailable"
    TOOL_TIMEOUT = "tool_timeout"
    MODEL_OVERLOADED = "model_overloaded"
    THROTTLING = "throttling"
    STATE_DRIFT = "state_drift"


@dataclass(frozen=True)
class FaultInfo:
    fault: Fault
    title: str
    effect: str
    expected: str


CATALOGUE: tuple[FaultInfo, ...] = (
    FaultInfo(
        Fault.PRICING_UNAVAILABLE,
        "Price source down",
        "Every price lookup fails: the live API and the snapshots are unreachable.",
        "Estimates fall back to billing history, the run is marked degraded and the report says so.",
    ),
    FaultInfo(
        Fault.TOOL_TIMEOUT,
        "Metrics timeout",
        "The first utilization lookup times out.",
        "The tool returns a retryable error; the agent retries once and carries on.",
    ),
    FaultInfo(
        Fault.MODEL_OVERLOADED,
        "Model overloaded",
        "The first model call fails with HTTP 529 (overloaded).",
        "The runtime retries with exponential backoff and jitter; the turn succeeds.",
    ),
    FaultInfo(
        Fault.THROTTLING,
        "Cloud API throttling",
        "The first attempt of every execution step is throttled (RequestLimitExceeded).",
        "The executor retries each step with backoff; idempotency keys keep every effect single.",
    ),
    FaultInfo(
        Fault.STATE_DRIFT,
        "State drift",
        "Between approval and execution, one approved resource changes state.",
        "The precondition check stops that action; the others run; the run ends completed_with_failures.",
    ),
)
BY_NAME = {f.fault.value: f for f in CATALOGUE}

# How many times each fault fires in one run.
SHOTS = {
    Fault.PRICING_UNAVAILABLE: 10_000,
    Fault.TOOL_TIMEOUT: 1,
    Fault.MODEL_OVERLOADED: 1,
    Fault.THROTTLING: 10_000,
    Fault.STATE_DRIFT: 1,
}


class FaultPlan:
    """The faults active in one run, with the shots already fired (persisted in the run state)."""

    def __init__(self, active: list[str], fired: dict[str, int]) -> None:
        self.active = frozenset(Fault(a) for a in active)
        self.fired = fired

    def is_active(self, fault: Fault) -> bool:
        return fault in self.active

    def fire(self, fault: Fault) -> bool:
        """True if the fault applies to this call (and counts the shot)."""
        if fault not in self.active or self.fired.get(fault.value, 0) >= SHOTS[fault]:
            return False
        self.fired[fault.value] = self.fired.get(fault.value, 0) + 1
        return True

    def inert(self) -> FaultPlan:
        """The same faults with every shot already spent, on a copy of the counts: a condition of the run (a
        price outage) still holds through `is_active`, but nothing fires and the run's own counts stay as they
        are. For checks the runtime makes on its own behalf, which are not tool calls."""
        return FaultPlan([f.value for f in self.active], {f.value: SHOTS[f] for f in self.active})
