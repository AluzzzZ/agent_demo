from __future__ import annotations

from typing import Any, Protocol

from ..models import LLMResponse


class LLMClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse: ...

    def summarize(self, *, previous_summary: str, transcript: str) -> str: ...

