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
