"""Conversations: what was asked, what the model saw, and what the Canvas showed.

Kept in SQLite on this machine so a conversation survives the agent restarting and
the Canvas reloading. The model's messages for a turn include whatever it looked up,
file contents too, so this database holds those as well; it lives in ~/.praxis,
which praxis-fs does not read.
"""

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse

from .ui.plan import Mode

# Turns of a conversation the model is shown. Older turns stay stored and visible.
REMEMBERED_TURNS = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    session_id  TEXT NOT NULL,
    n           INTEGER NOT NULL,
    at          TEXT NOT NULL,
    -- What the user asked, as the conversation trail shows it.
    label       TEXT NOT NULL,
    title       TEXT NOT NULL,
    mode        TEXT NOT NULL,
    -- Everything the model was sent and answered during the turn.
    messages    TEXT NOT NULL,
    -- The A2UI messages that drew the turn's screen, to show it again.
    screen      TEXT NOT NULL,
    -- The file or folder that screen shows, if any, in words the model is given.
    on_screen   TEXT,
    PRIMARY KEY (session_id, n)
);
"""


@dataclass(frozen=True)
class TurnSummary:
    n: int
    label: str
    title: str
    mode: Mode


@dataclass(frozen=True)
class StoredScreen:
    mode: Mode
    messages: list[dict[str, Any]]
    on_screen: str | None


def default_path() -> Path:
    return Path(
        os.environ.get("PRAXIS_CONVERSATIONS_DB") or Path.home() / ".praxis" / "conversations.db"
    )


class Conversations:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def record(
        self,
        session_id: str,
        *,
        label: str,
        title: str,
        mode: Mode,
        messages: list[ModelMessage],
        screen: list[dict[str, Any]],
        on_screen: str | None,
    ) -> int:
        """Store a finished turn. Returns its number in the conversation, from 1."""
        with closing(self._connect()) as db, db:
            # One statement, so two writers at once cannot both take the same number.
            (n,) = db.execute(
                "INSERT INTO turns"
                " (session_id, n, at, label, title, mode, messages, screen, on_screen)"
                " SELECT ?, COALESCE(MAX(n), 0) + 1, ?, ?, ?, ?, ?, ?, ?"
                " FROM turns WHERE session_id = ?"
                " RETURNING n",
                (
                    session_id,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    label,
                    title,
                    mode,
                    ModelMessagesTypeAdapter.dump_json(messages).decode(),
                    json.dumps(screen, ensure_ascii=False),
                    on_screen,
                    session_id,
                ),
            ).fetchone()
            return n

    def forget(self, session_id: str) -> int:
        """Delete a conversation: every turn, with what was read in it. Returns how many turns."""
        with closing(self._connect()) as db, db:
            deleted = db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,)).rowcount
        # Deleted rows linger in the file's free pages until it is rewritten.
        with closing(self._connect()) as db:
            db.execute("VACUUM")
        return deleted

    def turns(self, session_id: str) -> list[TurnSummary]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT n, label, title, mode FROM turns WHERE session_id = ? ORDER BY n",
                (session_id,),
            ).fetchall()
        return [TurnSummary(*row) for row in rows]

    def screen(self, session_id: str, n: int) -> StoredScreen | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT mode, screen, on_screen FROM turns WHERE session_id = ? AND n = ?",
                (session_id, n),
            ).fetchone()
        if row is None:
            return None
        return StoredScreen(mode=row[0], messages=json.loads(row[1]), on_screen=row[2])

    def history(self, session_id: str) -> list[ModelMessage]:
        """What the model is shown of the conversation so far.

        The latest turn in full, lookups and all, so the model can refer to what it
        just found. Earlier turns as the request and the final reply only: what was
        read in them stays out of the prompt, which keeps it short.
        """
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT messages FROM turns WHERE session_id = ? ORDER BY n DESC LIMIT ?",
                (session_id, REMEMBERED_TURNS),
            ).fetchall()
        turns = [ModelMessagesTypeAdapter.validate_json(row[0]) for row in reversed(rows)]
        history: list[ModelMessage] = []
        for i, messages in enumerate(turns):
            history.extend(messages if i == len(turns) - 1 else compact(messages))
        return history


def compact(messages: list[ModelMessage]) -> list[ModelMessage]:
    """A turn reduced to the request that opened it and the response that closed it."""
    requests = [m for m in messages if isinstance(m, ModelRequest)]
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    if not requests or not responses:
        return []
    return [requests[0], responses[-1]]
