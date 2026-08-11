from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm.base import LLMClient
from .models import StoredMessage
from .storage import SessionStore
from .tracing import TraceRecorder


BASE_SYSTEM_PROMPT = """你是一个最小可用的工具型 Agent。
根据用户目标自主决定直接回答或调用工具。需要外部结果时调用工具；拿到结果后继续判断，直到能给出最终答案。
不要编造工具执行结果。工具失败时可以修正参数后重试，或清楚解释失败。
待办和会话记忆仅属于当前 user_id/session_id。回答简洁、准确。"""


@dataclass(frozen=True)
class ContextWindow:
    system: str
    messages: list[dict[str, Any]]
    estimated_characters: int


class ContextManager:
    def __init__(
        self,
        *,
        store: SessionStore,
        llm: LLMClient,
        max_characters: int = 24_000,
        keep_recent_messages: int = 8,
    ) -> None:
        if keep_recent_messages < 2:
            raise ValueError("keep_recent_messages must be at least 2")
        self.store = store
        self.llm = llm
        self.max_characters = max_characters
        self.keep_recent_messages = keep_recent_messages

    def prepare(
        self,
        *,
        user_id: str,
        session_id: str,
        trace_id: str,
        tracer: TraceRecorder,
    ) -> ContextWindow:
        summary, through_id = self.store.get_memory(user_id, session_id)
        messages = self.store.get_messages(user_id, session_id, after_id=through_id)
        size = self._estimate(summary, messages)
        if size > self.max_characters and len(messages) > self.keep_recent_messages:
            summary, through_id = self._compact(
                user_id=user_id,
                session_id=session_id,
                previous_summary=summary,
                messages=messages,
                trace_id=trace_id,
                tracer=tracer,
            )
            messages = self.store.get_messages(user_id, session_id, after_id=through_id)
            size = self._estimate(summary, messages)

        todos = self.store.list_todos(user_id, session_id)
        memory_sections = []
        if summary:
            memory_sections.append(f"<session_summary>\n{summary}\n</session_summary>")
        if todos:
            memory_sections.append(
                "<session_todos>\n"
                + json.dumps(todos, ensure_ascii=False)
                + "\n</session_todos>"
            )
        system = BASE_SYSTEM_PROMPT
        if memory_sections:
            system += "\n\n以下记忆仅用于当前会话：\n" + "\n".join(memory_sections)
        return ContextWindow(
            system=system,
            messages=[{"role": item.role, "content": item.content} for item in messages],
            estimated_characters=size,
        )

    def _compact(
        self,
        *,
        user_id: str,
        session_id: str,
        previous_summary: str,
        messages: list[StoredMessage],
        trace_id: str,
        tracer: TraceRecorder,
    ) -> tuple[str, int]:
        cut = len(messages) - self.keep_recent_messages
        cut = self._safe_cut(messages, cut)
        if cut <= 0:
            return previous_summary, messages[0].id - 1

        old_messages = messages[:cut]
        transcript = "\n".join(
            f"[{message.role}] {json.dumps(message.content, ensure_ascii=False)}"
            for message in old_messages
        )
        try:
            summary = self.llm.summarize(
                previous_summary=previous_summary, transcript=transcript
            )
            method = "llm"
        except Exception as exc:
            # A bounded local fallback keeps the main request usable during API trouble.
            summary = self._fallback_summary(previous_summary, transcript)
            method = "fallback"
            tracer.record(
                trace_id=trace_id,
                user_id=user_id,
                session_id=session_id,
                event="context_compaction_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        through_id = old_messages[-1].id
        self.store.update_memory(
            user_id,
            session_id,
            summary=summary,
            through_message_id=through_id,
        )
        tracer.record(
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            event="context_compacted",
            method=method,
            messages_compacted=len(old_messages),
            through_message_id=through_id,
        )
        return summary, through_id

    @staticmethod
    def _safe_cut(messages: list[StoredMessage], proposed: int) -> int:
        """Keep complete turns and never split tool_use from tool_result."""
        cut = max(0, min(proposed, len(messages)))
        while cut > 0 and cut < len(messages):
            first_kept = messages[cut]
            if first_kept.role == "assistant" or _contains_type(
                first_kept.content, "tool_result"
            ):
                cut -= 1
                continue
            break
        if cut > 0 and _contains_type(messages[cut - 1].content, "tool_use"):
            cut -= 1
        return max(cut, 0)

    @staticmethod
    def _estimate(summary: str, messages: list[StoredMessage]) -> int:
        return len(summary) + sum(
            len(message.role)
            + len(json.dumps(message.content, ensure_ascii=False, default=str))
            for message in messages
        )

    @staticmethod
    def _fallback_summary(previous_summary: str, transcript: str) -> str:
        combined = "\n".join(part for part in (previous_summary, transcript) if part)
        if len(combined) <= 4000:
            return combined
        return "[较早记忆已截断]\n" + combined[-4000:]


def _contains_type(content: Any, block_type: str) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == block_type for block in content
    )
