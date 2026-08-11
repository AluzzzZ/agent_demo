from __future__ import annotations

import os
from typing import Any

from ..models import LLMResponse


class LLMConfigurationError(RuntimeError):
    pass


class AnthropicLLM:
    """Thin Anthropic SDK adapter; all agent orchestration stays in our runtime."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 2048,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not set; export it before starting the CLI"
            )
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - installation error
            raise LLMConfigurationError(
                "anthropic SDK is not installed; run: pip install -e ."
            ) from exc

        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.max_tokens = max_tokens
        self._client = Anthropic(
            api_key=key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        return self._normalise(response)

    def summarize(self, *, previous_summary: str, transcript: str) -> str:
        prompt = (
            "把下面的旧会话压缩成可供 Agent 后续使用的简明记忆。"
            "只保留：用户目标、关键事实、工具结论、约束、待完成事项。"
            "不要补充原文没有的信息，不要记录隐藏推理。\n\n"
            f"已有摘要：\n{previous_summary or '（无）'}\n\n"
            f"待压缩会话：\n{transcript}"
        )
        response = self._client.messages.create(
            model=self.model,
            max_tokens=800,
            system="你是会话记忆压缩器。输出纯文本摘要。",
            messages=[{"role": "user", "content": prompt}],
        )
        normalised = self._normalise(response)
        text = "\n".join(
            block.get("text", "")
            for block in normalised.content
            if block.get("type") == "text"
        ).strip()
        if not text:
            raise RuntimeError("summary model returned no text")
        return text

    @staticmethod
    def _normalise(response: Any) -> LLMResponse:
        blocks: list[dict[str, Any]] = []
        for block in response.content:
            if hasattr(block, "model_dump"):
                blocks.append(block.model_dump(exclude_none=True))
            elif isinstance(block, dict):
                blocks.append(dict(block))
            else:  # pragma: no cover - defensive SDK compatibility
                raise TypeError(f"unsupported Anthropic content block: {type(block)!r}")

        usage: dict[str, int] = {}
        if getattr(response, "usage", None) is not None:
            dumped = response.usage.model_dump(exclude_none=True)
            usage = {key: value for key, value in dumped.items() if isinstance(value, int)}
        return LLMResponse(
            content=blocks,
            stop_reason=getattr(response, "stop_reason", None),
            model=getattr(response, "model", None),
            usage=usage,
        )
