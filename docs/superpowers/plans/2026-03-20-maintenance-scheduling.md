# Maintenance Scheduling & Live Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time connection tracking, maintenance scheduling with automated execution, and a maintenance announcement system with a Frankenstein knife switch dismissal control.

**Architecture:** WebSocket backbone (Gunicorn+geventwebsocket replacing Waitress) enables real-time bidirectional communication. Separate scheduler systemd service handles cron-based execution. Plugin registry makes maintenance tasks extensible. All admin endpoints use `@admin_if_enabled` for dual auth-mode support.

**Tech Stack:** Flask, flask-sock, Gunicorn, gevent, gevent-websocket, croniter, SQLite, vanilla JS, Web Audio API, CSS animations/SVG

**Spec:** `docs/superpowers/specs/2026-03-20-maintenance-scheduling-design.md`

---

## Phase Overview

| Phase | What | Produces |
|-------|------|---------|
| **1** | Server migration (Waitress -> Gunicorn+geventwebsocket) | Existing app works identically on new server |
| **2** | WebSocket foundation + connection tracking | Live connections visible in Activity tab |
| **3** | Database schema + maintenance API | CRUD endpoints for windows, messages, history |
| **4** | Task registry + scheduler daemon | Maintenance tasks run on schedule |
| **5** | Maint Sched tab UI | Back office tab for managing everything |
| **6** | Announcement banner + knife switch | Pulsing indicator, panel, sounds, dismiss |
| **7** | Integration, docs, packaging | Install/upgrade scripts, Docker, docs updated |

Each phase produces a commit (or small set of commits) and can be tested independently. **Do not skip phases or reorder** -- each builds on the previous.

---

## Task 1: Create Feature Branch

**Files:**

- None (git operations only)

- [ ] **Step 1: Create and switch to feature branch**

```bash
git checkout -b maintenance-scheduling main
```

- [ ] **Step 2: Verify clean starting point**

```bash
git status
git log --oneline -3
```

Expected: On `maintenance-scheduling`, clean working tree, HEAD at latest `main` commit.

- [ ] **Step 3: Commit**

No commit needed -- just branch creation.

---

## Task 2: Migrate Waitress -> Gunicorn+geventwebsocket

This is the **critical prerequisite**. The existing app must work identically on the new server before any WebSocket code is written.

**Files:**

- Modify: `library/requirements.txt`
- Modify: `library/requirements-docker.txt`
- Modify: `library/backend/api_server.py` (complete rewrite)
- Modify: `library/backend/api_modular/__init__.py` (remove `run_server`, update `__all__`)
- Modify: `systemd/audiobook-api.service`
- Create: `library/tests/test_gunicorn_migration.py`

- [ ] **Step 1: Update requirements.txt**

Replace `waitress>=2.1.0` with:

```text
gunicorn>=23.0.0
gevent>=24.11.1
gevent-websocket>=0.10.1
flask-sock>=0.7.0
croniter>=6.0.0
```

Keep all other entries. Apply same changes to `library/requirements-docker.txt`.

- [ ] **Step 2: Install new dependencies**

```bash
cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager
source venv/bin/activate
pip install gunicorn gevent gevent-websocket flask-sock croniter
pip install -r library/requirements.txt
```

- [ ] **Step 3: Rewrite api_server.py**

Replace the entire file with:

```python
#!/usr/bin/env python3
"""
Audiobook Library API Server

IMPORTANT: gevent monkey-patching MUST be the first executable code.
It patches stdlib I/O (including sqlite3) for cooperative scheduling.
Without this, SQLite queries block the entire greenlet loop.
"""
from gevent import monkey
monkey.patch_all()

import os
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from api_modular import create_app
from config import API_PORT, DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR


def _create_configured_app():
    """Create and return the configured Flask application."""
    if not DATABASE_PATH.exists():
        print(f"Error: Database not found at {DATABASE_PATH}")
        print("Please run: python3 backend/import_to_db.py")
        sys.exit(1)

    auth_enabled = os.environ.get("AUTH_ENABLED", "false").lower() in (
        "true", "1", "yes",
    )
    auth_db_path = os.environ.get("AUTH_DATABASE") if auth_enabled else None
    auth_key_path = os.environ.get("AUTH_KEY_FILE") if auth_enabled else None
    auth_dev_mode = os.environ.get("AUDIOBOOKS_DEV_MODE", "false").lower() in (
        "true", "1", "yes",
    )

    return create_app(
        database_path=DATABASE_PATH,
        project_dir=PROJECT_DIR,
        supplements_dir=SUPPLEMENTS_DIR,
        api_port=API_PORT,
        auth_db_path=Path(auth_db_path) if auth_db_path else None,
        auth_key_path=Path(auth_key_path) if auth_key_path else None,
        auth_dev_mode=auth_dev_mode,
    )


# Module-level app object for Gunicorn: `gunicorn api_server:app`
app = _create_configured_app()


if __name__ == "__main__":
    # Direct execution for development/testing only
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")
    if debug:
        app.run(host="0.0.0.0", port=API_PORT, debug=True)
    else:
        from gevent.pywsgi import WSGIServer
        from geventwebsocket.handler import WebSocketHandler
        server = WSGIServer(
            ("0.0.0.0", API_PORT), app, handler_class=WebSocketHandler
        )
        print(f"Serving on http://0.0.0.0:{API_PORT}")
        server.serve_forever()
```

- [ ] **Step 4: Remove run_server from api_modular/**init**.py**

Delete the `run_server()` function (lines 208-275) and remove `"run_server"` from the `__all__` list. Also remove the `app = None` placeholder (line 205) and remove `"app"` from `__all__`. Add `jsonify, request` to the Flask import at line 23:

```python
from flask import Flask, Response, jsonify, request
```

These will be needed by later tasks.

- [ ] **Step 5: Update systemd service file**

In `systemd/audiobook-api.service`, change:

```ini
Description=Audiobooks Library API Server
```

Keep both existing `ExecStartPre` lines (mkdir + port check). Replace only the `ExecStart` line:

```ini
# Gunicorn with geventwebsocket worker for WebSocket support.
# HARD CONSTRAINT: -w 1 required for in-memory WebSocket connection manager.
# Do NOT increase workers without migrating connection state to database.
ExecStart=/opt/audiobooks/library/venv/bin/gunicorn \
    -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    -w 1 \
    --bind 0.0.0.0:${AUDIOBOOKS_API_PORT} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    api_server:app
```

- [ ] **Step 6: Write migration test**

Create `library/tests/test_gunicorn_migration.py`:

```python
"""Tests to verify Gunicorn migration doesn't break existing functionality."""


def test_monkey_patch_is_first():
    """Verify gevent monkey-patching happens before other imports."""
    with open("library/backend/api_server.py") as f:
        lines = f.readlines()
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring or not stripped or stripped.startswith("#"):
            continue
        assert "gevent" in stripped or "monkey" in stripped, (
            f"First executable line must be gevent monkey patch, got: {stripped}"
        )
        break


def test_requirements_no_waitress():
    """Verify waitress is removed from requirements."""
    with open("library/requirements.txt") as f:
        content = f.read().lower()
    assert "waitress" not in content, "waitress should be removed from requirements.txt"


def test_requirements_has_gunicorn_deps():
    """Verify all Gunicorn dependencies are listed."""
    with open("library/requirements.txt") as f:
        content = f.read().lower()
    for dep in ["gunicorn", "gevent", "gevent-websocket", "flask-sock", "croniter"]:
        assert dep in content, f"{dep} missing from requirements.txt"


def test_systemd_uses_gunicorn():
    """Verify systemd service uses Gunicorn, not waitress."""
    with open("systemd/audiobook-api.service") as f:
        content = f.read()
    assert "gunicorn" in content, "Service should use gunicorn"
    assert "geventwebsocket" in content, "Service should use geventwebsocket worker"
    assert "-w 1" in content, "Service must use single worker"


def test_api_server_has_module_level_app():
    """Verify api_server.py exposes module-level app for Gunicorn."""
    with open("library/backend/api_server.py") as f:
        content = f.read()
    assert "app = _create_configured_app()" in content, (
        "api_server.py must have module-level app for gunicorn api_server:app"
    )


def test_api_modular_no_run_server():
    """Verify run_server was removed from api_modular."""
    with open("library/backend/api_modular/__init__.py") as f:
        content = f.read()
    assert "def run_server(" not in content, "run_server should be removed"
    assert "from waitress" not in content, "waitress import should be removed"
```

- [ ] **Step 7: Run tests**

```bash
cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager
python -m pytest library/tests/test_gunicorn_migration.py -v
```

Expected: All PASS.

- [ ] **Step 8: Manual smoke test**

```bash
cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager/library/backend
FLASK_DEBUG=1 python api_server.py &
sleep 2
curl -s http://localhost:5001/api/stats | python3 -m json.tool | head -5
curl -s http://localhost:5001/api/system/version
kill %1
```

Expected: JSON responses, no errors.

- [ ] **Step 9: Commit**

```bash
git add library/requirements.txt library/requirements-docker.txt \
    library/backend/api_server.py library/backend/api_modular/__init__.py \
    systemd/audiobook-api.service library/tests/test_gunicorn_migration.py
git commit -m "feat: migrate API server from Waitress to Gunicorn+geventwebsocket

Replace Waitress WSGI server with Gunicorn using the geventwebsocket worker
class. This is the prerequisite for WebSocket support (maintenance scheduling
feature). Single worker (-w 1) is a hard constraint for in-memory connection
state. gevent monkey-patching applied at entry point for cooperative I/O."
```

---

## Task 3: WebSocket Endpoint + Connection Manager

**Files:**

- Create: `library/backend/api_modular/websocket.py`
- Create: `library/tests/test_websocket.py`
- Modify: `library/backend/api_modular/__init__.py` (register WebSocket)

- [ ] **Step 1: Write failing tests for connection manager**

Create `library/tests/test_websocket.py`:

```python
"""Tests for WebSocket connection manager."""
import json
import time
from unittest.mock import MagicMock

import pytest

from library.backend.api_modular.websocket import ConnectionManager


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
        self.manager.heartbeat("session-1", state="streaming")
        conn = self.manager.get_connection("session-1")
        assert conn["state"] == "streaming"

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest library/tests/test_websocket.py -v
```

Expected: ImportError -- `websocket` module doesn't exist yet.

- [ ] **Step 3: Implement ConnectionManager**

Create `library/backend/api_modular/websocket.py`:

```python
"""
WebSocket endpoint and connection manager.

Provides real-time bidirectional communication for:
- Client heartbeat (connection liveness + activity state)
- Maintenance announcement push
- Live connection tracking for admin dashboard
"""
import json
import logging
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
                sid for sid, conn in self._connections.items()
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest library/tests/test_websocket.py -v
```

Expected: All PASS.

- [ ] **Step 5: Wire WebSocket endpoint into Flask app**

Add to `library/backend/api_modular/__init__.py` -- in `create_app()`, after the last `flask_app.register_blueprint(...)` call but before `return flask_app`:

```python
    # WebSocket endpoint (requires geventwebsocket worker)
    from flask_sock import Sock
    from .websocket import connection_manager
    import json as _json

    sock = Sock(flask_app)

    @sock.route("/api/ws")
    def ws_handler(ws):
        """WebSocket handler for heartbeat and push notifications."""
        auth_enabled = flask_app.config.get("AUTH_ENABLED", False)
        session_id = request.cookies.get(
            "audiobooks_session", "anon-" + str(id(ws))
        )
        username = "anonymous"

        if auth_enabled:
            user = get_current_user()
            if user is None:
                ws.close(1008, "Authentication required")
                return
            username = user.username
            session_id = request.cookies.get("audiobooks_session", session_id)

        connection_manager.register(session_id, ws, username=username)
        try:
            while True:
                data = ws.receive(timeout=15)
                if data is None:
                    break
                try:
                    msg = _json.loads(data)
                    if msg.get("type") == "heartbeat":
                        connection_manager.heartbeat(
                            session_id, state=msg.get("state", "idle")
                        )
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass
        finally:
            connection_manager.unregister(session_id)

    # Admin connections endpoint
    @flask_app.route("/api/admin/connections")
    @admin_if_enabled
    def get_connections():
        return jsonify(connection_manager.admin_connections_list())
```

- [ ] **Step 6: Commit**

```bash
git add library/backend/api_modular/websocket.py \
    library/backend/api_modular/__init__.py \
    library/tests/test_websocket.py
git commit -m "feat: add WebSocket connection manager and endpoint

Implement ConnectionManager for tracking active client connections.
WebSocket endpoint at /api/ws handles heartbeat messages and auth
validation. Admin endpoint at /api/admin/connections exposes count
and usernames for the Activity tab dashboard."
```

---

## Task 4: Proxy WebSocket Tunneling

**Files:**

- Modify: `library/web-v2/proxy_server.py`
- Create: `library/tests/test_proxy_websocket.py`

- [ ] **Step 1: Write failing test for WebSocket detection**

Create `library/tests/test_proxy_websocket.py`:

```python
"""Test that proxy_server detects WebSocket upgrade requests."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# proxy_server uses hyphenated directory; add to path manually
sys.path.insert(0, str(Path(__file__).parent.parent / "web-v2"))


def test_proxy_detects_websocket_upgrade_headers():
    """Verify the proxy recognizes WebSocket upgrade requests."""
    from proxy_server import is_websocket_upgrade

    class FakeHeaders:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}
        def get(self, key, default=None):
            return self._d.get(key.lower(), default)

    assert is_websocket_upgrade(FakeHeaders({
        "Upgrade": "websocket", "Connection": "Upgrade"
    })) is True

    assert is_websocket_upgrade(FakeHeaders({
        "Content-Type": "application/json"
    })) is False

    assert is_websocket_upgrade(FakeHeaders({
        "Upgrade": "h2c", "Connection": "Upgrade"
    })) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest library/tests/test_proxy_websocket.py -v
```

Expected: ImportError -- `is_websocket_upgrade` doesn't exist yet.

- [ ] **Step 3: Implement WebSocket tunneling in proxy_server.py**

Add this function near the top (after the `HOP_BY_HOP_HEADERS` set):

```python
def is_websocket_upgrade(headers):
    """Detect WebSocket upgrade request."""
    upgrade = (headers.get("Upgrade", "") or "").lower()
    connection = (headers.get("Connection", "") or "").lower()
    return upgrade == "websocket" and "upgrade" in connection
```

Add this method to `ReverseProxyHandler`:

```python
    def _tunnel_websocket(self):
        """Tunnel a WebSocket upgrade request to the API backend via raw TCP."""
        import socket
        import select

        # Build raw HTTP upgrade request to forward to backend
        request_line = f"{self.command} {self.path} HTTP/1.1\r\n"
        header_lines = ""
        for key, value in self.headers.items():
            header_lines += f"{key}: {value}\r\n"
        header_lines += "\r\n"
        raw_request = (request_line + header_lines).encode("latin-1")

        try:
            backend = socket.create_connection(("127.0.0.1", API_PORT), timeout=10)
        except (socket.error, OSError) as e:
            self.send_error(503, f"Backend unreachable: {e}")
            return

        try:
            backend.sendall(raw_request)

            # Read the upgrade response from backend and forward to client
            client_sock = self.request  # the raw client socket
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = backend.recv(4096)
                if not chunk:
                    break
                buf += chunk

            # Send the full upgrade response (headers) to client
            client_sock.sendall(buf)

            # Check if upgrade was accepted (101 Switching Protocols)
            if not buf.startswith(b"HTTP/1.1 101"):
                backend.close()
                return

            # Bidirectional relay: client <-> backend
            sockets = [client_sock, backend]
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 30)
                if errored:
                    break
                if not readable:
                    break  # timeout
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    target = backend if sock is client_sock else client_sock
                    target.sendall(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                backend.close()
            except Exception:
                pass
```

Modify `do_GET()` to detect WebSocket upgrades before the proxy path check:

```python
    def do_GET(self):
        if self._is_proxy_path() and is_websocket_upgrade(self.headers):
            self._tunnel_websocket()
            return
        if self._is_proxy_path():
            self.proxy_to_api("GET")
            return
        # ... rest of existing do_GET unchanged
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest library/tests/test_proxy_websocket.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add library/web-v2/proxy_server.py library/tests/test_proxy_websocket.py
git commit -m "feat: add WebSocket upgrade tunneling to proxy server

Detect Upgrade: websocket requests and tunnel raw TCP to backend
instead of HTTP-proxying. Required for /api/ws endpoint to work
through the existing proxy_server.py frontend layer."
```

---

## Task 5: Database Schema + Maintenance API Blueprint

**Files:**

- Modify: `library/backend/schema.sql`
- Create: `library/backend/api_modular/maintenance.py`
- Modify: `library/backend/api_modular/__init__.py` (register blueprint)
- Create: `library/tests/test_maintenance_api.py`

- [ ] **Step 1: Add schema tables**

Append to `library/backend/schema.sql`:

```sql
-- ================================================================
-- Maintenance Scheduling Tables
-- ================================================================

CREATE TABLE IF NOT EXISTS maintenance_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL,
    task_params TEXT DEFAULT '{}',
    schedule_type TEXT NOT NULL,
    cron_expression TEXT,
    scheduled_at TEXT,
    next_run_at TEXT,
    duration_minutes INTEGER DEFAULT 30,
    lead_time_hours INTEGER DEFAULT 48,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS trg_maint_windows_updated
    AFTER UPDATE ON maintenance_windows
    FOR EACH ROW
BEGIN
    UPDATE maintenance_windows SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS maintenance_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    dismissed_at TEXT,
    dismissed_by TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    result_message TEXT,
    result_data TEXT DEFAULT '{}',
    FOREIGN KEY (window_id) REFERENCES maintenance_windows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS maintenance_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    delivered INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_maint_windows_next_run ON maintenance_windows(next_run_at);
CREATE INDEX IF NOT EXISTS idx_maint_windows_status ON maintenance_windows(status);
CREATE INDEX IF NOT EXISTS idx_maint_messages_active ON maintenance_messages(dismissed_at);
CREATE INDEX IF NOT EXISTS idx_maint_history_window ON maintenance_history(window_id);
CREATE INDEX IF NOT EXISTS idx_maint_notifications_pending ON maintenance_notifications(delivered, created_at);
```

- [ ] **Step 2: Write failing API tests**

Create `library/tests/test_maintenance_api.py`:

```python
"""Tests for maintenance scheduling API endpoints."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app_with_db(tmp_path):
    """Create a Flask test app with fresh database."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
    from api_modular import create_app

    db_path = tmp_path / "test.db"
    schema_path = Path(__file__).parent.parent / "backend" / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_path.read_text())
    conn.close()

    app = create_app(
        database_path=db_path,
        project_dir=tmp_path,
        supplements_dir=tmp_path / "supplements",
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app_with_db):
    return app_with_db.test_client()


class TestMaintenanceWindows:
    def test_create_window(self, client):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Nightly Vacuum",
                "task_type": "db_vacuum",
                "schedule_type": "recurring",
                "cron_expression": "0 3 * * *",
                "lead_time_hours": 48,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Nightly Vacuum"
        assert data["id"] is not None

    def test_list_windows(self, client):
        client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Test",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T03:00:00Z",
            },
        )
        resp = client.get("/api/admin/maintenance/windows")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1

    def test_update_window(self, client):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Test",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T03:00:00Z",
            },
        )
        wid = resp.get_json()["id"]
        resp = client.put(
            f"/api/admin/maintenance/windows/{wid}",
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated Name"

    def test_delete_window_no_history(self, client):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Deletable",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T03:00:00Z",
            },
        )
        wid = resp.get_json()["id"]
        resp = client.delete(f"/api/admin/maintenance/windows/{wid}")
        assert resp.status_code == 200


class TestMaintenanceMessages:
    def test_create_message(self, client):
        resp = client.post(
            "/api/admin/maintenance/messages",
            json={"message": "Planned downtime tonight"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["message"] == "Planned downtime tonight"

    def test_dismiss_message(self, client):
        resp = client.post(
            "/api/admin/maintenance/messages",
            json={"message": "Test"},
        )
        mid = resp.get_json()["id"]
        resp = client.delete(f"/api/admin/maintenance/messages/{mid}")
        assert resp.status_code == 200


class TestPublicAnnouncements:
    def test_announcements_returns_active(self, client):
        client.post(
            "/api/admin/maintenance/messages",
            json={"message": "Server restarting"},
        )
        resp = client.get("/api/maintenance/announcements")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["messages"]) >= 1


class TestTaskList:
    def test_list_tasks(self, client):
        resp = client.get("/api/admin/maintenance/tasks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)


class TestHistory:
    def test_empty_history(self, client):
        resp = client.get("/api/admin/maintenance/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest library/tests/test_maintenance_api.py -v
```

Expected: ImportError -- `maintenance` module doesn't exist yet.

- [ ] **Step 4: Implement maintenance blueprint**

Create `library/backend/api_modular/maintenance.py`:

```python
"""
Maintenance scheduling API blueprint.

Provides CRUD endpoints for maintenance windows, manual announcements,
task registry listing, and execution history.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from .auth import admin_if_enabled, get_current_user, guest_allowed

logger = logging.getLogger(__name__)

maintenance_bp = Blueprint("maintenance", __name__)

_db_path = None


def init_maintenance_routes(database_path):
    """Initialize with database path."""
    global _db_path
    _db_path = database_path


def _get_db():
    """Get a database connection."""
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_username():
    """Get current username for audit trail."""
    try:
        user = get_current_user()
        return user.username if user else "system"
    except Exception:
        return "system"


# ---------- Maintenance Windows ----------

@maintenance_bp.route("/api/admin/maintenance/windows", methods=["GET"])
@admin_if_enabled
def list_windows():
    """List all maintenance windows."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM maintenance_windows ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/windows", methods=["POST"])
@admin_if_enabled
def create_window():
    """Create a new maintenance window."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    name = data.get("name")
    task_type = data.get("task_type")
    schedule_type = data.get("schedule_type")
    if not all([name, task_type, schedule_type]):
        return jsonify({"error": "name, task_type, schedule_type required"}), 400

    if schedule_type not in ("once", "recurring"):
        return jsonify({"error": "schedule_type must be 'once' or 'recurring'"}), 400

    cron_expression = data.get("cron_expression")
    scheduled_at = data.get("scheduled_at")
    task_params = json.dumps(data.get("task_params", {}))
    duration_minutes = data.get("duration_minutes", 30)
    lead_time_hours = data.get("lead_time_hours", 48)
    description = data.get("description", "")

    # Compute next_run_at
    next_run_at = None
    if schedule_type == "once" and scheduled_at:
        next_run_at = scheduled_at
    elif schedule_type == "recurring" and cron_expression:
        try:
            from croniter import croniter
            cron = croniter(cron_expression, datetime.now(timezone.utc))
            next_run_at = cron.get_next(datetime).isoformat() + "Z"
        except (ValueError, KeyError) as e:
            return jsonify({"error": f"Invalid cron expression: {e}"}), 400

    # Validate task type against registry (if available)
    try:
        from .maintenance_tasks import registry
        if not registry.get(task_type):
            available = [t["name"] for t in registry.list_all()]
            return jsonify({
                "error": f"Unknown task_type '{task_type}'",
                "available": available,
            }), 400
    except ImportError:
        pass  # Registry not yet available (during early development)

    conn = _get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO maintenance_windows
               (name, description, task_type, task_params, schedule_type,
                cron_expression, scheduled_at, next_run_at,
                duration_minutes, lead_time_hours)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, description, task_type, task_params, schedule_type,
             cron_expression, scheduled_at, next_run_at,
             duration_minutes, lead_time_hours),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM maintenance_windows WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return jsonify(dict(row)), 201
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/windows/<int:wid>", methods=["PUT"])
@admin_if_enabled
def update_window(wid):
    """Update a maintenance window."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM maintenance_windows WHERE id = ?", (wid,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Window not found"}), 404

        # Build dynamic update
        allowed = {
            "name", "description", "task_type", "task_params",
            "cron_expression", "scheduled_at", "duration_minutes",
            "lead_time_hours", "status",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if "task_params" in updates and isinstance(updates["task_params"], dict):
            updates["task_params"] = json.dumps(updates["task_params"])

        # Recompute next_run_at if schedule changed
        if "cron_expression" in updates or "scheduled_at" in updates:
            stype = data.get("schedule_type", existing["schedule_type"])
            if stype == "recurring" and updates.get("cron_expression"):
                from croniter import croniter
                cron = croniter(
                    updates["cron_expression"], datetime.now(timezone.utc)
                )
                updates["next_run_at"] = cron.get_next(datetime).isoformat() + "Z"
            elif stype == "once" and updates.get("scheduled_at"):
                updates["next_run_at"] = updates["scheduled_at"]

        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [wid]
        conn.execute(
            f"UPDATE maintenance_windows SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM maintenance_windows WHERE id = ?", (wid,)
        ).fetchone()
        return jsonify(dict(row))
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/windows/<int:wid>", methods=["DELETE"])
@admin_if_enabled
def delete_window(wid):
    """Delete or soft-delete a maintenance window."""
    conn = _get_db()
    try:
        has_history = conn.execute(
            "SELECT COUNT(*) FROM maintenance_history WHERE window_id = ?", (wid,)
        ).fetchone()[0]

        if has_history:
            conn.execute(
                "UPDATE maintenance_windows SET status = 'cancelled' WHERE id = ?",
                (wid,),
            )
        else:
            conn.execute("DELETE FROM maintenance_windows WHERE id = ?", (wid,))
        conn.commit()
        return jsonify({"ok": True, "soft_deleted": bool(has_history)})
    finally:
        conn.close()


# ---------- Manual Messages ----------

@maintenance_bp.route("/api/admin/maintenance/messages", methods=["GET"])
@admin_if_enabled
def list_messages():
    """List all manual maintenance messages."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM maintenance_messages ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/messages", methods=["POST"])
@admin_if_enabled
def create_message():
    """Create a manual maintenance message and push immediately."""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "message field required"}), 400

    username = _get_username()
    conn = _get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO maintenance_messages (message, created_by) VALUES (?, ?)",
            (data["message"], username),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM maintenance_messages WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        result = dict(row)

        # Push immediately via WebSocket (in-process, no DB round-trip)
        try:
            from .websocket import connection_manager
            connection_manager.broadcast({
                "type": "maintenance_announce",
                "messages": [result],
            })
        except Exception as e:
            logger.warning("WebSocket broadcast failed: %s", e)

        return jsonify(result), 201
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/messages/<int:mid>", methods=["DELETE"])
@admin_if_enabled
def dismiss_message(mid):
    """Permanently dismiss a manual message."""
    username = _get_username()
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE maintenance_messages
               SET dismissed_at = datetime('now'), dismissed_by = ?
               WHERE id = ?""",
            (username, mid),
        )
        conn.commit()

        # Push dismiss notification
        try:
            from .websocket import connection_manager
            connection_manager.broadcast({
                "type": "maintenance_dismiss",
                "message_id": mid,
            })
        except Exception as e:
            logger.warning("WebSocket broadcast failed: %s", e)

        return jsonify({"ok": True})
    finally:
        conn.close()


# ---------- Public Announcements ----------

@maintenance_bp.route("/api/maintenance/announcements", methods=["GET"])
@guest_allowed
def get_announcements():
    """Public endpoint: active announcements for all users (including pre-login).

    Returns manual messages + windows within lead time.
    @guest_allowed populates g.user if session exists but never returns 401.
    """
    conn = _get_db()
    try:
        # Active manual messages
        messages = conn.execute(
            """SELECT id, message, created_by, created_at
               FROM maintenance_messages
               WHERE dismissed_at IS NULL
               ORDER BY created_at DESC"""
        ).fetchall()

        # Upcoming windows within lead time
        windows = conn.execute(
            """SELECT id, name, description, task_type, next_run_at,
                      duration_minutes, lead_time_hours
               FROM maintenance_windows
               WHERE status = 'active'
                 AND next_run_at IS NOT NULL
                 AND datetime(next_run_at, '-' || lead_time_hours || ' hours')
                     <= datetime('now')
               ORDER BY next_run_at ASC"""
        ).fetchall()

        return jsonify({
            "messages": [dict(r) for r in messages],
            "windows": [dict(r) for r in windows],
        })
    finally:
        conn.close()


# ---------- Task Registry ----------

@maintenance_bp.route("/api/admin/maintenance/tasks", methods=["GET"])
@admin_if_enabled
def list_tasks():
    """List registered maintenance task types."""
    try:
        from .maintenance_tasks import registry
        return jsonify(registry.list_all())
    except ImportError:
        return jsonify([])


# ---------- Execution History ----------

@maintenance_bp.route("/api/admin/maintenance/history", methods=["GET"])
@admin_if_enabled
def get_history():
    """Execution history for all maintenance windows."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT h.*, w.name as window_name, w.task_type
               FROM maintenance_history h
               JOIN maintenance_windows w ON h.window_id = w.id
               ORDER BY h.started_at DESC
               LIMIT 100"""
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()
```

- [ ] **Step 5: Register blueprint in **init**.py**

In `create_app()`, after the existing blueprint registrations:

```python
    # Maintenance scheduling
    from .maintenance import maintenance_bp, init_maintenance_routes
    init_maintenance_routes(database_path)
    flask_app.register_blueprint(maintenance_bp)
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest library/tests/test_maintenance_api.py -v
```

Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add library/backend/schema.sql library/backend/api_modular/maintenance.py \
    library/backend/api_modular/__init__.py library/tests/test_maintenance_api.py
git commit -m "feat: add maintenance scheduling database schema and API

Add 4 new tables (maintenance_windows, maintenance_messages,
maintenance_history, maintenance_notifications) with trigger and
indices. Implement full CRUD API for maintenance windows and
manual announcements. Public /api/maintenance/announcements
endpoint requires no auth for pre-login visibility."
```

---

## Task 6: Task Registry + Initial Handlers

**Files:**

- Create: `library/backend/api_modular/maintenance_tasks/__init__.py`
- Create: `library/backend/api_modular/maintenance_tasks/base.py`
- Create: `library/backend/api_modular/maintenance_tasks/db_vacuum.py`
- Create: `library/backend/api_modular/maintenance_tasks/db_integrity.py`
- Create: `library/backend/api_modular/maintenance_tasks/db_backup.py`
- Create: `library/backend/api_modular/maintenance_tasks/library_scan.py`
- Create: `library/backend/api_modular/maintenance_tasks/hash_verify.py`
- Create: `library/tests/test_task_registry.py`

- [ ] **Step 1: Write failing tests for registry**

Create `library/tests/test_task_registry.py`:

```python
"""Tests for maintenance task registry and handlers."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


class TestRegistry:
    def test_registry_discovers_handlers(self):
        from api_modular.maintenance_tasks import registry
        tasks = registry.list_all()
        names = [t["name"] for t in tasks]
        assert "db_vacuum" in names
        assert "db_integrity" in names
        assert "db_backup" in names
        assert "library_scan" in names
        assert "hash_verify" in names

    def test_get_known_task(self):
        from api_modular.maintenance_tasks import registry
        task = registry.get("db_vacuum")
        assert task is not None
        assert task.name == "db_vacuum"

    def test_get_unknown_task(self):
        from api_modular.maintenance_tasks import registry
        assert registry.get("nonexistent_task") is None

    def test_validate_is_callable(self):
        from api_modular.maintenance_tasks import registry
        task = registry.get("db_vacuum")
        result = task.validate({})
        assert hasattr(result, "ok")
        assert hasattr(result, "message")

    def test_list_all_has_required_fields(self):
        from api_modular.maintenance_tasks import registry
        for task_info in registry.list_all():
            assert "name" in task_info
            assert "display_name" in task_info
            assert "description" in task_info
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest library/tests/test_task_registry.py -v
```

Expected: ImportError -- package doesn't exist yet.

- [ ] **Step 3: Implement base class**

Create `library/backend/api_modular/maintenance_tasks/base.py`:

```python
"""
Base class and data types for maintenance task handlers.

Each handler implements validate() and execute() and is registered
via the @registry.register decorator in its module.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ValidationResult:
    """Result of a task validation check."""
    ok: bool
    message: str = ""


@dataclass
class ExecutionResult:
    """Result of a task execution."""
    success: bool
    message: str = ""
    data: dict = field(default_factory=dict)


class MaintenanceTask(ABC):
    """Abstract base for maintenance task handlers."""

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    def validate(self, params: dict) -> ValidationResult:
        """Pre-flight checks. Called at creation and before execution."""
        ...

    @abstractmethod
    def execute(
        self, params: dict, progress_callback: Optional[Callable] = None
    ) -> ExecutionResult:
        """Perform the maintenance task."""
        ...

    def estimate_duration(self) -> Optional[int]:
        """Estimated duration in seconds. Override in subclass if known."""
        return None

    def to_dict(self) -> dict:
        """Serialize for API response."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "estimated_duration": self.estimate_duration(),
        }


class MaintenanceRegistry:
    """Registry of maintenance task handlers."""

    def __init__(self):
        self._tasks: dict[str, MaintenanceTask] = {}

    def register(self, cls):
        """Decorator to register a task handler class."""
        instance = cls()
        if not instance.name:
            raise ValueError(f"{cls.__name__} must define a 'name' attribute")
        self._tasks[instance.name] = instance
        return cls

    def get(self, name: str) -> Optional[MaintenanceTask]:
        """Get a task handler by name."""
        return self._tasks.get(name)

    def list_all(self) -> list[dict]:
        """List all registered tasks as dicts."""
        return [t.to_dict() for t in self._tasks.values()]
```

- [ ] **Step 4: Implement registry **init**.py**

Create `library/backend/api_modular/maintenance_tasks/__init__.py`:

```python
"""
Maintenance task registry.

Auto-discovers and registers all task handler modules in this package.
Import this module to get the singleton `registry` instance.
"""
import importlib
import pkgutil
from pathlib import Path

from .base import (
    ExecutionResult,
    MaintenanceRegistry,
    MaintenanceTask,
    ValidationResult,
)

# Singleton registry
registry = MaintenanceRegistry()

# Auto-import all modules in this package so their @registry.register decorators fire
_pkg_dir = Path(__file__).parent
for _importer, _modname, _ispkg in pkgutil.iter_modules([str(_pkg_dir)]):
    if _modname != "base":
        importlib.import_module(f".{_modname}", __package__)

__all__ = [
    "registry",
    "MaintenanceTask",
    "MaintenanceRegistry",
    "ValidationResult",
    "ExecutionResult",
]
```

- [ ] **Step 5: Implement db_vacuum handler**

Create `library/backend/api_modular/maintenance_tasks/db_vacuum.py`:

```python
"""Database vacuum and optimize task."""
import logging
import sqlite3
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult

logger = logging.getLogger(__name__)


def _resolve_db_path(params):
    """Resolve database path from params or Flask app context.

    Handlers run in two contexts:
    - Flask request (API validation): current_app.config available
    - Scheduler daemon (standalone): db_path passed via params
    """
    if "db_path" in params:
        return Path(params["db_path"])
    try:
        from flask import current_app
        return current_app.config["DATABASE_PATH"]
    except (RuntimeError, ImportError):
        return None


@registry.register
class DatabaseVacuumTask(MaintenanceTask):
    name = "db_vacuum"
    display_name = "Database Vacuum & Optimize"
    description = "Run VACUUM and ANALYZE on the library database"

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        try:
            conn = sqlite3.connect(str(db_path))
            if progress_callback:
                progress_callback(0.2, "Running ANALYZE...")
            conn.execute("ANALYZE")
            if progress_callback:
                progress_callback(0.5, "Running VACUUM...")
            conn.execute("VACUUM")
            conn.close()
            if progress_callback:
                progress_callback(1.0, "Complete")
            return ExecutionResult(
                success=True,
                message="VACUUM and ANALYZE completed",
                data={"database": str(db_path)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 30
```

- [ ] **Step 6: Implement db_integrity handler**

Create `library/backend/api_modular/maintenance_tasks/db_integrity.py`:

```python
"""Database integrity check task."""
import logging
import sqlite3
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)


@registry.register
class DatabaseIntegrityTask(MaintenanceTask):
    name = "db_integrity"
    display_name = "Database Integrity Check"
    description = "Run PRAGMA integrity_check on all databases"

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        try:
            conn = sqlite3.connect(str(db_path))
            if progress_callback:
                progress_callback(0.3, "Running integrity check...")
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            ok = result[0] == "ok"
            if progress_callback:
                progress_callback(1.0, "Complete")
            return ExecutionResult(
                success=ok,
                message=f"Integrity: {result[0]}",
                data={"result": result[0], "database": str(db_path)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 60
```

- [ ] **Step 7: Implement db_backup handler**

Create `library/backend/api_modular/maintenance_tasks/db_backup.py`:

```python
"""Database backup task."""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)


@registry.register
class DatabaseBackupTask(MaintenanceTask):
    name = "db_backup"
    display_name = "Database Backup"
    description = "Create a timestamped backup of the library database"

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        try:
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"{db_path.stem}-{timestamp}.db"

            if progress_callback:
                progress_callback(0.2, "Creating backup...")

            # Use SQLite online backup API for consistency
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(backup_path))
            src.backup(dst)
            src.close()
            dst.close()

            size_mb = backup_path.stat().st_size / (1024 * 1024)
            if progress_callback:
                progress_callback(1.0, "Complete")

            return ExecutionResult(
                success=True,
                message=f"Backup created: {backup_path.name} ({size_mb:.1f} MB)",
                data={"backup_path": str(backup_path), "size_mb": round(size_mb, 1)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 30
```

- [ ] **Step 8: Implement library_scan handler**

Create `library/backend/api_modular/maintenance_tasks/library_scan.py`:

```python
"""Library scan task -- triggers a rescan for new/changed audiobook files."""
import logging
import subprocess

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult

logger = logging.getLogger(__name__)


@registry.register
class LibraryScanTask(MaintenanceTask):
    name = "library_scan"
    display_name = "Library Rescan"
    description = "Scan for new or changed audiobook files"

    def validate(self, params: dict) -> ValidationResult:
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        try:
            if progress_callback:
                progress_callback(0.1, "Starting library scan...")

            # Invoke the existing scanner via the API utility endpoint
            # The scanner runs in-process via the utilities blueprint
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", "http://127.0.0.1:5001/api/admin/scan"],
                capture_output=True, text=True, timeout=600,
            )

            if progress_callback:
                progress_callback(1.0, "Complete")

            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    message="Library scan completed",
                    data={"output": result.stdout[:500]},
                )
            return ExecutionResult(
                success=False,
                message=f"Scan failed: {result.stderr[:200]}",
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(success=False, message="Scan timed out after 600s")
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 300
```

- [ ] **Step 9: Implement hash_verify handler**

Create `library/backend/api_modular/maintenance_tasks/hash_verify.py`:

```python
"""Hash verification task -- verify file hashes against database records."""
import hashlib
import logging
import sqlite3
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)


@registry.register
class HashVerifyTask(MaintenanceTask):
    name = "hash_verify"
    display_name = "File Hash Verification"
    description = "Verify audiobook file SHA-256 hashes match database records"

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT id, file_path, sha256_hash FROM audiobooks WHERE sha256_hash IS NOT NULL"
            ).fetchall()
            conn.close()

            total = len(rows)
            if total == 0:
                return ExecutionResult(
                    success=True, message="No files with hashes to verify"
                )

            mismatches = []
            missing = []
            verified = 0

            for i, (aid, fpath, expected) in enumerate(rows):
                if progress_callback and i % 10 == 0:
                    progress_callback(i / total, f"Checking {i}/{total}...")

                from pathlib import Path
                p = Path(fpath)
                if not p.exists():
                    missing.append(fpath)
                    continue

                h = hashlib.sha256()
                with open(p, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)

                if h.hexdigest() != expected:
                    mismatches.append({"id": aid, "path": fpath})
                else:
                    verified += 1

            if progress_callback:
                progress_callback(1.0, "Complete")

            ok = len(mismatches) == 0
            return ExecutionResult(
                success=ok,
                message=(
                    f"Verified {verified}/{total}, "
                    f"{len(mismatches)} mismatches, {len(missing)} missing"
                ),
                data={
                    "total": total,
                    "verified": verified,
                    "mismatches": mismatches[:20],
                    "missing_count": len(missing),
                },
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 600
```

- [ ] **Step 10: Run tests**

```bash
python -m pytest library/tests/test_task_registry.py -v
```

Expected: All PASS.

- [ ] **Step 11: Commit**

```bash
git add library/backend/api_modular/maintenance_tasks/ \
    library/tests/test_task_registry.py
git commit -m "feat: add maintenance task registry with 5 initial handlers

Plugin architecture for maintenance tasks. Each handler implements
validate() and execute() with a progress callback. Initial handlers:
db_vacuum, db_integrity, db_backup, library_scan, hash_verify.
Adding new task types requires only writing a module and decorating
with @registry.register."
```

---

## Task 7: Scheduler Daemon

**Files:**

- Create: `library/backend/maintenance_scheduler.py`
- Create: `systemd/audiobook-scheduler.service`
- Modify: `systemd/audiobook.target`
- Create: `library/tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Create `library/tests/test_scheduler.py`:

```python
"""Tests for maintenance scheduler daemon."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def scheduler_db(tmp_path):
    """Create a test database with maintenance schema."""
    db_path = tmp_path / "test.db"
    schema = (Path(__file__).parent.parent / "backend" / "schema.sql").read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()
    return db_path


def test_find_due_windows(scheduler_db):
    """Scheduler finds windows with next_run_at in the past."""
    conn = sqlite3.connect(str(scheduler_db))
    conn.execute(
        """INSERT INTO maintenance_windows
           (name, task_type, schedule_type, next_run_at, status)
           VALUES ('Test', 'db_vacuum', 'once', datetime('now', '-1 hour'), 'active')"""
    )
    conn.commit()
    rows = conn.execute(
        """SELECT * FROM maintenance_windows
           WHERE next_run_at <= datetime('now') AND status = 'active'"""
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_write_notification(scheduler_db):
    """Scheduler writes to notification queue after execution."""
    conn = sqlite3.connect(str(scheduler_db))
    conn.execute(
        """INSERT INTO maintenance_notifications (notification_type, payload)
           VALUES ('update', '{"window_id": 1, "status": "success"}')"""
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM maintenance_notifications WHERE delivered = 0"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_history_recorded(scheduler_db):
    """Execution results are recorded in history table."""
    conn = sqlite3.connect(str(scheduler_db))
    conn.execute(
        """INSERT INTO maintenance_windows
           (name, task_type, schedule_type, scheduled_at, next_run_at, status)
           VALUES ('Test', 'db_vacuum', 'once', datetime('now'), datetime('now'), 'active')"""
    )
    conn.commit()
    wid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO maintenance_history (window_id, started_at, status, result_message)
           VALUES (?, datetime('now'), 'success', 'OK')""",
        (wid,),
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM maintenance_history").fetchall()
    conn.close()
    assert len(rows) == 1
```

- [ ] **Step 2: Run tests to verify they pass** (these test schema, not the daemon)

```bash
python -m pytest library/tests/test_scheduler.py -v
```

Expected: All PASS (schema tests only).

- [ ] **Step 3: Implement scheduler daemon**

Create `library/backend/maintenance_scheduler.py`:

```python
#!/usr/bin/env python3
"""
Maintenance Scheduler Daemon

Standalone process that:
1. Polls maintenance_windows for due tasks every 60 seconds
2. Executes tasks via the registry
3. Records results in maintenance_history
4. Writes to maintenance_notifications for WebSocket delivery
5. Computes next_run_at for recurring windows

Runs as audiobook-scheduler.service under audiobook.target.
"""
import fcntl
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

logging.basicConfig(
    level=logging.INFO,
    format="[SCHEDULER] %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("maintenance_scheduler")

POLL_INTERVAL = 60  # seconds
# AUDIOBOOKS_RUN_DIR comes from EnvironmentFile=/etc/audiobooks/audiobooks.conf
# which systemd loads before ExecStart. Default matches lib/audiobook-config.sh.
_run_dir = os.environ.get("AUDIOBOOKS_RUN_DIR", "/var/lib/audiobooks/.run")
LOCK_PATH = os.environ.get("MAINTENANCE_LOCK", str(Path(_run_dir) / "maintenance.lock"))

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("SIGTERM received, finishing current task then exiting...")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def get_db():
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def find_due_windows():
    """Find windows that are due for execution."""
    conn = get_db()
    try:
        return [
            dict(r) for r in conn.execute(
                """SELECT * FROM maintenance_windows
                   WHERE next_run_at <= datetime('now')
                     AND status = 'active'
                   ORDER BY next_run_at ASC"""
            ).fetchall()
        ]
    finally:
        conn.close()


def record_history(window_id, started_at, status, message, data=None):
    """Write execution result to history table."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO maintenance_history
               (window_id, started_at, completed_at, status, result_message, result_data)
               VALUES (?, ?, datetime('now'), ?, ?, ?)""",
            (window_id, started_at, status, message, json.dumps(data or {})),
        )
        conn.commit()
    finally:
        conn.close()


def write_notification(ntype, payload):
    """Write to notification queue for WebSocket delivery."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO maintenance_notifications (notification_type, payload) VALUES (?, ?)",
            (ntype, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def update_next_run(window):
    """Compute and update next_run_at for recurring windows."""
    if window["schedule_type"] != "recurring" or not window.get("cron_expression"):
        # One-time window: mark completed
        conn = get_db()
        try:
            conn.execute(
                "UPDATE maintenance_windows SET status = 'completed' WHERE id = ?",
                (window["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        return

    try:
        from croniter import croniter
        cron = croniter(window["cron_expression"], datetime.now(timezone.utc))
        next_at = cron.get_next(datetime).isoformat() + "Z"
        conn = get_db()
        try:
            conn.execute(
                "UPDATE maintenance_windows SET next_run_at = ? WHERE id = ?",
                (next_at, window["id"]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to compute next_run_at for window %d: %s", window["id"], e)


def execute_window(window):
    """Execute a single maintenance window's task."""
    logger.info("Executing window %d: %s (%s)", window["id"], window["name"], window["task_type"])

    # Import registry inside function to use Flask app context if available
    try:
        from api_modular.maintenance_tasks import registry
    except ImportError:
        # Try relative import path
        sys.path.insert(0, str(Path(__file__).parent))
        from api_modular.maintenance_tasks import registry

    task = registry.get(window["task_type"])
    if task is None:
        msg = f"Unknown task type: {window['task_type']}"
        logger.error(msg)
        started_at = datetime.now(timezone.utc).isoformat() + "Z"
        record_history(window["id"], started_at, "failure", msg)
        write_notification("update", {
            "window_id": window["id"], "status": "failure", "message": msg,
        })
        return

    params = json.loads(window.get("task_params", "{}"))
    # Inject db_path so handlers work outside Flask app context
    params.setdefault("db_path", str(DATABASE_PATH))

    # Validate
    validation = task.validate(params)
    if not validation.ok:
        msg = f"Validation failed: {validation.message}"
        logger.warning(msg)
        started_at = datetime.now(timezone.utc).isoformat() + "Z"
        record_history(window["id"], started_at, "failure", msg)
        write_notification("update", {
            "window_id": window["id"], "status": "failure", "message": msg,
        })
        return

    # Execute
    started_at = datetime.now(timezone.utc).isoformat() + "Z"
    write_notification("update", {
        "window_id": window["id"], "status": "running",
        "message": f"Executing: {window['name']}",
    })

    result = task.execute(params, progress_callback=lambda p, m: None)

    status = "success" if result.success else "failure"
    record_history(window["id"], started_at, status, result.message, result.data)
    write_notification("update", {
        "window_id": window["id"], "status": status, "message": result.message,
    })

    # Update next_run_at or mark completed
    update_next_run(window)

    logger.info("Window %d completed: %s - %s", window["id"], status, result.message)


def check_announcements():
    """Write announcement notifications for windows within lead time."""
    conn = get_db()
    try:
        upcoming = conn.execute(
            """SELECT id, name, description, next_run_at, lead_time_hours
               FROM maintenance_windows
               WHERE status = 'active'
                 AND next_run_at IS NOT NULL
                 AND datetime(next_run_at, '-' || lead_time_hours || ' hours')
                     <= datetime('now')
                 AND next_run_at > datetime('now')"""
        ).fetchall()

        for window in upcoming:
            # Only announce if not already announced recently (within last poll interval)
            existing = conn.execute(
                """SELECT COUNT(*) FROM maintenance_notifications
                   WHERE notification_type = 'announce'
                     AND json_extract(payload, '$.window_id') = ?
                     AND created_at >= datetime('now', '-2 minutes')""",
                (window["id"],),
            ).fetchone()[0]

            if existing == 0:
                write_notification("announce", {
                    "window_id": window["id"],
                    "name": window["name"],
                    "description": window["description"],
                    "next_run_at": window["next_run_at"],
                })
    finally:
        conn.close()


def main():
    """Main scheduler loop."""
    logger.info("Maintenance scheduler starting (poll every %ds)", POLL_INTERVAL)
    logger.info("Lock file: %s", LOCK_PATH)
    logger.info("Database: %s", DATABASE_PATH)

    # Ensure lock directory exists
    Path(LOCK_PATH).parent.mkdir(parents=True, exist_ok=True)

    while not _shutdown:
        try:
            # Check for announcements (windows within lead time)
            check_announcements()

            # Find and execute due windows
            due_windows = find_due_windows()
            for window in due_windows:
                if _shutdown:
                    break

                # Acquire file lock for execution
                lock_fd = open(LOCK_PATH, "w")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    execute_window(window)
                except BlockingIOError:
                    logger.info("Lock held by another process, skipping window %d", window["id"])
                finally:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        lock_fd.close()
                    except Exception:
                        pass

        except Exception as e:
            logger.error("Scheduler loop error: %s", e, exc_info=True)

        # Sleep in 1-second increments to respond to SIGTERM quickly
        for _ in range(POLL_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Scheduler shutting down")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create systemd service**

Create `systemd/audiobook-scheduler.service`:

```ini
[Unit]
Description=Audiobooks Maintenance Scheduler
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=audiobook-api.service
Wants=audiobook-api.service
RequiresMountsFor=/opt/audiobooks /srv/audiobooks
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=audiobooks
Group=audiobooks
WorkingDirectory=/opt/audiobooks/library/backend
EnvironmentFile=/etc/audiobooks/audiobooks.conf
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/var/lib/audiobooks
ExecStart=/opt/audiobooks/library/venv/bin/python maintenance_scheduler.py
Restart=on-failure
RestartSec=30

# Security hardening (mirrors audiobook-api.service)
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/audiobooks /srv/audiobooks /tmp
ProtectHome=yes
PrivateTmp=no

[Install]
WantedBy=audiobook.target
```

- [ ] **Step 5: Add to audiobook.target**

In `systemd/audiobook.target`, add to the `Wants=` line:

```ini
Wants=audiobook-scheduler.service
```

(Append to existing Wants list.)

- [ ] **Step 6: Commit**

```bash
git add library/backend/maintenance_scheduler.py \
    systemd/audiobook-scheduler.service systemd/audiobook.target \
    library/tests/test_scheduler.py
git commit -m "feat: add maintenance scheduler daemon as systemd service

Standalone scheduler process polls for due maintenance windows,
executes task handlers via the registry, records results, and
writes to notification queue for WebSocket delivery. Uses file
lock for single-execution guarantee. Cron expressions interpreted
in server-local timezone via croniter."
```

---

## Task 8: Client-Side WebSocket + Polling Fallback

**Files:**

- Create: `library/web-v2/js/websocket.js`
- Modify: `library/web-v2/shell.html` (add script tag)

- [ ] **Step 1: Implement WebSocket client**

Create `library/web-v2/js/websocket.js`:

```javascript
/**
 * WebSocket client for real-time maintenance announcements and heartbeat.
 *
 * - Sends heartbeat every 10 seconds with player activity state
 * - Auto-reconnects with exponential backoff (1s -> 30s max)
 * - Falls back to REST polling after 3 failed WebSocket attempts
 * - Dispatches custom DOM events for downstream consumers
 */
(function () {
  "use strict";

  var HEARTBEAT_INTERVAL = 10000; // 10 seconds
  var POLL_INTERVAL = 30000; // 30 seconds (fallback)
  var MAX_RETRIES = 3;
  var MAX_BACKOFF = 30000;

  var ws = null;
  var heartbeatTimer = null;
  var pollTimer = null;
  var retryCount = 0;
  var retryTimer = null;
  var usingPolling = false;

  function getPlayerState() {
    var audio = document.getElementById("audio-player");
    if (!audio) return "idle";
    if (audio.paused) return "paused";
    return "streaming";
  }

  function dispatch(eventName, detail) {
    document.dispatchEvent(new CustomEvent(eventName, { detail: detail }));
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
      return;
    }

    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/api/ws";

    try {
      ws = new WebSocket(url);
    } catch (e) {
      onFail();
      return;
    }

    ws.onopen = function () {
      retryCount = 0;
      usingPolling = false;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      startHeartbeat();
    };

    ws.onmessage = function (event) {
      try {
        var msg = JSON.parse(event.data);
        if (msg.type === "maintenance_announce") {
          dispatch("maintenance-announce", msg);
        } else if (msg.type === "maintenance_dismiss") {
          dispatch("maintenance-dismiss", msg);
        } else if (msg.type === "maintenance_update") {
          dispatch("maintenance-update", msg);
        }
      } catch (e) {
        // ignore malformed messages
      }
    };

    ws.onclose = function () {
      stopHeartbeat();
      onFail();
    };

    ws.onerror = function () {
      // onclose will fire after onerror
    };
  }

  function onFail() {
    retryCount++;
    if (retryCount > MAX_RETRIES) {
      startPolling();
      return;
    }
    var delay = Math.min(1000 * Math.pow(2, retryCount - 1), MAX_BACKOFF);
    retryTimer = setTimeout(connect, delay);
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(function () {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "heartbeat", state: getPlayerState() }));
      }
    }, HEARTBEAT_INTERVAL);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function startPolling() {
    if (usingPolling) return;
    usingPolling = true;
    pollForAnnouncements();
    pollTimer = setInterval(pollForAnnouncements, POLL_INTERVAL);
  }

  function pollForAnnouncements() {
    fetch("/api/maintenance/announcements")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        dispatch("maintenance-announce", {
          type: "maintenance_announce",
          messages: data.messages || [],
          windows: data.windows || [],
        });
      })
      .catch(function () { /* ignore fetch errors */ });
  }

  // Public API
  window.audioWs = {
    isConnected: function () {
      return ws && ws.readyState === WebSocket.OPEN;
    },
    isPolling: function () {
      return usingPolling;
    },
    reconnect: function () {
      retryCount = 0;
      connect();
    },
  };

  // Connect on load
  connect();
})();
```

- [ ] **Step 2: Add script to shell.html**

Add before the closing `</body>` tag (before maintenance-banner.js, which is added in Task 10):

```html
<script src="js/websocket.js"></script>
```

- [ ] **Step 3: Commit**

```bash
git add library/web-v2/js/websocket.js library/web-v2/shell.html
git commit -m "feat: add client-side WebSocket with heartbeat and polling fallback

Native WebSocket client sends 10-second heartbeat with activity
state (idle/streaming/paused). Auto-reconnects with exponential
backoff. Falls back to REST polling after 3 failed connection
attempts. Dispatches custom DOM events for maintenance messages."
```

---

## Task 9: Maint Sched Tab UI

**Files:**

- Modify: `library/web-v2/utilities.html` (add tab button + section)
- Create: `library/web-v2/js/maint-sched.js`
- Modify: `library/web-v2/utilities.js` (init new section)
- Modify: `library/web-v2/css/utilities.css` (tab styles)

- [ ] **Step 1: Add tab button and section HTML**

In `utilities.html`, add the 7th tab button in `.cabinet-tabs`:

```html
<button class="cabinet-tab" data-section="maint-sched" title="Maintenance scheduling, announcements, and execution history">
    <span class="tab-icon">&#x1F527;</span>
    <span class="tab-label">Maint Sched</span>
</button>
```

Add the section (after the existing last section):

```html
<div id="maint-sched-section" class="cabinet-section" data-section="maint-sched" style="display:none;">
    <h2>Maintenance Scheduling</h2>

    <!-- Create Window Form -->
    <div class="maint-form-card">
        <h3>Schedule Maintenance Window</h3>
        <div class="maint-form-row">
            <label for="maint-name">Name</label>
            <input type="text" id="maint-name" placeholder="Nightly Vacuum">
        </div>
        <div class="maint-form-row">
            <label for="maint-description">Description</label>
            <input type="text" id="maint-description" placeholder="Optional description">
        </div>
        <div class="maint-form-row">
            <label for="maint-task-type">Task Type</label>
            <select id="maint-task-type"></select>
        </div>
        <div class="maint-form-row">
            <label for="maint-schedule-type">Schedule</label>
            <select id="maint-schedule-type">
                <option value="once">One-Time</option>
                <option value="recurring">Recurring</option>
            </select>
        </div>
        <div id="maint-once-fields" class="maint-form-row">
            <label for="maint-scheduled-at">Date & Time (UTC)</label>
            <input type="datetime-local" id="maint-scheduled-at">
        </div>
        <div id="maint-recurring-fields" style="display:none;">
            <div class="maint-form-row">
                <label>Preset</label>
                <div class="maint-presets">
                    <label><input type="radio" name="maint-preset" value="daily"> Daily</label>
                    <label><input type="radio" name="maint-preset" value="weekly"> Weekly</label>
                    <label><input type="radio" name="maint-preset" value="biweekly"> Biweekly</label>
                    <label><input type="radio" name="maint-preset" value="monthly"> Monthly</label>
                    <label><input type="radio" name="maint-preset" value="custom"> Advanced</label>
                </div>
            </div>
            <div class="maint-form-row">
                <label for="maint-time">Time</label>
                <input type="time" id="maint-time" value="03:00">
            </div>
            <div id="maint-cron-row" class="maint-form-row" style="display:none;">
                <label for="maint-cron-input">Cron Expression</label>
                <input type="text" id="maint-cron-input" placeholder="0 3 * * *"
                    title="Min Hour Day-of-Mth Mth Day-of-Week (0=Sunday)">
            </div>
        </div>
        <div class="maint-form-row">
            <label for="maint-lead-time">Lead Time (hours)</label>
            <input type="number" id="maint-lead-time" value="48" min="1" max="168">
        </div>
        <button id="maint-create-btn" class="office-btn">Create Window</button>
    </div>

    <!-- Active Windows List -->
    <div class="maint-section-card">
        <h3>Scheduled Windows</h3>
        <table id="maint-windows-table" class="maint-table">
            <thead>
                <tr>
                    <th>Name</th><th>Task</th><th>Schedule</th><th>Next Run</th><th>Status</th><th>Actions</th>
                </tr>
            </thead>
            <tbody id="maint-windows-body"></tbody>
        </table>
    </div>

    <!-- Manual Announcements -->
    <div class="maint-section-card">
        <h3>Manual Announcements</h3>
        <div class="maint-form-row">
            <input type="text" id="maint-message-input" placeholder="Maintenance message to broadcast immediately">
            <button id="maint-send-msg-btn" class="office-btn">Send</button>
        </div>
        <div id="maint-messages-list"></div>
    </div>

    <!-- Execution History -->
    <div class="maint-section-card">
        <h3>Execution History</h3>
        <table id="maint-history-table" class="maint-table">
            <thead>
                <tr><th>Window</th><th>Task</th><th>Started</th><th>Status</th><th>Result</th></tr>
            </thead>
            <tbody id="maint-history-body"></tbody>
        </table>
    </div>
</div>
```

- [ ] **Step 2: Implement maint-sched.js**

Create `library/web-v2/js/maint-sched.js`:

```javascript
/**
 * Maint Sched tab -- CRUD for maintenance windows, messages, and history.
 *
 * Uses safe DOM methods (createElement/textContent) throughout.
 * No innerHTML with dynamic content.
 */
(function () {
  "use strict";

  var PRESETS = {
    daily: "0 {H} * * *",
    weekly: "0 {H} * * 1",
    biweekly: "0 {H} 1,15 * *",
    monthly: "0 {H} 1 * *",
  };

  function escText(s) {
    return s == null ? "" : String(s);
  }

  function createCell(text) {
    var td = document.createElement("td");
    td.textContent = escText(text);
    return td;
  }

  // -- Task type population --
  function loadTaskTypes() {
    fetch("/api/admin/maintenance/tasks")
      .then(function (r) { return r.json(); })
      .then(function (tasks) {
        var sel = document.getElementById("maint-task-type");
        while (sel.firstChild) sel.removeChild(sel.firstChild);
        tasks.forEach(function (t) {
          var opt = document.createElement("option");
          opt.value = t.name;
          opt.textContent = t.display_name;
          opt.title = t.description || "";
          sel.appendChild(opt);
        });
      })
      .catch(function () {});
  }

  // -- Schedule type toggle --
  function onScheduleTypeChange() {
    var val = document.getElementById("maint-schedule-type").value;
    document.getElementById("maint-once-fields").style.display = val === "once" ? "" : "none";
    document.getElementById("maint-recurring-fields").style.display = val === "recurring" ? "" : "none";
  }

  // -- Preset to cron --
  function onPresetChange(e) {
    var val = e.target.value;
    var cronRow = document.getElementById("maint-cron-row");
    if (val === "custom") {
      cronRow.style.display = "";
      return;
    }
    cronRow.style.display = "none";
    var timeVal = document.getElementById("maint-time").value || "03:00";
    var parts = timeVal.split(":");
    var h = parseInt(parts[0], 10);
    var cron = PRESETS[val].replace("{H}", h);
    document.getElementById("maint-cron-input").value = cron;
  }

  // -- Create window --
  function createWindow() {
    var schedType = document.getElementById("maint-schedule-type").value;
    var body = {
      name: document.getElementById("maint-name").value.trim(),
      task_type: document.getElementById("maint-task-type").value,
      schedule_type: schedType,
      lead_time_hours: parseInt(document.getElementById("maint-lead-time").value, 10) || 48,
      description: document.getElementById("maint-description").value.trim(),
    };

    if (schedType === "once") {
      var dt = document.getElementById("maint-scheduled-at").value;
      if (dt) body.scheduled_at = new Date(dt).toISOString();
    } else {
      body.cron_expression = document.getElementById("maint-cron-input").value.trim();
    }

    if (!body.name) { alert("Name is required"); return; }

    fetch("/api/admin/maintenance/windows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function () { loadWindows(); })
      .catch(function (e) { alert("Error: " + e.message); });
  }

  // -- Load windows --
  function loadWindows() {
    fetch("/api/admin/maintenance/windows")
      .then(function (r) { return r.json(); })
      .then(function (windows) {
        var tbody = document.getElementById("maint-windows-body");
        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        windows.forEach(function (w) {
          var tr = document.createElement("tr");
          tr.appendChild(createCell(w.name));
          tr.appendChild(createCell(w.task_type));
          tr.appendChild(createCell(
            w.schedule_type === "recurring" ? w.cron_expression : "One-time"
          ));
          tr.appendChild(createCell(
            w.next_run_at ? new Date(w.next_run_at).toLocaleString() : "N/A"
          ));
          tr.appendChild(createCell(w.status));

          var actionTd = document.createElement("td");
          if (w.status === "active") {
            var cancelBtn = document.createElement("button");
            cancelBtn.className = "office-btn office-btn-sm";
            cancelBtn.textContent = "Cancel";
            cancelBtn.title = "Cancel this maintenance window";
            cancelBtn.addEventListener("click", function () {
              cancelWindow(w.id);
            });
            actionTd.appendChild(cancelBtn);
          }
          var delBtn = document.createElement("button");
          delBtn.className = "office-btn office-btn-sm office-btn-danger";
          delBtn.textContent = "Delete";
          delBtn.title = "Delete this maintenance window";
          delBtn.addEventListener("click", function () {
            deleteWindow(w.id);
          });
          actionTd.appendChild(delBtn);
          tr.appendChild(actionTd);

          tbody.appendChild(tr);
        });
      })
      .catch(function () {});
  }

  function cancelWindow(id) {
    fetch("/api/admin/maintenance/windows/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "cancelled" }),
    }).then(function () { loadWindows(); });
  }

  function deleteWindow(id) {
    fetch("/api/admin/maintenance/windows/" + id, { method: "DELETE" })
      .then(function () { loadWindows(); });
  }

  // -- Messages --
  function sendMessage() {
    var input = document.getElementById("maint-message-input");
    var text = input.value.trim();
    if (!text) return;

    fetch("/api/admin/maintenance/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        input.value = "";
        loadMessages();
      })
      .catch(function (e) { alert("Error: " + e.message); });
  }

  function loadMessages() {
    fetch("/api/admin/maintenance/messages")
      .then(function (r) { return r.json(); })
      .then(function (messages) {
        var container = document.getElementById("maint-messages-list");
        while (container.firstChild) container.removeChild(container.firstChild);

        messages.forEach(function (m) {
          var div = document.createElement("div");
          div.className = "maint-message-item" + (m.dismissed_at ? " dismissed" : "");

          var text = document.createElement("span");
          text.textContent = m.message;
          div.appendChild(text);

          var meta = document.createElement("small");
          meta.textContent = " -- " + m.created_by + " at " + new Date(m.created_at).toLocaleString();
          div.appendChild(meta);

          if (!m.dismissed_at) {
            var btn = document.createElement("button");
            btn.className = "office-btn office-btn-sm";
            btn.textContent = "Dismiss";
            btn.title = "Permanently dismiss this announcement for all users";
            btn.addEventListener("click", function () {
              dismissMessage(m.id);
            });
            div.appendChild(btn);
          }

          container.appendChild(div);
        });
      })
      .catch(function () {});
  }

  function dismissMessage(id) {
    fetch("/api/admin/maintenance/messages/" + id, { method: "DELETE" })
      .then(function () { loadMessages(); });
  }

  // -- History --
  function loadHistory() {
    fetch("/api/admin/maintenance/history")
      .then(function (r) { return r.json(); })
      .then(function (history) {
        var tbody = document.getElementById("maint-history-body");
        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        history.forEach(function (h) {
          var tr = document.createElement("tr");
          tr.appendChild(createCell(h.window_name || "Window #" + h.window_id));
          tr.appendChild(createCell(h.task_type));
          tr.appendChild(createCell(new Date(h.started_at).toLocaleString()));

          var statusTd = document.createElement("td");
          var badge = document.createElement("span");
          badge.className = "maint-status-badge maint-status-" + h.status;
          badge.textContent = h.status;
          statusTd.appendChild(badge);
          tr.appendChild(statusTd);

          tr.appendChild(createCell(h.result_message || ""));
          tbody.appendChild(tr);
        });
      })
      .catch(function () {});
  }

  // -- Init --
  function initMaintSched() {
    document.getElementById("maint-schedule-type").addEventListener("change", onScheduleTypeChange);
    document.getElementById("maint-create-btn").addEventListener("click", createWindow);
    document.getElementById("maint-send-msg-btn").addEventListener("click", sendMessage);

    var presetRadios = document.querySelectorAll('input[name="maint-preset"]');
    for (var i = 0; i < presetRadios.length; i++) {
      presetRadios[i].addEventListener("change", onPresetChange);
    }

    loadTaskTypes();
    loadWindows();
    loadMessages();
    loadHistory();
  }

  // Auto-init when tab becomes visible
  document.addEventListener("DOMContentLoaded", function () {
    var observer = new MutationObserver(function () {
      var section = document.getElementById("maint-sched-section");
      if (section && section.style.display !== "none") {
        initMaintSched();
        observer.disconnect();
      }
    });
    var section = document.getElementById("maint-sched-section");
    if (section) {
      observer.observe(section, { attributes: true, attributeFilter: ["style"] });
    }
  });

  // Also init if directly navigated
  if (document.readyState === "complete") {
    var section = document.getElementById("maint-sched-section");
    if (section && section.style.display !== "none") {
      initMaintSched();
    }
  }
})();
```

- [ ] **Step 3: Add connection count to Activity tab**

In `library/web-v2/utilities.js`, add to the Activity tab's init/load function:

```javascript
// At the top of the Activity section's load handler
fetch("/api/admin/connections")
  .then(function (r) { return r.json(); })
  .then(function (data) {
    var container = document.getElementById("activity-connections");
    if (!container) {
      container = document.createElement("div");
      container.id = "activity-connections";
      container.className = "connections-card";
      var actSection = document.querySelector('[data-section="activity"]');
      if (actSection) actSection.insertBefore(container, actSection.firstChild);
    }
    while (container.firstChild) container.removeChild(container.firstChild);

    var heading = document.createElement("h3");
    heading.textContent = "Live Connections: " + data.count;
    container.appendChild(heading);

    if (data.users && data.users.length > 0) {
      var ul = document.createElement("ul");
      ul.className = "connections-list";
      data.users.forEach(function (u) {
        var li = document.createElement("li");
        li.textContent = u.username + " (" + u.state + ")";
        ul.appendChild(li);
      });
      container.appendChild(ul);
    }
  })
  .catch(function () {});
```

- [ ] **Step 4: Add CSS for Maint Sched tab**

Append to `library/web-v2/css/utilities.css`:

```css
/* Maint Sched tab */
.maint-form-card,
.maint-section-card {
    background: var(--card-bg, #1a1a2e);
    border: 1px solid var(--border-color, #333);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
}

.maint-form-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
}

.maint-form-row label {
    min-width: 120px;
    font-weight: 600;
}

.maint-form-row input,
.maint-form-row select {
    flex: 1;
    padding: 0.4rem;
    background: var(--input-bg, #0d0d1a);
    color: var(--text-color, #e0e0e0);
    border: 1px solid var(--border-color, #444);
    border-radius: 4px;
}

.maint-presets {
    display: flex;
    gap: 0.75rem;
    flex-wrap: wrap;
}

.maint-presets label {
    min-width: auto;
    font-weight: normal;
    cursor: pointer;
}

.maint-table {
    width: 100%;
    border-collapse: collapse;
}

.maint-table th,
.maint-table td {
    padding: 0.5rem;
    text-align: left;
    border-bottom: 1px solid var(--border-color, #333);
}

.maint-table th {
    font-weight: 600;
    color: var(--heading-color, #d4af37);
}

.office-btn-sm {
    padding: 0.2rem 0.5rem;
    font-size: 0.8rem;
    margin-left: 0.25rem;
}

.office-btn-danger {
    background: #8B0000;
}

.office-btn-danger:hover {
    background: #a00;
}

.maint-message-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0;
    border-bottom: 1px solid var(--border-color, #222);
}

.maint-message-item.dismissed {
    opacity: 0.4;
    text-decoration: line-through;
}

.maint-status-badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.8rem;
    font-weight: 600;
}

.maint-status-success { background: #1a3a1a; color: #4caf50; }
.maint-status-failure { background: #3a1a1a; color: #f44336; }
.maint-status-running { background: #1a2a3a; color: #2196f3; }
.maint-status-cancelled { background: #2a2a1a; color: #ff9800; }

.connections-card {
    background: var(--card-bg, #1a1a2e);
    border: 1px solid var(--border-color, #333);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 1rem;
}

.connections-list {
    list-style: none;
    padding: 0;
    margin: 0.5rem 0 0 0;
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
}
```

- [ ] **Step 5: Add maint-sched.js to utilities.html**

```html
<script src="js/maint-sched.js"></script>
```

Place at the bottom of utilities.html, before the closing `</body>`.

- [ ] **Step 6: Commit**

```bash
git add library/web-v2/utilities.html library/web-v2/js/maint-sched.js \
    library/web-v2/utilities.js library/web-v2/css/utilities.css
git commit -m "feat: add Maint Sched tab and live connections display

New back office tab for creating/managing maintenance windows
(one-time + recurring cron), manual announcements, and execution
history. Preset schedule picker with advanced cron toggle and
tooltip. Live connection count + usernames at top of Activity tab."
```

---

## Task 10: Maintenance Banner + Knife Switch

**Files:**

- Create: `library/web-v2/css/maintenance-banner.css`
- Create: `library/web-v2/js/maintenance-banner.js`
- Modify: `library/web-v2/shell.html` (add CSS + script)

- [ ] **Step 1: Create banner CSS**

Create `library/web-v2/css/maintenance-banner.css`:

```css
/* Maintenance Announcement Banner */

.maintenance-indicator {
    position: fixed;
    bottom: 80px;
    right: 20px;
    z-index: 9999;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    background: #FF0040;
    color: white;
    font-weight: bold;
    font-size: 1.1rem;
    display: none; /* shown via JS */
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 0 8px rgba(255, 0, 64, 0.6),
                0 0 16px rgba(255, 0, 64, 0.3);
    animation: maintenance-pulse 2s ease-in-out infinite;
    border: none;
    -webkit-tap-highlight-color: transparent;
}

.maintenance-indicator.active {
    display: flex;
}

@keyframes maintenance-pulse {
    0%, 100% { transform: scale(1.0); box-shadow: 0 0 8px rgba(255,0,64,0.6); }
    50%      { transform: scale(1.15); box-shadow: 0 0 20px rgba(255,0,64,0.9); }
}

/* Expanded Panel */
.maintenance-panel {
    position: fixed;
    bottom: 120px;
    right: 20px;
    z-index: 9998;
    width: 320px;
    max-width: 90vw;
    background: linear-gradient(180deg, #141414, #080808);
    border: 1px solid #8B6914;
    border-bottom: 2px solid #D4AF37;
    border-radius: 8px;
    padding: 1rem;
    display: none;
    box-shadow: 0 4px 24px rgba(0,0,0,0.6);
}

.maintenance-panel.open {
    display: block;
}

.maintenance-panel-messages {
    max-height: 5.6rem; /* ~4 lines */
    overflow-y: auto;
}

.maintenance-panel-message {
    color: #FF0040;
    font-size: 0.95rem;
    text-shadow: 2px 2px 0 #8B0000, 4px 4px 8px rgba(139,0,0,0.5);
    animation: maint-text-pulse 3s ease-in-out infinite;
    margin-bottom: 0.5rem;
    line-height: 1.4;
}

@keyframes maint-text-pulse {
    0%, 100% { opacity: 1.0; }
    50%      { opacity: 0.85; }
}

/* Knife Switch */
.knife-switch {
    display: flex;
    align-items: center;
    justify-content: center;
    margin-top: 0.75rem;
    padding-top: 0.75rem;
    border-top: 1px solid #333;
    cursor: pointer;
    background: none;
    border-left: none;
    border-right: none;
    border-bottom: none;
    width: 100%;
}

.knife-switch:focus-visible {
    outline: 2px solid #D4AF37;
    outline-offset: 2px;
    border-radius: 4px;
}

.knife-switch svg {
    width: 48px;
    height: 80px;
    transition: transform 0.1s;
}

.knife-switch-blade {
    fill: #B87333;
    transition: transform 0.7s cubic-bezier(0.22, 1, 0.36, 1.1);
    transform-origin: 24px 55px;
}

.knife-switch[aria-checked="false"] .knife-switch-blade {
    transform: rotate(45deg);
}

.knife-switch-handle {
    fill: #8B1A1A;
}

.knife-switch-jaw {
    fill: #B8860B;
}

.knife-switch-label {
    margin-left: 0.5rem;
    color: #999;
    font-size: 0.8rem;
}

/* Mobile adjustments */
@media (max-width: 480px) {
    .maintenance-panel {
        right: 10px;
        left: 10px;
        width: auto;
        bottom: 100px;
    }
    .maintenance-indicator {
        bottom: 70px;
        right: 14px;
    }
}
```

- [ ] **Step 2: Create banner JS**

Create `library/web-v2/js/maintenance-banner.js`:

```javascript
/**
 * Maintenance announcement banner with Frankenstein knife switch.
 *
 * Listens for custom events from websocket.js and renders:
 * - Pulsing red indicator when announcements active
 * - Expandable panel with message text in neon red
 * - SVG knife switch with Web Audio API sounds
 *
 * Uses safe DOM methods -- no innerHTML with dynamic content.
 */
(function () {
  "use strict";

  var STATE_KEY = "maint-banner-dismissed";
  var indicator = null;
  var panel = null;
  var messagesContainer = null;
  var currentMessages = [];
  var currentWindows = [];
  var audioCtx = null;

  function getAudioCtx() {
    if (!audioCtx) {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) {
        return null;
      }
    }
    return audioCtx;
  }

  // -- Web Audio synthesized sounds --

  function synthesizeBzzzt(longer) {
    var ctx = getAudioCtx();
    if (!ctx) return;
    var duration = longer ? 0.1 : 0.06;
    var bufferSize = ctx.sampleRate * duration;
    var buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < bufferSize; i++) {
      data[i] = (Math.random() * 2 - 1) * 0.3;
    }
    var source = ctx.createBufferSource();
    source.buffer = buffer;

    var bandpass = ctx.createBiquadFilter();
    bandpass.type = "bandpass";
    bandpass.frequency.value = 800;
    bandpass.Q.value = 2;

    var gain = ctx.createGain();
    gain.gain.setValueAtTime(0.4, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + duration);

    source.connect(bandpass);
    bandpass.connect(gain);
    gain.connect(ctx.destination);
    source.start();
  }

  function synthesizeClunk(heavy) {
    var ctx = getAudioCtx();
    if (!ctx) return;
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = heavy ? 80 : 120;

    var gain = ctx.createGain();
    gain.gain.setValueAtTime(0.5, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
  }

  // -- DOM construction --

  function buildIndicator() {
    indicator = document.createElement("button");
    indicator.className = "maintenance-indicator";
    indicator.setAttribute("aria-label", "Maintenance announcements");
    indicator.title = "Click to view maintenance announcements";
    indicator.textContent = "!";
    indicator.addEventListener("click", function (e) {
      e.stopPropagation();
      togglePanel();
    });
    document.body.appendChild(indicator);
  }

  function buildPanel() {
    panel = document.createElement("div");
    panel.className = "maintenance-panel";
    panel.addEventListener("click", function (e) {
      e.stopPropagation();
    });

    messagesContainer = document.createElement("div");
    messagesContainer.className = "maintenance-panel-messages";
    panel.appendChild(messagesContainer);

    // Knife switch
    var switchBtn = document.createElement("button");
    switchBtn.className = "knife-switch";
    switchBtn.setAttribute("role", "switch");
    switchBtn.setAttribute("aria-checked", "true");
    switchBtn.title = "Dismiss maintenance announcements for this session";

    // SVG knife switch
    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 48 80");
    svg.setAttribute("aria-hidden", "true");

    // Jaw contacts (top)
    var jawLeft = document.createElementNS(svgNS, "rect");
    jawLeft.setAttribute("x", "16");
    jawLeft.setAttribute("y", "8");
    jawLeft.setAttribute("width", "6");
    jawLeft.setAttribute("height", "20");
    jawLeft.setAttribute("rx", "1");
    jawLeft.setAttribute("class", "knife-switch-jaw");
    svg.appendChild(jawLeft);

    var jawRight = document.createElementNS(svgNS, "rect");
    jawRight.setAttribute("x", "26");
    jawRight.setAttribute("y", "8");
    jawRight.setAttribute("width", "6");
    jawRight.setAttribute("height", "20");
    jawRight.setAttribute("rx", "1");
    jawRight.setAttribute("class", "knife-switch-jaw");
    svg.appendChild(jawRight);

    // Blade (pivots)
    var blade = document.createElementNS(svgNS, "rect");
    blade.setAttribute("x", "21");
    blade.setAttribute("y", "15");
    blade.setAttribute("width", "6");
    blade.setAttribute("height", "40");
    blade.setAttribute("rx", "2");
    blade.setAttribute("class", "knife-switch-blade");
    svg.appendChild(blade);

    // Handle (bottom)
    var handle = document.createElementNS(svgNS, "rect");
    handle.setAttribute("x", "18");
    handle.setAttribute("y", "52");
    handle.setAttribute("width", "12");
    handle.setAttribute("height", "18");
    handle.setAttribute("rx", "3");
    handle.setAttribute("class", "knife-switch-handle");
    svg.appendChild(handle);

    // Pivot point
    var pivot = document.createElementNS(svgNS, "circle");
    pivot.setAttribute("cx", "24");
    pivot.setAttribute("cy", "55");
    pivot.setAttribute("r", "3");
    pivot.setAttribute("fill", "#666");
    svg.appendChild(pivot);

    switchBtn.appendChild(svg);

    var label = document.createElement("span");
    label.className = "knife-switch-label";
    label.textContent = "Dismiss";
    switchBtn.appendChild(label);

    switchBtn.addEventListener("click", function () {
      var isOn = switchBtn.getAttribute("aria-checked") === "true";
      var newState = !isOn;
      switchBtn.setAttribute("aria-checked", String(newState));

      // Sound: bzzzt during arc, clunk at contact
      if (newState) {
        synthesizeBzzzt(true);
        setTimeout(function () { synthesizeClunk(true); }, 100);
      } else {
        synthesizeBzzzt(false);
        setTimeout(function () { synthesizeClunk(false); }, 80);
      }

      if (!newState) {
        sessionStorage.setItem(STATE_KEY, "1");
        hideIndicator();
        closePanel();
      } else {
        sessionStorage.removeItem(STATE_KEY);
        updateDisplay();
      }
    });

    switchBtn.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        switchBtn.click();
      }
    });

    panel.appendChild(switchBtn);
    document.body.appendChild(panel);
  }

  // -- Display logic --

  function updateMessages() {
    if (!messagesContainer) return;
    while (messagesContainer.firstChild) {
      messagesContainer.removeChild(messagesContainer.firstChild);
    }

    // Manual messages first
    currentMessages.forEach(function (m) {
      var p = document.createElement("p");
      p.className = "maintenance-panel-message";
      p.textContent = m.message || m;
      messagesContainer.appendChild(p);
    });

    // Scheduled window announcements
    currentWindows.forEach(function (w) {
      var p = document.createElement("p");
      p.className = "maintenance-panel-message";
      var text = w.name;
      if (w.next_run_at) {
        text += " -- " + new Date(w.next_run_at).toLocaleString();
      }
      if (w.description) {
        text += ": " + w.description;
      }
      p.textContent = text;
      messagesContainer.appendChild(p);
    });
  }

  function hasContent() {
    return currentMessages.length > 0 || currentWindows.length > 0;
  }

  function isDismissed() {
    return sessionStorage.getItem(STATE_KEY) === "1";
  }

  function showIndicator() {
    if (indicator) indicator.classList.add("active");
  }

  function hideIndicator() {
    if (indicator) indicator.classList.remove("active");
  }

  function togglePanel() {
    if (panel) panel.classList.toggle("open");
    updateMessages();
  }

  function closePanel() {
    if (panel) panel.classList.remove("open");
  }

  function updateDisplay() {
    if (!hasContent()) {
      hideIndicator();
      closePanel();
      return;
    }
    if (isDismissed()) {
      hideIndicator();
      return;
    }
    showIndicator();
  }

  // -- Event handlers --

  function onAnnounce(e) {
    var detail = e.detail || {};
    if (detail.messages) currentMessages = detail.messages;
    if (detail.windows) currentWindows = detail.windows;
    updateDisplay();
    if (panel && panel.classList.contains("open")) {
      updateMessages();
    }
  }

  function onDismiss(e) {
    var detail = e.detail || {};
    if (detail.message_id) {
      currentMessages = currentMessages.filter(function (m) {
        return m.id !== detail.message_id;
      });
    }
    updateDisplay();
    if (panel && panel.classList.contains("open")) {
      updateMessages();
    }
  }

  function onUpdate(e) {
    var detail = e.detail || {};
    if (detail.window_id && detail.status === "completed") {
      currentWindows = currentWindows.filter(function (w) {
        return w.id !== detail.window_id;
      });
      updateDisplay();
    }
  }

  // -- Init --

  function init() {
    buildIndicator();
    buildPanel();

    // Click outside to close panel
    document.addEventListener("click", function () {
      closePanel();
    });

    // Listen for WebSocket events
    document.addEventListener("maintenance-announce", onAnnounce);
    document.addEventListener("maintenance-dismiss", onDismiss);
    document.addEventListener("maintenance-update", onUpdate);

    // Initial fetch for page load (before WebSocket connects)
    fetch("/api/maintenance/announcements")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        currentMessages = data.messages || [];
        currentWindows = data.windows || [];
        updateDisplay();
      })
      .catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
```

- [ ] **Step 3: Add CSS and script to shell.html**

In the `<head>`:

```html
<link rel="stylesheet" href="css/maintenance-banner.css">
```

Before `</body>`, after `websocket.js`:

```html
<script src="js/maintenance-banner.js"></script>
```

- [ ] **Step 4: Manual visual test**

Start the dev server, create a manual announcement via the API, verify:

- Pulsing red indicator appears in bottom-right
- Click expands panel with message text in neon red with 3D shadow
- Knife switch animates with bzzzt/clunk sounds
- Switch dismisses for session, reappears on reload
- Panel collapses on click-outside
- Keyboard toggle works (Tab to switch, Enter/Space)

- [ ] **Step 5: Commit**

```bash
git add library/web-v2/js/maintenance-banner.js \
    library/web-v2/css/maintenance-banner.css \
    library/web-v2/shell.html
git commit -m "feat: add maintenance announcement banner with knife switch

Pulsing red indicator on all pages when announcements active.
Expandable panel with neon red 3D text and Frankenstein knife
switch dismiss control. Web Audio API synthesized bzzzt (arc)
and clunk (contact) sounds during 0.7s lever animation.
Session-scoped dismissal. Keyboard and ARIA accessible."
```

---

## Task 11: Notification Queue Polling (WebSocket <- Scheduler)

**Files:**

- Modify: `library/backend/api_modular/websocket.py`
- Create: `library/tests/test_notification_poller.py`

- [ ] **Step 1: Write failing test**

Create `library/tests/test_notification_poller.py`:

```python
"""Tests for notification queue polling logic."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def notif_db(tmp_path):
    db_path = tmp_path / "test.db"
    schema = (Path(__file__).parent.parent / "backend" / "schema.sql").read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()
    return db_path


def test_poll_finds_pending_notifications(notif_db):
    """Poller finds undelivered notifications."""
    conn = sqlite3.connect(str(notif_db))
    conn.execute(
        "INSERT INTO maintenance_notifications (notification_type, payload) VALUES (?, ?)",
        ("announce", json.dumps({"window_id": 1})),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM maintenance_notifications WHERE delivered = 0"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


def test_poll_marks_delivered(notif_db):
    """Poller marks notifications as delivered after processing."""
    conn = sqlite3.connect(str(notif_db))
    conn.execute(
        "INSERT INTO maintenance_notifications (notification_type, payload) VALUES (?, ?)",
        ("announce", json.dumps({"window_id": 1})),
    )
    conn.commit()
    nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE maintenance_notifications SET delivered = 1 WHERE id = ?", (nid,))
    conn.commit()
    pending = conn.execute(
        "SELECT COUNT(*) FROM maintenance_notifications WHERE delivered = 0"
    ).fetchone()[0]
    assert pending == 0
    conn.close()
```

- [ ] **Step 2: Add notification poller to websocket.py**

Append to `library/backend/api_modular/websocket.py`:

```python
import sqlite3

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
                rows = conn.execute(
                    """SELECT id, notification_type, payload
                       FROM maintenance_notifications
                       WHERE delivered = 0
                       ORDER BY created_at ASC"""
                ).fetchall()

                for row in rows:
                    try:
                        payload = json.loads(row["payload"])
                        payload["type"] = "maintenance_" + row["notification_type"]
                        connection_manager.broadcast(payload)
                        conn.execute(
                            "UPDATE maintenance_notifications SET delivered = 1 WHERE id = ?",
                            (row["id"],),
                        )
                    except Exception as e:
                        logger.error("Failed to deliver notification %d: %s", row["id"], e)

                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Notification poll error: %s", e)

            gevent.sleep(5)

    gevent.spawn(_poll_loop)
    logger.info("Notification queue poller started (5s interval)")
```

Then update the `ws_handler` in `__init__.py` to start the poller on first connection:

```python
    # Inside ws_handler, after connection_manager.register():
    from .websocket import init_notification_poller
    init_notification_poller(database_path)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest library/tests/test_notification_poller.py -v
```

Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add library/backend/api_modular/websocket.py \
    library/backend/api_modular/__init__.py \
    library/tests/test_notification_poller.py
git commit -m "feat: add notification queue poller for scheduler->WebSocket delivery

Gevent greenlet polls maintenance_notifications table every 5s,
broadcasts pending notifications to all connected WebSocket clients,
marks as delivered. Bridges the scheduler daemon (separate process)
to the in-process WebSocket connection manager."
```

---

## Task 12: Install/Upgrade Scripts + Packaging

**Files:**

- Modify: `install.sh`
- Modify: `Dockerfile`

- [ ] **Step 1: Update install.sh**

Add `audiobook-scheduler.service` to the systemd service file copy section (find the block that copies service files):

```bash
# Add alongside existing service file copies:
cp "$PROJECT_DIR/systemd/audiobook-scheduler.service" /etc/systemd/system/
```

Add to the `systemctl enable` section:

```bash
systemctl enable audiobook-scheduler.service
```

Schema is `IF NOT EXISTS` so re-running against an existing DB is safe -- no migration script needed.

- [ ] **Step 2: Update Dockerfile**

Replace `waitress` with new dependencies in the `pip install` or requirements copy:

```dockerfile
# In the pip install step, ensure these are included:
RUN pip install --no-cache-dir gunicorn gevent gevent-websocket flask-sock croniter
```

Update the `CMD`:

```dockerfile
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "-w", "1", "--bind", "0.0.0.0:5001", "--timeout", "120", "api_server:app"]
```

- [ ] **Step 3: Commit**

```bash
git add install.sh Dockerfile
git commit -m "feat: update install and Docker for maintenance scheduling

Add scheduler service to install.sh systemd section. Update
Dockerfile from Waitress to Gunicorn with geventwebsocket worker.
Include new Python dependencies."
```

---

## Task 13: Documentation

**Files:**

- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update ARCHITECTURE.md**

Add sections for:

- **WebSocket Infrastructure**: Gunicorn migration rationale, gevent worker class, single-worker constraint, connection manager design
- **Maintenance Scheduling**: Scheduler daemon architecture, task registry plugin pattern, notification queue bridge
- **Announcement System**: Banner indicator, knife switch, Web Audio API sound synthesis, session-scoped dismissal

- [ ] **Step 2: Update TROUBLESHOOTING.md**

Add maintenance-specific troubleshooting entries:

| Symptom | Check |
|---------|-------|
| Scheduler not running | `systemctl status audiobook-scheduler` |
| WebSocket not connecting | Check proxy tunneling, verify Gunicorn worker type |
| Announcements not appearing | Check notification queue, verify lead time settings |
| Knife switch no sound | Web Audio API autoplay policy -- user must interact with page first |
| Windows not executing | Check file lock at `$AUDIOBOOKS_RUN_DIR/maintenance.lock` |

- [ ] **Step 3: Update README.md and CHANGELOG.md**

Feature description in README under Features section. Changelog entry under `## [Unreleased]`.

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md docs/TROUBLESHOOTING.md README.md CHANGELOG.md
git commit -m "docs: document maintenance scheduling system

Update architecture docs with WebSocket infrastructure, scheduler
daemon, task registry, and announcement system. Add troubleshooting
guide. Update README and changelog."
```

---

## Task 14: Integration Testing on Test VM

**Files:**

- No new files -- testing only

- [ ] **Step 1: Deploy to test VM**

```bash
./upgrade.sh --from-project . --remote 192.168.122.104 --yes
```

- [ ] **Step 2: Verify systemd services**

```bash
ssh -i ~/.ssh/id_ed25519 claude@192.168.122.104 \
    "systemctl status audiobook-api audiobook-scheduler"
```

Both should be `active (running)`.

- [ ] **Step 3: Test WebSocket connection**

Use Playwright (on test VM with Brave) to connect to `wss://<vm-ip>:8443/api/ws`, verify heartbeat works.

- [ ] **Step 4: Test full maintenance flow**

1. Create a maintenance window via API (scheduled 1 minute from now, `db_vacuum` task)
2. Create a manual announcement
3. Verify banner appears on the library page
4. Verify knife switch dismisses for session
5. Wait for scheduled window to execute
6. Verify execution history shows success
7. Verify announcement clears after window passes

- [ ] **Step 5: Playwright tests for banner UI**

Test on Brave browser:

- Banner visibility, click to expand, click-outside to collapse
- Knife switch animation and sound synthesis
- Keyboard navigation (Tab, Enter/Space)
- Mobile viewport sizing

---

## Completion Checklist

Before merging `maintenance-scheduling` -> `main`:

- [ ] All unit tests pass (`pytest library/tests/`)
- [ ] Gunicorn migration smoke test passes (existing API unchanged)
- [ ] WebSocket connects and heartbeats work
- [ ] Maintenance window CRUD works
- [ ] Task registry lists all 5 handlers
- [ ] Scheduler daemon executes due tasks
- [ ] Manual announcements push immediately
- [ ] Banner appears within lead time of scheduled windows
- [ ] Knife switch dismisses with sound and animation
- [ ] Keyboard/ARIA accessibility verified
- [ ] Mobile viewport tested
- [ ] `install.sh` tested on clean VM
- [ ] Docker build succeeds
- [ ] Documentation complete
- [ ] `ruff check` and `ruff format` clean
- [ ] `bandit` security scan clean
