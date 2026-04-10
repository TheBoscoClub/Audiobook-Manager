"""Tests for WebSocket connection manager."""

import time
from unittest.mock import MagicMock


from backend.api_modular.websocket import ConnectionManager


class TestConnectionManager:
    def setup_method(self):
        self.manager = ConnectionManager()

    def test_register_connection(self):
        ws = MagicMock()
        self.manager.register("session-1", ws, username="alice")
        assert self.manager.active_count() == 1
        assert "alice" in self.manager.active_usernames()

    def test_unregister_connection(self):
        ws = MagicMock()
        self.manager.register("session-1", ws, username="alice")
        self.manager.unregister("session-1")
        assert self.manager.active_count() == 0

    def test_heartbeat_updates_last_seen(self):
        ws = MagicMock()
        self.manager.register("session-1", ws, username="alice")
        self.manager.heartbeat("session-1", state="listening")
        conn = self.manager.get_connection("session-1")
        assert conn["state"] == "listening"

    def test_stale_connections_detected(self):
        ws = MagicMock()
        self.manager.register("session-1", ws, username="alice")
        self.manager._connections["session-1"]["last_seen"] = time.time() - 60
        stale = self.manager.get_stale_connections(timeout=30)
        assert "session-1" in stale

    def test_broadcast_sends_to_all(self):
        ws1, ws2 = MagicMock(), MagicMock()
        self.manager.register("s1", ws1, username="alice")
        self.manager.register("s2", ws2, username="bob")
        self.manager.broadcast({"type": "test", "data": "hello"})
        ws1.send.assert_called_once()
        ws2.send.assert_called_once()

    def test_duplicate_session_replaces_old(self):
        ws1, ws2 = MagicMock(), MagicMock()
        self.manager.register("session-1", ws1, username="alice")
        self.manager.register("session-1", ws2, username="alice")
        assert self.manager.active_count() == 1

    def test_connections_list_for_admin(self):
        ws = MagicMock()
        self.manager.register("s1", ws, username="alice")
        result = self.manager.admin_connections_list()
        assert result["count"] == 1
        assert result["users"][0]["username"] == "alice"
