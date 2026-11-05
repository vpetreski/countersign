"""The saga executor and the sandbox boundary, driven through a real run up to approval."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import pytest

from countersign.agent.state import PlanAction
from countersign.data.estate import ESTATE
from countersign.domain.resources import Environment, Resource, State
from countersign.execution import tokens
from countersign.execution.sandbox import (
    GuardrailError,
    PreconditionFailedError,
    SandboxCloud,
    guardrail,
    idempotency_key,
)
from countersign.execution.tokens import CapabilityError
from countersign.store.schema import ExecutionRow, RunRow
from tests.conftest import FINAL, GROUND_TRUTH, WAITING, Harness

SCEN = GROUND_TRUTH["scenarios"]
DEFAULT = "Find idle development VMs and unattached disks and propose savings."

pytestmark = pytest.mark.integration


def current(h: Harness, ref: str) -> Resource:
    with h.db.session() as s:
        return ESTATE.view(s, h.ws).by_ref[ref]


def approved(h: Harness, request: str) -> tuple[str, list[PlanAction], str]:
    run_id = h.start(request)
    h.drive(run_id, WAITING | FINAL)
    h.approve_all(run_id)
    state = h.run(run_id).state
    return run_id, [PlanAction.model_validate(a) for a in state["plan"]["actions"]], state["capability"]


def test_resize_failure_is_compensated_by_starting_the_vm_again(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FM-22: a failure inside an action's chain undoes that action's completed steps, in reverse."""
    run_id, actions, _ = approved(harness, "Rightsize oversized VMs")
    original = SandboxCloud._effect

    def flaky(
        self: SandboxCloud, s: Any, run: str, plan_hash: str, action: PlanAction, step: str, r: Any
    ) -> Any:
        if step == "resize":
            raise PreconditionFailedError("simulated: the target size is unavailable in this zone")
        return original(self, s, run, plan_hash, action, step, r)

    monkeypatch.setattr(SandboxCloud, "_effect", flaky)
    row = harness.drive(run_id, FINAL)
    first = row.state["outcomes"][0]
    assert row.status == "completed_with_failures"
    assert first["status"] == "compensated"
    assert [(s["step"], s["status"]) for s in first["steps"]] == [
        ("stop", "done"),
        ("resize", "failed"),
        ("start", "compensated"),
    ]
    vm = harness.runtime.sandbox.current(harness.ws, actions[0])
    assert vm.state is State.RUNNING and vm.sku == actions[0].current_sku
    plan_hash = harness.run(run_id).state["plan"]["hash"]
    undo = idempotency_key(run_id, plan_hash, actions[0].id, "start", undo=True)
    assert (
        first["steps"][2]["idempotency_key"]
        == undo
        != idempotency_key(run_id, plan_hash, actions[0].id, "start")
    )


def test_an_action_edited_after_approval_never_reaches_the_sandbox(harness: Harness) -> None:
    """FM-14: the stored plan is edited after the countersigns, a production VM swapped in under the same
    action id; the token binds each id to a digest of the action's content, so the sandbox refuses it."""
    run_id, _, _ = approved(harness, DEFAULT)
    prod = current(harness, SCEN["idle-prod-vm"][0])
    with harness.db.session() as s:
        row = s.get(RunRow, run_id)
        assert row is not None
        state = copy.deepcopy(row.state)
        state["plan"]["actions"][0] |= {"resource_id": prod.id, "ref": prod.ref, "name": prod.name}
        row.state = state
    row = harness.drive(run_id, FINAL)
    first = row.state["outcomes"][0]
    assert row.status == "completed_with_failures" and first["status"] == "failed"
    assert "changed after approval" in first["detail"]
    assert current(harness, prod.ref).state is State.RUNNING
    assert all(o["status"] == "succeeded" for o in row.state["outcomes"][1:])


def test_the_sandbox_guardrails_hold_whatever_the_token_says(harness: Harness) -> None:
    """FM-31: a capability minted for a production, protected or flagged resource (a bug above the sandbox)
    still cannot change it, because the guardrail reads the resource as it is now."""
    run_id, actions, _ = approved(harness, DEFAULT)
    stop = next(a for a in actions if a.operation == "stop_vm")
    plan_hash = harness.run(run_id).state["plan"]["hash"]
    secret = harness.settings.capability_secret.get_secret_value()
    cases = [
        (SCEN["idle-prod-vm"][0], "production"),
        (SCEN["protected-vm"][0], "protected by"),
        (SCEN["injection-vm"][0], "awaits a human review"),
    ]
    for ref, why in cases:
        r = current(harness, ref)
        target = stop.model_copy(update={"resource_id": r.id, "ref": r.ref})
        token = tokens.mint(
            secret,
            run_id=run_id,
            workspace_id=harness.ws,
            plan_hash=plan_hash,
            actions={target.id: tokens.action_digest(target.model_dump(mode="json"))},
            approvers=["a", "b"],
            exp=int((harness.clock() + dt.timedelta(minutes=10)).timestamp()),
        )
        with pytest.raises(GuardrailError, match=why):
            harness.runtime.sandbox.apply(
                token=token,
                run_id=run_id,
                workspace_id=harness.ws,
                plan_hash=plan_hash,
                action=target,
                step="stop",
            )
        assert current(harness, ref).state is r.state


def test_the_guardrail_refuses_each_unchangeable_kind_of_resource(harness: Harness) -> None:
    dev = current(harness, SCEN["idle-dev-vm"][0])
    guardrail(dev)  # a plain development VM passes
    for change, why in [
        ({"environment": Environment.PROD}, "production"),
        ({"environment": Environment.UNKNOWN}, "no environment tag"),
        ({"tags": {**dev.tags, "compliance": "legal-hold"}}, "protected by"),
        ({"flags": (*dev.flags, "suspicious_content")}, "awaits a human review"),
    ]:
        with pytest.raises(GuardrailError, match=why):
            guardrail(dev.model_copy(update=change))


def test_an_expired_capability_finishes_and_undoes_but_starts_nothing_new(harness: Harness) -> None:
    """FM-32: past its hour the token still replays and undoes what it named; a new effect needs a new run."""
    run_id, actions, token = approved(harness, "Rightsize oversized VMs")
    resize = next(a for a in actions if a.operation == "resize_vm")
    plan_hash = harness.run(run_id).state["plan"]["hash"]
    sandbox = harness.runtime.sandbox
    base: dict[str, Any] = {
        "token": token,
        "run_id": run_id,
        "workspace_id": harness.ws,
        "plan_hash": plan_hash,
        "action": resize,
    }
    assert not sandbox.apply(step="stop", **base).replayed
    harness.clock.advance(hours=2)  # past the capability's one-hour lifetime
    assert sandbox.apply(step="stop", **base).replayed
    with pytest.raises(CapabilityError, match="no longer authorise new effects"):
        sandbox.apply(step="resize", **base)
    undo = sandbox.apply(step="start", compensation=True, **base)
    assert not undo.replayed and current(harness, resize.ref).state is State.RUNNING


def test_an_undo_must_undo_a_step_that_ran_and_a_step_must_belong_to_its_action(harness: Harness) -> None:
    """FM-32: "compensation" is the caller's word, and the sandbox does not take it. An undo must be the inverse
    of one of the action's own steps that really ran, and a forward step must be one of the action's steps, so
    a mislabelled call cannot use the undo path to skip the expiry check and the guardrails."""
    run_id, actions, token = approved(harness, DEFAULT)
    stop = next(a for a in actions if a.operation == "stop_vm")
    plan_hash = harness.run(run_id).state["plan"]["hash"]
    sandbox = harness.runtime.sandbox
    base: dict[str, Any] = {
        "token": token,
        "run_id": run_id,
        "workspace_id": harness.ws,
        "plan_hash": plan_hash,
        "action": stop,
    }
    before = current(harness, stop.ref).state
    with pytest.raises(CapabilityError, match="not a step of the approved action"):
        sandbox.apply(step="start", **base)  # a stop_vm action has no start step
    with pytest.raises(PreconditionFailedError, match="nothing to undo"):
        sandbox.apply(step="start", compensation=True, **base)  # the stop never ran
    harness.clock.advance(hours=2)  # past the capability's one-hour lifetime
    with pytest.raises(GuardrailError, match="does not undo a step"):
        sandbox.apply(step="stop", compensation=True, **base)  # a new effect labelled as an undo
    assert current(harness, stop.ref).state is before
    with harness.db.session() as s:
        assert s.get(ExecutionRow, idempotency_key(run_id, plan_hash, stop.id, "stop")) is None


def test_a_new_target_size_after_approval_is_stopped_by_the_digest_alone(harness: Harness) -> None:
    """FM-14: the same development VM, a different size. The guardrail has nothing to object to, so only the
    token's digest of the approved content stands between the edit and the sandbox."""
    run_id, actions, _ = approved(harness, "Rightsize oversized VMs")
    i, resize = next((i, a) for i, a in enumerate(actions) if a.operation == "resize_vm")
    guardrail(current(harness, resize.ref))  # the resource itself may be changed
    with harness.db.session() as s:
        row = s.get(RunRow, run_id)
        assert row is not None
        state = copy.deepcopy(row.state)
        state["plan"]["actions"][i]["target_sku"] = "m5.4xlarge"  # a bigger size than the one approved
        row.state = state
    row = harness.drive(run_id, FINAL)
    outcome = next(o for o in row.state["outcomes"] if o["action_id"] == resize.id)
    assert outcome["status"] == "failed" and "changed after approval" in outcome["detail"]
    after = current(harness, resize.ref)
    assert after.sku == resize.current_sku and after.state is State.RUNNING


def test_the_sandbox_refuses_calls_without_a_valid_capability(harness: Harness) -> None:
    run_id, actions, token = approved(harness, "Release unused public IPs on AWS")
    plan_hash = harness.run(run_id).state["plan"]["hash"]
    sandbox = harness.runtime.sandbox
    base = {
        "run_id": run_id,
        "workspace_id": harness.ws,
        "plan_hash": plan_hash,
        "action": actions[0],
        "step": "release",
    }
    with pytest.raises(CapabilityError):
        sandbox.apply(token=None, **base)  # type: ignore[arg-type]
    with pytest.raises(CapabilityError, match="plan changed"):
        sandbox.apply(token=token, **{**base, "plan_hash": "f" * 64})  # type: ignore[arg-type]
    with pytest.raises(CapabilityError, match="another run"):
        sandbox.apply(token=token, **{**base, "workspace_id": "ws_other"})  # type: ignore[arg-type]
    effect = sandbox.apply(token=token, **base)  # type: ignore[arg-type]
    again = sandbox.apply(token=token, **base)  # type: ignore[arg-type]
    assert not effect.replayed and again.replayed and again.result == effect.result


def test_a_disk_is_never_deleted_without_its_snapshot(harness: Harness) -> None:
    run_id, actions, token = approved(harness, "Delete unattached disks")
    disk = next(a for a in actions if a.operation == "delete_disk")
    plan_hash = harness.run(run_id).state["plan"]["hash"]
    with pytest.raises(PreconditionFailedError, match="no snapshot"):
        harness.runtime.sandbox.apply(
            token=token,
            run_id=run_id,
            workspace_id=harness.ws,
            plan_hash=plan_hash,
            action=disk,
            step="delete",
        )
    snap = harness.runtime.sandbox.apply(
        token=token, run_id=run_id, workspace_id=harness.ws, plan_hash=plan_hash, action=disk, step="snapshot"
    )
    done = harness.runtime.sandbox.apply(
        token=token, run_id=run_id, workspace_id=harness.ws, plan_hash=plan_hash, action=disk, step="delete"
    )
    assert snap.result["snapshot_id"] in done.detail


def test_changes_stay_inside_their_workspace(harness: Harness) -> None:
    """FM-19 at the data layer: a run's effects are an overlay on its own workspace only."""
    run_id = harness.start("Release unused public IPs on AWS")
    harness.drive(run_id, WAITING | FINAL)
    harness.approve_all(run_id)
    harness.drive(run_id, FINAL)
    with harness.db.session() as s:
        mine = ESTATE.view(s, harness.ws)
        other = ESTATE.view(s, "ws_someone_else")
    ref = harness.plan(run_id)["actions"][0]["ref"]
    assert mine.by_ref[ref].state is State.RELEASED
    assert other.by_ref[ref].state is State.UNASSOCIATED
