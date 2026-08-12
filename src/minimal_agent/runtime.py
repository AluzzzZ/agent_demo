from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from .context import ContextManager, ContextWindow
from .errors import AgentErrorInfo, classify_model_error
from .llm.base import LLMClient
from .parser import parse_model_response
from .storage import SessionStore
from .tools.registry import ToolContext, ToolExecution, ToolRegistry
from .tracing import TraceRecorder


@dataclass(frozen=True)
class AgentResult:
    answer: str
    trace_id: str
    iterations: int
    exit_reason: str


class AgentRuntime:
    """Framework-free model → tool → model loop with bounded failure handling."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        store: SessionStore,
        tools: ToolRegistry,
        tracer: TraceRecorder,
        max_iterations: int = 8,
        max_tool_calls: int = 24,
        max_total_tokens: int | None = None,
        context_max_tokens: int = 6_000,
        context_window_tokens: int = 32_768,
        reserved_output_tokens: int = 2_048,
        context_max_characters: int | None = 24_000,
        keep_recent_turns: int = 4,
        keep_recent_messages: int | None = None,
        max_tool_result_tokens: int = 800,
        full_tool_catalog_threshold: int = 12,
        max_selected_tools: int = 8,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self.llm = llm
        self.store = store
        self.tools = tools
        self.tracer = tracer
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.max_total_tokens = max_total_tokens
        self.full_tool_catalog_threshold = full_tool_catalog_threshold
        self.max_selected_tools = max_selected_tools
        self.context = ContextManager(
            store=store,
            llm=llm,
            max_tokens=context_max_tokens,
            context_window_tokens=context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
            keep_recent_turns=keep_recent_turns,
            keep_recent_messages=keep_recent_messages,
            max_tool_result_tokens=max_tool_result_tokens,
            legacy_max_characters=context_max_characters,
        )

    def run(self, *, user_id: str, session_id: str, user_input: str) -> AgentResult:
        if not user_input.strip():
            raise ValueError("user_input cannot be empty")
        trace_id = uuid.uuid4().hex
        clean_input = user_input.strip()
        self.store.ensure_session(user_id, session_id)
        self.store.append_message(user_id, session_id, "user", clean_input)
        self.tracer.record(
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            event="request_started",
            max_iterations=self.max_iterations,
            max_tool_calls=self.max_tool_calls,
            max_total_tokens=self.max_total_tokens,
        )

        pinned_tools: set[str] = set()
        tool_call_count = 0
        total_model_tokens = 0

        for iteration in range(1, self.max_iterations + 1):
            selection = self.tools.select(
                clean_input,
                pinned_names=pinned_tools,
                full_catalog_threshold=self.full_tool_catalog_threshold,
                max_selected=self.max_selected_tools,
            )
            active_names = set(selection.names)
            tool_schemas = self.tools.schemas(active_names)
            self.tracer.record(
                trace_id=trace_id,
                user_id=user_id,
                session_id=session_id,
                event="tool_schema_selected",
                iteration=iteration,
                strategy=selection.strategy,
                active_tools=list(selection.names),
                active_tool_count=len(selection.names),
            )
            window = self.context.prepare(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                tracer=self.tracer,
                tools=tool_schemas,
            )
            if window.budget.exceeds_hard_limit:
                return self._finish_budget_exceeded(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    iteration=iteration,
                    reason="prompt_budget_exceeded",
                    answer=(
                        "当前请求即使压缩后仍超过模型上下文上限，请新建会话或缩短输入。"
                    ),
                    tool_calls=tool_call_count,
                    total_tokens=total_model_tokens,
                )

            response, failure, model_duration_ms = self._complete_with_reactive_compaction(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                iteration=iteration,
                window=window,
                tool_schemas=tool_schemas,
            )
            if failure is not None:
                return self._finish_failure(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    iteration=iteration,
                    failure=failure,
                )
            assert response is not None

            try:
                turn = parse_model_response(response)
            except Exception as exc:
                self.tracer.record(
                    trace_id=trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="model_response_invalid",
                    iteration=iteration,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                return self._finish_failure(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    iteration=iteration,
                    failure=AgentErrorInfo(
                        "model_invalid_response",
                        "模型返回了无法解析的响应，请重试。",
                        retryable=True,
                    ),
                )

            total_model_tokens += self._usage_total(response.usage)
            self.store.append_message(
                user_id, session_id, "assistant", turn.assistant_content
            )
            self.tracer.record(
                trace_id=trace_id,
                user_id=user_id,
                session_id=session_id,
                event="llm_finished",
                iteration=iteration,
                stop_reason=turn.stop_reason,
                model=response.model,
                usage=response.usage,
                duration_ms=model_duration_ms,
                accumulated_model_tokens=total_model_tokens,
                tool_call_count=len(turn.tool_calls),
                decision_summary=turn.reasoning_summary,
            )

            if self.max_total_tokens is not None and total_model_tokens > self.max_total_tokens:
                return self._finish_budget_exceeded(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    iteration=iteration,
                    reason="token_budget_exceeded",
                    answer="已达到本次请求的模型 Token 预算，执行已停止。",
                    tool_calls=tool_call_count,
                    total_tokens=total_model_tokens,
                )

            if not turn.tool_calls:
                answer = turn.final_answer or "模型没有返回可展示的文本，请重试。"
                self.tracer.record(
                    trace_id=trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="request_finished",
                    iteration=iteration,
                    exit_reason="final_answer",
                    tool_calls=tool_call_count,
                    total_model_tokens=total_model_tokens,
                )
                return AgentResult(answer, trace_id, iteration, "final_answer")

            results = []
            for call in turn.tool_calls:
                if tool_call_count >= self.max_tool_calls:
                    return self._finish_budget_exceeded(
                        user_id=user_id,
                        session_id=session_id,
                        trace_id=trace_id,
                        iteration=iteration,
                        reason="tool_budget_exceeded",
                        answer="已达到本次请求的工具调用上限，执行已停止。",
                        tool_calls=tool_call_count,
                        total_tokens=total_model_tokens,
                    )
                tool_call_count += 1
                execution = self.tools.execute(
                    call_id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                    context=ToolContext(
                        user_id=user_id,
                        session_id=session_id,
                        trace_id=trace_id,
                        iteration=iteration,
                        store=self.store,
                    ),
                    tracer=self.tracer,
                    allowed_names=active_names,
                )
                results.append(execution.as_tool_result())
                if call.name in active_names:
                    pinned_tools.add(call.name)
                if call.name == "tool_search" and not execution.is_error:
                    pinned_tools.update(self._tool_search_matches(execution))
            self.store.append_message(user_id, session_id, "user", results)

        answer = f"已达到最大执行轮次（{self.max_iterations}），为避免无限循环已停止。"
        self.store.append_message(
            user_id,
            session_id,
            "assistant",
            [{"type": "text", "text": answer}],
        )
        self.tracer.record(
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            event="request_finished",
            iteration=self.max_iterations,
            exit_reason="max_iterations",
            tool_calls=tool_call_count,
            total_model_tokens=total_model_tokens,
        )
        return AgentResult(answer, trace_id, self.max_iterations, "max_iterations")

    def _complete_with_reactive_compaction(
        self,
        *,
        user_id: str,
        session_id: str,
        trace_id: str,
        iteration: int,
        window: ContextWindow,
        tool_schemas: list[dict],
    ):
        current_window = window
        total_started = time.perf_counter()
        for attempt in range(2):
            started = time.perf_counter()
            self.tracer.record(
                trace_id=trace_id,
                user_id=user_id,
                session_id=session_id,
                event="llm_started",
                iteration=iteration,
                retry_attempt=attempt,
                context_characters=current_window.estimated_characters,
                projected_input_tokens=current_window.budget.projected_input_tokens,
                tool_schema_tokens=current_window.budget.tool_schema_tokens,
            )
            try:
                response = self.llm.complete(
                    system=current_window.system,
                    messages=current_window.messages,
                    tools=tool_schemas,
                )
                return (
                    response,
                    None,
                    round((time.perf_counter() - total_started) * 1000, 3),
                )
            except Exception as exc:
                failure = classify_model_error(exc)
                self.tracer.record(
                    trace_id=trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="llm_failed",
                    iteration=iteration,
                    retry_attempt=attempt,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    error_code=failure.code,
                    retryable=failure.retryable,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                if failure.code != "model_context_too_long" or attempt > 0:
                    return None, failure, round(
                        (time.perf_counter() - total_started) * 1000, 3
                    )
                self.tracer.record(
                    trace_id=trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="context_reactive_compaction",
                    iteration=iteration,
                )
                current_window = self.context.prepare(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    tracer=self.tracer,
                    tools=tool_schemas,
                    force_compaction=True,
                )
        return (
            None,
            AgentErrorInfo("model_error", "Agent 暂时无法完成请求。"),
            round((time.perf_counter() - total_started) * 1000, 3),
        )

    def _finish_failure(
        self,
        *,
        user_id: str,
        session_id: str,
        trace_id: str,
        iteration: int,
        failure: AgentErrorInfo,
    ) -> AgentResult:
        self.store.append_message(
            user_id,
            session_id,
            "assistant",
            [{"type": "text", "text": failure.public_message}],
        )
        self.tracer.record(
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            event="request_failed",
            iteration=iteration,
            error_code=failure.code,
            retryable=failure.retryable,
        )
        return AgentResult(failure.public_message, trace_id, iteration, failure.code)

    def _finish_budget_exceeded(
        self,
        *,
        user_id: str,
        session_id: str,
        trace_id: str,
        iteration: int,
        reason: str,
        answer: str,
        tool_calls: int,
        total_tokens: int,
    ) -> AgentResult:
        self.store.append_message(
            user_id,
            session_id,
            "assistant",
            [{"type": "text", "text": answer}],
        )
        self.tracer.record(
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            event="request_finished",
            iteration=iteration,
            exit_reason=reason,
            tool_calls=tool_calls,
            total_model_tokens=total_tokens,
        )
        return AgentResult(answer, trace_id, iteration, reason)

    @staticmethod
    def _tool_search_matches(execution: ToolExecution) -> set[str]:
        try:
            payload = json.loads(execution.content)
        except json.JSONDecodeError:
            return set()
        matches = payload.get("matches") if isinstance(payload, dict) else None
        if not isinstance(matches, list):
            return set()
        return {
            str(item["name"])
            for item in matches
            if isinstance(item, dict) and item.get("name")
        }

    @staticmethod
    def _usage_total(usage: dict[str, int]) -> int:
        if isinstance(usage.get("total_tokens"), int):
            return int(usage["total_tokens"])
        return sum(
            int(usage.get(key, 0))
            for key in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens")
        )
