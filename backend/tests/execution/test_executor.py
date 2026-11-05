"""The saga executor and the sandbox boundary, driven through a real run up to approval."""

from __future__ import annotations

from typing import Any

import pytest

from countersign.agent.state import PlanAction
from countersign.data.estate import ESTATE
from countersign.domain.resources import State
from countersign.execution.sandbox import PreconditionFailedError, SandboxCloud
from countersign.execution.tokens import CapabilityError
from tests.conftest import FINAL, WAITING, Harness

pytestmark = pytest.mark.integration


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
