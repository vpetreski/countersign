"""Capability tokens: proof that humans approved exactly these actions of exactly this plan.

Minted only when the approval policy is satisfied, scoped to the run, the plan hash and the approved actions,
signed with HMAC-SHA256 and short-lived. Each approved action is bound by id and by a digest of its content
(resource, operation, target, steps and every other field the approvers read), and the sandbox cloud verifies
the token against the action it is asked to perform, on every call. So a code path that skipped approval, an
action nobody approved, or an action whose content changed after approval under the same id, cannot reach a
side effect. It guards against mistakes and approve-then-change; it does not protect against a compromised
server that holds the key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

PREFIX = "cap1"


class CapabilityError(PermissionError):
    pass


class CapabilityClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    workspace_id: str
    plan_hash: str
    action_ids: list[str]
    action_digests: dict[str, str]
    approvers: list[str]
    exp: int
    nonce: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def action_digest(action: Mapping[str, Any]) -> str:
    """SHA-256 of an action's canonical JSON (sorted keys, no spaces): what the token binds to each id."""
    canonical = json.dumps(action, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def mint(
    secret: str,
    *,
    run_id: str,
    workspace_id: str,
    plan_hash: str,
    actions: Mapping[str, str],
    approvers: list[str],
    exp: int,
) -> str:
    """`actions` maps each approved action id to its `action_digest`."""
    claims = CapabilityClaims(
        run_id=run_id,
        workspace_id=workspace_id,
        plan_hash=plan_hash,
        action_ids=sorted(actions),
        action_digests=dict(sorted(actions.items())),
        approvers=sorted(approvers),
        exp=exp,
        nonce=secrets.token_hex(8),
    )
    body = _b64(json.dumps(claims.model_dump(), sort_keys=True, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), f"{PREFIX}.{body}".encode(), hashlib.sha256).digest())
    return f"{PREFIX}.{body}.{sig}"


def verify(
    secret: str,
    token: str | None,
    *,
    run_id: str,
    workspace_id: str,
    plan_hash: str,
    action_id: str,
    action_digest: str,
    now: int,
    allow_expired: bool = False,
) -> CapabilityClaims:
    """Check the token against the action about to run. `allow_expired` is for the sandbox's replays and
    compensations: a correctly signed token still authorises finishing or undoing what it named."""
    if not token:
        raise CapabilityError("no capability token: the change was not approved")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise CapabilityError("malformed capability token")
    expected = _b64(hmac.new(secret.encode(), f"{PREFIX}.{parts[1]}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise CapabilityError("capability token signature is invalid")
    try:
        claims = CapabilityClaims.model_validate_json(_unb64(parts[1]))
    except ValueError as exc:
        raise CapabilityError("capability token payload is invalid") from exc
    if claims.exp < now and not allow_expired:
        raise CapabilityError(
            "capability token expired: the countersignatures no longer authorise new effects; start a new run"
        )
    if (claims.run_id, claims.workspace_id) != (run_id, workspace_id):
        raise CapabilityError("capability token belongs to another run")
    if claims.plan_hash != plan_hash:
        raise CapabilityError("the plan changed after approval")
    if action_id not in claims.action_ids:
        raise CapabilityError(f"action {action_id} was not approved")
    if not hmac.compare_digest(claims.action_digests.get(action_id, ""), action_digest):
        raise CapabilityError(f"action {action_id} changed after approval")
    return claims
