from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import sqlite3
import time
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
                    title TEXT NOT NULL DEFAULT '新对话',
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

                CREATE TABLE IF NOT EXISTS tool_executions (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, session_id, call_id),
                    FOREIGN KEY (user_id, session_id)
                        REFERENCES sessions(user_id, session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tool_executions_session
                    ON tool_executions(user_id, session_id, created_at);

                CREATE TABLE IF NOT EXISTS app_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at_epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES app_users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_user
                    ON auth_tokens(user_id, expires_at_epoch);
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "title" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT '新对话'"
                )
            conn.execute("PRAGMA optimize")

    @staticmethod
    def _validate_key(value: str, label: str) -> None:
        if not value or len(value) > 128:
            raise ValueError(f"{label} must contain 1-128 characters")

    def ensure_session(
        self, user_id: str, session_id: str, *, title: str = "新对话"
    ) -> None:
        self._validate_key(user_id, "user_id")
        self._validate_key(session_id, "session_id")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions(user_id, session_id, title) VALUES (?, ?, ?)
                ON CONFLICT(user_id, session_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, session_id, title.strip()[:80] or "新对话"),
            )

    def session_exists(self, user_id: str, session_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
        return row is not None

    def delete_session(self, user_id: str, session_id: str) -> bool:
        self._validate_key(user_id, "user_id")
        self._validate_key(session_id, "session_id")
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
        return cursor.rowcount > 0

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at,
                       COUNT(m.id) AS message_count
                FROM sessions AS s
                LEFT JOIN messages AS m
                  ON m.user_id = s.user_id AND m.session_id = s.session_id
                WHERE s.user_id = ?
                GROUP BY s.user_id, s.session_id
                ORDER BY s.updated_at DESC, s.session_id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_session_title(self, user_id: str, session_id: str, title: str) -> None:
        clean = " ".join(title.split())[:80] or "新对话"
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE sessions SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND session_id = ?
                """,
                (clean, user_id, session_id),
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

    def get_tool_execution(
        self, user_id: str, session_id: str, call_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT tool_name, arguments_hash, result_json
                FROM tool_executions
                WHERE user_id = ? AND session_id = ? AND call_id = ?
                """,
                (user_id, session_id, call_id),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        return {
            "tool_name": row["tool_name"],
            "arguments_hash": row["arguments_hash"],
            "result": result,
        }

    def save_tool_execution(
        self,
        user_id: str,
        session_id: str,
        call_id: str,
        *,
        tool_name: str,
        arguments_hash: str,
        result: dict[str, Any],
    ) -> bool:
        """Persist a completed call; return False when another call already won."""

        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO tool_executions(
                    user_id, session_id, call_id, tool_name, arguments_hash, result_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    call_id,
                    tool_name,
                    arguments_hash,
                    encoded,
                ),
            )
        return cursor.rowcount > 0

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

    def seed_user(
        self,
        *,
        user_id: str,
        username: str,
        password: str,
        display_name: str,
    ) -> None:
        self._validate_key(user_id, "user_id")
        clean_username = username.strip()
        if not clean_username or len(clean_username) > 64:
            raise ValueError("username must contain 1-64 characters")
        if len(password) < 6 or len(password) > 256:
            raise ValueError("password must contain 6-256 characters")
        clean_display_name = display_name.strip()[:80] or clean_username
        salt = secrets.token_bytes(16)
        password_hash = _derive_password_hash(password, salt)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO app_users(
                    user_id, username, display_name, password_salt, password_hash
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    password_salt = excluded.password_salt,
                    password_hash = excluded.password_hash,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, clean_username, clean_display_name, salt.hex(), password_hash),
            )

    def authenticate_user(self, username: str, password: str) -> dict[str, str] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT user_id, username, display_name, password_salt, password_hash
                FROM app_users WHERE username = ?
                """,
                (username.strip(),),
            ).fetchone()
        if row is None:
            return None
        actual = _derive_password_hash(password, bytes.fromhex(row["password_salt"]))
        if not hmac.compare_digest(actual, row["password_hash"]):
            return None
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "display_name": row["display_name"],
        }

    def create_auth_token(self, user_id: str, *, ttl_seconds: int = 86_400) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = _token_hash(token)
        expires_at = int(time.time()) + max(ttl_seconds, 60)
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM auth_tokens WHERE expires_at_epoch <= ?", (int(time.time()),)
            )
            conn.execute(
                """
                INSERT INTO auth_tokens(token_hash, user_id, expires_at_epoch)
                VALUES (?, ?, ?)
                """,
                (token_hash, user_id, expires_at),
            )
        return token

    def resolve_auth_token(self, token: str) -> dict[str, str] | None:
        if not token:
            return None
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT u.user_id, u.username, u.display_name
                FROM auth_tokens AS t
                JOIN app_users AS u ON u.user_id = t.user_id
                WHERE t.token_hash = ? AND t.expires_at_epoch > ?
                """,
                (_token_hash(token), int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def revoke_auth_token(self, token: str) -> None:
        if not token:
            return
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM auth_tokens WHERE token_hash = ?", (_token_hash(token),)
            )


def _derive_password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 200_000
    ).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
