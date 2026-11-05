"""Append-only, hash-chained audit log per workspace.

Each entry's hash covers the previous hash and the entry's canonical JSON, so editing, reordering or deleting
an entry breaks verification from that point on. Removing the newest entries leaves a shorter chain that still
verifies, and the chain lives in the same database as the data it protects: detecting truncation, or a rewrite
of the whole chain by the database owner, needs the head hash anchored outside it (a WORM bucket or a
transparency log), which production would add.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from countersign.store.schema import AuditRow

GENESIS = "0" * 64


def _iso(at: dt.datetime) -> str:
    at = at.replace(tzinfo=dt.UTC) if at.tzinfo is None else at.astimezone(dt.UTC)
    return at.isoformat()


def entry_hash(
    prev_hash: str,
    workspace_id: str,
    seq: int,
    at: dt.datetime,
    actor: str,
    action: str,
    subject: str,
    payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "ws": workspace_id,
            "seq": seq,
            "at": _iso(at),
            "actor": actor,
            "action": action,
            "subject": subject,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def append(
    session: Session,
    workspace_id: str,
    *,
    actor: str,
    action: str,
    subject: str,
    payload: dict[str, Any],
    at: dt.datetime,
) -> AuditRow:
    payload = json.loads(json.dumps(payload, default=str))  # store exactly what was hashed
    for _ in range(5):
        last = session.scalar(
            select(AuditRow)
            .where(AuditRow.workspace_id == workspace_id)
            .order_by(AuditRow.seq.desc())
            .limit(1)
        )
        seq = (last.seq + 1) if last else 1
        prev = last.hash if last else GENESIS
        row = AuditRow(
            workspace_id=workspace_id,
            seq=seq,
            at=at,
            actor=actor,
            action=action,
            subject=subject,
            payload=payload,
            prev_hash=prev,
            hash=entry_hash(prev, workspace_id, seq, at, actor, action, subject, payload),
        )
        try:
            with session.begin_nested():
                session.add(row)
        except IntegrityError:
            continue  # a concurrent writer took this sequence number; re-read the head and retry
        return row
    raise RuntimeError(f"audit append for {workspace_id} kept colliding")


@dataclass(frozen=True)
class Verification:
    valid: bool
    entries: int
    head: str
    broken_at: int | None = None
    reason: str | None = None


def verify(session: Session, workspace_id: str) -> Verification:
    prev = GENESIS
    count = 0
    for row in session.scalars(
        select(AuditRow).where(AuditRow.workspace_id == workspace_id).order_by(AuditRow.seq)
    ):
        count += 1
        if row.seq != count:
            return Verification(
                False, count, prev, row.seq, f"sequence gap: expected {count}, found {row.seq}"
            )
        if row.prev_hash != prev:
            return Verification(False, count, prev, row.seq, "prev_hash does not match the previous entry")
        expected = entry_hash(
            prev, workspace_id, row.seq, row.at, row.actor, row.action, row.subject, row.payload
        )
        if expected != row.hash:
            return Verification(False, count, prev, row.seq, "entry content does not match its hash")
        prev = row.hash
    return Verification(True, count, prev)
