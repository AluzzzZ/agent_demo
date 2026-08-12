from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator

from ..errors import AgentToolError
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
    routing_hints: tuple[str, ...] = ()
    always_available: bool = False

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

    def as_storage_payload(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "content": self.content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_storage_payload(cls, payload: dict[str, Any]) -> "ToolExecution":
        return cls(
            call_id=str(payload["call_id"]),
            name=str(payload["name"]),
            content=str(payload["content"]),
            is_error=bool(payload["is_error"]),
        )


@dataclass(frozen=True)
class ToolSelection:
    names: tuple[str, ...]
    strategy: str
    scores: dict[str, int]


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

    def schemas(self, names: set[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        if names is None:
            return [tool.as_llm_schema() for tool in self._tools.values()]
        selected = set(names)
        return [
            tool.as_llm_schema()
            for tool in self._tools.values()
            if tool.name in selected
        ]

    def search_catalog(self, query: str, *, limit: int = 8) -> list[dict[str, str]]:
        terms = self._query_terms(query)
        ranked: list[tuple[int, str, str]] = []
        for tool in self._tools.values():
            if tool.name == "tool_search":
                continue
            score = self._score_tool(tool, query.casefold(), terms)
            if score:
                ranked.append((score, tool.name, tool.description))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"name": name, "description": description}
            for _, name, description in ranked[: max(1, min(limit, 20))]
        ]

    def select(
        self,
        user_input: str,
        *,
        pinned_names: set[str] | None = None,
        full_catalog_threshold: int = 12,
        max_selected: int = 8,
    ) -> ToolSelection:
        """Return all small catalogs, otherwise route to the most relevant schemas."""

        if len(self._tools) <= full_catalog_threshold:
            return ToolSelection(tuple(self._tools), "full_catalog", {})

        pinned = set(pinned_names or ())
        query = user_input.casefold()
        terms = self._query_terms(user_input)
        scores = {
            tool.name: self._score_tool(tool, query, terms)
            for tool in self._tools.values()
        }
        required = {
            tool.name for tool in self._tools.values() if tool.always_available
        } | (pinned & set(self._tools))
        ranked = sorted(self._tools, key=lambda name: (-scores.get(name, 0), name))
        selected = list(required)
        for name in ranked:
            if name in selected or scores.get(name, 0) <= 0:
                continue
            selected.append(name)
            if len(selected) >= max(max_selected, len(required)):
                break
        if not selected:
            selected = ranked[: max(1, min(max_selected, len(ranked)))]
        selected_set = set(selected)
        ordered = tuple(name for name in self._tools if name in selected_set)
        return ToolSelection(ordered, "routed", scores)

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        return {
            term.casefold()
            for term in re.findall(r"[A-Za-z0-9_+-]{2,}|[\u3400-\u9fff]{1,4}", query)
        }

    @staticmethod
    def _score_tool(tool: ToolDefinition, query: str, terms: set[str]) -> int:
        score = 0
        for candidate in (tool.name, *tool.routing_hints):
            clean = candidate.casefold().strip()
            if clean and clean in query:
                score += 8
        haystack = f"{tool.name} {tool.description} {' '.join(tool.routing_hints)}".casefold()
        score += sum(1 for term in terms if term in haystack)
        return score

    def execute(
        self,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        tracer: TraceRecorder,
        allowed_names: set[str] | None = None,
    ) -> ToolExecution:
        arguments_json = json.dumps(
            arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        arguments_hash = hashlib.sha256(arguments_json.encode("utf-8")).hexdigest()
        existing = context.store.get_tool_execution(
            context.user_id, context.session_id, call_id
        )
        if existing is not None:
            if (
                existing["tool_name"] == name
                and existing["arguments_hash"] == arguments_hash
            ):
                execution = ToolExecution.from_storage_payload(existing["result"])
                tracer.record(
                    trace_id=context.trace_id,
                    user_id=context.user_id,
                    session_id=context.session_id,
                    event="tool_replayed",
                    iteration=context.iteration,
                    tool=name,
                    call_id=call_id,
                    status="error" if execution.is_error else "success",
                )
                return execution
            conflict = {
                "error_code": "tool_call_id_conflict",
                "message": "同一个 tool call id 被用于不同的工具或参数，已拒绝重复执行。",
                "retryable": False,
            }
            return ToolExecution(call_id, name, json.dumps(conflict, ensure_ascii=False), True)

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
        definition = self._tools.get(name)
        error_payload: dict[str, Any] | None = None
        internal_error_type: str | None = None
        internal_error_message: str | None = None
        if definition is None:
            error_payload = {
                "error_code": "tool_unknown",
                "message": f"unknown tool: {name}",
                "retryable": False,
            }
        elif allowed_names is not None and name not in allowed_names:
            error_payload = {
                "error_code": "tool_not_active",
                "message": "该工具本轮未激活，请先调用 tool_search 或调整请求。",
                "retryable": False,
            }
        else:
            errors = sorted(
                Draft202012Validator(definition.input_schema).iter_errors(arguments),
                key=lambda error: list(error.path),
            )
            if errors:
                details = "; ".join(error.message for error in errors)
                error_payload = {
                    "error_code": "tool_invalid_arguments",
                    "message": f"invalid arguments: {details}",
                    "retryable": False,
                }

        if error_payload is None and definition is not None:
            try:
                output = definition.handler(arguments, context)
                content = json.dumps(output, ensure_ascii=False, default=str)
                execution = ToolExecution(call_id, name, content, False)
                status = "success"
            except AgentToolError as exc:
                error_payload = {
                    "error_code": exc.code,
                    "message": exc.safe_message,
                    "retryable": exc.retryable,
                }
            except Exception as exc:
                internal_error_type = type(exc).__name__
                internal_error_message = str(exc)
                error_payload = {
                    "error_code": "tool_execution_error",
                    "message": "工具执行失败，请稍后重试。",
                    "retryable": False,
                }

        if error_payload is not None:
            content = json.dumps(error_payload, ensure_ascii=False)
            execution = ToolExecution(call_id, name, content, True)
            status = "error"

        context.store.save_tool_execution(
            context.user_id,
            context.session_id,
            call_id,
            tool_name=name,
            arguments_hash=arguments_hash,
            result=execution.as_storage_payload(),
        )

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
            error_code=(error_payload["error_code"] if error_payload else None),
            internal_error_type=internal_error_type,
            internal_error_message=internal_error_message,
        )
        return execution
