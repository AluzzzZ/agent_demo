from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from .context import ContextManager
from .llm.base import LLMClient
from .parser import parse_model_response
from .storage import SessionStore
from .tools.registry import ToolContext, ToolRegistry
from .tracing import TraceRecorder


@dataclass(frozen=True)
class AgentResult:
    answer: str
    trace_id: str
    iterations: int
    exit_reason: str


class AgentRuntime:
    """The complete framework-free model → tool → model loop."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        store: SessionStore,
        tools: ToolRegistry,
        tracer: TraceRecorder,
        max_iterations: int = 8,
        context_max_characters: int = 24_000,
        keep_recent_messages: int = 8,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self.llm = llm
        self.store = store
        self.tools = tools
        self.tracer = tracer
        self.max_iterations = max_iterations
        self.context = ContextManager(
            store=store,
            llm=llm,
            max_characters=context_max_characters,
            keep_recent_messages=keep_recent_messages,
        )

    def run(self, *, user_id: str, session_id: str, user_input: str) -> AgentResult:
        if not user_input.strip():
            raise ValueError("user_input cannot be empty")
        trace_id = uuid.uuid4().hex
        self.store.ensure_session(user_id, session_id)
        self.store.append_message(user_id, session_id, "user", user_input.strip())
        self.tracer.record(
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            event="request_started",
            max_iterations=self.max_iterations,
        )

        for iteration in range(1, self.max_iterations + 1):
            window = self.context.prepare(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                tracer=self.tracer,
            )
            started = time.perf_counter()
            self.tracer.record(
                trace_id=trace_id,
                user_id=user_id,
                session_id=session_id,
                event="llm_started",
                iteration=iteration,
                context_characters=window.estimated_characters,
            )
            try:
                response = self.llm.complete(
                    system=window.system,
                    messages=window.messages,
                    tools=self.tools.schemas(),
                )
                turn = parse_model_response(response)
            except Exception as exc:
                self.tracer.record(
                    trace_id=trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="llm_failed",
                    iteration=iteration,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                self.tracer.record(
                    trace_id=trace_id,
                    user_id=user_id,
                    session_id=session_id,
                    event="request_failed",
                    iteration=iteration,
                    error_type=type(exc).__name__,
                )
                raise

            self.store.append_message(
                user_id, session_id, "assistant", turn.assistant_content
            )
            self.tracer.record(
                trace_id=trace_id,
                user_id=user_id,
                session_id=session_id,
                event="llm_finished",
                iteration=iteration,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                stop_reason=turn.stop_reason,
                model=response.model,
                usage=response.usage,
                tool_call_count=len(turn.tool_calls),
                decision_summary=turn.reasoning_summary,
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
                )
                return AgentResult(answer, trace_id, iteration, "final_answer")

            results = []
            for call in turn.tool_calls:
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
                )
                results.append(execution.as_tool_result())
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
        )
        return AgentResult(answer, trace_id, self.max_iterations, "max_iterations")
