from pipeline.event_log import EventLog


def test_record_and_retrieve(tmp_path):
    """Record an event and verify get_pending() returns it."""
    db_path = str(tmp_path / "events.db")
    log = EventLog(db_path)
    try:
        event_id = log.record("pipeline_completed", {"genes": 5})
        assert event_id == 1

        pending = log.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == 1
        assert pending[0]["event_type"] == "pipeline_completed"
        assert pending[0]["payload"] == {"genes": 5}
    finally:
        log.close()


def test_mark_notified(tmp_path):
    """After marking notified, get_pending() returns empty."""
    db_path = str(tmp_path / "events.db")
    log = EventLog(db_path)
    try:
        event_id = log.record("pipeline_completed", {"genes": 3})
        log.mark_notified([event_id])

        pending = log.get_pending()
        assert len(pending) == 0
    finally:
        log.close()


def test_multiple_events(tmp_path):
    """Multiple events are all returned as pending."""
    db_path = str(tmp_path / "events.db")
    log = EventLog(db_path)
    try:
        id1 = log.record("pipeline_completed", {"run": 1})
        id2 = log.record("pipeline_completed", {"run": 2})

        pending = log.get_pending()
        assert len(pending) == 2
        assert pending[0]["id"] == id1
        assert pending[1]["id"] == id2
    finally:
        log.close()


def test_mark_notified_empty_list(tmp_path):
    """Marking an empty list is a no-op."""
    db_path = str(tmp_path / "events.db")
    log = EventLog(db_path)
    try:
        log.record("test", {})
        log.mark_notified([])  # should not raise
        assert len(log.get_pending()) == 1
    finally:
        log.close()


def test_partial_mark(tmp_path):
    """Marking one of two events leaves the other pending."""
    db_path = str(tmp_path / "events.db")
    log = EventLog(db_path)
    try:
        id1 = log.record("first", {})
        id2 = log.record("second", {})

        log.mark_notified([id1])
        pending = log.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == id2
    finally:
        log.close()
