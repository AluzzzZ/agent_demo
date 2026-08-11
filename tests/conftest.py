from __future__ import annotations

import pytest

from minimal_agent.runtime import AgentRuntime
from minimal_agent.storage import SessionStore
from minimal_agent.tools import create_default_registry
from minimal_agent.tracing import TraceRecorder


@pytest.fixture
def runtime_factory(tmp_path):
    def build(llm, **kwargs):
        store = SessionStore(tmp_path / "agent.db")
        tracer = TraceRecorder(tmp_path / "traces.jsonl")
        runtime = AgentRuntime(
            llm=llm,
            store=store,
            tools=create_default_registry(),
            tracer=tracer,
            **kwargs,
        )
        return runtime, store, tmp_path / "traces.jsonl"

    return build

