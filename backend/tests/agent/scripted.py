"""Test doubles for the model boundary: scripted turns and failing providers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from countersign.models.base import ModelError, ModelRequest, ModelResponse, Usage
from countersign.models.offline import ReferencePolicy, read

Turn = Callable[[ModelRequest, dict[str, Any]], list[dict[str, Any]]]


def tool(name: str, call_id: str, **args: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def results(request: ModelRequest) -> dict[str, Any]:
    """Every tool result so far, keyed by tool_use id, parsed from JSON."""
    out: dict[str, Any] = {}
    for m in request.messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if b.get("type") == "tool_result":
                out[b["tool_use_id"]] = json.loads(b["content"])
    return out


class ScriptedModel:
    """Plays a fixed list of turns; each turn is a function of the request and the tool results so far."""

    def __init__(self, turns: list[Turn]) -> None:
        self.turns = turns
        self.calls = 0

    @property
    def provider(self) -> str:
        return "offline"

    @property
    def model(self) -> str:
        return "scripted"

    def complete(self, request: ModelRequest) -> ModelResponse:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        content = turn(request, results(request))
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return ModelResponse(
            id=f"msg_{self.calls}",
            model="scripted",
            content=content,
            stop_reason=stop,
            usage=Usage(input_tokens=100, output_tokens=20),
            simulated=True,
        )


class FailingModel:
    def __init__(self, kind: str, *, times: int = 10_000) -> None:
        self.kind = kind
        self.times = times
        self.calls = 0
        self.inner = ReferencePolicy()

    @property
    def provider(self) -> str:
        return "failing"

    @property
    def model(self) -> str:
        return "failing"

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls <= self.times:
            raise ModelError(
                self.kind, f"simulated {self.kind}", status=529 if self.kind == "overloaded" else 401
            )  # type: ignore[arg-type]
        return self.inner.complete(request)


def findings_in(res: dict[str, Any]) -> list[dict[str, Any]]:
    for value in res.values():
        if isinstance(value, dict) and "findings" in value:
            return list(value["findings"])
    return []


__all__ = ["FailingModel", "ScriptedModel", "findings_in", "read", "results", "tool"]
