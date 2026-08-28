import json
import sqlite3

from pipeline.event_log import EventLog


def _read_events(db_path: str) -> list[tuple[str, dict, str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_type, payload, created_at FROM events ORDER BY id"
        ).fetchall()
    return [
        (event_type, json.loads(payload), created_at)
        for event_type, payload, created_at in rows
    ]


def test_record_persists_event(tmp_path):
    db_path = str(tmp_path / "events.db")

    with EventLog(db_path) as log:
        log.record("pipeline_completed", {"genes": 5})

    events = _read_events(db_path)
    assert len(events) == 1
    assert events[0][0] == "pipeline_completed"
    assert events[0][1] == {"genes": 5}
    assert events[0][2]


def test_multiple_events_preserve_order(tmp_path):
    db_path = str(tmp_path / "events.db")

    with EventLog(db_path) as log:
        log.record("pipeline_completed", {"run": 1})
        log.record("pipeline_completed", {"run": 2})

    assert [payload for _, payload, _ in _read_events(db_path)] == [
        {"run": 1},
        {"run": 2},
    ]
