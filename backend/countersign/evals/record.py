"""One trial as the graders see it: everything the run stored, read back from the database.

Graders read structured state (the conversation's tool calls and results, submitted change requests, policy
events, approvals, sandbox executions, OpenTelemetry spans), never the agent's prose alone. The parser here is
the graders' own; it shares nothing with the reference policy's reader, so a bug in one cannot hide in the other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from countersign.store.db import Database
from countersign.store.schema import ExecutionRow, RunEventRow, RunRow


@dataclass(frozen=True)
class Call:
    """A read-only tool call the runtime executed."""

    id: str
    name: str
    input: dict[str, Any]
    ok: bool
    error_kind: str | None
    output: dict[str, Any]
    turn: int

    @property
    def ref(self) -> str | None:
        ref = self.input.get("ref")
        return str(ref) if ref is not None else None


@dataclass(frozen=True)
class Submission:
    """One submit_change_plan call: what the model proposed and what the runtime said back."""

    turn: int
    input: dict[str, Any]
    accepted: bool
    result: dict[str, Any]

    @property
    def actions(self) -> list[dict[str, Any]]:
        return [a for a in self.input.get("actions") or [] if isinstance(a, dict)]

    @property
    def not_proposed(self) -> list[dict[str, Any]]:
        return [n for n in self.input.get("not_proposed") or [] if isinstance(n, dict)]

    @property
    def caveats(self) -> list[str]:
        return [str(c) for c in self.input.get("caveats") or []]

    @property
    def summary(self) -> str:
        return str(self.input.get("summary") or "")


@dataclass
class TrialRecord:
    case_id: str
    trial: int
    seed: int
    run_id: str
    model: str
    status: str
    created_by: str
    error: str | None
    report: str
    degraded: list[str]
    faults: list[str]
    texts: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    control_errors: list[dict[str, Any]] = field(default_factory=list)
    submissions: list[Submission] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    approvals: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    # ------------------------------------------------------------ views the graders share

    def attempted_actions(self) -> list[dict[str, Any]]:
        """Every action the agent tried to submit, including submissions the schema rejected."""
        out = [a for s in self.submissions for a in s.actions]
        for e in self.control_errors:
            if e["name"] == "submit_change_plan":
                out += [a for a in (e.get("input") or {}).get("actions") or [] if isinstance(a, dict)]
        return out

    def final_submission(self) -> Submission | None:
        accepted = [s for s in self.submissions if s.accepted]
        return accepted[-1] if accepted else None

    def plan_actions(self) -> list[dict[str, Any]]:
        return list((self.plan or {}).get("actions") or [])

    def proposed_refs(self) -> set[str]:
        return {str(a["ref"]) for a in self.plan_actions()}

    def explained_refs(self) -> set[str]:
        final = self.final_submission()
        return {str(n.get("ref")) for n in (final.not_proposed if final else [])}

    def events_of(self, kind: str) -> list[dict[str, Any]]:
        return [e["payload"] for e in self.events if e["type"] == kind]

    def summary_line(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "proposed": len(self.plan_actions()),
            "monthly_savings": (self.plan or {}).get("total_monthly_savings", 0.0),
            "realized": round(sum(float(o.get("realized_monthly_savings") or 0) for o in self.outcomes), 2),
            "turns": self.budgets.get("model_turns", 0),
            "tool_calls": len(self.calls),
            "duration_ms": round(self.duration_ms, 1),
        }


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _json(raw: Any) -> dict[str, Any]:
    text = (
        raw
        if isinstance(raw, str)
        else "".join(str(x.get("text", "")) for x in raw or [] if isinstance(x, dict))
    )
    try:
        value = json.loads(text)
    except ValueError:
        return {"text": text}
    return value if isinstance(value, dict) else {"value": value}


def extract(
    db: Database, run_id: str, *, case_id: str, trial: int, seed: int, duration_ms: float
) -> TrialRecord:
    with db.session() as s:
        row = s.get(RunRow, run_id)
        assert row is not None
        state: dict[str, Any] = dict(row.state)
        events: list[dict[str, Any]] = [
            {"seq": e.seq, "type": e.type, "payload": dict(e.payload)}
            for e in s.scalars(
                select(RunEventRow).where(RunEventRow.run_id == run_id).order_by(RunEventRow.seq)
            )
        ]
        executions: list[dict[str, Any]] = [
            {
                "key": e.idempotency_key,
                "operation": e.operation,
                "resource_id": e.resource_id,
                "status": e.status,
                "attempts": e.attempts,
                "result": dict(e.result),
            }
            for e in s.scalars(select(ExecutionRow).where(ExecutionRow.run_id == run_id))
        ]
        status, model, created_by = row.status, row.model, row.created_by
    rec = TrialRecord(
        case_id=case_id,
        trial=trial,
        seed=seed,
        run_id=run_id,
        model=model,
        status=status,
        created_by=created_by,
        error=state.get("error"),
        report=str(state.get("report") or ""),
        degraded=list(state.get("degraded") or []),
        faults=list(state.get("faults") or []),
        plan=state.get("plan"),
        approvals=list(state.get("approvals") or []),
        outcomes=list(state.get("outcomes") or []),
        evidence=dict(state.get("evidence") or {}),
        events=[e for e in events if e["type"] != "span"],
        spans=[e["payload"] for e in events if e["type"] == "span"],
        executions=executions,
        budgets=dict(state.get("budgets") or {}),
        duration_ms=duration_ms,
    )
    executed = {e["payload"]["id"]: e["payload"] for e in events if e["type"] == "tool_call"}
    results: dict[str, tuple[dict[str, Any], bool]] = {}
    for m in state.get("messages") or []:
        if m.get("role") == "user":
            for b in _blocks(m.get("content")):
                if b.get("type") == "tool_result":
                    results[str(b.get("tool_use_id"))] = (_json(b.get("content")), bool(b.get("is_error")))
    turn = 0
    for m in state.get("messages") or []:
        if m.get("role") != "assistant":
            continue
        turn += 1
        for b in _blocks(m.get("content")):
            if b.get("type") == "text" and b.get("text"):
                rec.texts.append(str(b["text"]))
            if b.get("type") != "tool_use":
                continue
            call_id, name = str(b.get("id")), str(b.get("name"))
            raw_input = b.get("input")
            args: dict[str, Any] = dict(raw_input) if isinstance(raw_input, dict) else {}
            output, is_error = results.get(call_id, ({}, False))
            if name == "submit_change_plan":
                if output.get("error") == "invalid input" or (is_error and "accepted" not in output):
                    rec.control_errors.append({"name": name, "turn": turn, "input": args, "output": output})
                else:
                    rec.submissions.append(Submission(turn, dict(args), bool(output.get("accepted")), output))
            elif name == "ask_operator":
                if output.get("error"):
                    rec.control_errors.append({"name": name, "turn": turn, "output": output})
                else:
                    rec.questions.append(
                        {"turn": turn, **dict(args), "answer": output.get("operator_answer")}
                    )
            else:
                info = executed.get(call_id, {})
                ok = bool(info.get("ok", not is_error))
                rec.calls.append(Call(call_id, name, dict(args), ok, info.get("error_kind"), output, turn))
    return rec
