"""The saga executor: runs one approved action as a sequence of idempotent steps.

Throttling is retried with exponential backoff and full jitter. A step whose precondition fails stops the
action, and the steps it already completed are compensated in reverse order (a resize that stopped a VM
starts it again). Independent actions are never rolled back because a sibling failed: each action is its own
saga, irreversible steps run last, and a run with some failed actions ends completed_with_failures.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass

from countersign.agent.state import ActionOutcome, PlanAction, StepResult
from countersign.execution.sandbox import (
    COMPENSATION,
    PreconditionFailedError,
    SandboxCloud,
    ThrottledError,
    idempotency_key,
)
from countersign.execution.tokens import CapabilityError


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 0.2
    max_delay_s: float = 2.0

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Full jitter (AWS Architecture Blog, "Exponential Backoff And Jitter")."""
        return rng.uniform(0, min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1)))


class Executor:
    def __init__(
        self,
        sandbox: SandboxCloud,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], dt.datetime],
        retry: RetryPolicy | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._sleep = sleep
        self._clock = clock
        self._retry = retry or RetryPolicy()

    def run(
        self,
        action: PlanAction,
        *,
        token: str | None,
        run_id: str,
        workspace_id: str,
        plan_hash: str,
        throttled: Callable[[], bool],
        seed: int,
    ) -> ActionOutcome:
        rng = random.Random(f"{seed}:{action.id}")
        done: list[str] = []
        steps: list[StepResult] = []
        for step in action.steps:
            key = idempotency_key(run_id, plan_hash, action.id, step)
            attempt = 0
            while True:
                attempt += 1
                try:
                    effect = self._sandbox.apply(
                        token=token,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        plan_hash=plan_hash,
                        action=action,
                        step=step,
                        throttle=attempt == 1 and throttled(),
                    )
                except ThrottledError as exc:
                    if attempt < self._retry.max_attempts:
                        self._sleep(self._retry.delay(attempt, rng))
                        continue
                    steps.append(
                        StepResult(
                            step=step,
                            status="failed",
                            idempotency_key=key,
                            attempts=attempt,
                            detail=str(exc),
                            at=self._clock(),
                        )
                    )
                    return self._fail(
                        action,
                        steps,
                        done,
                        run_id,
                        workspace_id,
                        plan_hash,
                        token,
                        f"{step} throttled {attempt} times",
                    )
                except (PreconditionFailedError, CapabilityError) as exc:
                    steps.append(
                        StepResult(
                            step=step,
                            status="failed",
                            idempotency_key=key,
                            attempts=attempt,
                            detail=str(exc),
                            at=self._clock(),
                        )
                    )
                    return self._fail(action, steps, done, run_id, workspace_id, plan_hash, token, str(exc))
                steps.append(
                    StepResult(
                        step=step,
                        status="replayed" if effect.replayed else "done",
                        idempotency_key=key,
                        attempts=attempt,
                        detail=effect.detail,
                        at=self._clock(),
                        api=str(effect.result.get("api") or "") or None,
                    )
                )
                done.append(step)
                break
        return ActionOutcome(
            action_id=action.id, status="succeeded", steps=steps, detail=steps[-1].detail if steps else ""
        )

    def _fail(
        self,
        action: PlanAction,
        steps: list[StepResult],
        done: list[str],
        run_id: str,
        workspace_id: str,
        plan_hash: str,
        token: str | None,
        reason: str,
    ) -> ActionOutcome:
        compensated = False
        for step in reversed(done):
            inverse = COMPENSATION.get(step)
            if inverse is None:
                continue
            key = idempotency_key(run_id, plan_hash, action.id, inverse)
            try:
                effect = self._sandbox.apply(
                    token=token,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    plan_hash=plan_hash,
                    action=action,
                    step=inverse,
                )
            except (PreconditionFailedError, ThrottledError, CapabilityError) as exc:
                steps.append(
                    StepResult(
                        step=inverse,
                        status="failed",
                        idempotency_key=key,
                        attempts=1,
                        detail=f"compensation failed: {exc}",
                        at=self._clock(),
                    )
                )
                return ActionOutcome(
                    action_id=action.id,
                    status="failed",
                    steps=steps,
                    detail=f"{reason}; compensation failed, needs a human",
                )
            steps.append(
                StepResult(
                    step=inverse,
                    status="compensated",
                    idempotency_key=key,
                    attempts=1,
                    detail=effect.detail,
                    at=self._clock(),
                    api=str(effect.result.get("api") or "") or None,
                )
            )
            compensated = True
        return ActionOutcome(
            action_id=action.id, status="compensated" if compensated else "failed", steps=steps, detail=reason
        )
