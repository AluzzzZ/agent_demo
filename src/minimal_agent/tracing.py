from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceRecorder:
    """Append-only JSONL trace recorder safe for multiple threads in one process."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        *,
        trace_id: str,
        user_id: str,
        session_id: str,
        event: str,
        **fields: Any,
    ) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "user_id": user_id,
            "session_id": session_id,
            "event": event,
            **fields,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)

