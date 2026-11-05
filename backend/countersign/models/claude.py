"""The Claude adapter (official Anthropic SDK). Placeholder until the adapter lands with its contract tests."""

from __future__ import annotations

from countersign.models.base import ModelRequest, ModelResponse
from countersign.settings import Settings


class ClaudeModel:
    def __init__(self, model: str) -> None:
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> ClaudeModel:
        return cls(settings.claude_model)

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError("the Claude adapter is not wired yet")
