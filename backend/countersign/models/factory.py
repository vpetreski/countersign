"""Model selection: Claude when ANTHROPIC_API_KEY is set (or COUNTERSIGN_MODEL_PROVIDER=claude), otherwise the
offline reference policy. The choice is made per run and recorded on it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from countersign.models.base import ChatModel
from countersign.models.offline import ReferencePolicy

if TYPE_CHECKING:
    from countersign.agent.state import RunState
    from countersign.settings import Settings


def make_model(state: RunState, settings: Settings) -> ChatModel:
    if settings.model_mode == "claude":
        from countersign.models.claude import ClaudeModel  # noqa: PLC0415 - the SDK client is only built when a key exists

        return ClaudeModel.from_settings(settings)
    return ReferencePolicy(seed=state.seed)
