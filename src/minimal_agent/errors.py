from __future__ import annotations


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
