"""Capability tokens: proof that humans approved exactly these actions of exactly this plan.

Minted only when the approval policy is satisfied, scoped to the run, the plan hash and the approved action
ids, signed with HMAC-SHA256 and short-lived. The sandbox cloud verifies the token on every call, so a code
path that skipped approval, or a plan edited after approval, cannot reach a side effect. It guards against
mistakes and approve-then-change; it does not protect against a compromised server that holds the key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

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
    approvers: list[str]
    exp: int
    nonce: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(
    secret: str,
    *,
    run_id: str,
    workspace_id: str,
    plan_hash: str,
    action_ids: list[str],
    approvers: list[str],
    exp: int,
) -> str:
    claims = CapabilityClaims(
        run_id=run_id,
        workspace_id=workspace_id,
        plan_hash=plan_hash,
        action_ids=sorted(action_ids),
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
    now: int,
) -> CapabilityClaims:
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
    if claims.exp < now:
        raise CapabilityError("capability token expired; the approval must be renewed")
    if (claims.run_id, claims.workspace_id) != (run_id, workspace_id):
        raise CapabilityError("capability token belongs to another run")
    if claims.plan_hash != plan_hash:
        raise CapabilityError("the plan changed after approval")
    if action_id not in claims.action_ids:
        raise CapabilityError(f"action {action_id} was not approved")
    return claims
