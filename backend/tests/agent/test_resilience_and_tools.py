from __future__ import annotations

import random

import pytest

from countersign.agent.faults import Fault, FaultPlan
from countersign.agent.resilience import CircuitBreaker, CircuitOpenError, Retry, call_with_retries
from countersign.models.base import ModelError, ModelRequest
from countersign.tools.registry import MAX_RESULT_BYTES, _bounded
from tests.agent.scripted import FailingModel

pytestmark = pytest.mark.unit
REQ = ModelRequest(
    system="s",
    messages=[{"role": "user", "content": [{"type": "text", "text": "stop idle dev vms"}]}],
    tools=[],
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_the_circuit_opens_after_repeated_failures_and_half_opens_after_cooldown() -> None:
    """FM-06: fail fast instead of piling onto a provider that is down."""
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, cooldown_s=30, clock=clock)
    for _ in range(3):
        breaker.failure()
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        breaker.before("anthropic")
    clock.t = 31
    breaker.before("anthropic")  # half-open probe allowed
    breaker.success()
    assert breaker.state == "closed"


def test_retries_honour_retry_after_and_stop_at_the_limit() -> None:
    waits: list[float] = []
    model = FailingModel("rate_limited", times=2)
    response = call_with_retries(
        model,
        REQ,
        sleep=waits.append,
        rng=random.Random(1),
        on_retry=lambda *_: None,
        breaker=CircuitBreaker(),
    )
    assert response.content and model.calls == 3 and len(waits) == 2
    always = FailingModel("overloaded")
    with pytest.raises(ModelError):
        call_with_retries(
            always,
            REQ,
            sleep=lambda _s: None,
            rng=random.Random(1),
            on_retry=lambda *_: None,
            breaker=CircuitBreaker(),
        )
    assert always.calls == 3


def test_backoff_is_jittered_and_capped() -> None:
    retry = Retry(attempts=5, base_s=0.5, cap_s=4)
    rng = random.Random(7)
    delays = [retry.delay(n, rng, None) for n in range(1, 6) for _ in range(50)]
    assert all(0 <= d <= 4 for d in delays) and len({round(d, 3) for d in delays}) > 50
    assert retry.delay(1, rng, 2.5) == 2.5 and retry.delay(1, rng, 99) == 4


def test_non_retryable_errors_are_raised_at_once() -> None:
    model = FailingModel("bad_request")
    with pytest.raises(ModelError):
        call_with_retries(
            model,
            REQ,
            sleep=lambda _s: pytest.fail("no sleep"),
            rng=random.Random(1),
            on_retry=lambda *_: None,
            breaker=CircuitBreaker(),
        )
    assert model.calls == 1


def test_oversized_tool_results_are_trimmed_and_say_so() -> None:
    big = {"count": 500, "findings": [{"ref": f"i-{i:017x}", "blob": "x" * 200} for i in range(500)]}
    out = _bounded(big)
    assert len(str(out)) < MAX_RESULT_BYTES * 1.2
    assert out["truncated"]["total"] == 500 and out["truncated"]["kept"] < 500


def test_an_inert_fault_plan_keeps_conditions_but_never_fires() -> None:
    fired = {"tool_timeout": 0}
    plan = FaultPlan(["pricing_unavailable", "tool_timeout", "state_drift"], fired)
    inert = plan.inert()
    assert inert.is_active(Fault.PRICING_UNAVAILABLE)  # a price outage still holds for the whole run
    assert not any(inert.fire(f) for f in Fault)
    assert fired == {"tool_timeout": 0}  # the run's own counts are untouched
    assert plan.fire(Fault.TOOL_TIMEOUT)  # and the real plan still has its shot
