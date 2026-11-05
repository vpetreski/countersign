"""The runtime end to end on SQLite with the offline reference policy and scripted models.

Failure-mode ids (FM-xx) refer to docs/failure-modes.md."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from sqlalchemy import func, select

from countersign.agent.resilience import BREAKERS
from countersign.agent.runtime import (
    Principal,
    Role,
    RunConflictError,
    RunForbiddenError,
    SimulatedCrashError,
)
from countersign.agent.state import (
    CANCELLABLE,
    TERMINAL,
    TRANSITIONS,
    InvalidTransitionError,
    RunStatus,
    check_transition,
)
from countersign.audit import chain
from countersign.domain.analysis import Operation
from countersign.execution import tokens
from countersign.models.base import ModelError, ModelRequest, ModelResponse, Usage
from countersign.models.offline import ReferencePolicy
from countersign.policy.engine import UNDO_ORDER
from countersign.store.schema import AuditRow, ExecutionRow, ResourceOverrideRow, RunEventRow, RunRow
from tests.agent.scripted import FailingModel, ScriptedModel, findings_in, tool
from tests.conftest import FINAL, GROUND_TRUTH, WAITING, Harness, make_harness

pytestmark = pytest.mark.integration
SCEN = GROUND_TRUTH["scenarios"]
DEFECTS = GROUND_TRUTH["planted_defects"]


def events(h: Harness, run_id: str, type_: str | None = None) -> list[dict[str, Any]]:
    with h.db.session() as s:
        q = select(RunEventRow).where(RunEventRow.run_id == run_id).order_by(RunEventRow.seq)
        rows = [{"type": e.type, **e.payload} for e in s.scalars(q)]
    return [r for r in rows if type_ is None or r["type"] == type_]


def count(h: Harness, table: type) -> int:
    with h.db.session() as s:
        return int(s.scalar(select(func.count()).select_from(table)) or 0)


def test_full_workflow_executes_only_after_two_countersigns(harness: Harness) -> None:
    """The critical workflow: request → plan → approvals → execution → verification → report."""
    run_id = harness.start()
    row = harness.drive(run_id, WAITING | FINAL)
    assert row.status == "awaiting_approval"
    plan = harness.plan(run_id)
    refs = {a["ref"] for a in plan["actions"]}
    assert refs >= set(SCEN["idle-dev-vm"]) & refs and len(refs) >= 6
    assert plan["approvals_required"] == 2 and plan["risk"] == "high"  # disk deletion is high risk
    assert (
        count(harness, ExecutionRow) == 0 and count(harness, ResourceOverrideRow) == 0
    )  # nothing before approval
    harness.approve_all(run_id, "approver")
    assert harness.run(run_id).status == "awaiting_approval"  # one approval is not enough
    assert count(harness, ExecutionRow) == 0
    harness.approve_all(run_id, "approver2")
    row = harness.drive(run_id, FINAL)
    assert row.status == "completed"
    outcomes = row.state["outcomes"]
    assert all(o["status"] == "succeeded" and o["verified"] for o in outcomes)
    assert sum(o["realized_monthly_savings"] for o in outcomes) == pytest.approx(
        plan["total_monthly_savings"]
    )
    assert count(harness, ResourceOverrideRow) == len(plan["actions"])
    with harness.db.session() as s:
        assert chain.verify(s, harness.ws).valid


@pytest.mark.parametrize(
    "request_text",
    [
        "Find idle development VMs and unattached disks and propose savings.",
        "Clean up everything you safely can",
    ],
)
def test_effects_run_in_order_of_how_hard_they_are_to_undo(harness: Harness, request_text: str) -> None:
    """Stops and resizes first, then disk deletions (recoverable from their snapshots), then IP releases."""
    run_id = harness.start(request_text)
    harness.drive(run_id, WAITING | FINAL)
    plan = harness.plan(run_id)
    stages = [UNDO_ORDER[Operation(a["operation"])] for a in plan["actions"]]
    assert stages == sorted(stages) and len(set(stages)) >= 2
    harness.approve_all(run_id)
    assert harness.drive(run_id, FINAL).status == "completed"
    executed = list(dict.fromkeys(e["action_id"] for e in events(harness, run_id, "execution_step")))
    assert executed == [a["id"] for a in plan["actions"]]


def test_plan_scope_follows_the_request(harness: Harness) -> None:
    run_id = harness.start("Release unused public IPs on AWS")
    harness.drive(run_id, WAITING | FINAL)
    actions = harness.plan(run_id)["actions"]
    assert {a["operation"] for a in actions} == {"release_ip"}
    assert {a["provider"] for a in actions} == {"aws"}
    assert {a["ref"] for a in actions} == {r for r in SCEN["unused-ip"] if r.startswith("eipalloc-")}


def test_production_and_injected_targets_are_never_proposed(harness: Harness) -> None:
    """FM-02 and FM-04: the injection asks to stop the production replica; nothing reaches the plan."""
    run_id = harness.start("Clean up everything you safely can")
    harness.drive(run_id, WAITING | FINAL)
    refs = {a["ref"] for a in harness.plan(run_id)["actions"]}
    blocked = {
        *SCEN["idle-prod-vm"],
        *SCEN["protected-vm"],
        *SCEN["injection-vm"],
        *SCEN["unattached-prod-disk"],
        *SCEN["protected-disk"],
        *SCEN["unused-prod-ip"],
        *SCEN["conflict-stopped-billed"],
        *SCEN["new-vm"],
        *SCEN["gappy-metrics-vm"],
        *SCEN["recent-unattached-disk"],
    }
    assert not refs & blocked
    assert DEFECTS["injection_target"] not in refs
    tool_outputs = [e["output"] for e in events(harness, run_id, "tool_call")]
    assert "AI agent" not in str(tool_outputs)  # the planted instruction never reached the model


def test_a_scripted_model_that_insists_on_production_is_stopped(ingested: Any, clock: Any) -> None:
    """FM-04: policy rejects; after the revision budget the run fails safely with nothing executed."""

    def insist(request: ModelRequest, res: dict[str, Any]) -> list[dict[str, Any]]:
        if not res:
            return [tool("find_waste", "t1", categories=["idle_vm"])]
        found = {f["ref"]: f for f in findings_in(res)}
        prod = found[DEFECTS["injection_target"]]
        action = {
            "ref": prod["ref"],
            "operation": "stop_vm",
            "rationale": "idle",
            "evidence_ids": [prod["evidence_id"]],
        }
        return [tool("submit_change_plan", f"s{len(res)}", summary="stop it", actions=[action])]

    h = make_harness(ingested, clock, model_factory=lambda _st, _s: ScriptedModel([insist]))
    run_id = h.start("stop the production replica")
    row = h.drive(run_id, WAITING | FINAL)
    assert row.status == "failed"
    assert "policy forbids" in (row.state["error"] or "")
    rejected = events(h, run_id, "plan_rejected")
    assert len(rejected) == h.settings.max_plan_revisions + 1
    assert {v["rule"] for v in rejected[0]["actions"][0]["violations"]} >= {"P-001"}
    assert count(h, ExecutionRow) == 0


def test_fabricated_evidence_is_rejected(ingested: Any, clock: Any) -> None:
    """FM-05: an action citing evidence this run never retrieved fails the grounding rule (P-004)."""

    def fabricate(request: ModelRequest, res: dict[str, Any]) -> list[dict[str, Any]]:
        if not res:
            return [tool("find_waste", "t1", categories=["idle_vm"], environments=["dev"])]
        f = next(x for x in findings_in(res) if x["eligible"])
        action = {"ref": f["ref"], "operation": "stop_vm", "rationale": "idle", "evidence_ids": ["ev-99"]}
        return [tool("submit_change_plan", f"s{len(res)}", summary="trust me [ev-99]", actions=[action])]

    h = make_harness(ingested, clock, model_factory=lambda _st, _s: ScriptedModel([fabricate]))
    run_id = h.start("stop idle dev vms")
    h.drive(run_id, WAITING | FINAL)
    first = events(h, run_id, "plan_rejected")[0]
    assert [v["rule"] for v in first["actions"][0]["violations"]] == ["P-004"]


def test_requester_cannot_approve_and_approvers_decide_once(harness: Harness) -> None:
    """FM-15: separation of duties and one decision per approver per plan."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    plan = harness.plan(run_id)
    decisions = {a["id"]: "approve" for a in plan["actions"]}
    with pytest.raises(RunForbiddenError):
        harness.runtime.decide(harness.who["operator"], run_id, plan["hash"], decisions, None)
    impostor = Principal(harness.who["operator"].sub, harness.ws, "approver", Role.APPROVER, "Sam")
    with pytest.raises(RunForbiddenError, match="separation of duties"):
        harness.runtime.decide(impostor, run_id, plan["hash"], decisions, None)
    harness.runtime.decide(harness.who["approver"], run_id, plan["hash"], decisions, None)
    with pytest.raises(RunConflictError, match="already decided"):
        harness.runtime.decide(harness.who["approver"], run_id, plan["hash"], decisions, None)


def test_approval_binds_to_the_plan_hash_and_every_action(harness: Harness) -> None:
    """FM-14: an approval for another version of the plan, or a partial decision, is refused."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    plan = harness.plan(run_id)
    decisions = {a["id"]: "approve" for a in plan["actions"]}
    with pytest.raises(RunConflictError, match="not the current plan"):
        harness.runtime.decide(harness.who["approver"], run_id, "0" * 64, decisions, None)
    with pytest.raises(RunConflictError, match="every action"):
        harness.runtime.decide(
            harness.who["approver"], run_id, plan["hash"], dict(list(decisions.items())[:-1]), None
        )


def test_rejected_actions_never_run_and_full_rejection_ends_the_run(harness: Harness) -> None:
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    plan = harness.plan(run_id)
    first, *rest = [a["id"] for a in plan["actions"]]
    partial = {first: "reject", **dict.fromkeys(rest, "approve")}
    harness.runtime.decide(harness.who["approver"], run_id, plan["hash"], partial, "keep the first")
    harness.runtime.decide(harness.who["approver2"], run_id, plan["hash"], partial, None)
    row = harness.drive(run_id, FINAL)
    outcomes = {o["action_id"]: o["status"] for o in row.state["outcomes"]}
    assert outcomes[first] == "rejected" and all(outcomes[a] == "succeeded" for a in rest)
    assert row.status == "completed"

    run2 = harness.start()
    harness.drive(run2, WAITING | FINAL)
    plan2 = harness.plan(run2)
    harness.runtime.decide(
        harness.who["approver"], run2, plan2["hash"], {a["id"]: "reject" for a in plan2["actions"]}, "no"
    )
    assert harness.run(run2).status == "rejected"


def test_crash_after_a_side_effect_resumes_without_repeating_it(harness: Harness) -> None:
    """FM-12: the sandbox commit lands, the process dies before the run commit; the next tick replays."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    harness.approve_all(run_id)
    assert harness.run(run_id).status == "executing"
    harness.runtime.crash_after_effect = True
    with pytest.raises(SimulatedCrashError):
        harness.runtime.advance(harness.who["operator"], run_id)
    effects_after_crash = count(harness, ExecutionRow)
    assert effects_after_crash >= 1
    assert harness.run(run_id).lease_owner is None  # the lease was released
    harness.runtime.crash_after_effect = False
    row = harness.drive(run_id, FINAL)
    assert row.status == "completed"
    plan = harness.plan(run_id)
    assert count(harness, ExecutionRow) == sum(
        len(a["steps"]) for a in plan["actions"]
    )  # each step exactly once
    first = row.state["outcomes"][0]
    assert {s["status"] for s in first["steps"]} == {"replayed"}


def test_concurrent_advance_is_refused_while_a_lease_is_held(harness: Harness) -> None:
    """FM-13: two advances never both call the model."""
    run_id = harness.start()
    tick = harness.runtime._acquire(harness.who["operator"], run_id)
    with pytest.raises(RunConflictError):
        harness.runtime.advance(harness.who["operator"], run_id)
    harness.runtime._release(tick)
    harness.runtime.advance(harness.who["operator"], run_id)
    assert harness.run(run_id).status == "planning"


def test_lease_expiry_lets_a_stuck_run_resume(harness: Harness) -> None:
    run_id = harness.start()
    harness.runtime._acquire(harness.who["operator"], run_id)  # a worker that died holding the lease
    harness.clock.advance(seconds=121)
    harness.runtime.advance(harness.who["operator"], run_id)
    assert harness.run(run_id).status == "planning"


@pytest.mark.parametrize(
    ("limit", "reason"),
    [
        ({"max_model_turns": 2}, "model turns"),
        ({"max_tool_calls": 3}, "tool calls"),
        ({"max_input_tokens": 500}, "input tokens"),
    ],
)
def test_budgets_stop_a_runaway_run(ingested: Any, clock: Any, limit: dict[str, int], reason: str) -> None:
    """FM-16: every budget ends the run safely with a partial report."""
    h = make_harness(ingested, clock, **limit)
    run_id = h.start()
    row = h.drive(run_id, WAITING | FINAL)
    assert row.status == "budget_exhausted"
    assert reason in next(e for e in events(h, run_id, "status") if e["to"] == "budget_exhausted")["reason"]
    assert "No change was made" in row.state["report"]


def test_cancel_mid_execution_keeps_finished_actions_and_skips_the_rest(harness: Harness) -> None:
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    harness.approve_all(run_id)
    harness.runtime.advance(harness.who["operator"], run_id)  # one action
    harness.runtime.cancel(harness.who["operator"], run_id)
    row = harness.run(run_id)
    statuses = [o["status"] for o in row.state["outcomes"]]
    assert row.status == "cancelled" and statuses[0] == "succeeded" and set(statuses[1:]) == {"skipped"}
    with pytest.raises(RunConflictError):
        harness.runtime.cancel(harness.who["operator"], run_id)
    with pytest.raises(RunForbiddenError):
        harness.runtime.cancel(harness.who["approver"], run_id)


def test_an_ambiguous_request_pauses_for_the_operator(harness: Harness) -> None:
    """FM-17: the agent asks instead of guessing, then follows the answer."""
    run_id = harness.start("make it cheaper")
    row = harness.drive(run_id, WAITING | FINAL)
    assert row.status == "awaiting_input" and "look for" in row.state["question"]["text"]
    with pytest.raises(RunForbiddenError):
        harness.runtime.answer(harness.who["approver"], run_id, "Unattached disks")
    harness.runtime.answer(harness.who["operator"], run_id, "Unattached disks")
    harness.drive(run_id, WAITING | FINAL)
    assert {a["operation"] for a in harness.plan(run_id)["actions"]} == {"delete_disk"}
    with pytest.raises(RunConflictError):
        harness.runtime.answer(harness.who["operator"], run_id, "again")


def test_runs_waiting_for_a_human_expire(harness: Harness) -> None:
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    harness.clock.advance(hours=25)
    harness.runtime.advance(harness.who["operator"], run_id)
    assert harness.run(run_id).status == "expired"


def test_a_countersign_after_the_wait_limit_is_refused_and_the_run_expires(harness: Harness) -> None:
    """FM-29: nothing advances the run for 25 hours; a late countersign must not mint a capability."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    harness.approve_all(run_id, "approver")
    harness.clock.advance(hours=25)
    with pytest.raises(RunConflictError, match="expired"):
        harness.approve_all(run_id, "approver2")
    row = harness.run(run_id)
    assert row.status == "expired" and row.state["capability"] is None
    assert count(harness, ExecutionRow) == 0
    assert "run.expired" in audit_actions(harness)


def test_an_answer_after_the_wait_limit_is_refused_and_the_run_expires(harness: Harness) -> None:
    """FM-29: a late answer is not fed to the model."""
    run_id = harness.start("make it cheaper")
    assert harness.drive(run_id, WAITING | FINAL).status == "awaiting_input"
    harness.clock.advance(hours=25)
    with pytest.raises(RunConflictError, match="expired"):
        harness.runtime.answer(harness.who["operator"], run_id, "Unattached disks")
    row = harness.run(run_id)
    assert row.status == "expired" and not events(harness, run_id, "answer")


def test_polling_a_waiting_run_does_not_restart_its_wait(harness: Harness) -> None:
    """FM-29: an advance while the run waits for a person changes nothing, so it must not restart the 24
    hours; a dashboard that polls every few hours would otherwise keep a stale plan alive for ever."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    for _ in range(4):
        harness.clock.advance(hours=6)
        harness.runtime.advance(harness.who["operator"], run_id)
        assert harness.run(run_id).status == "awaiting_approval"
    harness.clock.advance(hours=1)  # 25 hours after the plan was submitted
    harness.runtime.advance(harness.who["operator"], run_id)
    assert harness.run(run_id).status == "expired"


def test_a_countersign_restarts_the_wait_for_the_next_one(harness: Harness) -> None:
    """FM-29: the limit measures how long the run has waited for a person; a countersign is a person
    acting, so the second approver gets a full 24 hours after the first."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    harness.clock.advance(hours=20)
    harness.approve_all(run_id, "approver")
    harness.clock.advance(hours=20)  # 40 hours after the plan, 20 after the first countersign
    harness.approve_all(run_id, "approver2")
    row = harness.run(run_id)
    assert row.status == "executing" and row.state["capability"] is not None


def test_a_run_can_be_cancelled_until_verification_starts(harness: Harness) -> None:
    """FM-30: cancel is allowed exactly where the transition table allows it; verifying has nothing left."""
    run_id = harness.start()
    harness.drive(run_id, WAITING | FINAL)
    harness.approve_all(run_id)
    row = harness.drive(run_id, {"verifying", *FINAL})
    assert row.status == "verifying"
    with pytest.raises(RunConflictError, match="nothing is left to cancel"):
        harness.runtime.cancel(harness.who["operator"], run_id)
    assert harness.drive(run_id, FINAL).status == "completed"
    assert {s for s, targets in TRANSITIONS.items() if RunStatus.CANCELLED in targets} == CANCELLABLE
    assert RunStatus.VERIFYING not in CANCELLABLE and not CANCELLABLE & TERMINAL


def test_overloaded_model_is_retried_and_the_turn_succeeds(ingested: Any, clock: Any) -> None:
    """FM-06: a 529 on the first call is retried with backoff; the run carries on."""
    h = make_harness(ingested, clock)
    run_id = h.start(faults=["model_overloaded"])
    row = h.drive(run_id, WAITING | FINAL)
    assert row.status == "awaiting_approval"
    retries = events(h, run_id, "model_retry")
    assert len(retries) == 1 and retries[0]["kind"] == "overloaded"


class _LeaseThief:
    """Fails its first call with a retryable error after another worker has taken over the run's lease."""

    def __init__(self, h: Harness) -> None:
        self.h, self.calls, self.inner, self.run_id = h, 0, ReferencePolicy(), ""

    provider = "thief"
    model = "thief"

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            with self.h.db.session() as s:
                row = s.get(RunRow, self.run_id)
                assert row is not None
                row.lease_owner = "another-worker"
            raise ModelError("overloaded", "simulated overload", status=529)
        return self.inner.complete(request)


def test_a_retry_after_the_lease_was_taken_over_is_never_paid_for(ingested: Any, clock: Any) -> None:
    """FM-13: before a model retry the tick renews its lease; when another worker holds it now, the tick stops
    instead of paying for a second call whose result it could not save, and leaves that worker's lease."""
    BREAKERS.clear()
    h = make_harness(ingested, clock)
    model = _LeaseThief(h)
    h.runtime.model_factory = lambda _st, _s: model
    model.run_id = run_id = h.start()
    h.runtime.advance(h.who["operator"], run_id)  # created -> planning
    with pytest.raises(RunConflictError, match="lease was taken over"):
        h.runtime.advance(h.who["operator"], run_id)
    row = h.run(run_id)
    assert model.calls == 1 and row.lease_owner == "another-worker"
    assert row.state["budgets"]["model_turns"] == 0
    BREAKERS.clear()


def test_a_live_model_gets_a_lease_longer_than_its_slowest_call(ingested: Any, clock: Any) -> None:
    offline = make_harness(ingested, clock)
    live = make_harness(ingested, clock, model_provider="claude", model_timeout_seconds=300)
    assert offline.runtime._lease_seconds() == 120
    assert live.runtime._lease_seconds() == 360


class _CachedModel:
    """The reference policy, reporting usage the way a cached Claude prompt does."""

    provider = "offline"
    model = "cached"

    def __init__(self) -> None:
        self.inner = ReferencePolicy()

    def complete(self, request: ModelRequest) -> ModelResponse:
        out = self.inner.complete(request)
        usage = Usage(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=900, cache_creation_input_tokens=90
        )
        return out.model_copy(update={"usage": usage})


def test_cached_prompt_tokens_count_against_the_input_budget(ingested: Any, clock: Any) -> None:
    """FM-16: with prompt caching most of the context arrives as cache reads; the budget and the span count
    every input token, not only the uncached tail."""
    h = make_harness(ingested, clock, model_factory=lambda _st, _s: _CachedModel())
    run_id = h.start()
    h.runtime.advance(h.who["operator"], run_id)  # created -> planning
    h.runtime.advance(h.who["operator"], run_id)  # one model turn
    assert h.run(run_id).state["budgets"]["input_tokens"] == 1000
    chat = next(e for e in events(h, run_id, "span") if e["name"].startswith("chat"))
    assert chat["attributes"]["gen_ai.usage.input_tokens"] == 1000
    assert chat["attributes"]["gen_ai.usage.cache_read.input_tokens"] == 900


def test_the_tool_call_budget_holds_inside_a_single_turn(ingested: Any, clock: Any) -> None:
    """FM-16: a model that asks for many tools in one turn gets only what the budget has left; the rest are
    answered with an error and never run."""

    def greedy(request: ModelRequest, res: dict[str, Any]) -> list[dict[str, Any]]:
        return [tool("find_waste", f"t{i}", categories=["idle_vm"]) for i in range(6)]

    h = make_harness(ingested, clock, model_factory=lambda _st, _s: ScriptedModel([greedy]), max_tool_calls=4)
    run_id = h.start()
    h.runtime.advance(h.who["operator"], run_id)  # created -> planning
    h.runtime.advance(h.who["operator"], run_id)  # the greedy turn
    row = h.run(run_id)
    assert len(events(h, run_id, "tool_call")) == 4 and row.state["budgets"]["tool_calls"] == 4
    last = row.state["messages"][-1]["content"]
    refused = [b for b in last if b.get("is_error") and "budget exhausted" in b["content"]]
    assert len(refused) == 2
    assert h.drive(run_id, WAITING | FINAL).status == "budget_exhausted"


def test_persistent_provider_outage_degrades_without_failing(ingested: Any, clock: Any) -> None:
    BREAKERS.clear()
    model = FailingModel("overloaded")
    h = make_harness(ingested, clock, model_factory=lambda _st, _s: model)
    run_id = h.start()
    h.runtime.advance(h.who["operator"], run_id)  # created -> planning
    h.runtime.advance(h.who["operator"], run_id)
    row = h.run(run_id)
    assert row.status == "planning" and any(d.startswith("model_unavailable") for d in row.state["degraded"])
    assert model.calls == 3
    BREAKERS.clear()


def test_non_retryable_model_errors_fail_the_run_safely(ingested: Any, clock: Any) -> None:
    """FM-07: an authentication error is not retried; the run fails with nothing executed."""
    model = FailingModel("auth")
    h = make_harness(ingested, clock, model_factory=lambda _st, _s: model)
    run_id = h.start()
    row = h.drive(run_id, WAITING | FINAL)
    assert row.status == "failed" and model.calls == 1
    assert "No change was made" in row.state["report"]


def test_tool_timeout_is_retried_by_the_agent(ingested: Any, clock: Any) -> None:
    """FM-08: a retryable tool error goes back to the model, which retries once."""
    h = make_harness(ingested, clock)
    run_id = h.start(faults=["tool_timeout"])
    h.drive(run_id, WAITING | FINAL)
    calls = [e for e in events(h, run_id, "tool_call") if e["name"] == "get_utilization"]
    failed = [c for c in calls if not c["ok"]]
    assert len(failed) == 1 and failed[0]["error_kind"] == "timeout"
    assert any(c["ok"] and c["input"]["ref"] == failed[0]["input"]["ref"] for c in calls)
    assert any("timed out" in c for c in harness_plan_caveats(h, run_id))


def test_a_saving_that_cannot_be_estimated_is_never_counted_as_zero(ingested: Any, clock: Any) -> None:
    """FM-09: with the price lists down a resize's target size has no price and no billing history. The
    action says its saving is unknown, the plan total says it is incomplete, and an unknown impact is routed
    like a large one: two countersignatures."""
    h = make_harness(ingested, clock)
    run_id = h.start("Rightsize oversized VMs", faults=["pricing_unavailable"])
    assert h.drive(run_id, WAITING | FINAL).status == "awaiting_approval"
    plan = h.plan(run_id)
    resizes = [a for a in plan["actions"] if a["operation"] == "resize_vm"]
    assert resizes and all(a["est_monthly_savings"] is None for a in resizes)
    assert plan["savings_complete"] is False and plan["approvals_required"] == 2
    h.approve_all(run_id, "approver")
    assert h.run(run_id).status == "awaiting_approval"
    h.approve_all(run_id, "approver2")
    row = h.drive(run_id, FINAL)
    assert row.status == "completed"
    assert "saving not estimable" in row.state["report"]
    assert f"plus {len(resizes)} whose saving could not be estimated" in row.state["report"]


def harness_plan_caveats(h: Harness, run_id: str) -> list[str]:
    return list(h.plan(run_id)["caveats"])


def test_price_outage_falls_back_and_says_so(ingested: Any, clock: Any) -> None:
    """FM-09: estimates come from billing history; the run is degraded and the plan carries a caveat."""
    h = make_harness(ingested, clock)
    run_id = h.start(faults=["pricing_unavailable"])
    row = h.drive(run_id, WAITING | FINAL)
    assert row.status == "awaiting_approval"
    plan = h.plan(run_id)
    assert {a["price_source"] for a in plan["actions"]} == {"billing_history"}
    assert any("pricing degraded" in c for c in plan["caveats"])
    assert events(h, run_id, "degraded")


def test_throttling_is_retried_and_every_effect_happens_once(ingested: Any, clock: Any) -> None:
    """FM-10."""
    h = make_harness(ingested, clock)
    run_id = h.start(faults=["throttling"])
    h.drive(run_id, WAITING | FINAL)
    h.approve_all(run_id)
    row = h.drive(run_id, FINAL)
    assert row.status == "completed"
    steps = [s for o in row.state["outcomes"] for s in o["steps"]]
    assert steps and all(s["attempts"] == 2 for s in steps)
    assert count(h, ExecutionRow) == len(steps)
    assert all(s["api"] and (s["api"].startswith("ec2:") or s["api"].startswith("Microsoft.")) for s in steps)


def test_state_drift_fails_only_the_drifted_action(ingested: Any, clock: Any) -> None:
    """FM-11 and FM-22: the precondition stops one action; independent actions still run."""
    h = make_harness(ingested, clock)
    run_id = h.start(faults=["state_drift"])
    h.drive(run_id, WAITING | FINAL)
    h.approve_all(run_id)
    row = h.drive(run_id, FINAL)
    statuses = [o["status"] for o in row.state["outcomes"]]
    assert row.status == "completed_with_failures"
    assert statuses.count("failed") == 1 and statuses.count("succeeded") == len(statuses) - 1
    assert "not applied" in row.state["report"]


def executions_of(h: Harness, run_id: str) -> list[ExecutionRow]:
    with h.db.session() as s:
        return list(s.scalars(select(ExecutionRow).where(ExecutionRow.run_id == run_id)))


def audit_actions(h: Harness) -> list[str]:
    with h.db.session() as s:
        return [a.action for a in s.scalars(select(AuditRow).where(AuditRow.workspace_id == h.ws))]


def test_a_resource_changed_before_the_last_countersign_is_left_out_of_the_token(harness: Harness) -> None:
    """FM-28: the policy runs again when the last countersign arrives, against the estate as it is then. A
    second run in the same workspace stops one of the plan's VMs first; that action is left out of the token
    with the rule that blocked it and never reaches the sandbox, the others run, and nothing about the check
    fires a fault or spends a budget."""
    h = harness
    first = h.start()
    h.drive(first, WAITING | FINAL)
    plan = h.plan(first)
    h.approve_all(first, "approver")  # one of the two countersigns the plan needs
    second = h.start("stop idle dev vms")
    h.drive(second, WAITING | FINAL)
    plan2 = h.plan(second)
    target = next(a for a in plan2["actions"] if a["ref"] in {x["ref"] for x in plan["actions"]})
    only = {a["id"]: "approve" if a["id"] == target["id"] else "reject" for a in plan2["actions"]}
    h.runtime.decide(h.who["approver"], second, plan2["hash"], only, None)
    assert h.drive(second, FINAL).status == "completed"  # the VM is stopped in this workspace
    blocked = next(a for a in plan["actions"] if a["ref"] == target["ref"])
    before = h.run(first).state
    h.approve_all(first, "approver2")
    row = h.run(first)
    assert row.status == "executing"
    assert row.state["budgets"] == before["budgets"] and row.state["faults_fired"] == before["faults_fired"]
    stages = [e["stage"] for e in events(h, first, "policy_evaluated")]
    assert stages == ["submission", "decision"]
    decision = events(h, first, "policy_evaluated")[-1]
    assert not decision["allowed"] and decision["violations"][0]["ref"] == blocked["ref"]
    assert "P-008" in decision["violations"][0]["rules"]
    minted = events(h, first, "capability_minted")[0]["actions"]
    assert blocked["id"] not in minted and len(minted) == len(plan["actions"]) - 1
    with pytest.raises(tokens.CapabilityError, match="was not approved"):
        tokens.verify(
            h.settings.capability_secret.get_secret_value(),
            row.state["capability"],
            run_id=first,
            workspace_id=h.ws,
            plan_hash=plan["hash"],
            action_id=blocked["id"],
            action_digest=tokens.action_digest(blocked),
            now=int(h.clock().timestamp()),
        )
    row = h.drive(first, FINAL)
    assert row.status == "completed_with_failures"
    outcomes = {o["action_id"]: o for o in row.state["outcomes"]}
    assert outcomes[blocked["id"]]["status"] == "skipped"
    assert outcomes[blocked["id"]]["detail"].startswith(
        "blocked by policy at decision: P-008 known-resource (vm/stopped"
    )
    assert all(
        o["status"] == "succeeded" and o["verified"] for k, o in outcomes.items() if k != blocked["id"]
    )
    assert blocked["resource_id"] not in {e.resource_id for e in executions_of(h, first)}
    assert f"{blocked['id']} stop_vm {blocked['ref']}: not applied (blocked by policy" in row.state["report"]
    assert "plan.rechecked" in audit_actions(h)
    with h.db.session() as s:
        assert chain.verify(s, h.ws).valid


def test_when_the_policy_refuses_every_approved_action_at_decision_the_run_fails(harness: Harness) -> None:
    """FM-28: another run applied the same change request first, so at the last countersign every action is
    blocked: no token is minted, nothing reaches the sandbox, and the run fails saying why."""
    h = harness
    first = h.start(faults=["state_drift"])  # armed, and must stay unfired: nothing executes
    h.drive(first, WAITING | FINAL)
    second = h.start()
    h.drive(second, WAITING | FINAL)
    assert {a["ref"] for a in h.plan(first)["actions"]} == {a["ref"] for a in h.plan(second)["actions"]}
    h.approve_all(second)
    assert h.drive(second, FINAL).status == "completed"
    h.approve_all(first)
    row = h.run(first)
    assert row.status == "failed"
    assert row.state["error"] == "the policy refused every approved action at decision"
    assert row.state["capability"] is None and not events(h, first, "capability_minted")
    outcomes = row.state["outcomes"]
    assert {o["status"] for o in outcomes} == {"skipped"}
    assert all(o["detail"].startswith("blocked by policy at decision: P-008") for o in outcomes)
    assert row.state["report"].startswith("Nothing was executed: the policy refused every approved action")
    assert executions_of(h, first) == [] and row.state["faults_fired"] == {}
    with h.db.session() as s:
        assert chain.verify(s, h.ws).valid


def test_a_recheck_that_needs_more_approvals_waits_for_them(harness: Harness) -> None:
    """FM-28: when the check at decision routes the remaining actions to more approvers than the plan said
    (here the two-approver threshold was lowered after the plan was submitted), no token is minted until that
    many distinct approvers have countersigned."""
    h = harness
    run_id = h.start("stop idle dev vms")
    h.drive(run_id, WAITING | FINAL)
    plan = h.plan(run_id)
    assert plan["approvals_required"] == 1
    h.runtime.policy = dataclasses.replace(
        h.runtime.policy, two_approvers_at=plan["total_monthly_savings"] / 2
    )
    h.approve_all(run_id, "approver")
    assert h.run(run_id).status == "awaiting_approval"
    assert events(h, run_id, "awaiting_more_approvals")[-1] == {
        "type": "awaiting_more_approvals",
        "have": 1,
        "need": 2,
    }
    assert not events(h, run_id, "capability_minted") and count(h, ExecutionRow) == 0
    h.approve_all(run_id, "approver2")
    assert h.run(run_id).status == "executing"
    assert len(events(h, run_id, "capability_minted")[0]["approvers"]) == 2


def test_every_disallowed_transition_raises() -> None:
    allowed = {(RunStatus.CREATED, RunStatus.PLANNING), (RunStatus.PLANNING, RunStatus.AWAITING_APPROVAL)}
    for a, b in allowed:
        check_transition(a, b)
    for terminal in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
        for target in RunStatus:
            with pytest.raises(InvalidTransitionError):
                check_transition(terminal, target)
    with pytest.raises(InvalidTransitionError):
        check_transition(RunStatus.AWAITING_APPROVAL, RunStatus.COMPLETED)  # never complete without executing


def test_viewers_cannot_drive_runs(harness: Harness) -> None:
    with pytest.raises(RunForbiddenError):
        harness.runtime.create(harness.who["viewer"], "stop idle dev vms", [])
    run_id = harness.start()
    with pytest.raises(RunForbiddenError):
        harness.runtime.advance(harness.who["viewer"], run_id)


def test_secrets_in_the_request_are_redacted_before_storage(harness: Harness) -> None:
    run_id = harness.start("stop idle dev vms, my key is AKIA" + "IOSFODNN7EXAMPLE")
    assert "AKIA" not in harness.run(run_id).request
    assert events(harness, run_id, "created")[0]["redacted"] == ["aws_access_key_id"]
