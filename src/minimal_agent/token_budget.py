from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenBudgetSnapshot:
    """Conservative prompt budget including tool schemas and output reserve."""

    system_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    reserved_output_tokens: int
    projected_input_tokens: int
    soft_input_limit_tokens: int
    hard_input_limit_tokens: int

    @property
    def exceeds_soft_limit(self) -> bool:
        return self.projected_input_tokens > self.soft_input_limit_tokens

    @property
    def exceeds_hard_limit(self) -> bool:
        return self.projected_input_tokens > self.hard_input_limit_tokens

    def as_trace_fields(self) -> dict[str, int | bool]:
        return {
            "system_tokens": self.system_tokens,
            "message_tokens": self.message_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "projected_input_tokens": self.projected_input_tokens,
            "soft_input_limit_tokens": self.soft_input_limit_tokens,
            "hard_input_limit_tokens": self.hard_input_limit_tokens,
            "exceeds_soft_limit": self.exceeds_soft_limit,
            "exceeds_hard_limit": self.exceeds_hard_limit,
        }


class TokenEstimator:
    """Dependency-free token estimate suitable for preflight budgeting.

    ASCII text is estimated at roughly four characters per token while CJK and
    other non-ASCII characters are counted more conservatively at one token each.
    Provider usage remains the source of truth after a model call.
    """

    _ASCII_RUN = re.compile(r"[\x00-\x7f]+")

    def estimate_text(self, value: str) -> int:
        if not value:
            return 0
        ascii_characters = sum(len(match.group(0)) for match in self._ASCII_RUN.finditer(value))
        non_ascii_characters = len(value) - ascii_characters
        return max(1, (ascii_characters + 3) // 4 + non_ascii_characters)

    def estimate_json(self, value: Any) -> int:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        return self.estimate_text(encoded)

    def snapshot(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        soft_input_limit_tokens: int,
        context_window_tokens: int,
        reserved_output_tokens: int,
    ) -> TokenBudgetSnapshot:
        hard_limit = max(context_window_tokens - reserved_output_tokens, 1)
        soft_limit = min(max(soft_input_limit_tokens, 1), hard_limit)
        system_tokens = self.estimate_text(system)
        message_tokens = self.estimate_json(messages)
        tool_schema_tokens = self.estimate_json(tools)
        projected = system_tokens + message_tokens + tool_schema_tokens
        return TokenBudgetSnapshot(
            system_tokens=system_tokens,
            message_tokens=message_tokens,
            tool_schema_tokens=tool_schema_tokens,
            reserved_output_tokens=reserved_output_tokens,
            projected_input_tokens=projected,
            soft_input_limit_tokens=soft_limit,
            hard_input_limit_tokens=hard_limit,
        )
