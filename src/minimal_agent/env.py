from __future__ import annotations

import json
import os
import re
from pathlib import Path


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_env(path: str | Path | None = None) -> Path | None:
    """Load a project ``.env`` without overriding existing environment values.

    By default only the current working directory is considered. This keeps an
    installed package from unexpectedly importing credentials from a parent
    directory. Invalid lines are ignored and secret values are never logged.
    """

    env_path = Path(path) if path is not None else Path.cwd() / ".env"
    if not env_path.is_file():
        return None

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()

        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            continue

        value = _parse_value(raw_value.strip())
        os.environ.setdefault(key, value)

    return env_path.resolve()


def _parse_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]

    # In unquoted values, treat whitespace followed by ``#`` as a comment.
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
