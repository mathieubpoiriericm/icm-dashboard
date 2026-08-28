"""SQLite-backed audit log for pipeline events."""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
"""


class EventLog:
    """Append-only event store backed by SQLite.

    Supports ``with EventLog(path) as log:`` so a crash mid-run can't leave
    the SQLite WAL/SHM files unclosed (which would block later readers).

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Retry locked DB for 5s instead of failing immediately — concurrent
        # pipeline invocations would otherwise silently drop events.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def record(self, event_type: str, payload: Any) -> None:
        """Insert an event.

        Args:
            event_type: Category string (e.g. "pipeline_completed").
            payload: JSON-serialisable data attached to the event.

        """
        now = datetime.now(UTC).isoformat()
        # default=str is intentionally lossy — payloads are for human audit,
        # not round-trip typed.
        payload_json = json.dumps(payload, default=str)
        self._conn.execute(
            "INSERT INTO events (event_type, payload, created_at) VALUES (?, ?, ?)",
            (event_type, payload_json, now),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection.

        Errors during close are logged, not raised — so calling this from a
        finally block cannot mask the original exception being unwound.
        """
        try:
            self._conn.close()
        except Exception as exc:
            logger.warning(f"Error closing event log: {exc}")
