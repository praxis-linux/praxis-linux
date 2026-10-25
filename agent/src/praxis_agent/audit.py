"""Audit log of every model call and every tool call the agent makes.

A row is written before the call is made and completed afterwards. If the row
cannot be written, the call must not be made — see `begin` and `begin_tool`.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_calls (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    model        TEXT NOT NULL,
    destination  TEXT NOT NULL CHECK (destination IN ('local', 'cloud')),
    purpose      TEXT NOT NULL,
    prompt_chars INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'started',
    requests     INTEGER,
    duration_ms  INTEGER,
    error        TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    tool         TEXT NOT NULL,
    arguments    TEXT NOT NULL,
    -- 'model' when the model asked for it, 'user' when the user clicked.
    asked_by     TEXT NOT NULL CHECK (asked_by IN ('model', 'user')),
    status       TEXT NOT NULL DEFAULT 'started',
    error        TEXT
);
"""


def default_path() -> Path:
    return Path(os.environ.get("PRAXIS_AUDIT_DB") or Path.home() / ".praxis" / "audit.db")


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def begin(
        self, *, session_id: str, model: str, destination: str, purpose: str, prompt_chars: int
    ) -> int:
        """Record a model call that is about to be made. Raises if it cannot be recorded."""
        return self._insert(
            "INSERT INTO model_calls"
            " (started_at, session_id, model, destination, purpose, prompt_chars)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), session_id, model, destination, purpose, prompt_chars),
        )

    def finish(
        self,
        call_id: int,
        *,
        status: str,
        duration_ms: int,
        requests: int | None = None,
        error: str | None = None,
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE model_calls SET status = ?, duration_ms = ?, requests = ?, error = ?"
                " WHERE id = ?",
                (status, duration_ms, requests, error, call_id),
            )

    def begin_tool(
        self, *, session_id: str, tool: str, arguments: dict[str, Any], asked_by: str
    ) -> int:
        """Record a tool call that is about to be made. Raises if it cannot be recorded."""
        return self._insert(
            "INSERT INTO tool_calls (started_at, session_id, tool, arguments, asked_by)"
            " VALUES (?, ?, ?, ?, ?)",
            (_now(), session_id, tool, json.dumps(arguments, ensure_ascii=False), asked_by),
        )

    def finish_tool(
        self, call_id: int, *, error: str | None = None, status: str | None = None
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE tool_calls SET status = ?, error = ? WHERE id = ?",
                (status or ("error" if error else "ok"), error, call_id),
            )

    def _insert(self, statement: str, values: tuple[Any, ...]) -> int:
        with closing(self._connect()) as db, db:
            cursor = db.execute(statement, values)
            assert cursor.lastrowid is not None
            return cursor.lastrowid


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
