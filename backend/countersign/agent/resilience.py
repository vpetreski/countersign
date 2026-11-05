"""Retries and a circuit breaker for model calls.

Retries: exponential backoff with full jitter, honouring a provider's retry-after when it sends one, only for
errors classified retryable (overloaded, rate limited, timeout, connection, server). The circuit breaker is
per provider and per process: after `threshold` consecutive failures it opens for `cooldown_s`, and calls fail
fast instead of piling onto a provider that is down; one probe call is let through when the cooldown ends.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from countersign.models.base import ChatModel, ModelError, ModelRequest, ModelResponse


class CircuitOpenError(ModelError):
    def __init__(self, provider: str, retry_in: float) -> None:
        super().__init__(
            "overloaded",
            f"circuit open for {provider}: failing fast for {retry_in:.0f} s",
            retry_after=retry_in,
        )


@dataclass
class CircuitBreaker:
    threshold: int = 5
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _failures: int = 0
    _opened_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def before(self, provider: str) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            waited = self.clock() - self._opened_at
            if waited < self.cooldown_s:
                raise CircuitOpenError(provider, self.cooldown_s - waited)
            self._opened_at = None  # half-open: let one call probe

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._opened_at = self.clock()

    @property
    def state(self) -> str:
        return "open" if self._opened_at is not None else ("degraded" if self._failures else "closed")


BREAKERS: dict[str, CircuitBreaker] = {}


def breaker_for(provider: str) -> CircuitBreaker:
    return BREAKERS.setdefault(provider, CircuitBreaker())


@dataclass(frozen=True)
class Retry:
    attempts: int = 3
    base_s: float = 0.5
    cap_s: float = 8.0

    def delay(self, attempt: int, rng: random.Random, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(self.cap_s, retry_after)
        return rng.uniform(0, min(self.cap_s, self.base_s * 2 ** (attempt - 1)))


def call_with_retries(
    model: ChatModel,
    request: ModelRequest,
    *,
    sleep: Callable[[float], None],
    rng: random.Random,
    on_retry: Callable[[int, ModelError, float], None],
    retry: Retry = Retry(),  # noqa: B008 - frozen dataclass, safe as a default
    breaker: CircuitBreaker | None = None,
) -> ModelResponse:
    breaker = breaker or breaker_for(model.provider)
    for attempt in range(1, retry.attempts + 1):
        breaker.before(model.provider)
        try:
            response = model.complete(request)
        except ModelError as exc:
            breaker.failure()
            if not exc.retryable or attempt == retry.attempts:
                raise
            wait = retry.delay(attempt, rng, exc.retry_after)
            on_retry(attempt, exc, wait)
            sleep(wait)
            continue
        breaker.success()
        return response
    raise AssertionError("unreachable")
