"""Fixtures for the Claude adapter's contract tests. Nothing here can reach the network or spend money.

The Anthropic SDK 2.x sends HTTP through httpx2, which respx 0.23 does not patch (it patches httpx and httpcore).
`anthropic_api` therefore replaces `httpx2.HTTPTransport.handle_request`, the SDK's default transport, with a
bridge that mirrors each httpx2 request as an httpx request, resolves it with a respx router (respx's documented
`router.handler`) and returns the router's response as an httpx2 response. The SDK still serializes and parses
real wire JSON. The router mocks only POST https://api.anthropic.com/v1/messages; anything else is refused
(`assert_all_mocked`), recorded and fails the test, and a pass-through route is refused too.

Two more fences hold for every test in this package: sockets cannot connect, and no ANTHROPIC_* or COUNTERSIGN_*
variable of the developer's environment is visible.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpx2
import pytest
import respx
from respx.models import AllMockedAssertionError, PassThrough

API = "https://api.anthropic.com"
FAKE_KEY = "sk-ant-test-" + "0" * 40  # obviously fake; never a real credential


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("network access is disabled in the Claude contract tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "COUNTERSIGN_")):
            monkeypatch.delenv(name)


@dataclass
class AnthropicAPI:
    router: respx.Router
    messages: respx.Route  # POST /v1/messages
    refused: list[str] = field(default_factory=list)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(call.request.content) for call in self.messages.calls]

    def raw_bodies(self) -> list[bytes]:
        return [call.request.content for call in self.messages.calls]

    def headers(self, index: int = -1) -> httpx.Headers:
        return self.messages.calls[index].request.headers


def _bridge(router: respx.Router, refused: list[str]) -> Any:
    def handle_request(_transport: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:
        body = request.read()
        mirrored = httpx.Request(request.method, str(request.url), headers=request.headers.raw, content=body)
        try:
            response = router.handler(mirrored)
        except AllMockedAssertionError:
            refused.append(f"{request.method} {request.url}")
            raise
        except PassThrough as exc:
            refused.append(f"pass-through {request.method} {request.url}")
            raise AssertionError("pass-through to the real network is disabled") from exc
        return httpx2.Response(
            response.status_code, headers=response.headers.raw, content=response.content, request=request
        )

    return handle_request


@pytest.fixture
def anthropic_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[AnthropicAPI]:
    router = respx.Router(base_url=API, assert_all_called=False, assert_all_mocked=True)
    api = AnthropicAPI(router=router, messages=router.post("/v1/messages", name="messages"))
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", _bridge(router, api.refused))
    yield api
    assert not api.refused, f"requests outside the mocked API: {api.refused}"


# ---------------------------------------------------------------- Claude-shaped wire JSON


def message(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
    *,
    usage: dict[str, Any] | None = None,
    id_: str = "msg_01TestAAAAAAAAAAAAAAAAAA",
    model: str = "claude-opus-5-5",
) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage
        or {
            "input_tokens": 21,
            "output_tokens": 34,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body, headers={"request-id": "req_test_ok"})


def failure(status: int, type_: str, text: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "error", "error": {"type": type_, "message": text}, "request_id": "req_test_err"},
        headers={"request-id": "req_test_err", **(headers or {})},
    )


def thinking(signature: str = "sig-test-0001", text: str = "") -> dict[str, Any]:
    # Claude Opus 5.5 returns thinking text empty by default (display "omitted"); the signature carries it.
    return {"type": "thinking", "thinking": text, "signature": signature}


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(id_: str, name: str, input_: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": name, "input": input_}
