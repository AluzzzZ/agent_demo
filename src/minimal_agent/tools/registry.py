from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator

from ..storage import SessionStore
from ..tracing import TraceRecorder


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    session_id: str
    trace_id: str
    iteration: int
    store: SessionStore


ToolHandler = Callable[[dict[str, Any], ToolContext], Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def as_llm_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class ToolExecution:
    call_id: str
    name: str
    content: str
    is_error: bool

    def as_tool_result(self) -> dict[str, Any]:
        result = {
            "type": "tool_result",
            "tool_use_id": self.call_id,
            "content": self.content,
        }
        if self.is_error:
            result["is_error"] = True
        return result


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", definition.name):
            raise ValueError(
                "tool name must be 1-64 ASCII letters, numbers, underscores, or hyphens"
            )
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        Draft202012Validator.check_schema(definition.input_schema)
        self._tools[definition.name] = definition

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.as_llm_schema() for tool in self._tools.values()]

    def execute(
        self,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        tracer: TraceRecorder,
    ) -> ToolExecution:
        started = time.perf_counter()
        tracer.record(
            trace_id=context.trace_id,
            user_id=context.user_id,
            session_id=context.session_id,
            event="tool_started",
            iteration=context.iteration,
            tool=name,
            call_id=call_id,
            arguments=arguments,
        )
        try:
            definition = self._tools.get(name)
            if definition is None:
                raise ValueError(f"unknown tool: {name}")
            errors = sorted(
                Draft202012Validator(definition.input_schema).iter_errors(arguments),
                key=lambda error: list(error.path),
            )
            if errors:
                details = "; ".join(error.message for error in errors)
                raise ValueError(f"invalid arguments: {details}")
            output = definition.handler(arguments, context)
            content = json.dumps(output, ensure_ascii=False, default=str)
            execution = ToolExecution(call_id, name, content, False)
            status = "success"
        except Exception as exc:  # tools must return failures to the model, not crash the loop
            content = json.dumps(
                {"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False
            )
            execution = ToolExecution(call_id, name, content, True)
            status = "error"

        tracer.record(
            trace_id=context.trace_id,
            user_id=context.user_id,
            session_id=context.session_id,
            event="tool_finished",
            iteration=context.iteration,
            tool=name,
            call_id=call_id,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            status=status,
            is_error=execution.is_error,
        )
        return execution
