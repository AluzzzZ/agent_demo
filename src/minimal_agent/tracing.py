from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact


class TraceRecorder:
    """Append-only JSONL trace recorder safe for multiple threads in one process."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequences: dict[str, int] = {}

    def record(
        self,
        *,
        trace_id: str,
        user_id: str,
        session_id: str,
        event: str,
        **fields: Any,
    ) -> None:
        with self._lock:
            sequence_no = self._sequences.get(trace_id, 0) + 1
            self._sequences[trace_id] = sequence_no
            payload = redact(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "sequence_no": sequence_no,
                    "event": event,
                    **fields,
                }
            )
            line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def read_events(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("user_id") != user_id:
                continue
            if session_id is not None and payload.get("session_id") != session_id:
                continue
            if trace_id is not None and payload.get("trace_id") != trace_id:
                continue
            events.append(payload)
        return events

    def list_traces(
        self, *, user_id: str, session_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for event in self.read_events(user_id=user_id, session_id=session_id):
            current_trace_id = str(event.get("trace_id", ""))
            if not current_trace_id:
                continue
            summary = grouped.setdefault(
                current_trace_id,
                {
                    "trace_id": current_trace_id,
                    "started_at": event.get("timestamp"),
                    "finished_at": event.get("timestamp"),
                    "status": "running",
                    "iterations": 0,
                    "tools": [],
                },
            )
            summary["finished_at"] = event.get("timestamp")
            if isinstance(event.get("iteration"), int):
                summary["iterations"] = max(summary["iterations"], event["iteration"])
            if event.get("event") == "tool_started" and event.get("tool"):
                summary["tools"].append(event["tool"])
            if event.get("event") == "request_finished":
                summary["status"] = event.get("exit_reason", "finished")
            if event.get("event") == "request_failed":
                summary["status"] = "failed"
        return sorted(
            grouped.values(), key=lambda item: str(item["started_at"]), reverse=True
        )[: max(1, min(limit, 100))]

    def delete_session_events(self, *, user_id: str, session_id: str) -> int:
        if not self.path.exists():
            return 0
        with self._lock:
            kept_lines: list[str] = []
            removed_trace_ids: set[str] = set()
            removed_count = 0
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                if (
                    payload.get("user_id") == user_id
                    and payload.get("session_id") == session_id
                ):
                    removed_count += 1
                    if payload.get("trace_id"):
                        removed_trace_ids.add(str(payload["trace_id"]))
                    continue
                kept_lines.append(line)
            if removed_count:
                temporary = self.path.with_name(f".{self.path.name}.tmp")
                content = "\n".join(kept_lines)
                temporary.write_text(content + ("\n" if content else ""), encoding="utf-8")
                temporary.replace(self.path)
                for trace_id in removed_trace_ids:
                    self._sequences.pop(trace_id, None)
            return removed_count
