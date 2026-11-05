"""End to end without a key: the real runtime, wired as the API wires it (`make_model`), drives Claude through the
adapter while respx plays Claude. The scripted replies read the tool results the runtime actually sent, so the
refs and evidence ids in the plan come from this run's own tools, and every request is checked to replay the
conversation append-only with Claude's blocks (thinking signatures included) exactly as Claude returned them."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from countersign.agent.resilience import BREAKERS, CircuitBreaker
from countersign.agent.runtime import Principal, Role, Runtime
from countersign.agent.state import RunState, RunStatus
from countersign.data.pricing import PriceCatalog
from countersign.models.factory import make_model
from countersign.policy.engine import default_policy
from countersign.store.db import Database
from countersign.store.schema import AuditRow, RunEventRow, RunRow, WorkspaceRow
from tests.conftest import NOW, make_settings
from tests.models.conftest import FAKE_KEY, AnthropicAPI, message, ok, text, thinking, tool_use

pytestmark = pytest.mark.claude_contract

WORKSPACE = "ws_claude_contract"
OPERATOR = Principal(
    sub="persona:operator", workspace_id=WORKSPACE, persona="operator", role=Role.OPERATOR, name="Operator"
)


class ScriptedClaude:
    """Answers POST /v1/messages in four Claude-shaped turns: survey, inspect, check policy, submit."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.replies: list[list[dict[str, Any]]] = []
        self.actions: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self._check_replay(body)
        self.bodies.append(body)
        turn = len(self.bodies)
        steps = {1: self._survey, 2: self._inspect, 3: self._check, 4: self._submit}
        assert turn in steps, f"unexpected model turn {turn}"
        content = [thinking(f"sig-test-turn-{turn}"), *steps[turn](body["messages"])]
        self.replies.append(content)
        usage = {
            "input_tokens": 400 + turn,
            "output_tokens": 120,
            "cache_creation_input_tokens": 3000 if turn == 1 else 200,
            "cache_read_input_tokens": 0 if turn == 1 else 3000 * turn,
        }
        return ok(message(content, "tool_use", usage=usage, id_=f"msg_01TestTurn{turn:02d}"))

    def _check_replay(self, body: dict[str, Any]) -> None:
        if not self.bodies:
            return
        previous, reply = self.bodies[-1], self.replies[-1]
        assert body["system"] == previous["system"]  # the cached prefix is stable
        assert body["tools"] == previous["tools"]
        n = len(previous["messages"])
        assert body["messages"][:n] == previous["messages"]  # append-only history
        assert body["messages"][n] == {"role": "assistant", "content": reply}  # Claude's blocks, verbatim

    @staticmethod
    def _results(messages: list[dict[str, Any]]) -> dict[str, Any]:
        """The tool results of the last user message, by tool_use id."""
        last = messages[-1]
        assert last["role"] == "user"
        return {
            b["tool_use_id"]: json.loads(b["content"]) for b in last["content"] if b["type"] == "tool_result"
        }

    def _survey(self, _messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scope = {
            "categories": ["idle_vm", "unattached_disk"],
            "environments": ["dev", "test"],
            "include_blocked": False,
        }
        return [
            text("I'll read the estate and the idle VMs and unattached disks in dev and test."),
            tool_use("toolu_01Survey", "get_estate_summary", {}),
            tool_use("toolu_01Waste", "find_waste", scope),
        ]

    def _inspect(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = self._results(messages)
        assert "evidence_id" in results["toolu_01Survey"]
        findings = [f for f in results["toolu_01Waste"]["findings"] if f["eligible"]]
        vms = [f for f in findings if f["recommended_operation"] == "stop_vm"][:2]
        disks = [f for f in findings if f["recommended_operation"] == "delete_disk"][:1]
        assert vms, "the fixture estate has idle dev VMs"
        assert disks, "the fixture estate has unattached dev disks"
        self.actions = [
            {
                "ref": f["ref"],
                "operation": f["recommended_operation"],
                "rationale": f"{f['category']} with ${f['est_monthly_savings_usd']}/mo of savings [{f['evidence_id']}]",
                "evidence_ids": [f["evidence_id"]],
            }
            for f in (*vms, *disks)
        ]
        calls = [
            tool_use(f"toolu_01Util{i}", "get_utilization", {"ref": f["ref"]}) for i, f in enumerate(vms)
        ]
        calls += [
            tool_use(f"toolu_01Price{i}", "quote_price", {"ref": a["ref"]})
            for i, a in enumerate(self.actions)
        ]
        return [text("Checking utilization and prices for each finding in scope."), *calls]

    def _check(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = self._results(messages)
        for result in results.values():
            assert "error" not in result, result
            ref = result["ref"]
            action = next(a for a in self.actions if a["ref"] == ref)
            action["evidence_ids"].append(result["evidence_id"])
        return [
            text("Dry-running the policy."),
            tool_use("toolu_01Policy", "check_policy", {"actions": self.actions}),
        ]

    def _submit(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        verdict = self._results(messages)["toolu_01Policy"]
        assert verdict["allowed"] is True, verdict
        cited = ", ".join(f"[{a['evidence_ids'][0]}]" for a in self.actions)
        plan = {
            "summary": f"Stop idle dev VMs and delete an unattached dev disk {cited}.",
            "actions": self.actions,
            "caveats": [],
        }
        return [
            text("Submitting the change request."),
            tool_use("toolu_01Submit", "submit_change_plan", plan),
        ]


def _status(db: Database, run_id: str) -> str:
    with db.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        return row.status


def test_the_runtime_reaches_awaiting_approval_through_claude(
    ingested: Database, anthropic_api: AnthropicAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(anthropic_api_key=FAKE_KEY)
    assert settings.model_mode == "claude"
    monkeypatch.setitem(BREAKERS, "anthropic", CircuitBreaker())  # a fresh breaker, whatever ran before
    script = ScriptedClaude()
    anthropic_api.messages.mock(side_effect=script)
    with ingested.session() as session:
        session.add(WorkspaceRow(id=WORKSPACE, created_at=NOW, expires_at=NOW + dt.timedelta(days=1)))
    runtime = Runtime(
        ingested,
        settings,
        model_factory=make_model,  # the production selection: a key means Claude
        catalog=PriceCatalog(settings.fixtures_dir, now=lambda: NOW),
        policy=default_policy(),
        clock=lambda: NOW,
        sleep=lambda _seconds: None,
    )

    run_id = runtime.create(OPERATOR, "Stop idle VMs and delete unattached disks in dev and test.", [])
    for _ in range(8):
        if _status(ingested, run_id) == RunStatus.AWAITING_APPROVAL:
            break
        runtime.advance(OPERATOR, run_id)

    with ingested.session() as session:
        row = session.get(RunRow, run_id)
        assert row is not None
        state = RunState.model_validate(row.state)
        events = list(session.scalars(select(RunEventRow).where(RunEventRow.run_id == run_id)))
        audit = list(session.scalars(select(AuditRow).where(AuditRow.workspace_id == WORKSPACE)))
        assert (row.status, row.model, state.error) == (RunStatus.AWAITING_APPROVAL, "claude-opus-5-5", None)
        stored = json.dumps([row.state, [e.payload for e in events], [a.payload for a in audit]], default=str)

    assert anthropic_api.messages.call_count == 4
    plan = state.plan
    assert plan is not None
    assert sorted(a.ref for a in plan.actions) == sorted(a["ref"] for a in script.actions)
    for action in plan.actions:
        assert action.evidence_ids
        assert set(action.evidence_ids) <= set(state.evidence)  # cited evidence came from this run's tools
        assert any(state.evidence[e].ref == action.ref for e in action.evidence_ids)
    assert plan.hash
    assert row.plan_hash == plan.hash

    # The conversation the runtime stored is the one Claude saw, thinking signatures included.
    assistant = [m["content"] for m in state.messages if m["role"] == "assistant"]
    assert assistant == script.replies
    # Real usage, not the offline estimate; cache reads reach the chat span.
    assert (state.budgets.model_turns, state.budgets.simulated_tokens) == (4, False)
    chats = [e.payload for e in events if e.type == "span" and e.payload["name"].startswith("chat ")]
    assert [c["attributes"]["gen_ai.provider.name"] for c in chats] == ["anthropic"] * 4
    assert [c["attributes"]["gen_ai.usage.cache_read.input_tokens"] for c in chats] == [0, 6000, 9000, 12000]
    # The run's trace context stays in the process: the SDK's own tracing (and its propagation) is off.
    for call in anthropic_api.messages.calls:
        assert "traceparent" not in call.request.headers
        assert "tracestate" not in call.request.headers
    # Nothing the run stored holds the key: state, events (spans included) and the audit chain.
    assert FAKE_KEY not in stored
    assert "sk-ant" not in stored
