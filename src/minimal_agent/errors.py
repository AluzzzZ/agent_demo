from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentErrorInfo:
    code: str
    public_message: str
    retryable: bool = False


class AgentRuntimeError(RuntimeError):
    """A runtime error with a safe user-facing representation."""

    def __init__(self, info: AgentErrorInfo) -> None:
        super().__init__(info.public_message)
        self.info = info


_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "context window",
    "maximum context length",
    "prompt is too long",
    "prompt too long",
    "input is too long",
)


def classify_model_error(exc: Exception) -> AgentErrorInfo:
    """Map provider-specific failures to stable runtime error codes."""

    name = type(exc).__name__.lower()
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if any(marker in message for marker in _CONTEXT_MARKERS):
        return AgentErrorInfo(
            "model_context_too_long",
            "会话上下文过长，压缩后仍无法完成请求，请新建会话或缩短输入。",
        )
    if status_code == 429 or "ratelimit" in name or "rate limit" in message:
        return AgentErrorInfo(
            "model_rate_limited",
            "模型服务当前请求较多，请稍后重试。",
            retryable=True,
        )
    if status_code in {500, 502, 503, 504} or "internalserver" in name:
        return AgentErrorInfo(
            "model_upstream_error",
            "模型服务暂时不可用，请稍后重试。",
            retryable=True,
        )
    if "timeout" in name or "timed out" in message or "timeout" in message:
        return AgentErrorInfo(
            "model_timeout",
            "模型响应超时，请稍后重试。",
            retryable=True,
        )
    if "connection" in name or "connection" in message:
        return AgentErrorInfo(
            "model_connection_error",
            "暂时无法连接模型服务，请检查网络后重试。",
            retryable=True,
        )
    return AgentErrorInfo(
        "model_error",
        "Agent 暂时无法完成请求，请在 Trace 中查看失败阶段。",
    )


class AgentToolError(RuntimeError):
    """A safe, structured tool error that may be returned to the model."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


class ToolTimeoutError(AgentToolError):
    def __init__(self, message: str = "上游免费 API 请求超时，请稍后重试。") -> None:
        super().__init__("tool_timeout", message, retryable=True)


class ToolRateLimitError(AgentToolError):
    def __init__(self, message: str = "上游免费 API 已达到限流，请稍后重试。") -> None:
        super().__init__("tool_rate_limited", message, retryable=True)


class ToolUpstreamError(AgentToolError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__("tool_upstream_error", message, retryable=retryable)
