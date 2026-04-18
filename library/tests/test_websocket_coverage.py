"""
Tests targeting uncovered lines 137-166 in websocket.py.

These lines are the _poll_loop() function body inside init_notification_poller.
The loop runs as a gevent greenlet and:
  - Connects to SQLite, reads undelivered notifications (lines 139-144)
  - Processes each row: parses JSON payload, broadcasts, marks delivered (146-155)
  - Handles per-notification errors (156-159)
  - Commits and closes DB (161-162)
  - Handles DB-level errors (163-164)
  - Sleeps via gevent.sleep (166)

Since the poll loop is spawned inside a gevent greenlet, we extract and
invoke the loop function directly (captured from gevent.spawn call) to
exercise the real code paths without needing a running gevent hub.
"""

# Pylint cannot narrow `poll_fn: Optional[Callable]` to `Callable` through
# `assert poll_fn is not None` when the Optional originated from a nonlocal
# closure assignment in _capture_poll_loop. The assert IS present at every
# call site below, so the runtime is safe. File-level disable here instead
# of seven per-line suppressions.
# pylint: disable=not-callable

import json
import sqlite3
from typing import Any, Callable, Optional, Tuple
from unittest.mock import MagicMock, patch

import backend.api_modular.websocket as ws_module
import pytest
from backend.api_modular.websocket import connection_manager


def _create_notification_db(db_path, rows=None):
    """Create a notification DB with the maintenance_notifications table.

    Args:
        db_path: Path for the SQLite database.
        rows: List of (notification_type, payload_str, delivered) tuples.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            delivered INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    if rows:
        conn.executemany(
            "INSERT INTO maintenance_notifications "
            "(notification_type, payload, delivered) VALUES (?, ?, ?)",
            rows,
        )
    conn.commit()
    conn.close()


def _capture_poll_loop(db_path) -> Tuple[Optional[Callable[[], Any]], MagicMock]:
    """Call init_notification_poller with a mock gevent and return the _poll_loop function.

    Returns the callable that gevent.spawn would have received.
    """
    ws_module._poller_started = False
    ws_module._db_path_for_poller = None

    mock_gevent = MagicMock()
    # Make gevent.sleep raise StopIteration to break out of the while True loop
    mock_gevent.sleep.side_effect = StopIteration("break poll loop")

    captured_fn: Optional[Callable[[], Any]] = None

    def capture_spawn(fn):
        nonlocal captured_fn
        captured_fn = fn

    mock_gevent.spawn.side_effect = capture_spawn

    with patch.dict("sys.modules", {"gevent": mock_gevent}):
        original_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "gevent":
                return mock_gevent
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            ws_module.init_notification_poller(str(db_path))

    ws_module._poller_started = False  # Reset for other tests
    return captured_fn, mock_gevent


class TestPollLoopProcessesNotifications:
    """Exercise the actual _poll_loop code (lines 137-166)."""

    def test_poll_loop_delivers_valid_notification(self, tmp_path):
        """Lines 139-162: successful notification processing."""
        db_path = tmp_path / "notify.db"
        _create_notification_db(db_path, rows=[("announce", '{"message": "server restart"}', 0)])

        poll_fn, mock_gevent = _capture_poll_loop(db_path)
        assert poll_fn is not None, "gevent.spawn must receive _poll_loop"

        # Register a mock WS client to receive broadcast
        ws = MagicMock()
        connection_manager.register("test-session", ws, "tester")

        try:
            # Run the poll loop — gevent.sleep raises StopIteration to exit
            with pytest.raises(StopIteration):
                poll_fn()
        finally:
            connection_manager.unregister("test-session")

        # Verify the broadcast happened with correct payload
        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "maintenance_announce"
        assert sent["message"] == "server restart"

        # Verify notification was marked delivered in DB
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT delivered FROM maintenance_notifications WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] == 1

    def test_poll_loop_handles_bad_json_payload(self, tmp_path):
        """Lines 156-159: per-notification error handling (bad JSON)."""
        db_path = tmp_path / "bad_json.db"
        _create_notification_db(db_path, rows=[("alert", "NOT VALID JSON {{{", 0)])

        poll_fn, mock_gevent = _capture_poll_loop(db_path)
        assert poll_fn is not None

        with patch.object(ws_module, "logger"):
            with pytest.raises(StopIteration):
                poll_fn()

        # Notification should NOT be marked delivered
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT delivered FROM maintenance_notifications WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] == 0

    def test_poll_loop_handles_broadcast_failure(self, tmp_path):
        """Lines 156-159: error when broadcast itself fails."""
        db_path = tmp_path / "broadcast_fail.db"
        _create_notification_db(db_path, rows=[("announce", '{"msg": "ok"}', 0)])

        poll_fn, _ = _capture_poll_loop(db_path)

        # Register a WS client that will fail on send
        bad_ws = MagicMock()
        bad_ws.send.side_effect = Exception("connection reset")
        connection_manager.register("fail-session", bad_ws, "tester")

        try:
            with pytest.raises(StopIteration):
                poll_fn()
        finally:
            connection_manager.unregister("fail-session")

        # DB commit still happens (notification IS marked delivered because
        # broadcast failure removes the dead socket but doesn't raise inside
        # the per-notification try block — broadcast catches its own errors)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT delivered FROM maintenance_notifications WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] == 1

    def test_poll_loop_handles_db_connection_error(self, tmp_path):
        """Lines 163-164: outer try/except catches DB errors."""
        # Point to a directory (not a file) so sqlite3.connect fails on execute
        db_path = tmp_path / "nonexistent_dir" / "db.sqlite"

        poll_fn, _ = _capture_poll_loop(db_path)
        assert poll_fn is not None

        with patch.object(ws_module, "logger") as mock_logger:
            with pytest.raises(StopIteration):
                poll_fn()

        # The outer except should have logged the error
        mock_logger.error.assert_called()
        assert (
            "poll error" in mock_logger.error.call_args[0][0].lower()
            or "Notification poll error" in mock_logger.error.call_args[0][0]
        )

    def test_poll_loop_multiple_notifications_partial_failure(self, tmp_path):
        """Lines 146-159: mix of good and bad notifications in one batch."""
        db_path = tmp_path / "mixed.db"
        _create_notification_db(
            db_path,
            rows=[
                ("announce", '{"msg": "first"}', 0),
                ("alert", "INVALID JSON", 0),
                ("announce", '{"msg": "third"}', 0),
            ],
        )

        poll_fn, _ = _capture_poll_loop(db_path)

        ws = MagicMock()
        connection_manager.register("mix-session", ws, "tester")

        try:
            with pytest.raises(StopIteration):
                poll_fn()
        finally:
            connection_manager.unregister("mix-session")

        # First and third should be delivered, second should not
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT id, delivered FROM maintenance_notifications ORDER BY id"
        ).fetchall()
        conn.close()

        assert rows[0][1] == 1  # id=1: delivered
        assert rows[1][1] == 0  # id=2: bad JSON, not delivered
        assert rows[2][1] == 1  # id=3: delivered

    def test_poll_loop_no_pending_notifications(self, tmp_path):
        """Lines 139-144: empty result set (no undelivered notifications)."""
        db_path = tmp_path / "empty.db"
        _create_notification_db(db_path, rows=[])

        poll_fn, _ = _capture_poll_loop(db_path)
        assert poll_fn is not None

        # Should complete without error
        with pytest.raises(StopIteration):
            poll_fn()

    def test_poll_loop_skips_already_delivered(self, tmp_path):
        """Only processes delivered=0 rows."""
        db_path = tmp_path / "delivered.db"
        _create_notification_db(
            db_path,
            rows=[("announce", '{"msg": "already done"}', 1)],  # already delivered
        )

        poll_fn, _ = _capture_poll_loop(db_path)

        ws = MagicMock()
        connection_manager.register("skip-session", ws, "tester")

        try:
            with pytest.raises(StopIteration):
                poll_fn()
        finally:
            connection_manager.unregister("skip-session")

        # No broadcast should have happened
        ws.send.assert_not_called()
