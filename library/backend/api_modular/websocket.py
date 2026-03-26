"""
WebSocket endpoint and connection manager.

Provides real-time bidirectional communication for:
- Client heartbeat (connection liveness + activity state)
- Maintenance announcement push
- Live connection tracking for admin dashboard
"""

import json
import logging
import sqlite3
import time
import threading

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections in-memory.

    CONSTRAINT: Requires single-worker deployment (-w 1).
    Multiple workers would each see a subset of connections.
    """

    def __init__(self):
        self._connections = {}  # session_id -> {ws, username, state, last_seen}
        self._lock = threading.Lock()

    def register(self, session_id, ws, username=None):
        """Register a new WebSocket connection."""
        with self._lock:
            if session_id in self._connections:
                old_ws = self._connections[session_id].get("ws")
                try:
                    if old_ws:
                        old_ws.close()
                except Exception:
                    pass
            self._connections[session_id] = {
                "ws": ws,
                "username": username or "anonymous",
                "state": "idle",
                "last_seen": time.time(),
                "connected_at": time.time(),
            }

    def unregister(self, session_id):
        """Remove a WebSocket connection."""
        with self._lock:
            self._connections.pop(session_id, None)

    def heartbeat(self, session_id, state="idle"):
        """Update last-seen time and activity state."""
        with self._lock:
            if session_id in self._connections:
                self._connections[session_id]["last_seen"] = time.time()
                self._connections[session_id]["state"] = state

    def get_connection(self, session_id):
        """Get connection info (without ws object)."""
        with self._lock:
            conn = self._connections.get(session_id)
            if conn:
                return {k: v for k, v in conn.items() if k != "ws"}
        return None

    def active_count(self):
        """Count of active connections."""
        with self._lock:
            return len(self._connections)

    def active_usernames(self):
        """Set of connected usernames."""
        with self._lock:
            return {c["username"] for c in self._connections.values()}

    def get_stale_connections(self, timeout=30):
        """Get session IDs that haven't sent a heartbeat within timeout."""
        now = time.time()
        with self._lock:
            return [
                sid
                for sid, conn in self._connections.items()
                if now - conn["last_seen"] > timeout
            ]

    def broadcast(self, message):
        """Send a message to all connected clients."""
        payload = json.dumps(message) if isinstance(message, dict) else message
        dead = []
        with self._lock:
            for sid, conn in self._connections.items():
                try:
                    conn["ws"].send(payload)
                except Exception:
                    dead.append(sid)
        for sid in dead:
            self.unregister(sid)

    def admin_connections_list(self):
        """Return connection data for admin dashboard."""
        with self._lock:
            users = [
                {"username": c["username"], "state": c["state"]}
                for c in self._connections.values()
            ]
        return {"count": len(users), "users": users}


# Singleton instance -- shared across the Flask app
connection_manager = ConnectionManager()

_poller_started = False
_db_path_for_poller = None


def init_notification_poller(db_path):
    """Start the notification queue poller greenlet.

    Called once when the first WebSocket connects. Polls
    maintenance_notifications for pending items every 5 seconds.
    """
    global _poller_started, _db_path_for_poller
    if _poller_started:
        return
    _poller_started = True
    _db_path_for_poller = db_path

    try:
        import gevent
    except ImportError:
        logger.warning("gevent not available; notification polling disabled")
        return

    def _poll_loop():
        while True:
            try:
                conn = sqlite3.connect(str(_db_path_for_poller))
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""SELECT id, notification_type, payload
                       FROM maintenance_notifications
                       WHERE delivered = 0
                       ORDER BY created_at ASC""").fetchall()

                for row in rows:
                    try:
                        payload = json.loads(row["payload"])
                        payload["type"] = "maintenance_" + row["notification_type"]
                        connection_manager.broadcast(payload)
                        conn.execute(
                            "UPDATE maintenance_notifications "
                            "SET delivered = 1 WHERE id = ?",
                            (row["id"],),
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to deliver notification %d: %s", row["id"], e
                        )

                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Notification poll error: %s", e)

            gevent.sleep(5)

    gevent.spawn(_poll_loop)
    logger.info("Notification queue poller started (5s interval)")
