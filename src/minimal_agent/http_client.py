from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest

from .errors import ToolRateLimitError, ToolTimeoutError, ToolUpstreamError


@dataclass
class JsonHttpClient:
    """Small JSON GET client with bounded retries and response-size checks."""

    timeout_seconds: float = 12.0
    max_response_bytes: int = 1_000_000
    max_retries: int = 2
    user_agent: str = "minimal-agent/0.2 (+local educational project)"
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)

    def get(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                request = urlrequest.Request(
                    url,
                    headers={"Accept": "application/json", "User-Agent": self.user_agent},
                )
                with urlrequest.urlopen(request, timeout=self.timeout_seconds) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_response_bytes:
                        raise ToolUpstreamError("上游 API 响应过大，已拒绝处理。", retryable=False)
                    raw = response.read(self.max_response_bytes + 1)
                    if len(raw) > self.max_response_bytes:
                        raise ToolUpstreamError("上游 API 响应过大，已拒绝处理。", retryable=False)
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ToolUpstreamError("上游 API 返回了无效的数据格式。", retryable=False)
                    return payload
            except urlerror.HTTPError as exc:
                last_error = exc
                if exc.code == 429:
                    if attempt < self.max_retries:
                        self._backoff(attempt)
                        continue
                    raise ToolRateLimitError() from exc
                if exc.code in {500, 502, 503, 504} and attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise ToolUpstreamError(
                    f"上游免费 API 返回 HTTP {exc.code}。",
                    retryable=exc.code >= 500,
                ) from exc
            except (TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise ToolTimeoutError() from exc
            except urlerror.URLError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    raise ToolTimeoutError() from exc
                raise ToolUpstreamError("无法连接上游免费 API。") from exc
            except json.JSONDecodeError as exc:
                raise ToolUpstreamError("上游 API 返回了无效 JSON。", retryable=False) from exc
        raise ToolUpstreamError(f"上游 API 请求失败：{type(last_error).__name__}")

    def _backoff(self, attempt: int) -> None:
        self.sleeper(0.2 * (2**attempt))
