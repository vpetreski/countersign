from __future__ import annotations

import datetime as dt
import itertools

import pytest
from sqlalchemy import select

from countersign.audit import chain
from countersign.execution import tokens
from countersign.store.db import Database
from countersign.store.schema import AuditRow

pytestmark = pytest.mark.unit
SECRET = "s" * 40
NOW = 1_800_000_000


def mint(**over: object) -> str:
    args = {
        "run_id": "run_1",
        "workspace_id": "ws_1",
        "plan_hash": "a" * 64,
        "action_ids": ["act-01", "act-02"],
        "approvers": ["ws_1:approver"],
        "exp": NOW + 60,
    }
    args.update(over)
    return tokens.mint(SECRET, **args)  # type: ignore[arg-type]


def check(token: str | None, **over: object) -> tokens.CapabilityClaims:
    args = {
        "run_id": "run_1",
        "workspace_id": "ws_1",
        "plan_hash": "a" * 64,
        "action_id": "act-01",
        "now": NOW,
    }
    args.update(over)
    return tokens.verify(SECRET, token, **args)  # type: ignore[arg-type]


def test_a_valid_token_names_exactly_what_was_approved() -> None:
    claims = check(mint())
    assert claims.action_ids == ["act-01", "act-02"] and claims.approvers == ["ws_1:approver"]


@pytest.mark.parametrize(
    ("token", "over", "message"),
    [
        (None, {}, "not approved"),
        ("garbage", {}, "malformed"),
        ("cap1.e30.AAAA", {}, "signature"),
        ("MINT", {"now": NOW + 61}, "expired"),
        ("MINT", {"run_id": "run_2"}, "another run"),
        ("MINT", {"workspace_id": "ws_2"}, "another run"),
        ("MINT", {"plan_hash": "b" * 64}, "plan changed"),
        ("MINT", {"action_id": "act-03"}, "was not approved"),
    ],
)
def test_tokens_fail_closed(token: str | None, over: dict[str, object], message: str) -> None:
    """FM-14: a plan edited after approval, or an action nobody approved, never reaches the sandbox."""
    with pytest.raises(tokens.CapabilityError, match=message):
        check(mint() if token == "MINT" else token, **over)


def test_tampered_payload_breaks_the_signature() -> None:
    prefix, body, sig = mint().split(".")
    forged = tokens._b64(tokens._unb64(body).replace(b"act-02", b"act-09"))
    with pytest.raises(tokens.CapabilityError, match="signature"):
        check(f"{prefix}.{forged}.{sig}", action_id="act-09")


def test_another_secret_cannot_mint_valid_tokens() -> None:
    other = tokens.mint(
        "x" * 40,
        run_id="run_1",
        workspace_id="ws_1",
        plan_hash="a" * 64,
        action_ids=["act-01"],
        approvers=[],
        exp=NOW + 60,
    )
    with pytest.raises(tokens.CapabilityError, match="signature"):
        check(other)


def _append(db: Database, n: int) -> None:
    at = dt.datetime(2026, 11, 5, 9, tzinfo=dt.UTC)
    with db.session() as s:
        for i in range(n):
            chain.append(
                s,
                "ws_1",
                actor="ws_1:operator",
                action="run.created",
                subject=f"run_{i}",
                payload={"i": i},
                at=at + dt.timedelta(seconds=i),
            )


def test_the_audit_chain_verifies_and_links_each_entry(db: Database) -> None:
    _append(db, 5)
    with db.session() as s:
        v = chain.verify(s, "ws_1")
        rows = list(s.scalars(select(AuditRow).order_by(AuditRow.seq)))
    assert v.valid and v.entries == 5 and v.head == rows[-1].hash
    assert rows[0].prev_hash == chain.GENESIS and all(
        b.prev_hash == a.hash for a, b in itertools.pairwise(rows)
    )


@pytest.mark.parametrize("tamper", ["payload", "actor", "delete", "reorder"])
def test_any_edit_to_the_audit_log_is_detected(db: Database, tamper: str) -> None:
    """FM-18."""
    _append(db, 5)
    with db.session() as s:
        rows = list(s.scalars(select(AuditRow).order_by(AuditRow.seq)))
        if tamper == "payload":
            rows[2].payload = {"i": 99}
        elif tamper == "actor":
            rows[2].actor = "ws_1:approver"
        elif tamper == "delete":
            s.delete(rows[2])
        else:
            rows[1].hash, rows[2].hash = rows[2].hash, rows[1].hash
    with db.session() as s:
        v = chain.verify(s, "ws_1")
    assert not v.valid and v.broken_at in (2, 3, 4)
