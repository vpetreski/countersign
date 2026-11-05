"""The model boundary. Every provider speaks the Anthropic Messages semantics: a system prompt, a list of
messages whose content is text, tool_use and tool_result blocks, tool definitions, a stop reason and usage.
The runtime keeps the conversation in exactly that wire format, so it can be replayed to Claude verbatim."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "stop_sequence", "pause_turn"]
Block = dict[str, Any]  # one content block in the Messages wire format
Message = dict[str, Any]  # {"role": "user" | "assistant", "content": [Block, ...]}


class ToolDefinition(BaseModel):
    """A tool as the Messages API receives it. `input_schema` is the full JSON Schema the runtime validates
    against; a provider that needs a restricted dialect (strict tool use) derives it from this one."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: str
    messages: list[Message]
    tools: list[ToolDefinition]
    max_tokens: int = 4096
    metadata: dict[str, str] = Field(
        default_factory=dict, description="run id and seed; never sent as prompt text"
    )


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class ModelResponse(BaseModel):
    id: str
    model: str
    content: list[Block]
    stop_reason: StopReason
    usage: Usage
    simulated: bool = Field(
        description="True for the offline model: its token counts are estimates, not billing"
    )

    def tool_uses(self) -> list[Block]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text" and b.get("text"))


ErrorKind = Literal[
    "overloaded", "rate_limited", "timeout", "connection", "server", "auth", "bad_request", "not_found"
]


class ModelError(Exception):
    """A provider failure, classified so the runtime can decide between retrying and failing the run."""

    RETRYABLE: frozenset[str] = frozenset({"overloaded", "rate_limited", "timeout", "connection", "server"})

    def __init__(
        self, kind: ErrorKind, message: str, *, retry_after: float | None = None, status: int | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE


@runtime_checkable
class ChatModel(Protocol):
    """One model turn: given the conversation so far, return the next assistant message."""

    @property
    def provider(self) -> str:  # "anthropic" or "offline"; OTel gen_ai.provider.name
        ...

    @property
    def model(self) -> str: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...
