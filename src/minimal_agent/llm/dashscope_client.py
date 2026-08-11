from __future__ import annotations

import json
import os
from typing import Any

from ..models import LLMResponse


class DashScopeConfigurationError(RuntimeError):
    pass


class DashScopeLLM:
    """OpenAI-compatible adapter for Alibaba Cloud Model Studio.

    The runtime stores one provider-neutral block format. This adapter translates
    it to OpenAI chat messages and translates function calls back to tool_use
    blocks, keeping provider-specific details outside the Agent Runtime.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = 2048,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        enable_thinking: bool = False,
    ) -> None:
        key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY")
        if not key:
            raise DashScopeConfigurationError(
                "DASHSCOPE_API_KEY is not set; export it before starting the CLI"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - installation error
            raise DashScopeConfigurationError(
                "openai SDK is not installed; run: pip install -e ."
            ) from exc

        configured_url = (
            base_url
            or os.getenv("DASHSCOPE_BASE_URL")
            or os.getenv("BASE_URL")
            or "https://dashscope.aliyuncs.com/api/v1"
        )
        self.base_url = self._compatible_base_url(configured_url)
        self.model = (
            model
            or os.getenv("DASHSCOPE_MODEL")
            or os.getenv("MODEL")
            or "deepseek-v4-pro"
        )
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self._client = OpenAI(
            api_key=key,
            base_url=self.base_url,
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
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=self._to_openai_messages(system, messages),
            tools=self._to_openai_tools(tools),
            extra_body={"enable_thinking": self.enable_thinking},
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
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=800,
            messages=[
                {"role": "system", "content": "你是会话记忆压缩器。输出纯文本摘要。"},
                {"role": "user", "content": prompt},
            ],
            extra_body={"enable_thinking": False},
        )
        text = response.choices[0].message.content
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("summary model returned no text")
        return text.strip()

    @staticmethod
    def _compatible_base_url(base_url: str) -> str:
        """Accept either the DashScope-native or OpenAI-compatible base URL."""
        clean = base_url.strip().rstrip("/")
        if not clean.startswith(("https://", "http://")):
            raise DashScopeConfigurationError("BASE_URL must start with http:// or https://")
        if clean.endswith("/api/v1"):
            return clean[: -len("/api/v1")] + "/compatible-mode/v1"
        return clean

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    @classmethod
    def _to_openai_messages(
        cls, system: str, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            role = message["role"]
            content = message["content"]
            if isinstance(content, str):
                converted.append({"role": role, "content": content})
                continue
            if role == "assistant":
                texts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                tool_calls = [
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"], ensure_ascii=False),
                        },
                    }
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "tool_use"
                ]
                assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n\n".join(text for text in texts if text) or None,
                }
                if tool_calls:
                    assistant["tool_calls"] = tool_calls
                converted.append(assistant)
                continue
            if role == "user" and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            ):
                for block in content:
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": str(block.get("content", "")),
                        }
                    )
                continue
            text = "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
            converted.append({"role": role, "content": text})
        return converted

    @staticmethod
    def _normalise(response: Any) -> LLMResponse:
        if not response.choices:
            raise RuntimeError("DashScope returned no choices")
        choice = response.choices[0]
        message = choice.message
        blocks: list[dict[str, Any]] = []
        if isinstance(message.content, str) and message.content.strip():
            blocks.append({"type": "text", "text": message.content})
        for call in message.tool_calls or []:
            raw_arguments = call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    arguments = {"_invalid_arguments": raw_arguments}
            except json.JSONDecodeError:
                arguments = {"_invalid_arguments": raw_arguments}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.function.name,
                    "input": arguments,
                }
            )

        usage: dict[str, int] = {}
        if getattr(response, "usage", None) is not None:
            dumped = response.usage.model_dump(exclude_none=True)
            usage = {key: value for key, value in dumped.items() if isinstance(value, int)}
        return LLMResponse(
            content=blocks,
            stop_reason=getattr(choice, "finish_reason", None),
            model=getattr(response, "model", None),
            usage=usage,
        )
