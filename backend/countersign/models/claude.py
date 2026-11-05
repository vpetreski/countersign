"""The Claude adapter: one model turn through the Messages API with the official Anthropic SDK.

Request. The model, `max_tokens`, the system prompt as one text block with an ephemeral cache breakpoint (tools
render before system, so the breakpoint caches the tool definitions and the prompt together), top-level automatic
caching for the growing conversation, every tool with `strict: true` and its schema translated to the strict
dialect (`strict_schema`), `tool_choice` auto (Claude Opus 5.5 rejects `any` and `tool`), adaptive thinking (always
on for Claude Opus 5.5) and `output_config.effort` from settings. Messages pass through unchanged. The request's
metadata (run id, seed) stays in the process: the API's only metadata field is `user_id`, and a run is not a user.

Response. Content blocks come back as the wire dicts the API sent (`to_dict`, the SDK's own replay form), so text,
tool_use, thinking with its signature and redacted_thinking replay verbatim on the next turn; the API ties thinking
blocks to the exact conversation that produced them.

Errors. SDK retries are off (`max_retries=0`): the runtime retries `ModelError`s itself, with backoff and a
circuit breaker. Every SDK failure becomes a `ModelError` whose message is safe to log: status, error type,
the API's message (redacted, truncated) and the request id; never the key, never the request body.

Secrets. The key comes only from settings; the SDK is given it explicitly, so it never reads credentials from
the environment or disk. The SDK's own OpenTelemetry spans are off, and with them its trace headers: the runtime
records the `chat` span itself, and the run's trace context stays in the process.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import json
import math
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Final, Literal, cast, get_args

import anthropic

from countersign.models.base import (
    Block,
    ErrorKind,
    ModelError,
    ModelRequest,
    ModelResponse,
    StopReason,
    ToolDefinition,
    Usage,
)
from countersign.security.screening import redact

if TYPE_CHECKING:
    import httpx2
    from anthropic.types import (
        CacheControlEphemeralParam,
        Message,
        MessageParam,
        OutputConfigParam,
        TextBlockParam,
        ThinkingConfigAdaptiveParam,
        ToolChoiceAutoParam,
        ToolParam,
    )

    from countersign.settings import Settings

Effort = Literal["low", "medium", "high", "xhigh", "max"]

EPHEMERAL: Final[CacheControlEphemeralParam] = {"type": "ephemeral"}
# Claude Opus 5.5 rejects the forced choices `any` and `tool`; strict schemas keep the arguments valid instead.
AUTO: Final[ToolChoiceAutoParam] = {"type": "auto"}
ADAPTIVE: Final[ThinkingConfigAdaptiveParam] = {"type": "adaptive"}  # always on for Claude Opus 5.5
STOP_REASONS: Final[frozenset[str]] = frozenset(get_args(StopReason))
MAX_DETAIL = 300  # characters of the API's error message kept in a ModelError

# ---------------------------------------------------------------- strict tool schemas
#
# The strict dialect is the JSON Schema subset of structured outputs, which strict tool use shares (platform docs,
# "JSON Schema limitations"): basic types, enum of primitives, const, anyOf, allOf without $ref, local $ref and
# $defs/definitions, required, additionalProperties false on every object, a short list of string formats and
# minItems 0 or 1. Everything else (numeric and length bounds, maxItems, patterns, defaults...) is moved into the
# node's description as `{name: value}`, the form the SDK's `transform_schema` uses, so the model still reads it;
# the runtime validates every tool input against the full Pydantic schema regardless. Where the docs and the SDK
# differ, the SDK's narrower choice is kept: `default` and `pattern` are documented as supported but the SDK
# moves them into the description, and so does this function. `oneOf` becomes `anyOf`, as in the SDK.

STRING_FORMATS: Final = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
TYPES: Final = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_DEF_KEYS: Final = ("$defs", "definitions")
_ORDER: Final = (
    "$ref",
    "type",
    "anyOf",
    "allOf",
    "enum",
    "const",
    "format",
    "items",
    "minItems",
    "properties",
    "required",
    "additionalProperties",
    "title",
    "description",
)


class StrictSchemaError(ValueError):
    """A schema the strict dialect cannot express at all: recursion, references outside the document."""


def strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """`schema` translated to the strict dialect. Deterministic: equal input gives byte-identical output, which
    keeps the prompt-cache prefix and the thinking-block bindings stable across turns."""
    root = dict(schema)
    defs: dict[str, dict[str, Any]] = {}
    for key in _DEF_KEYS:
        for name, value in (root.pop(key, None) or {}).items():
            defs[f"#/{key}/{name}"] = dict(value)
    _reject_cycles(defs)
    converted = {ref: _node(value, defs, inline=(), in_all_of=False) for ref, value in defs.items()}
    out = _node(root, defs, inline=(), in_all_of=False)
    reachable = _reachable(out, converted)  # definitions inlined everywhere they were used are dropped
    for key in _DEF_KEYS:
        prefix = f"#/{key}/"
        kept = {
            ref.removeprefix(prefix): converted[ref]
            for ref in converted
            if ref.startswith(prefix) and ref in reachable
        }
        if kept:
            out[key] = kept
    return out


def _node(
    node: Mapping[str, Any],
    defs: Mapping[str, Mapping[str, Any]],
    *,
    inline: tuple[str, ...],
    in_all_of: bool,
) -> dict[str, Any]:
    rest = dict(node)
    ref = rest.pop("$ref", None)
    if ref is not None:
        target = _target(ref, defs)
        if not rest and not in_all_of:
            return {"$ref": ref}  # a bare local reference is part of the dialect
        if ref in inline:
            raise StrictSchemaError(f"recursive schemas are not supported by strict tool use ({ref})")
        # Annotations next to a $ref (a field's description) and references inside allOf are not part of the
        # dialect: inline the definition so nothing is lost. The sibling keys win over the definition's.
        return _node({**target, **rest}, defs, inline=(*inline, ref), in_all_of=False)

    out: dict[str, Any] = {}
    moved: dict[str, Any] = {}

    def sub(value: Any, *, all_of: bool = False) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise StrictSchemaError(f"expected a schema object, got {type(value).__name__}")
        return _node(value, defs, inline=inline, in_all_of=all_of)

    type_ = rest.pop("type", None)
    if type_ is not None:
        names = type_ if isinstance(type_, list) else [type_]
        if any(t not in TYPES for t in names):
            raise StrictSchemaError(f"unsupported type {type_!r}")
        out["type"] = type_
    any_of, one_of = rest.pop("anyOf", None), rest.pop("oneOf", None)
    if any_of is not None and one_of is not None:
        raise StrictSchemaError("a schema with both anyOf and oneOf has no strict equivalent")
    if (variants := any_of if any_of is not None else one_of) is not None:
        out["anyOf"] = [sub(v) for v in variants]
    if (all_of := rest.pop("allOf", None)) is not None:
        out["allOf"] = [sub(v, all_of=True) for v in all_of]
    for key in ("enum", "const"):
        if key in rest:
            value = rest.pop(key)
            values = value if key == "enum" else [value]
            if isinstance(values, list) and all(_primitive(v) for v in values):
                out[key] = value
            else:
                moved[key] = value  # complex enum or const values are outside the dialect

    names = type_ if isinstance(type_, list) else [type_]
    if "string" in names and "format" in rest:
        fmt = rest.pop("format")
        if fmt in STRING_FORMATS:
            out["format"] = fmt
        else:
            moved["format"] = fmt
    if "items" in rest and isinstance(rest["items"], Mapping):
        out["items"] = sub(rest.pop("items"))
    if "minItems" in rest and rest["minItems"] in (0, 1) and not isinstance(rest["minItems"], bool):
        out["minItems"] = rest.pop("minItems")
    if "object" in names or "properties" in rest:
        out["properties"] = {k: sub(v) for k, v in (rest.pop("properties", None) or {}).items()}
        if "required" in rest:
            out["required"] = list(rest.pop("required"))
        extra = rest.pop("additionalProperties", False)
        if extra is not False:
            moved["additionalProperties"] = extra  # the dialect only allows false
        out["additionalProperties"] = False
    for key in ("title", "description"):
        if key in rest:
            out[key] = rest.pop(key)
    moved.update(rest)  # whatever is left is outside the dialect
    if moved:
        note = "{" + ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in moved.items()) + "}"
        out["description"] = f"{out['description']}\n\n{note}" if out.get("description") else note
    return {k: out[k] for k in _ORDER if k in out}


def _primitive(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _target(ref: object, defs: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise StrictSchemaError(f"only local references are supported by strict tool use, not {ref!r}")
    if ref not in defs:
        raise StrictSchemaError(f"unresolved reference {ref!r}")
    return defs[ref]


def _refs(value: object) -> Iterator[str]:
    if isinstance(value, Mapping):
        for k, v in value.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(value, list):
        for v in value:
            yield from _refs(v)


def _reject_cycles(defs: Mapping[str, Mapping[str, Any]]) -> None:
    edges = {ref: [r for r in _refs(schema) if r in defs] for ref, schema in defs.items()}
    done: set[str] = set()

    def visit(ref: str, path: tuple[str, ...]) -> None:
        if ref in path:
            raise StrictSchemaError(f"recursive schemas are not supported by strict tool use ({ref})")
        if ref in done:
            return
        for nxt in edges[ref]:
            visit(nxt, (*path, ref))
        done.add(ref)

    for ref in edges:
        visit(ref, ())


def _reachable(root: Mapping[str, Any], defs: Mapping[str, Mapping[str, Any]]) -> set[str]:
    seen: set[str] = set()
    todo = list(_refs(root))
    while todo:
        ref = todo.pop()
        if ref in seen or ref not in defs:
            continue
        seen.add(ref)
        todo.extend(_refs(defs[ref]))
    return seen


def tool_param(definition: ToolDefinition) -> ToolParam:
    """A tool as the API receives it: strict, with its schema in the strict dialect."""
    return {
        "name": definition.name,
        "description": definition.description,
        "input_schema": strict_schema(definition.input_schema),
        "strict": True,
    }


# ---------------------------------------------------------------- the model


class ClaudeModel:
    """`ChatModel` over the Anthropic Messages API. Built without a client (no key configured), every call fails
    with an `auth` ModelError and nothing is sent."""

    def __init__(
        self, model: str, client: anthropic.Anthropic | None, *, effort: Effort | None = None
    ) -> None:
        self._model = model
        self._client = client
        self._effort = effort

    @classmethod
    def from_settings(cls, settings: Settings) -> ClaudeModel:
        key = settings.anthropic_api_key
        client = None
        if key is not None and key.get_secret_value():
            client = anthropic.Anthropic(
                api_key=key.get_secret_value(),  # explicit, so the SDK reads no credential from env or disk
                max_retries=0,  # the runtime owns retries (backoff, retry-after, circuit breaker)
                timeout=settings.model_timeout_seconds,
                open_telemetry=False,  # the runtime records the chat span; nothing else traces the call
            )
        return cls(settings.claude_model, client, effort=settings.claude_effort)

    def __repr__(self) -> str:
        return f"ClaudeModel(model={self._model!r}, effort={self._effort!r}, client={'set' if self._client else 'none'})"

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._client is None:
            raise ModelError("auth", "no Anthropic API key is configured (set ANTHROPIC_API_KEY)")
        try:
            tools = [tool_param(t) for t in request.tools]
        except StrictSchemaError as exc:
            raise ModelError("bad_request", f"a tool schema has no strict-mode equivalent: {exc}") from None
        system: list[TextBlockParam] = (
            [{"type": "text", "text": request.system, "cache_control": EPHEMERAL}] if request.system else []
        )
        effort: OutputConfigParam | None = {"effort": self._effort} if self._effort else None
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=system or anthropic.omit,
                messages=cast("list[MessageParam]", request.messages),
                tools=tools or anthropic.omit,
                tool_choice=AUTO if tools else anthropic.omit,
                thinking=ADAPTIVE,
                output_config=effort or anthropic.omit,
                cache_control=EPHEMERAL,  # automatic caching: the breakpoint follows the conversation's last block
            )
        except anthropic.AnthropicError as exc:
            error = _model_error(exc, self._client)
        else:
            return _response(message)
        raise error  # raised outside the handler, so no SDK exception (which holds the request) is chained to it


def _response(message: Message) -> ModelResponse:
    if message.stop_reason not in STOP_REASONS:
        why = (
            "the conversation exceeded the model's context window"
            if message.stop_reason == "model_context_window_exceeded"
            else "this runtime cannot continue from it"
        )
        raise ModelError("bad_request", f"the model stopped with {message.stop_reason!r}: {why}")
    usage = message.usage
    return ModelResponse(
        id=message.id,
        model=message.model,
        content=[cast("Block", block.to_dict(mode="json")) for block in message.content],
        stop_reason=cast("StopReason", message.stop_reason),
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        ),
        simulated=False,
    )


# ---------------------------------------------------------------- errors

_STATUS_KIND: Final[dict[int, ErrorKind]] = {
    400: "bad_request",
    401: "auth",
    402: "auth",  # billing_error: the account, like the key, needs a human
    403: "auth",
    404: "not_found",
    408: "timeout",
    409: "server",  # transient conflict; the SDK's own policy retries it
    413: "bad_request",
    422: "bad_request",
    429: "rate_limited",
    529: "overloaded",
}


def _model_error(exc: anthropic.AnthropicError, client: anthropic.Anthropic) -> ModelError:
    secret = client.api_key
    if isinstance(exc, anthropic.APITimeoutError):
        return ModelError("timeout", f"no response from the Anthropic API within {_timeout(client)}")
    if isinstance(exc, anthropic.APIConnectionError):
        cause = type(exc.__cause__).__name__ if exc.__cause__ else "unknown"
        return ModelError("connection", f"could not reach the Anthropic API ({cause})")
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        kind = _STATUS_KIND.get(status, "server" if status >= 500 else "bad_request")
        text = f"HTTP {status}" + (f" {exc.type}" if exc.type else "")
        if exc.api_message:
            text += ": " + _safe(exc.api_message, secret)
        if exc.request_id:
            text += f" (request-id {exc.request_id})"
        return ModelError(kind, text, retry_after=_retry_after(exc.response.headers), status=status)
    if isinstance(exc, anthropic.APIError):
        return ModelError("server", f"unexpected response from the Anthropic API ({type(exc).__name__})")
    kind = "auth" if isinstance(exc, anthropic.CredentialsError) else "bad_request"
    return ModelError(
        kind, f"the Anthropic SDK refused the call ({type(exc).__name__}): {_safe(str(exc), secret)}"
    )


def _safe(text: str, secret: str | None) -> str:
    """One line, bounded, with anything key-like masked. The API's messages never carry the key; this holds even
    if one did."""
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = " ".join(redact(text)[0].split())
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 3] + "..."


def _timeout(client: anthropic.Anthropic) -> str:
    timeout = client.timeout
    return f"{timeout:g} s" if isinstance(timeout, int | float) else "the client timeout"


def _retry_after(headers: httpx2.Headers) -> float | None:
    """Seconds from a `retry-after` header: a number of seconds or an HTTP date."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except TypeError, ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        seconds = (when - dt.datetime.now(dt.UTC)).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None
