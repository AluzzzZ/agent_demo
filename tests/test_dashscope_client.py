from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minimal_agent.llm.dashscope_client import (
    DashScopeConfigurationError,
    DashScopeLLM,
)


class Usage:
    def model_dump(self, exclude_none=True):
        return {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_native_dashscope_url_is_converted_to_openai_compatible():
    client = DashScopeLLM(
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/api/v1/",
    )

    assert client.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_rejects_non_http_base_url():
    with pytest.raises(DashScopeConfigurationError, match="must start"):
        DashScopeLLM(api_key="test-key", base_url="dashscope.invalid/api/v1")


def test_converts_runtime_tool_protocol_to_openai_messages():
    messages = DashScopeLLM._to_openai_messages(
        "system",
        [
            {"role": "user", "content": "上海天气？"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "我先查询。"},
                    {
                        "type": "tool_use",
                        "id": "w1",
                        "name": "weather",
                        "input": {"city": "上海"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "w1", "content": "晴"}
                ],
            },
        ],
    )

    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[2]["tool_calls"][0]["function"]["name"] == "weather"
    assert json.loads(messages[2]["tool_calls"][0]["function"]["arguments"]) == {
        "city": "上海"
    }
    assert messages[3] == {"role": "tool", "tool_call_id": "w1", "content": "晴"}


def test_normalises_openai_function_call_to_runtime_blocks():
    response = SimpleNamespace(
        model="deepseek-v4-pro",
        usage=Usage(),
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="调用天气工具。",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="weather", arguments='{"city":"上海"}'
                            ),
                        )
                    ],
                ),
            )
        ],
    )

    result = DashScopeLLM._normalise(response)

    assert result.model == "deepseek-v4-pro"
    assert result.content[0]["type"] == "text"
    assert result.content[1] == {
        "type": "tool_use",
        "id": "call-1",
        "name": "weather",
        "input": {"city": "上海"},
    }
    assert result.usage["total_tokens"] == 15
