from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """Provider-neutral subset of a model response used by the runtime."""

    content: list[dict[str, Any]]
    stop_reason: str | None = None
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelTurn:
    assistant_content: list[dict[str, Any]]
    reasoning_summary: str
    tool_calls: list[ToolCall]
    final_answer: str | None
    stop_reason: str | None


@dataclass(frozen=True)
class StoredMessage:
    id: int
    role: str
    content: str | list[dict[str, Any]]
    created_at: str

