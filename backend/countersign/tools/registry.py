"""The tool registry: typed contracts between the model and the system.

Inputs are validated against Pydantic models before a handler runs; a validation error goes back to the model
as a tool error it can correct, never as an exception. Outputs are JSON, size-limited, and every fact a tool
returns gets an evidence id the model must cite. Only read tools run here; the two control tools
(submit_change_plan, ask_operator) are handled by the runtime, which owns every state change.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from countersign.models.base import ToolDefinition

MAX_RESULT_BYTES = 24_000


class ToolTimeoutError(TimeoutError):
    pass


class ToolFailureError(RuntimeError):
    """A dependency of the tool failed; the message is safe to show to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any, Any], dict[str, Any]] | None
    control: bool = False  # handled by the runtime, not by a handler

    def definition(self) -> ToolDefinition:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolDefinition(name=self.name, description=self.description, input_schema=schema)


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    output: dict[str, Any]
    error_kind: str | None = None
    retryable: bool = False
    duration_ms: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)

    def content(self) -> str:
        return json.dumps(self.output, separators=(",", ":"), default=str)


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        return [t.definition() for t in self._tools.values()]

    def parse(self, name: str, raw: object) -> BaseModel:
        tool = self._tools[name]
        return tool.input_model.model_validate(raw if isinstance(raw, dict) else {})

    def call(self, ctx: Any, name: str, raw: object, timeout_s: float) -> ToolOutcome:
        started = time.perf_counter()
        tool = self._tools.get(name)
        if tool is None or tool.control or tool.handler is None:
            return ToolOutcome(
                name, False, {"error": f"unknown tool {name!r}", "available": self.names}, "unknown_tool"
            )
        try:
            args = tool.input_model.model_validate(raw if isinstance(raw, dict) else {})
        except ValidationError as exc:
            problems = [
                {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()[:8]
            ]
            return ToolOutcome(
                name, False, {"error": "invalid input", "problems": problems}, "validation", retryable=True
            )
        before = len(ctx.state.evidence)
        try:
            output = tool.handler(ctx, args)
        except ToolTimeoutError as exc:
            return ToolOutcome(
                name,
                False,
                {"error": str(exc), "retryable": True},
                "timeout",
                retryable=True,
                duration_ms=_ms(started),
            )
        except ToolFailureError as exc:
            return ToolOutcome(name, False, {"error": str(exc)}, "dependency", duration_ms=_ms(started))
        elapsed = _ms(started)
        if elapsed > timeout_s * 1000:
            return ToolOutcome(
                name,
                False,
                {"error": f"timed out after {timeout_s:g} s", "retryable": True},
                "timeout",
                retryable=True,
                duration_ms=elapsed,
            )
        output = _bounded(output)
        new_ids = list(ctx.state.evidence)[before:]
        return ToolOutcome(name, True, output, duration_ms=elapsed, evidence_ids=new_ids)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _bounded(output: dict[str, Any]) -> dict[str, Any]:
    """Keep results under the size limit by trimming the longest list, and say so."""
    text = json.dumps(output, default=str)
    if len(text) <= MAX_RESULT_BYTES:
        return output
    lists = [(k, v) for k, v in output.items() if isinstance(v, list)]
    if not lists:
        return {"error": "result too large", "bytes": len(text)}
    key, items = max(lists, key=lambda kv: len(json.dumps(kv[1], default=str)))
    keep = len(items)
    trimmed = dict(output)
    while keep > 1 and len(json.dumps(trimmed, default=str)) > MAX_RESULT_BYTES:
        keep = max(1, keep * 3 // 4)
        trimmed = {
            **output,
            key: items[:keep],
            "truncated": {"field": key, "kept": keep, "total": len(items)},
        }
    return trimmed
