"""Durable run state. A run is a state machine persisted after every tick, so any process can resume it."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from countersign.domain.analysis import Operation
from countersign.models.base import Message
from countersign.policy.engine import Check, NotProposed, Risk


class RunStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EXPIRED = "expired"


TERMINAL = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_FAILURES,
        RunStatus.FAILED,
        RunStatus.REJECTED,
        RunStatus.CANCELLED,
        RunStatus.BUDGET_EXHAUSTED,
        RunStatus.EXPIRED,
    }
)
WAITING_FOR_HUMAN = frozenset({RunStatus.AWAITING_INPUT, RunStatus.AWAITING_APPROVAL})

# Every allowed transition. Anything else is a bug and raises.
TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.PLANNING, RunStatus.CANCELLED}),
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.PLANNING,
            RunStatus.AWAITING_INPUT,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.BUDGET_EXHAUSTED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.AWAITING_INPUT: frozenset({RunStatus.PLANNING, RunStatus.CANCELLED, RunStatus.EXPIRED}),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.EXECUTING,
            RunStatus.REJECTED,
            RunStatus.FAILED,  # the policy refused every approved action at decision
            RunStatus.CANCELLED,
            RunStatus.EXPIRED,
        }
    ),
    RunStatus.EXECUTING: frozenset(
        {RunStatus.EXECUTING, RunStatus.VERIFYING, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.VERIFYING: frozenset(
        {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_FAILURES, RunStatus.FAILED}
    ),
}
for _terminal in TERMINAL:
    TRANSITIONS[_terminal] = frozenset()


class InvalidTransitionError(RuntimeError):
    pass


def check_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransitionError(f"{current} -> {target} is not a valid run transition")


class Budgets(BaseModel):
    """Used and allowed. Human wait time is never counted against the wall-clock budget."""

    model_turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    planning_ms: int = 0
    plan_revisions: int = 0
    model_retries: int = 0
    max_model_turns: int
    max_tool_calls: int
    max_input_tokens: int
    max_wall_ms: int
    max_plan_revisions: int
    simulated_tokens: bool

    def exhausted(self) -> str | None:
        if self.model_turns >= self.max_model_turns:
            return f"model turns ({self.model_turns}/{self.max_model_turns})"
        if self.tool_calls >= self.max_tool_calls:
            return f"tool calls ({self.tool_calls}/{self.max_tool_calls})"
        if self.input_tokens >= self.max_input_tokens:
            return f"input tokens ({self.input_tokens}/{self.max_input_tokens})"
        if self.planning_ms >= self.max_wall_ms:
            return f"planning time ({self.planning_ms // 1000}/{self.max_wall_ms // 1000} s)"
        return None


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tool: str
    ref: str = Field(description="The resource this evidence is about; empty for estate-wide facts")
    kind: str
    summary: str
    turn: int


class PlanAction(BaseModel):
    """One change in the request. Only `ref`, `operation`, `target_sku`, `rationale` and `evidence_ids` came
    from the model; every number and every risk below was computed by code."""

    model_config = ConfigDict(frozen=True)

    id: str
    ref: str
    resource_id: str
    name: str
    provider: str
    kind: str
    environment: str
    team: str | None
    owner: str | None
    operation: Operation
    current_sku: str
    target_sku: str | None
    monthly_cost: float | None
    est_monthly_savings: float
    price_source: str | None
    risk: Risk
    reversible: bool
    steps: list[str]
    evidence_ids: list[str]
    rationale: str
    checks: list[Check]
    warnings: list[str]


class ChangePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    caveats: list[str]
    actions: list[PlanAction]
    not_proposed: list[NotProposed] = Field(default_factory=list)
    total_monthly_savings: float
    risk: Risk
    approvals_required: int
    revision: int
    policy_version: int
    hash: str


def plan_hash(
    summary: str,
    caveats: list[str],
    actions: list[dict[str, Any]],
    policy_version: int,
    not_proposed: list[dict[str, Any]] | None = None,
) -> str:
    """Hash of everything an approver approves: any change to the plan invalidates earlier approvals."""
    body: dict[str, Any] = {
        "summary": summary,
        "caveats": caveats,
        "actions": actions,
        "policy": policy_version,
    }
    if not_proposed:
        body["not_proposed"] = not_proposed
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


Verdict = Literal["approve", "reject"]


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    approver: str
    persona: str
    decided_at: dt.datetime
    plan_hash: str
    decisions: dict[str, Verdict]
    note: str | None = None


class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    step: str
    status: Literal["done", "replayed", "failed", "compensated"]
    idempotency_key: str
    attempts: int
    detail: str
    at: dt.datetime
    api: str | None = Field(
        default=None, description="The provider API this step stands for, e.g. ec2:StopInstances"
    )


class ActionOutcome(BaseModel):
    action_id: str
    status: Literal["pending", "succeeded", "failed", "compensated", "rejected", "skipped"]
    steps: list[StepResult] = Field(default_factory=list)
    verified: bool | None = None
    realized_monthly_savings: float = 0.0
    detail: str = ""


class Question(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_use_id: str
    text: str
    options: list[str]


class RunState(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    pending_results: list[dict[str, Any]] = Field(
        default_factory=list, description="tool_results awaiting a human answer"
    )
    budgets: Budgets
    evidence: dict[str, EvidenceItem] = Field(default_factory=dict)
    plan: ChangePlan | None = None
    not_proposed: list[NotProposed] = Field(
        default_factory=list, description="What the last accepted submission reviewed and left out, and why"
    )
    rejected_plans: int = 0
    approvals: list[Decision] = Field(default_factory=list)
    capability: str | None = None
    outcomes: list[ActionOutcome] = Field(default_factory=list)
    report: str | None = None
    question: Question | None = None
    faults: list[str] = Field(default_factory=list)
    faults_fired: dict[str, int] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)
    error: str | None = None
    seed: int = 0

    def next_evidence_id(self) -> str:
        return f"ev-{len(self.evidence) + 1:02d}"

    def degrade(self, reason: str) -> bool:
        if reason in self.degraded:
            return False
        self.degraded.append(reason)
        return True
