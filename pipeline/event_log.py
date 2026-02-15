"""SQLite-backed event log for pipeline notification persistence.

Records pipeline events and tracks notification delivery status,
enabling cross-run deduplication and audit trails.
"""

from __future__ import annotations

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
    created_at  TEXT    NOT NULL,
    notified    INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT
);
"""


class EventLog:
    """Append-only event store backed by SQLite.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record(self, event_type: str, payload: Any) -> int:
        """Insert an event and return its row ID.

        Args:
            event_type: Category string (e.g. "pipeline_completed").
            payload: JSON-serialisable data attached to the event.

        Returns:
            The auto-generated row ID.
        """
        now = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload, default=str)
        cur = self._conn.execute(
            "INSERT INTO events (event_type, payload, created_at) VALUES (?, ?, ?)",
            (event_type, payload_json, now),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def mark_notified(self, event_ids: list[int]) -> None:
        """Mark events as successfully notified.

        Args:
            event_ids: Row IDs to mark.
        """
        if not event_ids:
            return
        now = datetime.now(UTC).isoformat()
        placeholders = ",".join("?" for _ in event_ids)
        self._conn.execute(
            f"UPDATE events SET notified = 1, notified_at = ? "
            f"WHERE id IN ({placeholders})",
            [now, *event_ids],
        )
        self._conn.commit()

    def get_pending(self) -> list[dict[str, Any]]:
        """Retrieve all events that have not been notified yet.

        Returns:
            List of dicts with id, event_type, payload, and created_at.
        """
        cur = self._conn.execute(
            "SELECT id, event_type, payload, created_at "
            "FROM events WHERE notified = 0 ORDER BY id"
        )
        rows: list[dict[str, Any]] = []
        for row_id, event_type, payload_json, created_at in cur.fetchall():
            rows.append(
                {
                    "id": row_id,
                    "event_type": event_type,
                    "payload": json.loads(payload_json),
                    "created_at": created_at,
                }
            )
        return rows

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
