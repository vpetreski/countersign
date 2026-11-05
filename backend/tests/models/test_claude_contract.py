"""Contract tests of the Claude adapter against the Messages API wire format.

Every request goes to a respx mock of https://api.anthropic.com/v1/messages through the SDK's real HTTP stack
(tests/models/conftest.py), so the SDK serializes and parses real JSON; no test can reach the network.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import logging
from collections.abc import Callable
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
import time_machine

from countersign.agent.prompts import SYSTEM_PROMPT
from countersign.agent.state import Budgets, RunState
from countersign.models.base import ChatModel, ModelError, ModelRequest, ToolDefinition, Usage
from countersign.models.claude import ClaudeModel, StrictSchemaError, strict_schema, tool_param
from countersign.models.factory import make_model
from countersign.models.offline import ReferencePolicy
from countersign.settings import Settings
from countersign.tools.catalog import registry
from tests.conftest import make_settings
from tests.models import strict_subset
from tests.models.conftest import (
    FAKE_KEY,
    AnthropicAPI,
    failure,
    message,
    ok,
    text,
    thinking,
    tool_use,
)

pytestmark = pytest.mark.claude_contract

DEFINITIONS = registry().definitions()
MARKER = "marker-7f3c"  # appears only in the request body
USER: dict[str, Any] = {"role": "user", "content": [{"type": "text", "text": f"Find idle dev VMs. {MARKER}"}]}


def turn(
    messages: list[dict[str, Any]] | None = None, tools: list[ToolDefinition] = DEFINITIONS
) -> ModelRequest:
    return ModelRequest(
        system=SYSTEM_PROMPT,
        messages=messages or [USER],
        tools=tools,
        max_tokens=4096,
        metadata={"run_id": "run_0123456789abcdef", "seed": "42"},
    )


def compact(value: object) -> bytes:
    """JSON as the SDK writes a request body (its encoder: no spaces, non-ASCII kept)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture
def claude() -> ClaudeModel:
    return ClaudeModel.from_settings(make_settings(anthropic_api_key=FAKE_KEY))


def run_state() -> RunState:
    budgets = Budgets(
        max_model_turns=1,
        max_tool_calls=1,
        max_input_tokens=1,
        max_wall_ms=1,
        max_plan_revisions=1,
        simulated_tokens=True,
    )
    return RunState(budgets=budgets, seed=7)


def raises(call: Callable[[], object]) -> ModelError:
    with pytest.raises(ModelError) as caught:
        call()
    return caught.value


# ---------------------------------------------------------------- 1. the request contract


def test_the_request_carries_the_key_and_the_api_version(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    anthropic_api.messages.mock(return_value=ok(message([text("ok")])))
    claude.complete(turn())
    headers = anthropic_api.headers()
    # SDK 2.x sends an API key as a bearer token; the x-api-key header of the older SDKs and curl docs is not sent.
    assert headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert "x-api-key" not in headers
    assert headers["anthropic-version"] == "2026-10-21"  # the installed SDK's API version
    assert headers["content-type"] == "application/json"


def test_the_request_body_has_the_documented_shape(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    anthropic_api.messages.mock(return_value=ok(message([text("ok")])))
    request = turn()
    claude.complete(request)
    (body,) = anthropic_api.bodies()
    assert set(body) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "tools",
        "tool_choice",
        "thinking",
        "output_config",
        "cache_control",
    }  # no metadata (run id and seed stay in the process), no sampling parameters, no stream flag
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 4096
    assert body["system"] == [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert body["cache_control"] == {"type": "ephemeral"}  # automatic caching of the growing conversation
    assert body["tool_choice"] == {"type": "auto"}
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert body["messages"] == request.messages
    assert compact(request.messages) in anthropic_api.raw_bodies()[0]  # unchanged, byte for byte
    assert [t["name"] for t in body["tools"]] == [d.name for d in DEFINITIONS]
    for sent, definition in zip(body["tools"], DEFINITIONS, strict=True):
        assert sent == {
            "name": definition.name,
            "description": definition.description,
            "input_schema": strict_schema(definition.input_schema),
            "strict": True,
        }


def test_model_and_effort_come_from_settings(anthropic_api: AnthropicAPI) -> None:
    settings = make_settings(
        anthropic_api_key=FAKE_KEY, claude_model="claude-sonnet-5-5", claude_effort="low"
    )
    anthropic_api.messages.mock(return_value=ok(message([text("ok")])))
    ClaudeModel.from_settings(settings).complete(turn())
    (body,) = anthropic_api.bodies()
    assert (body["model"], body["output_config"]) == ("claude-sonnet-5-5", {"effort": "low"})


def test_a_request_without_tools_sends_no_tool_fields(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    anthropic_api.messages.mock(return_value=ok(message([text("ok")])))
    claude.complete(turn(tools=[]))
    (body,) = anthropic_api.bodies()
    assert "tools" not in body
    assert "tool_choice" not in body


@pytest.mark.parametrize(
    "respond",
    [
        lambda: ok(message([text("ok")])),
        lambda: failure(529, "overloaded_error", "Overloaded"),
        lambda: failure(429, "rate_limit_error", "slow down", {"retry-after": "1"}),
        lambda: failure(500, "api_error", "Internal server error"),
        lambda: failure(503, "api_error", "unavailable"),
    ],
    ids=["200", "529", "429", "500", "503"],
)
def test_each_complete_is_exactly_one_http_call(
    anthropic_api: AnthropicAPI, claude: ClaudeModel, respond: Callable[[], httpx.Response]
) -> None:
    response = respond()
    anthropic_api.messages.mock(return_value=response)
    if response.status_code == 200:
        assert claude.complete(turn()).text() == "ok"
    else:
        assert raises(lambda: claude.complete(turn())).retryable  # the runtime, not the SDK, retries it
    assert anthropic_api.messages.call_count == 1


def test_a_timeout_is_one_http_call_too(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    anthropic_api.messages.mock(side_effect=httpx2.ReadTimeout("timed out"))
    raises(lambda: claude.complete(turn()))
    assert anthropic_api.messages.call_count == 1


def test_from_settings_turns_sdk_retries_off_and_uses_the_timeout() -> None:
    model = ClaudeModel.from_settings(make_settings(anthropic_api_key=FAKE_KEY, model_timeout_seconds=12.5))
    client = model._client
    assert client is not None
    assert (client.max_retries, client.timeout) == (0, 12.5)


def test_an_injected_client_is_used_as_given() -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=message([text("from the injected client")]))

    client = anthropic.Anthropic(
        api_key=FAKE_KEY, max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handle))
    )
    response = ClaudeModel("claude-opus-5-5", client).complete(turn())
    assert response.text() == "from the injected client"
    assert [str(r.url) for r in seen] == ["https://api.anthropic.com/v1/messages"]


def test_requests_outside_the_mock_are_refused_not_sent(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    assert claude._client is not None
    with pytest.raises(anthropic.APIConnectionError):
        claude._client.models.list()
    assert [r.split("?")[0] for r in anthropic_api.refused] == ["GET https://api.anthropic.com/v1/models"]
    anthropic_api.refused.clear()  # the refusal is what this test checks; the fixture would fail on it otherwise


# ---------------------------------------------------------------- 1b. strict tool schemas


def test_every_catalog_tool_is_strict_and_inside_the_strict_subset() -> None:
    tools = [tool_param(d) for d in registry().definitions()]
    assert strict_subset.request_problems(tools) == []
    assert all(t["strict"] is True for t in tools)


def test_the_strict_subset_check_catches_violations() -> None:
    bad = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 1},
            "o": {"type": "object", "properties": {}},
            "s": {"type": "string", "format": "regex"},
            "a": {"type": "array", "minItems": 2},
            "e": {"$ref": "https://example.com/schema.json"},
            "r": {"$ref": "#/$defs/Node"},
        },
        "required": ["n", "missing"],
        "additionalProperties": True,
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"next": {"$ref": "#/$defs/Node"}},
                "additionalProperties": False,
            }
        },
    }
    found = "\n".join(strict_subset.problems(bad))
    for expected in (
        "unsupported keyword 'minimum'",
        "$.properties.o: an object without additionalProperties: false",
        "unsupported string format 'regex'",
        "minItems 2",
        "external or unresolved $ref 'https://example.com/schema.json'",
        "required names unknown properties ['missing']",
        "$: additionalProperties other than false",
        "#/$defs/Node: recursive schema",
    ):
        assert expected in found
    many = [
        {
            "name": f"t{i}",
            "strict": True,
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
        for i in range(21)
    ]
    assert strict_subset.request_problems(many) == ["21 strict tools (at most 20)"]


def _assert_moved(original: Any, translated: Any, path: str) -> None:
    """Every keyword of `original` is kept as is in `translated` or written into its description."""
    for key, value in original.items():
        where = f"{path}.{key}"
        if key in ("properties", "$defs"):
            for name, sub in value.items():
                _assert_moved(sub, translated[key][name], f"{where}.{name}")
        elif key == "items":
            _assert_moved(value, translated["items"], where)
        elif key == "anyOf":
            for i, (a, b) in enumerate(zip(value, translated["anyOf"], strict=True)):
                _assert_moved(a, b, f"{where}[{i}]")
        elif key == "description":
            assert translated["description"].startswith(value), where
        elif translated.get(key) != value:
            assert f"{key}: {json.dumps(value)}" in translated.get("description", ""), f"{where} was dropped"


def test_unsupported_constraints_move_into_descriptions_not_away() -> None:
    for definition in registry().definitions():
        _assert_moved(definition.input_schema, strict_schema(definition.input_schema), definition.name)
    limit = strict_schema(registry().definitions()[1].input_schema)["properties"]["limit"]
    assert limit == {
        "type": "integer",
        "title": "Limit",
        "description": "{default: 50, maximum: 100, minimum: 1}",
    }


def test_strict_schema_translates_what_the_dialect_lacks() -> None:
    schema = {
        "type": "object",
        "properties": {
            "op": {"$ref": "#/$defs/Op", "description": "what to do"},
            "tags": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[a-z]+$"},
                "minItems": 2,
                "uniqueItems": True,
            },
            "one": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
            "when": {"type": "string", "format": "date"},
            "pick": {"oneOf": [{"type": "integer"}, {"type": "null"}]},
            "both": {"allOf": [{"$ref": "#/$defs/Op"}]},
            "meta": {"type": "object", "additionalProperties": {"type": "string"}},
            "kind": {"$ref": "#/$defs/Kind"},
        },
        "required": ["op"],
        "$defs": {
            "Op": {"type": "string", "enum": ["a", "b"], "title": "Op"},
            "Kind": {"type": "string", "const": "vm"},
            "Unused": {"type": "string"},
        },
    }
    before = copy.deepcopy(schema)
    out = strict_schema(schema)
    assert schema == before  # the input is never modified
    props = out["properties"]
    assert props["op"] == {"type": "string", "enum": ["a", "b"], "title": "Op", "description": "what to do"}
    assert props["tags"] == {
        "type": "array",
        "items": {"type": "string", "description": '{pattern: "^[a-z]+$"}'},
        "description": "{minItems: 2, uniqueItems: true}",
    }
    assert props["one"] == {"type": "array", "items": {"type": "integer"}, "minItems": 1}
    assert props["when"] == {"type": "string", "format": "date"}
    assert props["pick"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert props["both"] == {"allOf": [{"type": "string", "enum": ["a", "b"], "title": "Op"}]}
    assert props["meta"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "description": '{additionalProperties: {"type": "string"}}',
    }
    assert props["kind"] == {"$ref": "#/$defs/Kind"}
    assert out["$defs"] == {
        "Kind": {"type": "string", "const": "vm"}
    }  # Op was inlined everywhere; Unused unused
    assert strict_subset.problems(out) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"x": {"$ref": "https://example.com/schema.json"}}},
        {
            "type": "object",
            "properties": {"n": {"$ref": "#/$defs/Node"}},
            "$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
        },
        {"type": "object", "properties": {"x": {"type": "date"}}},
    ],
    ids=["external-ref", "recursive", "unknown-type"],
)
def test_strict_schema_refuses_what_the_dialect_cannot_express(
    schema: dict[str, Any], anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    with pytest.raises(StrictSchemaError):
        strict_schema(schema)
    tool = ToolDefinition(
        name="odd", description="a tool with a schema strict mode cannot express", input_schema=schema
    )
    err = raises(lambda: claude.complete(turn(tools=[tool])))
    assert (err.kind, err.retryable) == ("bad_request", False)
    assert anthropic_api.messages.call_count == 0  # refused before anything is sent


def test_tool_translation_is_deterministic() -> None:
    first = compact([tool_param(d) for d in registry().definitions()])
    second = compact([tool_param(d) for d in registry().definitions()])
    assert first == second  # byte-identical tools keep the cached prefix and the thinking bindings valid


# ---------------------------------------------------------------- 2. response parsing and replay


@pytest.mark.parametrize(
    "stop_reason", ["end_turn", "tool_use", "max_tokens", "refusal", "pause_turn", "stop_sequence"]
)
def test_stop_reasons_map_one_to_one(
    anthropic_api: AnthropicAPI, claude: ClaudeModel, stop_reason: str
) -> None:
    content = (
        [tool_use("toolu_01A", "get_estate_summary", {})] if stop_reason == "tool_use" else [text("...")]
    )
    anthropic_api.messages.mock(return_value=ok(message(content, stop_reason)))
    assert claude.complete(turn()).stop_reason == stop_reason


def test_a_stop_reason_the_runtime_cannot_continue_from_is_an_error(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    anthropic_api.messages.mock(return_value=ok(message([text("...")], "model_context_window_exceeded")))
    err = raises(lambda: claude.complete(turn()))
    assert (err.kind, err.retryable) == ("bad_request", False)
    assert "context window" in str(err)


def test_text_responses_parse(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    anthropic_api.messages.mock(return_value=ok(message([text("Nothing to change.")], id_="msg_01Text")))
    response = claude.complete(turn())
    assert (response.id, response.model, response.simulated) == ("msg_01Text", "claude-opus-5-5", False)
    assert response.content == [text("Nothing to change.")]
    assert response.text() == "Nothing to change."
    assert response.tool_uses() == []


def test_parallel_tool_use_blocks_come_back_complete_and_in_order(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    blocks = [
        thinking(),
        text("Reading the estate and the findings."),
        tool_use("toolu_01A", "get_estate_summary", {}),
        tool_use(
            "toolu_01B", "find_waste", {"categories": ["idle_vm"], "environments": ["dev"], "limit": 20}
        ),
        tool_use("toolu_01C", "get_utilization", {"ref": "i-0abc12345678", "window_days": 14}),
    ]
    anthropic_api.messages.mock(return_value=ok(message(blocks, "tool_use")))
    response = claude.complete(turn())
    assert response.stop_reason == "tool_use"
    assert response.content == blocks
    assert [(b["id"], b["name"]) for b in response.tool_uses()] == [
        ("toolu_01A", "get_estate_summary"),
        ("toolu_01B", "find_waste"),
        ("toolu_01C", "get_utilization"),
    ]
    assert response.tool_uses()[1]["input"] == {
        "categories": ["idle_vm"],
        "environments": ["dev"],
        "limit": 20,
    }
    assert response.text() == "Reading the estate and the findings."


def test_thinking_blocks_keep_every_field(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    blocks = [
        thinking("sig-test-0002", "Summary: idle VMs in dev first."),
        {"type": "redacted_thinking", "data": "cmVkYWN0ZWQtdGVzdC1kYXRh"},
        thinking("sig-test-0003"),  # display "omitted": empty text, the signature still matters
        {**tool_use("toolu_01D", "get_estate_summary", {}), "caller": {"type": "direct"}},
    ]
    anthropic_api.messages.mock(return_value=ok(message(blocks, "tool_use")))
    assert claude.complete(turn()).content == blocks


def test_usage_maps_including_cache_tokens(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    usage = {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_creation_input_tokens": 5100,
        "cache_read_input_tokens": 41000,
        "service_tier": "standard",
    }
    anthropic_api.messages.mock(return_value=ok(message([text("ok")], usage=usage)))
    assert claude.complete(turn()).usage == Usage(
        input_tokens=1200, output_tokens=340, cache_read_input_tokens=41000, cache_creation_input_tokens=5100
    )


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 9, "output_tokens": 3},
        {
            "input_tokens": 9,
            "output_tokens": 3,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
        },
    ],
    ids=["absent", "null"],
)
def test_missing_cache_counts_are_zero(
    anthropic_api: AnthropicAPI, claude: ClaudeModel, usage: dict[str, Any]
) -> None:
    anthropic_api.messages.mock(return_value=ok(message([text("ok")], usage=usage)))
    assert claude.complete(turn()).usage == Usage(input_tokens=9, output_tokens=3)


def test_returned_content_replays_verbatim_on_the_next_turn(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    first_content = [
        thinking("sig-test-0004"),
        text("Two reads in parallel. Café"),
        tool_use("toolu_01E", "get_estate_summary", {}),
        {
            **tool_use(
                "toolu_01F", "find_waste", {"environments": ["dev", "test"], "min_monthly_savings_usd": 25.5}
            ),
            "caller": {"type": "direct"},
        },
    ]
    anthropic_api.messages.mock(
        side_effect=[ok(message(first_content, "tool_use")), ok(message([text("done")]))]
    )
    response = claude.complete(turn())
    results = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01E", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "toolu_01F", "content": '{"count":0}'},
        ],
    }
    claude.complete(turn([USER, {"role": "assistant", "content": response.content}, results]))
    second = anthropic_api.bodies()[1]
    assert second["messages"][1] == {"role": "assistant", "content": first_content}  # as the API sent it
    assert json.dumps(second["messages"][1]["content"], sort_keys=True) == json.dumps(
        first_content, sort_keys=True
    )
    assert compact(response.content) in anthropic_api.raw_bodies()[1]  # what we returned is what goes back


# ---------------------------------------------------------------- 3. errors

ERRORS = [
    (529, "overloaded_error", {}, "overloaded", True, None),
    (429, "rate_limit_error", {"retry-after": "7"}, "rate_limited", True, 7.0),
    (500, "api_error", {}, "server", True, None),
    (502, "api_error", {}, "server", True, None),
    (503, "api_error", {}, "server", True, None),
    (401, "authentication_error", {}, "auth", False, None),
    (403, "permission_error", {}, "auth", False, None),
    (400, "invalid_request_error", {}, "bad_request", False, None),
    (413, "request_too_large", {}, "bad_request", False, None),
    (422, "invalid_request_error", {}, "bad_request", False, None),
    (404, "not_found_error", {}, "not_found", False, None),
]


@pytest.mark.parametrize(
    ("status", "type_", "headers", "kind", "retryable", "retry_after"),
    ERRORS,
    ids=[str(e[0]) for e in ERRORS],
)
def test_http_errors_map_to_model_errors(
    anthropic_api: AnthropicAPI,
    claude: ClaudeModel,
    status: int,
    type_: str,
    headers: dict[str, str],
    kind: str,
    retryable: bool,
    retry_after: float | None,
) -> None:
    anthropic_api.messages.mock(return_value=failure(status, type_, f"{type_} detail", headers))
    err = raises(lambda: claude.complete(turn()))
    assert (err.kind, err.retryable, err.status, err.retry_after) == (kind, retryable, status, retry_after)
    assert str(err) == f"HTTP {status} {type_}: {type_} detail (request-id req_test_err)"
    assert anthropic_api.messages.call_count == 1


@pytest.mark.parametrize(
    ("effect", "kind", "detail"),
    [
        (httpx2.ReadTimeout("read timed out"), "timeout", "no response from the Anthropic API within 300 s"),
        (
            httpx2.ConnectTimeout("connect timed out"),
            "timeout",
            "no response from the Anthropic API within 300 s",
        ),
        (
            httpx2.ConnectError("connection refused"),
            "connection",
            "could not reach the Anthropic API (ConnectError)",
        ),
        (
            httpx2.RemoteProtocolError("peer closed"),
            "connection",
            "could not reach the Anthropic API (RemoteProtocolError)",
        ),
    ],
    ids=["read-timeout", "connect-timeout", "connect-error", "protocol-error"],
)
def test_transport_failures_map_to_retryable_model_errors(
    anthropic_api: AnthropicAPI, claude: ClaudeModel, effect: Exception, kind: str, detail: str
) -> None:
    anthropic_api.messages.mock(side_effect=effect)
    err = raises(lambda: claude.complete(turn()))
    assert (err.kind, err.retryable, err.status, str(err)) == (kind, True, None, detail)


def test_retry_after_accepts_an_http_date(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    date = "Wed, 04 Nov 2026 12:00:30 GMT"
    anthropic_api.messages.mock(
        return_value=failure(429, "rate_limit_error", "slow down", {"retry-after": date})
    )
    with time_machine.travel(dt.datetime(2026, 11, 4, 12, 0, 0, tzinfo=dt.UTC), tick=False):
        err = raises(lambda: claude.complete(turn()))
    assert err.retry_after == 30.0


def test_retry_after_is_honoured_on_overload_too(anthropic_api: AnthropicAPI, claude: ClaudeModel) -> None:
    anthropic_api.messages.mock(
        return_value=failure(529, "overloaded_error", "Overloaded", {"retry-after": "2"})
    )
    assert raises(lambda: claude.complete(turn())).retry_after == 2.0


def test_error_messages_are_bounded_and_carry_no_request_body(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    anthropic_api.messages.mock(return_value=failure(400, "invalid_request_error", "x" * 5000))
    err = raises(lambda: claude.complete(turn()))
    assert len(str(err)) < 400
    assert MARKER not in str(err)
    assert SYSTEM_PROMPT[:60] not in str(err)


def test_a_missing_key_fails_without_a_call(anthropic_api: AnthropicAPI) -> None:
    model = ClaudeModel.from_settings(make_settings(model_provider="claude"))
    err = raises(lambda: model.complete(turn()))
    assert (err.kind, err.retryable) == ("auth", False)
    assert anthropic_api.messages.call_count == 0


# ---------------------------------------------------------------- 4. selection: a key means Claude, none the offline policy


def test_without_a_key_the_offline_reference_policy_runs() -> None:
    settings = make_settings()
    assert settings.model_mode == "offline"
    assert isinstance(make_model(run_state(), settings), ReferencePolicy)


def test_with_a_key_claude_runs() -> None:
    model = make_model(run_state(), make_settings(anthropic_api_key=FAKE_KEY))
    assert isinstance(model, ClaudeModel)
    assert isinstance(model, ChatModel)
    assert (model.provider, model.model) == ("anthropic", "claude-opus-5-5")


def test_the_claude_provider_selects_claude_even_without_a_key() -> None:
    assert isinstance(make_model(run_state(), make_settings(model_provider="claude")), ClaudeModel)


def test_the_offline_provider_wins_over_a_key() -> None:
    settings = make_settings(anthropic_api_key=FAKE_KEY, model_provider="offline")
    assert isinstance(make_model(run_state(), settings), ReferencePolicy)


def test_the_variables_select_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    def fresh() -> Settings:
        return Settings(_env_file=None, database_url="sqlite://")  # type: ignore[call-arg]

    assert isinstance(make_model(run_state(), fresh()), ReferencePolicy)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    assert isinstance(make_model(run_state(), fresh()), ClaudeModel)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("COUNTERSIGN_MODEL_PROVIDER", "claude")
    assert isinstance(make_model(run_state(), fresh()), ClaudeModel)


# ---------------------------------------------------------------- 6. the key stays secret


def test_the_key_is_not_in_repr_or_str(claude: ClaudeModel) -> None:
    for shown in (repr(claude), str(claude)):
        assert FAKE_KEY not in shown
        assert "sk-ant" not in shown


def test_the_key_is_not_in_error_messages_even_when_the_api_echoes_it(
    anthropic_api: AnthropicAPI, claude: ClaudeModel
) -> None:
    anthropic_api.messages.mock(return_value=failure(401, "authentication_error", f"invalid key {FAKE_KEY}"))
    err = raises(lambda: claude.complete(turn()))
    for shown in (str(err), repr(err), *map(str, err.args)):
        assert FAKE_KEY not in shown
    assert "[REDACTED]" in str(err)
    # Raised outside the SDK's handler: no SDK exception, which holds the request and its headers, is attached.
    assert err.__cause__ is None
    assert err.__context__ is None


def test_the_key_is_not_logged(
    anthropic_api: AnthropicAPI, claude: ClaudeModel, caplog: pytest.LogCaptureFixture
) -> None:
    for name in ("", "anthropic", "httpx2", "httpcore2", "countersign"):
        caplog.set_level(logging.DEBUG, logger=name)
    anthropic_api.messages.mock(
        side_effect=[ok(message([text("ok")])), failure(401, "authentication_error", "invalid key")]
    )
    claude.complete(turn())
    raises(lambda: claude.complete(turn()))
    assert any(r.name.startswith("anthropic") for r in caplog.records)  # the SDK did log, at debug level
    assert FAKE_KEY not in caplog.text
    for record in caplog.records:
        assert FAKE_KEY not in record.getMessage()
