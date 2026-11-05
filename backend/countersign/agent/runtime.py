"""The run runtime: a durable, resumable state machine driven one tick at a time.

Each `advance` takes a lease on the run (compare-and-set), loads its state, does exactly one unit of work (one
model turn with its tool calls, one approved action, or the verification) and saves the new state, its events
and its audit entries in one transaction. A crash anywhere leaves the run resumable from its last saved state;
side effects are idempotent by key, so resuming never repeats one.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import secrets
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update

from countersign.agent.faults import Fault, FaultPlan
from countersign.agent.prompts import SYSTEM_PROMPT
from countersign.agent.resilience import call_with_retries
from countersign.agent.state import (
    TERMINAL,
    WAITING_FOR_HUMAN,
    ActionOutcome,
    Budgets,
    ChangePlan,
    Decision,
    PlanAction,
    Question,
    RunState,
    RunStatus,
    Verdict,
    check_transition,
    plan_hash,
)
from countersign.audit import chain
from countersign.data.estate import ESTATE, Estate
from countersign.data.pricing import PriceCatalog
from countersign.domain.analysis import Operation
from countersign.domain.resources import State
from countersign.execution import tokens
from countersign.execution.executor import Executor
from countersign.execution.sandbox import STEPS, SandboxCloud
from countersign.models.base import ChatModel, ModelError, ModelRequest, ModelResponse
from countersign.observability.tracing import agent_span, collect, provider_name, tracer
from countersign.policy.engine import REVERSIBLE, Policy, PolicyVerdict
from countersign.security.screening import redact
from countersign.settings import Settings
from countersign.store.db import Database
from countersign.store.schema import RunEventRow, RunRow
from countersign.tools.catalog import AskOperatorIn, SubmitPlanIn, ToolContext, evaluate, registry

REGISTRY = registry()
LEASE_SECONDS = 120
HUMAN_WAIT_LIMIT = dt.timedelta(hours=24)
CAPABILITY_TTL = dt.timedelta(hours=1)
END_STATE = {
    Operation.STOP_VM: State.STOPPED,
    Operation.RESIZE_VM: State.RUNNING,
    Operation.DELETE_DISK: State.DELETED,
    Operation.RELEASE_IP: State.RELEASED,
}


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"


@dataclass(frozen=True)
class Principal:
    sub: str
    workspace_id: str
    persona: str
    role: Role
    name: str


class RunError(Exception):
    status = 400
    code = "run_error"


class RunNotFoundError(RunError):
    status = 404
    code = "run_not_found"


class RunConflictError(RunError):
    status = 409
    code = "conflict"


class RunForbiddenError(RunError):
    status = 403
    code = "forbidden"


class SimulatedCrashError(RuntimeError):
    """Raised by tests between a side effect and the run's own commit."""


@dataclass
class Tick:
    row_id: str
    workspace_id: str
    created_by: str
    request: str
    status: RunStatus
    state: RunState
    lease: str
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    audit: list[tuple[str, str, str, dict[str, Any]]] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)

    def event(self, type_: str, payload: dict[str, Any]) -> None:
        self.events.append((type_, payload))

    def move(self, target: RunStatus, reason: str = "") -> None:
        check_transition(self.status, target)
        if target is not self.status:
            self.event("status", {"from": self.status, "to": target, "reason": reason})
        self.status = target


ModelFactory = Callable[[RunState, Settings], ChatModel]


class Runtime:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        model_factory: ModelFactory,
        catalog: PriceCatalog,
        policy: Policy,
        clock: Callable[[], dt.datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.model_factory = model_factory
        self.catalog = catalog
        self.policy = policy
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self.sleep = sleep or time.sleep
        self.sandbox = SandboxCloud(db, settings.capability_secret.get_secret_value(), self.clock)
        self.executor = Executor(self.sandbox, sleep=self.sleep, clock=self.clock)
        self.crash_after_effect = False  # test hook: die between the sandbox commit and the run commit

    # ------------------------------------------------------------------ commands

    def create(
        self, principal: Principal, request: str, faults: list[str], *, model_name: str | None = None
    ) -> str:
        if principal.role is not Role.OPERATOR:
            raise RunForbiddenError("only an operator can start a run")
        text, kinds = redact(request.strip())
        now = self.clock()
        s = self.settings
        seed = random.Random(f"{principal.workspace_id}:{now.isoformat()}:{secrets.token_hex(4)}").randrange(
            2**31
        )
        state = RunState(
            messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
            budgets=Budgets(
                max_model_turns=s.max_model_turns,
                max_tool_calls=s.max_tool_calls,
                max_input_tokens=s.max_input_tokens,
                max_wall_ms=s.max_wall_seconds * 1000,
                max_plan_revisions=s.max_plan_revisions,
                simulated_tokens=s.model_mode == "offline",
            ),
            faults=sorted(set(faults)),
            seed=seed,
        )
        model = model_name or self.model_factory(state, s).model
        run_id = "run_" + secrets.token_hex(8)
        with self.db.session() as session:
            session.add(
                RunRow(
                    id=run_id,
                    workspace_id=principal.workspace_id,
                    created_by=principal.sub,
                    request=text,
                    status=RunStatus.CREATED,
                    version=0,
                    model=model,
                    created_at=now,
                    updated_at=now,
                    state=state.model_dump(mode="json"),
                    plan_hash=None,
                )
            )
            session.flush()
            session.add(
                RunEventRow(
                    run_id=run_id,
                    seq=1,
                    at=now,
                    type="created",
                    payload={
                        "request": text,
                        "faults": state.faults,
                        "model": model,
                        "redacted": list(kinds),
                    },
                )
            )
            chain.append(
                session,
                principal.workspace_id,
                actor=principal.sub,
                action="run.created",
                subject=run_id,
                payload={"request": text, "faults": state.faults, "model": model},
                at=now,
            )
        return run_id

    @contextmanager
    def _leased(self, principal: Principal, run_id: str) -> Iterator[Tick]:
        """Lease the run, yield its state, save on success, release the lease on any failure."""
        tick = self._acquire(principal, run_id)
        try:
            yield tick
        except BaseException:
            self._release(tick)
            raise
        self._save(tick)

    def advance(self, principal: Principal, run_id: str) -> None:
        if principal.role not in (Role.OPERATOR, Role.APPROVER):
            raise RunForbiddenError("viewers cannot advance runs")
        with self._leased(principal, run_id) as tick:
            with collect() as spans, agent_span(run_id, tick.status):
                self._tick(tick)
            tick.spans = spans

    def answer(self, principal: Principal, run_id: str, text: str) -> None:
        if principal.role is not Role.OPERATOR:
            raise RunForbiddenError("only an operator can answer the agent")
        with self._leased(principal, run_id) as tick:
            st = tick.state
            if tick.status is not RunStatus.AWAITING_INPUT or st.question is None:
                raise RunConflictError(f"the run is {tick.status}, not waiting for an answer")
            answer, _ = redact(text.strip())
            reply = {
                "type": "tool_result",
                "tool_use_id": st.question.tool_use_id,
                "content": json.dumps({"operator_answer": answer}),
            }
            st.messages.append({"role": "user", "content": [*st.pending_results, reply]})
            st.pending_results, st.question = [], None
            tick.event("answer", {"by": principal.sub, "text": answer})
            tick.audit.append((principal.sub, "run.answered", run_id, {"answer": answer}))
            tick.move(RunStatus.PLANNING, "operator answered")

    def decide(
        self,
        principal: Principal,
        run_id: str,
        plan_hash_: str,
        decisions: Mapping[str, str],
        note: str | None,
    ) -> None:
        if principal.role is not Role.APPROVER:
            raise RunForbiddenError("only an approver can countersign")
        with self._leased(principal, run_id) as tick:
            self._decide(tick, principal, plan_hash_, dict(decisions), note)

    def cancel(self, principal: Principal, run_id: str) -> None:
        if principal.role is not Role.OPERATOR:
            raise RunForbiddenError("only an operator can cancel a run")
        with self._leased(principal, run_id) as tick:
            if tick.status in TERMINAL:
                raise RunConflictError(f"the run is already {tick.status}")
            for outcome in tick.state.outcomes:
                if outcome.status == "pending":
                    outcome.status, outcome.detail = "skipped", "cancelled before execution"
            tick.state.report = _report(tick, cancelled=True)
            tick.audit.append((principal.sub, "run.cancelled", run_id, {"at_status": tick.status}))
            tick.move(RunStatus.CANCELLED, f"cancelled by {principal.persona}")

    # ------------------------------------------------------------------ one tick

    def _tick(self, tick: Tick) -> None:
        now = self.clock()
        if tick.status in TERMINAL:
            return
        if tick.status in WAITING_FOR_HUMAN:
            if now - self._updated_at(tick) > HUMAN_WAIT_LIMIT:
                tick.state.report = "The run expired after waiting 24 hours for a human."
                tick.audit.append(("system", "run.expired", tick.row_id, {"at_status": tick.status}))
                tick.move(RunStatus.EXPIRED, "no human response within 24 hours")
            return
        if tick.status is RunStatus.CREATED:
            tick.move(RunStatus.PLANNING, "started")
            for f in tick.state.faults:
                tick.event("fault_injected", {"fault": f})
            return
        if tick.status is RunStatus.PLANNING:
            self._plan(tick)
        elif tick.status is RunStatus.EXECUTING:
            self._execute(tick)
        elif tick.status is RunStatus.VERIFYING:
            self._verify(tick)

    def _updated_at(self, tick: Tick) -> dt.datetime:
        with self.db.session() as s:
            row = s.get(RunRow, tick.row_id)
            at = row.updated_at if row else self.clock()
        return at.replace(tzinfo=dt.UTC) if at.tzinfo is None else at

    # ------------------------------------------------------------------ planning

    def _estate(self, workspace_id: str) -> Estate:
        with self.db.session() as s:
            return ESTATE.view(s, workspace_id)

    def _plan(self, tick: Tick) -> None:
        st = tick.state
        if (reason := st.budgets.exhausted()) is not None:
            st.report = _partial_report(st, f"The run stopped: its budget for {reason} is used up.")
            tick.audit.append(("system", "run.budget_exhausted", tick.row_id, {"budget": reason}))
            tick.move(RunStatus.BUDGET_EXHAUSTED, reason)
            return
        faults = FaultPlan(st.faults, st.faults_fired)
        model: ChatModel = _FaultyModel(self.model_factory(st, self.settings), faults)
        request = ModelRequest(
            system=SYSTEM_PROMPT,
            messages=st.messages,
            tools=REGISTRY.definitions(),
            max_tokens=4096,
            metadata={"run_id": tick.row_id, "seed": str(st.seed)},
        )
        started = time.perf_counter()
        with tracer.start_as_current_span(f"chat {model.model}", kind=_client_kind()) as span:
            span.set_attributes(
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": provider_name(model.provider),
                    "gen_ai.request.model": model.model,
                    "gen_ai.request.max_tokens": 4096,
                    "gen_ai.conversation.id": tick.row_id,
                }
            )
            try:
                response = call_with_retries(
                    model,
                    request,
                    sleep=self.sleep,
                    rng=random.Random(st.seed + st.budgets.model_turns),
                    on_retry=lambda n, e, w: self._on_retry(tick, n, e, w),
                )
            except ModelError as exc:
                span.set_attribute("error.type", exc.kind)
                st.budgets.planning_ms += int((time.perf_counter() - started) * 1000)
                if exc.retryable:
                    if st.degrade(f"model_unavailable: {exc}"):
                        tick.event("degraded", {"reason": f"model unavailable after retries: {exc}"})
                    return  # stays planning; the next advance retries
                st.error = f"model error ({exc.kind}): {exc}"
                st.report = _partial_report(
                    st, "The model provider rejected the request, so the run stopped safely."
                )
                tick.move(RunStatus.FAILED, st.error)
                return
            _usage_attributes(span, response)
        st.budgets.planning_ms += int((time.perf_counter() - started) * 1000)
        st.budgets.model_turns += 1
        st.budgets.input_tokens += response.usage.input_tokens
        st.budgets.output_tokens += response.usage.output_tokens
        st.messages.append({"role": "assistant", "content": response.content})
        tick.event(
            "model_turn",
            {
                "turn": st.budgets.model_turns,
                "text": response.text(),
                "stop_reason": response.stop_reason,
                "tool_calls": [{"id": b["id"], "name": b["name"]} for b in response.tool_uses()],
                "usage": response.usage.model_dump(),
                "simulated": response.simulated,
                "model": response.model,
            },
        )
        if response.stop_reason == "tool_use" and response.tool_uses():
            self._tools(tick, response)
        elif response.stop_reason in ("end_turn", "stop_sequence"):
            st.report = response.text() or "The agent finished without proposing a change."
            tick.event("report", {"text": st.report})
            tick.audit.append(("agent", "run.completed", tick.row_id, {"actions": 0}))
            tick.move(RunStatus.COMPLETED, "finished without a change request")
        elif response.stop_reason == "refusal":
            st.report = "The model declined this request. " + response.text()
            tick.event("refusal", {"text": response.text()})
            tick.move(RunStatus.COMPLETED, "the model declined")
        elif response.stop_reason == "pause_turn":
            return
        else:
            st.error = "the model's reply hit max_tokens before it finished"
            st.report = _partial_report(st, "The run stopped: the model's reply was cut off.")
            tick.move(RunStatus.FAILED, st.error)

    def _on_retry(self, tick: Tick, attempt: int, exc: ModelError, wait: float) -> None:
        tick.state.budgets.model_retries += 1
        tick.event(
            "model_retry",
            {
                "attempt": attempt,
                "kind": exc.kind,
                "status": exc.status,
                "wait_s": round(wait, 3),
                "detail": str(exc),
            },
        )

    def _tools(self, tick: Tick, response: ModelResponse) -> None:
        st = tick.state
        faults = FaultPlan(st.faults, st.faults_fired)
        ctx = ToolContext(
            state=st,
            estate=self._estate(tick.workspace_id),
            catalog=self.catalog,
            live_prices=self.settings.live_prices,
            policy=self.policy,
            faults=faults,
            turn=st.budgets.model_turns,
            timeout_s=self.settings.tool_timeout_seconds,
        )
        results: list[dict[str, Any]] = []
        control: dict[str, Any] | None = None
        for block in response.tool_uses():
            name, call_id, raw = block["name"], block["id"], block.get("input") or {}
            if name in ("submit_change_plan", "ask_operator"):
                if control is None:
                    control = block
                else:
                    results.append(
                        _result(
                            call_id,
                            {"error": "only one submit_change_plan or ask_operator per turn"},
                            error=True,
                        )
                    )
                continue
            st.budgets.tool_calls += 1
            with tracer.start_as_current_span(f"execute_tool {name}") as span:
                span.set_attributes(
                    {
                        "gen_ai.operation.name": "execute_tool",
                        "gen_ai.tool.name": name,
                        "gen_ai.tool.call.id": call_id,
                        "gen_ai.tool.type": "function",
                        "gen_ai.conversation.id": tick.row_id,
                    }
                )
                outcome = REGISTRY.call(ctx, name, raw, self.settings.tool_timeout_seconds)
                if not outcome.ok:
                    span.set_attribute("error.type", outcome.error_kind or "error")
            results.append(_result(call_id, outcome.output, error=not outcome.ok))
            tick.event(
                "tool_call",
                {
                    "id": call_id,
                    "name": name,
                    "input": raw,
                    "ok": outcome.ok,
                    "error_kind": outcome.error_kind,
                    "duration_ms": outcome.duration_ms,
                    "evidence_ids": outcome.evidence_ids,
                    "output": _preview(outcome.output),
                },
            )
            if not outcome.ok:
                tick.event(
                    "tool_error",
                    {
                        "id": call_id,
                        "name": name,
                        "kind": outcome.error_kind,
                        "error": outcome.output.get("error"),
                    },
                )
            for reason in ctx.newly_degraded:
                tick.event("degraded", {"reason": reason})
            ctx.newly_degraded.clear()
        if control is None:
            st.messages.append({"role": "user", "content": results})
            return
        if control["name"] == "ask_operator":
            self._ask(tick, control, results)
        else:
            self._submit(tick, ctx, control, results)

    def _ask(self, tick: Tick, block: dict[str, Any], results: list[dict[str, Any]]) -> None:
        st = tick.state
        try:
            args = AskOperatorIn.model_validate(block.get("input") or {})
        except ValidationError as exc:
            results.append(
                _result(block["id"], {"error": "invalid input", "problems": _problems(exc)}, error=True)
            )
            st.messages.append({"role": "user", "content": results})
            return
        st.question = Question(tool_use_id=block["id"], text=args.question, options=args.options)
        st.pending_results = results
        tick.event("question", {"text": args.question, "options": args.options})
        tick.move(RunStatus.AWAITING_INPUT, "the agent asked the operator a question")

    def _submit(
        self, tick: Tick, ctx: ToolContext, block: dict[str, Any], results: list[dict[str, Any]]
    ) -> None:
        st = tick.state
        try:
            args = SubmitPlanIn.model_validate(block.get("input") or {})
        except ValidationError as exc:
            results.append(
                _result(block["id"], {"error": "invalid input", "problems": _problems(exc)}, error=True)
            )
            st.messages.append({"role": "user", "content": results})
            tick.event("tool_error", {"id": block["id"], "name": "submit_change_plan", "kind": "validation"})
            return
        verdict = evaluate(ctx, args.actions)
        tick.event(
            "policy_evaluated",
            {
                "allowed": verdict.allowed,
                "risk": verdict.risk,
                "approvals_required": verdict.approvals_required,
                "violations": [
                    {"ref": v.ref, "rules": [c.rule for c in v.violations]}
                    for v in verdict.actions
                    if not v.allowed
                ],
            },
        )
        if not verdict.allowed:
            st.budgets.plan_revisions += 1
            st.rejected_plans += 1
            violations = [
                {
                    "ref": v.ref,
                    "violations": [
                        {"rule": c.rule, "name": c.name, "detail": c.detail} for c in v.violations
                    ],
                }
                for v in verdict.actions
                if not v.allowed
            ]
            results.append(
                _result(
                    block["id"],
                    {
                        "accepted": False,
                        "reason": "policy violations; revise and resubmit",
                        "actions": violations,
                    },
                    error=True,
                )
            )
            st.messages.append({"role": "user", "content": results})
            tick.event(
                "plan_rejected",
                {
                    "revision": st.rejected_plans,
                    "actions": violations,
                    "proposed": [a.model_dump(mode="json") for a in args.actions],
                },
            )
            tick.audit.append(("policy", "plan.denied", tick.row_id, {"violations": violations}))
            if st.budgets.plan_revisions > st.budgets.max_plan_revisions:
                st.error = "the agent kept proposing changes the policy forbids"
                st.report = _partial_report(
                    st, "The run stopped: the agent kept proposing changes the policy forbids."
                )
                tick.move(RunStatus.FAILED, st.error)
            return
        if not args.actions:
            st.report = args.summary
            results.append(
                _result(block["id"], {"accepted": True, "status": "nothing to approve; the run is complete"})
            )
            st.messages.append({"role": "user", "content": results})
            tick.event("report", {"text": st.report})
            tick.audit.append(("agent", "run.completed", tick.row_id, {"actions": 0}))
            tick.move(RunStatus.COMPLETED, "no change proposed")
            return
        plan = self._build_plan(ctx, args, verdict, revision=st.rejected_plans)
        st.plan = plan
        results.append(
            _result(
                block["id"],
                {
                    "accepted": True,
                    "plan_hash": plan.hash,
                    "status": "awaiting human approval",
                    "approvals_required": plan.approvals_required,
                },
            )
        )
        st.messages.append({"role": "user", "content": results})
        tick.event(
            "plan_submitted",
            {
                "hash": plan.hash,
                "actions": len(plan.actions),
                "risk": plan.risk,
                "total_monthly_savings": plan.total_monthly_savings,
                "approvals_required": plan.approvals_required,
            },
        )
        tick.audit.append(
            (
                "agent",
                "plan.submitted",
                tick.row_id,
                {
                    "plan_hash": plan.hash,
                    "actions": [a.id + ":" + a.ref for a in plan.actions],
                    "total_monthly_savings": plan.total_monthly_savings,
                    "risk": plan.risk,
                },
            )
        )
        tick.move(RunStatus.AWAITING_APPROVAL, "change request submitted")

    def _build_plan(
        self, ctx: ToolContext, args: SubmitPlanIn, verdict: PolicyVerdict, revision: int
    ) -> ChangePlan:
        findings = ctx.finding_map()
        staged: list[tuple[Any, ...]] = []
        for index, proposed in enumerate(args.actions):
            r = ctx.estate.resolve(proposed.ref)
            assert r is not None  # the verdict passed P-008
            f = findings[r.ref]
            staged.append((not REVERSIBLE[proposed.operation], index, proposed, r, f, verdict.actions[index]))
        staged.sort(key=lambda s: (s[0], s[1]))  # reversible first; irreversible steps run last
        actions = [
            PlanAction(
                id=f"act-{n:02d}",
                ref=r.ref,
                resource_id=r.id,
                name=r.name,
                provider=r.provider,
                kind=r.kind,
                environment=r.environment,
                team=r.team,
                owner=r.owner,
                operation=p.operation,
                current_sku=r.sku,
                target_sku=p.target_sku or f.target_sku,
                monthly_cost=f.monthly_cost_usd,
                est_monthly_savings=round(f.est_monthly_savings_usd or 0.0, 2),
                price_source=f.price_source,
                risk=v.risk,
                reversible=REVERSIBLE[p.operation],
                steps=STEPS[p.operation],
                evidence_ids=p.evidence_ids,
                rationale=p.rationale,
                checks=v.checks,
                warnings=list(f.warnings),
            )
            for n, (_irrev, _i, p, r, f, v) in enumerate(staged, start=1)
        ]
        dumped = [a.model_dump(mode="json") for a in actions]
        assert verdict.risk is not None
        return ChangePlan(
            summary=args.summary,
            caveats=args.caveats,
            actions=actions,
            total_monthly_savings=round(sum(a.est_monthly_savings for a in actions), 2),
            risk=verdict.risk,
            approvals_required=verdict.approvals_required,
            revision=revision,
            policy_version=verdict.policy_version,
            hash=plan_hash(args.summary, args.caveats, dumped, verdict.policy_version),
        )

    # ------------------------------------------------------------------ approval

    def _decide(
        self, tick: Tick, principal: Principal, plan_hash_: str, decisions: dict[str, str], note: str | None
    ) -> None:
        st = tick.state
        if tick.status is not RunStatus.AWAITING_APPROVAL or st.plan is None:
            raise RunConflictError(f"the run is {tick.status}, not awaiting approval")
        if plan_hash_ != st.plan.hash:
            raise RunConflictError("the plan you reviewed is not the current plan; reload and review again")
        if self.policy.separation_of_duties and principal.sub == tick.created_by:
            raise RunForbiddenError(
                "separation of duties: the requester cannot approve their own change request"
            )
        if any(d.approver == principal.sub and d.plan_hash == plan_hash_ for d in st.approvals):
            raise RunConflictError("you already decided on this plan")
        ids = {a.id for a in st.plan.actions}
        if set(decisions) != ids or any(v not in ("approve", "reject") for v in decisions.values()):
            raise RunConflictError("decide every action of the plan: approve or reject each one")
        now = self.clock()
        decision = Decision(
            approver=principal.sub,
            persona=principal.persona,
            decided_at=now,
            plan_hash=plan_hash_,
            decisions=cast("dict[str, Verdict]", dict(decisions)),
            note=note,
        )
        st.approvals.append(decision)
        approved_now = sorted(k for k, v in decisions.items() if v == "approve")
        tick.event(
            "approval",
            {
                "by": principal.sub,
                "persona": principal.persona,
                "name": principal.name,
                "approve": approved_now,
                "reject": sorted(ids - set(approved_now)),
                "note": note,
            },
        )
        tick.audit.append(
            (
                principal.sub,
                "plan.decided",
                tick.row_id,
                {"plan_hash": plan_hash_, "decisions": decisions, "note": note},
            )
        )
        rejected = {a for d in st.approvals for a, v in d.decisions.items() if v == "reject"}
        remaining = ids - rejected
        if not remaining:
            st.outcomes = [
                ActionOutcome(action_id=a.id, status="rejected", detail="rejected by an approver")
                for a in st.plan.actions
            ]
            st.report = _report(tick)
            tick.audit.append(("system", "run.rejected", tick.row_id, {"plan_hash": plan_hash_}))
            tick.move(RunStatus.REJECTED, "every action was rejected")
            return
        approvers = sorted({d.approver for d in st.approvals})
        if len(approvers) < st.plan.approvals_required:
            tick.event(
                "awaiting_more_approvals", {"have": len(approvers), "need": st.plan.approvals_required}
            )
            return
        exp = int((now + CAPABILITY_TTL).timestamp())
        st.capability = tokens.mint(
            self.settings.capability_secret.get_secret_value(),
            run_id=tick.row_id,
            workspace_id=tick.workspace_id,
            plan_hash=plan_hash_,
            action_ids=sorted(remaining),
            approvers=approvers,
            exp=exp,
        )
        st.outcomes = [
            ActionOutcome(
                action_id=a.id,
                status="pending" if a.id in remaining else "rejected",
                detail="" if a.id in remaining else "rejected by an approver",
            )
            for a in st.plan.actions
        ]
        tick.event(
            "capability_minted", {"actions": sorted(remaining), "approvers": approvers, "expires_at": exp}
        )
        tick.audit.append(
            (
                "system",
                "capability.minted",
                tick.row_id,
                {"plan_hash": plan_hash_, "actions": sorted(remaining), "approvers": approvers},
            )
        )
        tick.move(RunStatus.EXECUTING, f"countersigned by {len(approvers)} approver(s)")

    # ------------------------------------------------------------------ execution

    def _execute(self, tick: Tick) -> None:
        st = tick.state
        assert st.plan is not None
        faults = FaultPlan(st.faults, st.faults_fired)
        actions = {a.id: a for a in st.plan.actions}
        pending = [o for o in st.outcomes if o.status == "pending"]
        if pending and faults.fire(Fault.STATE_DRIFT):
            detail = self.sandbox.drift(tick.workspace_id, actions[pending[0].action_id])
            st.degrade("state_drift: " + detail)
            tick.event("degraded", {"reason": f"state drift injected: {detail}"})
        if not pending:
            tick.move(RunStatus.VERIFYING, "every approved action ran")
            return
        outcome = pending[0]
        action = actions[outcome.action_id]
        with tracer.start_as_current_span(f"execute_action {action.operation}") as span:
            span.set_attributes(
                {
                    "countersign.action.id": action.id,
                    "countersign.action.ref": action.ref,
                    "countersign.action.operation": action.operation,
                }
            )
            result = self.executor.run(
                action,
                token=st.capability,
                run_id=tick.row_id,
                workspace_id=tick.workspace_id,
                plan_hash=st.plan.hash,
                throttled=lambda: faults.fire(Fault.THROTTLING),
                seed=st.seed,
            )
            span.set_attribute("countersign.action.status", result.status)
        if self.crash_after_effect:
            raise SimulatedCrashError("crash between the sandbox commit and the run commit")
        st.outcomes = [result if o.action_id == result.action_id else o for o in st.outcomes]
        for step in result.steps:
            tick.event(
                "execution_step", {"action_id": action.id, "ref": action.ref, **step.model_dump(mode="json")}
            )
        tick.audit.append(
            (
                "executor",
                "action.executed",
                tick.row_id,
                {
                    "action_id": action.id,
                    "ref": action.ref,
                    "operation": action.operation,
                    "status": result.status,
                    "steps": [s.step + ":" + s.status for s in result.steps],
                },
            )
        )
        if not any(o.status == "pending" for o in st.outcomes):
            tick.move(RunStatus.VERIFYING, "every approved action ran")

    def _verify(self, tick: Tick) -> None:
        st = tick.state
        assert st.plan is not None
        actions = {a.id: a for a in st.plan.actions}
        for outcome in st.outcomes:
            if outcome.status != "succeeded":
                continue
            action = actions[outcome.action_id]
            current = self.sandbox.current(tick.workspace_id, action)
            ok = current.state is END_STATE[action.operation] and (
                action.operation is not Operation.RESIZE_VM or current.sku == action.target_sku
            )
            outcome.verified = ok
            outcome.realized_monthly_savings = action.est_monthly_savings if ok else 0.0
            tick.event(
                "verification",
                {
                    "action_id": action.id,
                    "ref": action.ref,
                    "verified": ok,
                    "state": current.state,
                    "sku": current.sku,
                },
            )
        st.report = _report(tick)
        approved = [o for o in st.outcomes if o.status != "rejected"]
        clean = all(o.status == "succeeded" and o.verified for o in approved)
        tick.event("report", {"text": st.report})
        tick.audit.append(
            (
                "system",
                "run.completed",
                tick.row_id,
                {
                    "verified": [o.action_id for o in approved if o.verified],
                    "failed": [o.action_id for o in approved if not o.verified],
                },
            )
        )
        tick.move(
            RunStatus.COMPLETED if clean else RunStatus.COMPLETED_WITH_FAILURES,
            "verified" if clean else "some actions failed",
        )

    # ------------------------------------------------------------------ persistence

    def _acquire(self, principal: Principal, run_id: str) -> Tick:
        now = self.clock()
        lease = secrets.token_hex(8)
        with self.db.session() as s:
            row = s.get(RunRow, run_id)
            if row is None or row.workspace_id != principal.workspace_id:
                raise RunNotFoundError(f"no run {run_id} in this workspace")
            taken = s.execute(
                update(RunRow)
                .where(RunRow.id == run_id, or_(RunRow.lease_until.is_(None), RunRow.lease_until < now))
                .values(lease_owner=lease, lease_until=now + dt.timedelta(seconds=LEASE_SECONDS))
            ).rowcount  # type: ignore[attr-defined]
            if taken != 1:
                raise RunConflictError("another request is advancing this run; retry in a moment")
            s.refresh(row)
            return Tick(
                row_id=row.id,
                workspace_id=row.workspace_id,
                created_by=row.created_by,
                request=row.request,
                status=RunStatus(row.status),
                state=RunState.model_validate(row.state),
                lease=lease,
            )

    def _release(self, tick: Tick) -> None:
        with self.db.session() as s:
            s.execute(
                update(RunRow)
                .where(RunRow.id == tick.row_id, RunRow.lease_owner == tick.lease)
                .values(lease_owner=None, lease_until=None)
            )

    def _save(self, tick: Tick) -> None:
        now = self.clock()
        with self.db.session() as s:
            saved = s.execute(
                update(RunRow)
                .where(RunRow.id == tick.row_id, RunRow.lease_owner == tick.lease)
                .values(
                    status=tick.status,
                    state=tick.state.model_dump(mode="json"),
                    version=RunRow.version + 1,
                    updated_at=now,
                    plan_hash=tick.state.plan.hash if tick.state.plan else None,
                    lease_owner=None,
                    lease_until=None,
                )
            ).rowcount  # type: ignore[attr-defined]
            if saved != 1:
                raise RunConflictError("the run's lease expired before the tick finished; nothing was saved")
            seq = (
                s.scalar(
                    select(func.coalesce(func.max(RunEventRow.seq), 0)).where(
                        RunEventRow.run_id == tick.row_id
                    )
                )
                or 0
            )
            for type_, payload in tick.events:
                seq += 1
                s.add(
                    RunEventRow(
                        run_id=tick.row_id,
                        seq=seq,
                        at=now,
                        type=type_,
                        payload=json.loads(json.dumps(payload, default=str)),
                    )
                )
            for span in tick.spans:
                seq += 1
                s.add(RunEventRow(run_id=tick.row_id, seq=seq, at=now, type="span", payload=span))
            for actor, action, subject, payload in tick.audit:
                chain.append(
                    s, tick.workspace_id, actor=actor, action=action, subject=subject, payload=payload, at=now
                )


class _FaultyModel:
    """Wraps a model with the model-side faults of a run."""

    def __init__(self, inner: ChatModel, faults: FaultPlan) -> None:
        self._inner = inner
        self._faults = faults

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._faults.fire(Fault.MODEL_OVERLOADED):
            raise ModelError("overloaded", "HTTP 529 overloaded_error (injected fault)", status=529)
        return self._inner.complete(request)


def _client_kind() -> Any:
    from opentelemetry.trace import SpanKind  # noqa: PLC0415

    return SpanKind.CLIENT


def _usage_attributes(span: Any, response: ModelResponse) -> None:
    span.set_attributes(
        {
            "gen_ai.response.model": response.model,
            "gen_ai.response.id": response.id,
            "gen_ai.response.finish_reasons": [response.stop_reason],
            "gen_ai.usage.input_tokens": response.usage.input_tokens,
            "gen_ai.usage.output_tokens": response.usage.output_tokens,
            "gen_ai.usage.cache_read.input_tokens": response.usage.cache_read_input_tokens,
            "gen_ai.usage.cache_write.input_tokens": response.usage.cache_creation_input_tokens,
            "countersign.usage.simulated": response.simulated,
        }
    )


def _result(call_id: str, output: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": json.dumps(output, separators=(",", ":"), default=str),
    }
    if error:
        block["is_error"] = True
    return block


def _problems(exc: ValidationError) -> list[dict[str, str]]:
    return [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()[:8]]


def _preview(output: dict[str, Any]) -> dict[str, Any]:
    """A compact copy for the timeline: lists are cut to their first five items."""
    return {
        k: ([*v[:5], f"... {len(v) - 5} more"] if isinstance(v, list) and len(v) > 5 else v)
        for k, v in output.items()
    }


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _partial_report(st: RunState, headline: str) -> str:
    lines = [headline]
    if st.plan:
        lines.append(
            f"A change request with {len(st.plan.actions)} action(s) was drafted; nothing was executed."
        )
    else:
        lines.append("No change was made.")
    if st.degraded:
        lines.append("Degraded: " + "; ".join(st.degraded) + ".")
    return " ".join(lines)


def _report(tick: Tick, *, cancelled: bool = False) -> str:
    st = tick.state
    if st.plan is None:
        return (
            "The run was cancelled before a change request was drafted. No change was made."
            if cancelled
            else "No change was made."
        )
    actions = {a.id: a for a in st.plan.actions}
    done = [o for o in st.outcomes if o.status == "succeeded" and o.verified]
    failed = [
        o
        for o in st.outcomes
        if o.status in ("failed", "compensated") or (o.status == "succeeded" and o.verified is False)
    ]
    rejected = [o for o in st.outcomes if o.status == "rejected"]
    skipped = [o for o in st.outcomes if o.status == "skipped"]
    realized = sum(o.realized_monthly_savings for o in done)
    parts = []
    if cancelled:
        parts.append(
            f"Cancelled. {len(done)} action(s) had already run and stay applied; {len(skipped)} did not run."
        )
    else:
        parts.append(
            f"Executed and verified {len(done)} of {len(st.plan.actions)} proposed action(s), saving an estimated {_money(realized)} per month."
        )
    for o in done:
        a = actions[o.action_id]
        target = f" to {a.target_sku}" if a.target_sku and a.operation is Operation.RESIZE_VM else ""
        parts.append(
            f"{a.id} {a.operation}{target} {a.ref}: verified, {_money(a.est_monthly_savings)}/mo [{', '.join(a.evidence_ids)}]."
        )
    for o in failed:
        a = actions[o.action_id]
        parts.append(f"{a.id} {a.operation} {a.ref}: not applied ({o.detail}).")
    for o in rejected:
        parts.append(f"{actions[o.action_id].id} {actions[o.action_id].ref}: rejected by an approver.")
    if st.degraded:
        parts.append("Degraded during the run: " + "; ".join(st.degraded) + ".")
    return " ".join(parts)
