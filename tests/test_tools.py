from __future__ import annotations

import json

import pytest

from minimal_agent.storage import SessionStore
from minimal_agent.tools import ToolContext, ToolDefinition, ToolRegistry, create_default_registry
from minimal_agent.tracing import TraceRecorder


def execute(tmp_path, name, arguments):
    store = SessionStore(tmp_path / "tools.db")
    store.ensure_session("user-a", "window-1")
    registry = create_default_registry()
    return registry.execute(
        call_id="call-1",
        name=name,
        arguments=arguments,
        context=ToolContext("user-a", "window-1", "trace-1", 1, store),
        tracer=TraceRecorder(tmp_path / "tools.jsonl"),
    )


def test_calculator_is_safe_and_correct(tmp_path):
    success = execute(tmp_path, "calculator", {"expression": "2 + 3 * 4"})
    unsafe = execute(
        tmp_path, "calculator", {"expression": "__import__('os').system('whoami')"}
    )

    assert json.loads(success.content)["result"] == 14
    assert success.is_error is False
    assert unsafe.is_error is True


def test_schema_validation_returns_error_to_model(tmp_path):
    result = execute(tmp_path, "weather", {"city": "上海", "day": "next-week"})

    assert result.is_error is True
    assert "invalid arguments" in result.content


def test_unknown_tool_returns_controlled_error(tmp_path):
    result = execute(tmp_path, "missing", {})

    assert result.is_error is True
    assert "unknown tool" in result.content


def test_duplicate_registration_is_rejected():
    registry = ToolRegistry()
    definition = ToolDefinition(
        "echo",
        "echo input",
        {"type": "object"},
        lambda arguments, context: arguments,
    )
    registry.register(definition)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)


def test_unexpected_handler_error_does_not_leak_secret(tmp_path):
    store = SessionStore(tmp_path / "secret.db")
    store.ensure_session("user-a", "window-1")
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "explode",
            "raise an internal exception",
            {"type": "object"},
            lambda arguments, context: (_ for _ in ()).throw(
                RuntimeError("internal sk-this-secret-must-not-leak")
            ),
        )
    )

    result = registry.execute(
        call_id="call-secret",
        name="explode",
        arguments={},
        context=ToolContext("user-a", "window-1", "trace-secret", 1, store),
        tracer=TraceRecorder(tmp_path / "secret-trace.jsonl"),
    )

    assert result.is_error is True
    assert json.loads(result.content)["error_code"] == "tool_execution_error"
    assert "sk-this-secret" not in result.content


def test_completed_tool_call_is_replayed_without_duplicate_side_effect(tmp_path):
    store = SessionStore(tmp_path / "replay.db")
    store.ensure_session("user-a", "window-1")
    registry = create_default_registry()
    tracer = TraceRecorder(tmp_path / "replay.jsonl")
    context = ToolContext("user-a", "window-1", "trace-1", 1, store)

    first = registry.execute(
        call_id="todo-stable-id",
        name="todo",
        arguments={"action": "add", "title": "只添加一次"},
        context=context,
        tracer=tracer,
    )
    replay = registry.execute(
        call_id="todo-stable-id",
        name="todo",
        arguments={"action": "add", "title": "只添加一次"},
        context=context,
        tracer=tracer,
    )

    assert replay == first
    assert len(store.list_todos("user-a", "window-1")) == 1
    events = tracer.read_events(user_id="user-a", trace_id="trace-1")
    assert events[-1]["event"] == "tool_replayed"


def test_large_tool_catalog_routes_schemas_and_keeps_catalog_search():
    registry = ToolRegistry()
    for index in range(15):
        registry.register(
            ToolDefinition(
                f"dummy_{index}",
                f"第 {index} 个无关工具",
                {"type": "object"},
                lambda arguments, context: arguments,
                routing_hints=(f"能力{index}",),
            )
        )
    registry.register(
        ToolDefinition(
            "weather_lookup",
            "查询真实天气",
            {"type": "object"},
            lambda arguments, context: arguments,
            routing_hints=("天气", "气温"),
        )
    )
    registry.register(
        ToolDefinition(
            "tool_search",
            "搜索工具目录",
            {"type": "object"},
            lambda arguments, context: arguments,
            always_available=True,
        )
    )

    selection = registry.select(
        "帮我查询上海天气", full_catalog_threshold=12, max_selected=4
    )

    assert selection.strategy == "routed"
    assert "weather_lookup" in selection.names
    assert "tool_search" in selection.names
    assert len(selection.names) <= 4
