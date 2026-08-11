from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import StoredMessage


class SessionStore:
    """SQLite persistence keyed by (user_id, session_id)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialise(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    summary_through_message_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, session_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id, session_id)
                        REFERENCES sessions(user_id, session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(user_id, session_id, id);

                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    FOREIGN KEY (user_id, session_id)
                        REFERENCES sessions(user_id, session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_todos_session
                    ON todos(user_id, session_id, id);
                """
            )

    @staticmethod
    def _validate_key(value: str, label: str) -> None:
        if not value or len(value) > 128:
            raise ValueError(f"{label} must contain 1-128 characters")

    def ensure_session(self, user_id: str, session_id: str) -> None:
        self._validate_key(user_id, "user_id")
        self._validate_key(session_id, "session_id")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions(user_id, session_id) VALUES (?, ?)
                ON CONFLICT(user_id, session_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, session_id),
            )

    def append_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str | list[dict[str, Any]],
    ) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported message role: {role}")
        encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages(user_id, session_id, role, content_json)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, session_id, role, encoded),
            )
            conn.execute(
                """
                UPDATE sessions SET updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND session_id = ?
                """,
                (user_id, session_id),
            )
            return int(cursor.lastrowid)

    def get_messages(
        self,
        user_id: str,
        session_id: str,
        *,
        after_id: int = 0,
    ) -> list[StoredMessage]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content_json, created_at FROM messages
                WHERE user_id = ? AND session_id = ? AND id > ?
                ORDER BY id
                """,
                (user_id, session_id, after_id),
            ).fetchall()
        return [
            StoredMessage(
                id=row["id"],
                role=row["role"],
                content=json.loads(row["content_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_memory(self, user_id: str, session_id: str) -> tuple[str, int]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT summary, summary_through_message_id FROM sessions
                WHERE user_id = ? AND session_id = ?
                """,
                (user_id, session_id),
            ).fetchone()
        return (row["summary"], row["summary_through_message_id"]) if row else ("", 0)

    def update_memory(
        self,
        user_id: str,
        session_id: str,
        *,
        summary: str,
        through_message_id: int,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE sessions SET summary = ?, summary_through_message_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND session_id = ?
                """,
                (summary, through_message_id, user_id, session_id),
            )

    def add_todo(self, user_id: str, session_id: str, title: str) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("todo title cannot be empty")
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO todos(user_id, session_id, title)
                VALUES (?, ?, ?)
                """,
                (user_id, session_id, clean_title),
            )
            todo_id = int(cursor.lastrowid)
        return {"id": todo_id, "title": clean_title, "status": "pending"}

    def list_todos(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, title, status FROM todos
                WHERE user_id = ? AND session_id = ? ORDER BY id
                """,
                (user_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_todo(
        self, user_id: str, session_id: str, todo_id: int
    ) -> dict[str, Any]:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE todos SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND session_id = ?
                """,
                (todo_id, user_id, session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"todo {todo_id} does not exist in this session")
            row = conn.execute(
                "SELECT id, title, status FROM todos WHERE id = ?", (todo_id,)
            ).fetchone()
        return dict(row)

