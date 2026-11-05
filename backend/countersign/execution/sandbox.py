"""The sandbox cloud: where approved changes land.

It plays the part of the provider APIs (StopInstances, ModifyInstanceAttribute, CreateSnapshot, DeleteVolume,
ReleaseAddress and their Azure equivalents) against a per-workspace overlay of the estate, with the contract a
real integration needs: every call carries a capability token, checks its precondition against current state,
and is idempotent by key, so a retry or a crash-and-resume never applies an effect twice.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from countersign.agent.state import PlanAction
from countersign.data.estate import ESTATE
from countersign.domain.analysis import Operation
from countersign.domain.resources import Environment, Provider, Resource, State
from countersign.domain.rules import protected_by
from countersign.execution import tokens
from countersign.store.db import Database
from countersign.store.schema import ExecutionRow, ResourceOverrideRow

STEPS: dict[Operation, list[str]] = {
    Operation.STOP_VM: ["stop"],
    Operation.RESIZE_VM: ["stop", "resize", "start"],
    Operation.DELETE_DISK: ["snapshot", "delete"],
    Operation.RELEASE_IP: ["release"],
}
COMPENSATION = {
    "stop": "start",
    "resize": "resize_back",
    "start": None,
    "snapshot": None,
    "delete": None,
    "release": None,
}
# The provider API each step stands for, shown in the UI and the audit log.
API = {
    (Provider.AWS, "stop"): "ec2:StopInstances",
    (Provider.AWS, "start"): "ec2:StartInstances",
    (Provider.AWS, "resize"): "ec2:ModifyInstanceAttribute (InstanceType)",
    (Provider.AWS, "resize_back"): "ec2:ModifyInstanceAttribute (InstanceType)",
    (Provider.AWS, "snapshot"): "ec2:CreateSnapshot",
    (Provider.AWS, "delete"): "ec2:DeleteVolume",
    (Provider.AWS, "release"): "ec2:ReleaseAddress",
    (Provider.AZURE, "stop"): "Microsoft.Compute/virtualMachines/deallocate",
    (Provider.AZURE, "start"): "Microsoft.Compute/virtualMachines/start",
    (Provider.AZURE, "resize"): "Microsoft.Compute/virtualMachines/write (hardwareProfile.vmSize)",
    (Provider.AZURE, "resize_back"): "Microsoft.Compute/virtualMachines/write (hardwareProfile.vmSize)",
    (Provider.AZURE, "snapshot"): "Microsoft.Compute/snapshots/write",
    (Provider.AZURE, "delete"): "Microsoft.Compute/disks/delete",
    (Provider.AZURE, "release"): "Microsoft.Network/publicIPAddresses/delete",
}


class ThrottledError(RuntimeError):
    """The provider's rate limit (RequestLimitExceeded / HTTP 429). Retryable; no effect happened."""


class PreconditionFailedError(RuntimeError):
    """Current state is not what the step expects (someone else changed the resource). Not retryable."""


class GuardrailError(PreconditionFailedError):
    """The sandbox's own deny rules refused the effect, whatever the token says: like a cloud's deny policies,
    they hold even if every check above them was skipped or wrong. Not retryable."""


@dataclass(frozen=True)
class Effect:
    step: str
    replayed: bool
    detail: str
    result: dict[str, Any]


def idempotency_key(run_id: str, plan_hash: str, action_id: str, step: str, *, undo: bool = False) -> str:
    """The key of one effect. A compensation has its own key ("undo:start"), so undoing a stop never collides
    with the forward start step of a resize."""
    name = f"undo:{step}" if undo else step
    return hashlib.sha256(f"{run_id}|{plan_hash}|{action_id}|{name}".encode()).hexdigest()


def guardrail(r: Resource) -> None:
    """Deny rules on the resource as it is now, checked before every forward effect: no production, no
    resource without an environment tag, nothing protected by a tag, nothing whose metadata awaits review."""
    if r.environment is Environment.PROD:
        raise GuardrailError(f"refusing to change {r.ref}: it is production")
    if r.environment is Environment.UNKNOWN:
        raise GuardrailError(f"refusing to change {r.ref}: it has no environment tag")
    tag = protected_by(r.tags)
    if tag:
        raise GuardrailError(f"refusing to change {r.ref}: protected by {tag}")
    if "suspicious_content" in r.flags:
        raise GuardrailError(f"refusing to change {r.ref}: its metadata awaits a human review")


class SandboxCloud:
    def __init__(self, db: Database, capability_secret: str, clock: Callable[[], dt.datetime]) -> None:
        self._db = db
        self._secret = capability_secret
        self._clock = clock

    def apply(
        self,
        *,
        token: str | None,
        run_id: str,
        workspace_id: str,
        plan_hash: str,
        action: PlanAction,
        step: str,
        throttle: bool = False,
        compensation: bool = False,
    ) -> Effect:
        """Perform one step of an approved action, at most once. The token must name this action with this
        content; a new effect also needs an unexpired token and passes the guardrails, while a replay of a
        step already done, or a compensation that undoes one, only needs the token to be genuine."""
        now = self._clock()
        claims = tokens.verify(
            self._secret,
            token,
            run_id=run_id,
            workspace_id=workspace_id,
            plan_hash=plan_hash,
            action_id=action.id,
            action_digest=tokens.action_digest(action.model_dump(mode="json")),
            now=int(now.timestamp()),
            allow_expired=True,
        )
        key = idempotency_key(run_id, plan_hash, action.id, step, undo=compensation)
        with self._db.session() as s:
            done = s.get(ExecutionRow, key)
            if done is not None:
                return Effect(step, True, f"replayed: {done.result.get('detail', '')}", dict(done.result))
            if claims.exp < int(now.timestamp()) and not compensation:
                raise tokens.CapabilityError(
                    "capability token expired: the countersignatures no longer authorise new effects; "
                    "start a new run"
                )
            if throttle:
                raise ThrottledError(f"{_api(action, step)}: RequestLimitExceeded (simulated throttling)")
            current = self._current(s, workspace_id, action)
            if not compensation:
                guardrail(current)
            detail, update, extra = self._effect(s, run_id, plan_hash, action, step, current)
            if update is not None:
                self._write_override(s, workspace_id, run_id, current, now, **update)
            result = {"detail": detail, "api": _api(action, step), **extra}
            s.add(
                ExecutionRow(
                    idempotency_key=key,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    operation=f"{action.operation}:{'undo:' if compensation else ''}{step}",
                    resource_id=action.resource_id,
                    status="done",
                    attempts=1,
                    result=result,
                    created_at=now,
                    updated_at=now,
                )
            )
        return Effect(step, False, detail, result)

    def current(self, workspace_id: str, action: PlanAction) -> Resource:
        with self._db.session() as s:
            return self._current(s, workspace_id, action)

    def drift(self, workspace_id: str, action: PlanAction) -> str:
        """Fault injection: an external actor changes the resource after approval (someone attaches the disk,
        stops the VM by hand or reuses the address)."""
        now = self._clock()
        with self._db.session() as s:
            r = self._current(s, workspace_id, action)
            changed = {
                State.RUNNING: (State.STOPPED, "stopped" if r.provider is Provider.AWS else "deallocated"),
                State.UNATTACHED: (State.ATTACHED, "in-use" if r.provider is Provider.AWS else "Attached"),
                State.UNASSOCIATED: (State.ASSOCIATED, "associated"),
            }.get(r.state)
            if changed is None:
                return f"{r.ref} left as {r.state}"
            self._write_override(
                s, workspace_id, "external-change", r, now, state=changed[0], native_state=changed[1]
            )
            return f"{r.ref} changed outside this run: {r.state} -> {changed[0]}"

    # ------------------------------------------------------------------ internals

    def _current(self, s: Session, workspace_id: str, action: PlanAction) -> Resource:
        r = ESTATE.view(s, workspace_id).resolve(action.resource_id)
        if r is None:
            raise PreconditionFailedError(f"{action.ref} no longer exists")
        return r

    def _effect(
        self, s: Session, run_id: str, plan_hash: str, action: PlanAction, step: str, r: Resource
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        stopped_native = "stopped" if r.provider is Provider.AWS else "deallocated"
        match step:
            case "stop":
                _expect(r, State.RUNNING)
                return (
                    f"{r.ref} {stopped_native}",
                    {"state": State.STOPPED, "native_state": stopped_native},
                    {},
                )
            case "start":
                _expect(r, State.STOPPED)
                return f"{r.ref} running", {"state": State.RUNNING, "native_state": "running"}, {}
            case "resize":
                _expect(r, State.STOPPED)
                if r.sku != action.current_sku:
                    raise PreconditionFailedError(f"{r.ref} is {r.sku}, approved from {action.current_sku}")
                return f"{r.ref} {action.current_sku} -> {action.target_sku}", {"sku": action.target_sku}, {}
            case "resize_back":
                return f"{r.ref} restored to {action.current_sku}", {"sku": action.current_sku}, {}
            case "snapshot":
                _expect(r, State.UNATTACHED)
                snap = ("snap-" if r.provider is Provider.AWS else "snapshot-") + idempotency_key(
                    run_id, plan_hash, action.id, "snapshot"
                )[:17]
                return f"snapshot {snap} of {r.ref} ({r.size_gb} GB)", None, {"snapshot_id": snap}
            case "delete":
                _expect(r, State.UNATTACHED)
                snap_row = s.get(ExecutionRow, idempotency_key(run_id, plan_hash, action.id, "snapshot"))
                if snap_row is None or snap_row.status != "done":
                    raise PreconditionFailedError(f"refusing to delete {r.ref}: no snapshot was taken first")
                snap = str(snap_row.result.get("snapshot_id"))
                return (
                    f"{r.ref} deleted (recoverable from {snap})",
                    {"state": State.DELETED, "native_state": "deleted", "snapshot_ref": snap},
                    {"snapshot_id": snap},
                )
            case "release":
                _expect(r, State.UNASSOCIATED)
                return f"{r.ref} released", {"state": State.RELEASED, "native_state": "released"}, {}
        raise PreconditionFailedError(f"unknown step {step}")

    def _write_override(
        self,
        s: Session,
        workspace_id: str,
        run_id: str,
        r: Resource,
        now: dt.datetime,
        *,
        state: State | None = None,
        native_state: str | None = None,
        sku: str | None = None,
        snapshot_ref: str | None = None,
    ) -> None:
        row = s.get(ResourceOverrideRow, (workspace_id, r.id))
        if row is None:
            row = ResourceOverrideRow(
                workspace_id=workspace_id,
                resource_id=r.id,
                state=r.state,
                native_state=r.native_state,
                sku=r.sku,
                updated_at=now,
                run_id=run_id,
                snapshot_ref=None,
            )
            s.add(row)
        row.state = (state or State(row.state)).value
        row.native_state = native_state or row.native_state
        row.sku = sku or row.sku
        row.snapshot_ref = snapshot_ref or row.snapshot_ref
        row.updated_at = now
        row.run_id = run_id


def _expect(r: Resource, state: State) -> None:
    if r.state is not state:
        raise PreconditionFailedError(f"{r.ref} is {r.state}, expected {state}")


def _api(action: PlanAction, step: str) -> str:
    return API.get((Provider(action.provider), step), step)
