from __future__ import annotations

import pytest

from minimal_agent.models import LLMResponse
from minimal_agent.parser import ModelOutputError, parse_model_response


def test_parses_public_decision_summary_and_tool_calls():
    response = LLMResponse(
        content=[
            {"type": "text", "text": "我先查询天气。"},
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "weather",
                "input": {"city": "上海"},
            },
        ],
        stop_reason="tool_use",
    )

    turn = parse_model_response(response)

    assert turn.reasoning_summary == "我先查询天气。"
    assert turn.final_answer is None
    assert turn.tool_calls[0].name == "weather"


def test_hidden_thinking_is_not_extracted_or_persisted():
    response = LLMResponse(
        content=[
            {"type": "thinking", "thinking": "private chain of thought", "signature": "x"},
            {"type": "text", "text": "最终答案"},
        ]
    )

    turn = parse_model_response(response)

    assert turn.final_answer == "最终答案"
    assert all(block.get("type") != "thinking" for block in turn.assistant_content)


def test_rejects_malformed_tool_call():
    with pytest.raises(ModelOutputError, match="missing an id"):
        parse_model_response(
            LLMResponse(content=[{"type": "tool_use", "name": "weather", "input": {}}])
        )

