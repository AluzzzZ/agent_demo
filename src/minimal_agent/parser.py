from __future__ import annotations

from typing import Any

from .models import LLMResponse, ModelTurn, ToolCall


class ModelOutputError(ValueError):
    """Raised when a model response cannot be safely interpreted."""


def parse_model_response(response: LLMResponse) -> ModelTurn:
    """Parse public text and tool calls without exposing hidden chain-of-thought.

    Text emitted next to tool calls is treated as a short, public decision summary.
    Hidden ``thinking`` blocks are deliberately neither extracted nor logged.
    """

    if not isinstance(response.content, list):
        raise ModelOutputError("model content must be a list of content blocks")

    texts: list[str] = []
    calls: list[ToolCall] = []
    assistant_content: list[dict[str, Any]] = []

    for raw_block in response.content:
        if not isinstance(raw_block, dict):
            raise ModelOutputError("every model content block must be an object")
        block = dict(raw_block)
        block_type = block.get("type")

        if block_type == "text":
            text = block.get("text", "")
            if not isinstance(text, str):
                raise ModelOutputError("text block must contain a string")
            if text.strip():
                texts.append(text.strip())
            assistant_content.append(block)
        elif block_type == "tool_use":
            call_id = block.get("id")
            name = block.get("name")
            arguments = block.get("input")
            if not isinstance(call_id, str) or not call_id:
                raise ModelOutputError("tool_use block is missing an id")
            if not isinstance(name, str) or not name:
                raise ModelOutputError("tool_use block is missing a name")
            if not isinstance(arguments, dict):
                raise ModelOutputError("tool_use input must be an object")
            calls.append(ToolCall(call_id, name, arguments))
            assistant_content.append(block)
        elif block_type in {"thinking", "redacted_thinking"}:
            # Extended thinking is not enabled by this project. If a provider adds
            # such a block, omit it from storage/tracing to avoid persisting CoT.
            continue
        else:
            # Preserve forward-compatible public blocks for the provider round-trip.
            assistant_content.append(block)

    public_text = "\n\n".join(texts).strip()
    final_answer = None if calls else (public_text or None)
    return ModelTurn(
        assistant_content=assistant_content,
        reasoning_summary=public_text if calls else "",
        tool_calls=calls,
        final_answer=final_answer,
        stop_reason=response.stop_reason,
    )

