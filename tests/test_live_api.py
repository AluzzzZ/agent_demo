from __future__ import annotations

import os

import pytest

from minimal_agent.llm import DashScopeLLM
from minimal_agent.runtime import AgentRuntime
from minimal_agent.storage import SessionStore
from minimal_agent.tools import create_default_registry
from minimal_agent.tracing import TraceRecorder


@pytest.mark.live_api
def test_real_dashscope_api_smoke(tmp_path):
    if os.getenv("RUN_LIVE_API_TEST") != "1" or not (
        os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY")
    ):
        pytest.skip("set RUN_LIVE_API_TEST=1 and DASHSCOPE_API_KEY to enable paid smoke test")
    runtime = AgentRuntime(
        llm=DashScopeLLM(max_tokens=300),
        store=SessionStore(tmp_path / "live.db"),
        tools=create_default_registry(),
        tracer=TraceRecorder(tmp_path / "live.jsonl"),
        max_iterations=3,
    )

    result = runtime.run(
        user_id="smoke-user",
        session_id="smoke-session",
        user_input="请用 calculator 计算 17*19，并只告诉我结果。",
    )

    assert "323" in result.answer
    assert result.exit_reason == "final_answer"
