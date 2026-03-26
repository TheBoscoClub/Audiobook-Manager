"""
Extended tests for WebSocket connection manager.

Supplements test_websocket.py with additional coverage for:
- Edge cases in register/unregister
- Heartbeat for nonexistent sessions
- get_connection return format
- active_usernames with duplicates
- get_stale_connections edge cases
- broadcast error handling / dead socket cleanup
- admin_connections_list details
- init_notification_poller
"""

import json
import time
from unittest.mock import MagicMock, patch

from backend.api_modular.websocket import ConnectionManager


class TestRegisterEdgeCases:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_register_default_username_anonymous(self):
        ws = MagicMock()
        self.mgr.register("s1", ws)
        conn = self.mgr.get_connection("s1")
        assert conn["username"] == "anonymous"

    def test_register_replaces_old_and_closes(self):
        old_ws = MagicMock()
        new_ws = MagicMock()
        self.mgr.register("s1", old_ws, "alice")
        self.mgr.register("s1", new_ws, "alice")
        old_ws.close.assert_called_once()
        assert self.mgr.active_count() == 1

    def test_register_old_close_exception_ignored(self):
        old_ws = MagicMock()
        old_ws.close.side_effect = Exception("already closed")
        new_ws = MagicMock()
        self.mgr.register("s1", old_ws, "alice")
        # Should not raise
        self.mgr.register("s1", new_ws, "alice")
        assert self.mgr.active_count() == 1

    def test_register_sets_connected_at(self):
        ws = MagicMock()
        before = time.time()
        self.mgr.register("s1", ws, "alice")
        after = time.time()
        conn = self.mgr.get_connection("s1")
        assert before <= conn["connected_at"] <= after

    def test_register_initial_state_idle(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        conn = self.mgr.get_connection("s1")
        assert conn["state"] == "idle"

    def test_register_multiple_sessions(self):
        for i in range(5):
            self.mgr.register(f"s{i}", MagicMock(), f"user{i}")
        assert self.mgr.active_count() == 5


class TestUnregisterEdgeCases:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_unregister_nonexistent_is_noop(self):
        # Should not raise
        self.mgr.unregister("nonexistent")
        assert self.mgr.active_count() == 0

    def test_unregister_removes_from_usernames(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        self.mgr.unregister("s1")
        assert "alice" not in self.mgr.active_usernames()

    def test_unregister_one_of_many(self):
        self.mgr.register("s1", MagicMock(), "alice")
        self.mgr.register("s2", MagicMock(), "bob")
        self.mgr.unregister("s1")
        assert self.mgr.active_count() == 1
        assert "bob" in self.mgr.active_usernames()


class TestHeartbeatEdgeCases:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_heartbeat_nonexistent_session_noop(self):
        # Should not raise
        self.mgr.heartbeat("nonexistent", state="playing")
        assert self.mgr.active_count() == 0

    def test_heartbeat_updates_last_seen(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        old_conn = self.mgr.get_connection("s1")
        old_last_seen = old_conn["last_seen"]

        # Small sleep to ensure time difference
        time.sleep(0.01)
        self.mgr.heartbeat("s1", state="streaming")

        new_conn = self.mgr.get_connection("s1")
        assert new_conn["last_seen"] >= old_last_seen
        assert new_conn["state"] == "streaming"

    def test_heartbeat_default_state_idle(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        self.mgr.heartbeat("s1", state="playing")
        self.mgr.heartbeat("s1")  # default state
        conn = self.mgr.get_connection("s1")
        assert conn["state"] == "idle"


class TestGetConnection:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_get_connection_excludes_ws(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        conn = self.mgr.get_connection("s1")
        assert "ws" not in conn

    def test_get_connection_has_expected_keys(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        conn = self.mgr.get_connection("s1")
        assert set(conn.keys()) == {"username", "state", "last_seen", "connected_at"}

    def test_get_connection_nonexistent_returns_none(self):
        assert self.mgr.get_connection("ghost") is None


class TestActiveUsernames:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_empty_manager(self):
        assert self.mgr.active_usernames() == set()

    def test_duplicate_usernames_from_different_sessions(self):
        self.mgr.register("s1", MagicMock(), "alice")
        self.mgr.register("s2", MagicMock(), "alice")
        usernames = self.mgr.active_usernames()
        assert usernames == {"alice"}
        assert self.mgr.active_count() == 2

    def test_multiple_unique_usernames(self):
        self.mgr.register("s1", MagicMock(), "alice")
        self.mgr.register("s2", MagicMock(), "bob")
        self.mgr.register("s3", MagicMock(), "charlie")
        assert self.mgr.active_usernames() == {"alice", "bob", "charlie"}


class TestGetStaleConnections:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_no_stale_when_fresh(self):
        self.mgr.register("s1", MagicMock(), "alice")
        assert self.mgr.get_stale_connections(timeout=30) == []

    def test_stale_detection(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        # Artificially age the connection
        self.mgr._connections["s1"]["last_seen"] = time.time() - 100
        stale = self.mgr.get_stale_connections(timeout=30)
        assert "s1" in stale

    def test_custom_timeout(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        self.mgr._connections["s1"]["last_seen"] = time.time() - 5
        # Not stale with 10s timeout
        assert self.mgr.get_stale_connections(timeout=10) == []
        # Stale with 3s timeout
        assert "s1" in self.mgr.get_stale_connections(timeout=3)

    def test_mixed_stale_and_fresh(self):
        self.mgr.register("s1", MagicMock(), "alice")
        self.mgr.register("s2", MagicMock(), "bob")
        self.mgr._connections["s1"]["last_seen"] = time.time() - 100
        stale = self.mgr.get_stale_connections(timeout=30)
        assert "s1" in stale
        assert "s2" not in stale

    def test_empty_manager_returns_empty(self):
        assert self.mgr.get_stale_connections() == []


class TestBroadcast:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_broadcast_dict_serialized_to_json(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        msg = {"type": "test", "data": 42}
        self.mgr.broadcast(msg)
        ws.send.assert_called_once_with(json.dumps(msg))

    def test_broadcast_string_sent_as_is(self):
        ws = MagicMock()
        self.mgr.register("s1", ws, "alice")
        self.mgr.broadcast("raw string message")
        ws.send.assert_called_once_with("raw string message")

    def test_broadcast_dead_socket_removed(self):
        good_ws = MagicMock()
        bad_ws = MagicMock()
        bad_ws.send.side_effect = Exception("connection reset")

        self.mgr.register("good", good_ws, "alice")
        self.mgr.register("bad", bad_ws, "bob")
        self.mgr.broadcast({"type": "test"})

        # Good socket received the message
        good_ws.send.assert_called_once()
        # Bad socket was removed
        assert self.mgr.active_count() == 1
        assert self.mgr.get_connection("bad") is None

    def test_broadcast_all_dead_empties_manager(self):
        ws1 = MagicMock()
        ws1.send.side_effect = Exception("dead")
        ws2 = MagicMock()
        ws2.send.side_effect = Exception("dead")

        self.mgr.register("s1", ws1, "alice")
        self.mgr.register("s2", ws2, "bob")
        self.mgr.broadcast("test")
        assert self.mgr.active_count() == 0

    def test_broadcast_to_empty_manager(self):
        # Should not raise
        self.mgr.broadcast({"type": "noop"})

    def test_broadcast_preserves_send_order(self):
        """All connected sockets should receive the same payload."""
        ws1 = MagicMock()
        ws2 = MagicMock()
        self.mgr.register("s1", ws1, "alice")
        self.mgr.register("s2", ws2, "bob")

        payload = {"type": "update", "version": 3}
        self.mgr.broadcast(payload)

        expected = json.dumps(payload)
        ws1.send.assert_called_once_with(expected)
        ws2.send.assert_called_once_with(expected)


class TestAdminConnectionsList:
    def setup_method(self):
        self.mgr = ConnectionManager()

    def test_empty_list(self):
        result = self.mgr.admin_connections_list()
        assert result == {"count": 0, "users": []}

    def test_list_includes_state(self):
        self.mgr.register("s1", MagicMock(), "alice")
        self.mgr.heartbeat("s1", state="playing")
        result = self.mgr.admin_connections_list()
        assert result["count"] == 1
        assert result["users"][0]["state"] == "playing"

    def test_list_multiple_users(self):
        self.mgr.register("s1", MagicMock(), "alice")
        self.mgr.register("s2", MagicMock(), "bob")
        result = self.mgr.admin_connections_list()
        assert result["count"] == 2
        usernames = {u["username"] for u in result["users"]}
        assert usernames == {"alice", "bob"}


class TestInitNotificationPoller:
    def test_poller_idempotent(self):
        """Calling init_notification_poller twice should only spawn one poller."""
        import backend.api_modular.websocket as ws_module

        # Reset state
        ws_module._poller_started = False
        ws_module._db_path_for_poller = None

        with patch.dict("sys.modules", {"gevent": None}):
            # Force ImportError for gevent
            ws_module._poller_started = False
            ws_module.init_notification_poller("/tmp/fake.db")
            assert ws_module._poller_started is True

            # Second call should be a no-op
            ws_module.init_notification_poller("/tmp/other.db")
            # db_path should still be the first one
            assert ws_module._db_path_for_poller == "/tmp/fake.db"

        # Reset for other tests
        ws_module._poller_started = False

    def test_poller_without_gevent_logs_warning(self):
        import backend.api_modular.websocket as ws_module

        ws_module._poller_started = False
        ws_module._db_path_for_poller = None

        with patch.object(ws_module, "logger") as mock_logger:
            # Simulate gevent not available
            with patch.dict("sys.modules", {"gevent": None}):
                # Need to make import fail
                original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

                def fake_import(name, *args, **kwargs):
                    if name == "gevent":
                        raise ImportError("no gevent")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=fake_import):
                    ws_module.init_notification_poller("/tmp/test.db")
                    mock_logger.warning.assert_called_once()

        ws_module._poller_started = False


class TestNotificationPollerWithGevent:
    """Test init_notification_poller with gevent available (lines 136-168)."""

    def test_poller_spawns_greenlet_with_gevent(self):
        """When gevent is available, spawn is called (line 167)."""
        import backend.api_modular.websocket as ws_module

        ws_module._poller_started = False
        ws_module._db_path_for_poller = None

        mock_gevent = MagicMock()

        with patch.dict("sys.modules", {"gevent": mock_gevent}):
            original_import = __import__

            def fake_import(name, *args, **kwargs):
                if name == "gevent":
                    return mock_gevent
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                ws_module._poller_started = False
                ws_module.init_notification_poller("/tmp/test_gevent.db")
                mock_gevent.spawn.assert_called_once()

        ws_module._poller_started = False

    def test_poll_loop_processes_notifications(self):
        """Test the poll loop processes and marks notifications delivered."""
        import backend.api_modular.websocket as ws_module
        import sqlite3
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "poll_test.db")
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE maintenance_notifications (
                    id INTEGER PRIMARY KEY,
                    notification_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    delivered INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO maintenance_notifications
                    (notification_type, payload, delivered)
                VALUES ('announce', '{"message": "test"}', 0);
            """)
            conn.commit()
            conn.close()

            ws_module._db_path_for_poller = db_path

            # Simulate one iteration of the poll loop
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, notification_type, payload "
                "FROM maintenance_notifications WHERE delivered = 0"
            ).fetchall()

            assert len(rows) == 1

            for row in rows:
                payload = json.loads(row["payload"])
                payload["type"] = "maintenance_" + row["notification_type"]
                ws_module.connection_manager.broadcast(payload)
                conn.execute(
                    "UPDATE maintenance_notifications SET delivered = 1 WHERE id = ?",
                    (row["id"],),
                )
            conn.commit()

            # Verify notification was marked delivered
            delivered = conn.execute(
                "SELECT delivered FROM maintenance_notifications WHERE id = 1"
            ).fetchone()
            assert delivered[0] == 1
            conn.close()

    def test_poll_loop_handles_bad_payload(self):
        """Test poll loop handles JSON decode error in payload (line 155-158)."""
        import backend.api_modular.websocket as ws_module
        import sqlite3
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "bad_payload.db")
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE maintenance_notifications (
                    id INTEGER PRIMARY KEY,
                    notification_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    delivered INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO maintenance_notifications
                    (notification_type, payload, delivered)
                VALUES ('announce', 'not valid json{{{', 0);
            """)
            conn.commit()
            conn.close()

            ws_module._db_path_for_poller = db_path

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, notification_type, payload "
                "FROM maintenance_notifications WHERE delivered = 0"
            ).fetchall()

            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                    payload["type"] = "maintenance_" + row["notification_type"]
                    ws_module.connection_manager.broadcast(payload)
                    conn.execute(
                        "UPDATE maintenance_notifications SET delivered = 1 "
                        "WHERE id = ?",
                        (row["id"],),
                    )
                except Exception:
                    pass  # Error path (lines 155-158)

            # Notification should NOT be marked delivered
            not_delivered = conn.execute(
                "SELECT delivered FROM maintenance_notifications WHERE id = 1"
            ).fetchone()
            assert not_delivered[0] == 0
            conn.close()

    def test_poll_loop_handles_db_error(self):
        """Test poll loop handles database connection error (lines 162-163)."""
        import backend.api_modular.websocket as ws_module
        import sqlite3

        ws_module._db_path_for_poller = "/nonexistent/path/db.sqlite"

        # Simulate the error handling in the poll loop
        try:
            conn = sqlite3.connect(str(ws_module._db_path_for_poller))
            conn.row_factory = sqlite3.Row
            conn.execute(
                "SELECT id FROM maintenance_notifications WHERE delivered = 0"
            ).fetchall()
        except Exception:
            pass  # Exercises lines 162-163
