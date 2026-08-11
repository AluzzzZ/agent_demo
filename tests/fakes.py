from __future__ import annotations

from collections import deque
from typing import Any

from minimal_agent.models import LLMResponse


def text_response(text: str) -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        model="fake-model",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def tool_response(*calls: tuple[str, str, dict[str, Any]], text: str = "") -> LLMResponse:
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(
        {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
        for call_id, name, arguments in calls
    )
    return LLMResponse(
        content=blocks,
        stop_reason="tool_use",
        model="fake-model",
        usage={"input_tokens": 12, "output_tokens": 7},
    )


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse], summary: str = "压缩后的会话摘要"):
        self.responses = deque(responses)
        self.summary_text = summary
        self.calls: list[dict[str, Any]] = []
        self.summary_calls: list[dict[str, str]] = []

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if not self.responses:
            raise AssertionError("ScriptedLLM has no response left")
        return self.responses.popleft()

    def summarize(self, *, previous_summary: str, transcript: str) -> str:
        self.summary_calls.append(
            {"previous_summary": previous_summary, "transcript": transcript}
        )
        return self.summary_text


class RepeatingToolLLM:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, *, system: str, messages: list, tools: list) -> LLMResponse:
        self.call_count += 1
        return tool_response(
            (f"repeat-{self.call_count}", "calculator", {"expression": "1 + 1"})
        )

    def summarize(self, *, previous_summary: str, transcript: str) -> str:
        return "summary"

