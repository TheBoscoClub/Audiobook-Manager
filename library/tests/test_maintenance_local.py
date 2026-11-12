"""The local maintenance door: /api/local/maintenance/* (bd Audiobook-Manager-bd38.1).

A root script on the same host asks who is connected, posts and clears an
announcement, and schedules a host-downtime window. The door opens only for
the token the API wrote into its run directory, only from a loopback peer
that did not come through the reverse proxy.
"""

import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))

TOKEN = "a" * 64
BASE = "/api/local/maintenance"
CLI = "maintenance-cli"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript((LIBRARY_DIR / "backend" / "schema.sql").read_text())
    conn.close()
    return path


@pytest.fixture
def app(tmp_path, db_path):
    from api_modular import create_app

    flask_app = create_app(
        database_path=db_path, project_dir=tmp_path, supplements_dir=tmp_path / "supplements"
    )
    flask_app.config["TESTING"] = True
    flask_app.config["MAINTENANCE_TOKEN"] = TOKEN
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_live_state():
    """The connection manager and poll tracker are process-wide singletons."""
    from api_modular.websocket import connection_manager, poll_tracker

    def _reset():
        with connection_manager._lock:
            connection_manager._connections.clear()
        poll_tracker.clear()

    _reset()
    yield
    _reset()


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _rows(db_path, sql, args=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


class _FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


# ---------------------------------------------------------------- the locks


class TestTheLocks:
    def test_right_token_from_loopback_is_let_in(self, client):
        resp = client.get(f"{BASE}/status", headers=_auth())
        assert resp.status_code == 200

    def test_no_token_is_refused(self, client):
        assert client.get(f"{BASE}/status").status_code == 401

    def test_wrong_token_is_refused(self, client):
        assert client.get(f"{BASE}/status", headers=_auth("b" * 64)).status_code == 401

    def test_token_in_the_wrong_scheme_is_refused(self, client):
        resp = client.get(f"{BASE}/status", headers={"Authorization": f"Basic {TOKEN}"})
        assert resp.status_code == 401

    def test_non_loopback_peer_is_refused_even_with_the_token(self, client):
        resp = client.get(
            f"{BASE}/status", headers=_auth(), environ_base={"REMOTE_ADDR": "192.0.2.5"}
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize("header", ["X-Real-IP", "X-Forwarded-For"])
    def test_request_relayed_by_the_proxy_is_refused_even_with_the_token(self, client, header):
        # The reverse proxy re-authors both headers on everything it relays.
        # 127.0.0.1 here is the worst case: a relayed request claiming loopback.
        resp = client.get(f"{BASE}/status", headers={**_auth(), header: "127.0.0.1"})
        assert resp.status_code == 404

    def test_no_token_issued_means_nobody_gets_in(self, app, client):
        app.config["MAINTENANCE_TOKEN"] = None
        assert client.get(f"{BASE}/status", headers=_auth()).status_code == 503

    def test_empty_bearer_never_matches_an_unissued_token(self, app, client):
        app.config["MAINTENANCE_TOKEN"] = ""
        resp = client.get(f"{BASE}/status", headers={"Authorization": "Bearer "})
        assert resp.status_code == 503

    @pytest.mark.parametrize("path", ["announce", "clear", "window", "window/cancel"])
    def test_every_write_path_is_locked_too(self, client, path):
        assert client.post(f"{BASE}/{path}", json={}).status_code == 401


class TestTokenFile:
    def test_issue_writes_a_private_file_and_arms_the_app(self, app, tmp_path):
        from api_modular.maintenance_local import issue_maintenance_token

        token_file = tmp_path / "run" / "maintenance-token"
        token_file.parent.mkdir()
        assert issue_maintenance_token(app, token_file) is True

        value = token_file.read_text()
        assert len(value) == 64 and int(value, 16) >= 0
        assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o400
        assert app.config["MAINTENANCE_TOKEN"] == value

    def test_each_issue_replaces_the_token(self, app, tmp_path):
        from api_modular.maintenance_local import issue_maintenance_token

        token_file = tmp_path / "maintenance-token"
        issue_maintenance_token(app, token_file)
        first = token_file.read_text()
        issue_maintenance_token(app, token_file)
        assert token_file.read_text() != first
        assert app.config["MAINTENANCE_TOKEN"] == token_file.read_text()

    def test_unwritable_location_leaves_the_door_shut(self, app, tmp_path):
        from api_modular.maintenance_local import issue_maintenance_token

        assert issue_maintenance_token(app, tmp_path / "missing-dir" / "token") is False
        assert app.config["MAINTENANCE_TOKEN"] is None


# ------------------------------------------------------------------- status


class TestStatus:
    def test_nobody(self, client):
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        uptime = body.pop("uptime_seconds")
        assert body == {"connected": 0, "listening": 0, "polling": 0, "users": []}
        assert isinstance(uptime, int) and uptime >= 0

    def test_uptime_counts_from_process_start(self, client, monkeypatch):
        from api_modular import maintenance_local

        monkeypatch.setattr(maintenance_local, "_STARTED", maintenance_local.time.monotonic() - 500)
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert 500 <= body["uptime_seconds"] <= 505

    def test_counts_websocket_states(self, client):
        from api_modular.websocket import connection_manager

        connection_manager.register("s1", _FakeWS(), username="alice")
        connection_manager.heartbeat("s1", state="listening")
        connection_manager.register("s2", _FakeWS(), username="bob")
        connection_manager.heartbeat("s2", state="connected")

        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert (body["connected"], body["listening"], body["polling"]) == (2, 1, 0)
        assert {"username": "alice", "state": "listening"} in body["users"]
        assert {"username": "bob", "state": "connected"} in body["users"]

    def test_a_recent_poller_is_counted_as_polling(self, client):
        from api_modular.websocket import poll_tracker

        poll_tracker.note("s9", "carol")
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert (body["connected"], body["listening"], body["polling"]) == (1, 0, 1)
        assert body["users"] == [{"username": "carol", "state": "polling"}]

    @pytest.mark.parametrize(
        "state, listening, polling",
        [("browsing", 0, 0), ("connected", 0, 0), ("listening", 1, 0)],
    )
    def test_a_poller_that_reports_its_player_is_counted_in_that_state(
        self, client, state, listening, polling
    ):
        from api_modular.websocket import poll_tracker

        poll_tracker.note("s9", "carol", state=state)
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert (body["connected"], body["listening"], body["polling"]) == (1, listening, polling)
        assert body["users"] == [{"username": "carol", "state": state}]

    @pytest.mark.parametrize("state", [None, "", "dancing", "LISTENING", "polling"])
    def test_a_poller_with_no_usable_state_stays_polling(self, client, state):
        from api_modular.websocket import poll_tracker

        poll_tracker.note("s9", "carol", state=state)
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert (body["listening"], body["polling"]) == (0, 1)
        assert body["users"] == [{"username": "carol", "state": "polling"}]

    def test_a_poller_that_went_quiet_is_dropped(self, client):
        from api_modular.websocket import poll_tracker

        poll_tracker.note("s9", "carol", now=time.time() - 61)
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert body["connected"] == 0

    def test_a_session_with_a_websocket_is_not_also_a_poller(self, client):
        from api_modular.websocket import connection_manager, poll_tracker

        connection_manager.register("s1", _FakeWS(), username="alice")
        poll_tracker.note("s1", "alice")
        body = client.get(f"{BASE}/status", headers=_auth()).get_json()
        assert (body["connected"], body["polling"]) == (1, 0)


# ------------------------------------------------------------ announcements


class TestAnnounce:
    def test_announce_stores_and_pushes(self, client, db_path):
        from api_modular.websocket import connection_manager

        ws = _FakeWS()
        connection_manager.register("s1", ws, username="alice")

        resp = client.post(f"{BASE}/announce", headers=_auth(), json={"message": "Back at 3"})
        assert resp.status_code == 201
        new_id = resp.get_json()["id"]

        rows = _rows(db_path, "SELECT * FROM maintenance_messages")
        assert [(r["id"], r["message"], r["created_by"]) for r in rows] == [
            (new_id, "Back at 3", CLI)
        ]
        pushed = json.loads(ws.sent[0])
        assert pushed["type"] == "maintenance_announce"
        assert pushed["messages"][0]["message"] == "Back at 3"

    def test_announce_needs_a_message(self, client):
        assert client.post(f"{BASE}/announce", headers=_auth(), json={}).status_code == 400

    def test_clear_dismisses_only_what_this_door_created(self, client, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_messages (message, created_by) VALUES ('admin note', 'admin')"
        )
        conn.commit()
        conn.close()
        client.post(f"{BASE}/announce", headers=_auth(), json={"message": "one"})
        client.post(f"{BASE}/announce", headers=_auth(), json={"message": "two"})

        resp = client.post(f"{BASE}/clear", headers=_auth(), json={})
        assert resp.status_code == 200
        assert resp.get_json()["cleared"] == 2

        open_rows = _rows(
            db_path, "SELECT message FROM maintenance_messages WHERE dismissed_at IS NULL"
        )
        assert [r["message"] for r in open_rows] == ["admin note"]

    def test_clear_by_id_refuses_an_admin_announcement(self, client, db_path):
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            "INSERT INTO maintenance_messages (message, created_by) VALUES ('admin note', 'admin')"
        )
        admin_id = cur.lastrowid
        conn.commit()
        conn.close()

        resp = client.post(f"{BASE}/clear", headers=_auth(), json={"id": admin_id})
        assert resp.status_code == 404
        still = _rows(db_path, "SELECT dismissed_at FROM maintenance_messages")
        assert still[0]["dismissed_at"] is None


# ------------------------------------------------------- host-downtime window


class TestWindow:
    def _add(self, client, **over):
        body = {
            "start": "2031-01-25T09:00:00Z",
            "minutes": 10,
            "lead_hours": 24,
            "message": "Host restart",
        }
        body.update(over)
        return client.post(f"{BASE}/window", headers=_auth(), json=body)

    def test_window_is_a_host_downtime_window_and_nothing_else(self, client, db_path):
        resp = self._add(client)
        assert resp.status_code == 201
        row = _rows(db_path, "SELECT * FROM maintenance_windows")[0]
        assert row["id"] == resp.get_json()["id"]
        assert row["task_type"] == "host_downtime"
        assert row["schedule_type"] == "once"
        assert row["next_run_at"] == "2031-01-25T09:00:00.000Z"
        assert row["scheduled_at"] == row["next_run_at"]
        assert (row["duration_minutes"], row["lead_time_hours"]) == (10, 24)
        assert row["description"] == "Host restart"
        assert json.loads(row["task_params"])["created_by"] == CLI

    def test_start_with_an_offset_is_stored_in_utc(self, client, db_path):
        self._add(client, start="2031-01-25T03:00:00-06:00")
        row = _rows(db_path, "SELECT next_run_at FROM maintenance_windows")[0]
        assert row["next_run_at"] == "2031-01-25T09:00:00.000Z"

    @pytest.mark.parametrize(
        "over",
        [
            {"start": "tomorrow"},
            {"start": "2031-01-25T09:00:00"},  # no zone: ambiguous, refused
            {"start": None},
            {"minutes": 0},
            {"minutes": "ten"},
            {"lead_hours": -1},
            {"message": ""},
        ],
    )
    def test_bad_input_creates_nothing(self, client, db_path, over):
        assert self._add(client, **over).status_code == 400
        assert _rows(db_path, "SELECT id FROM maintenance_windows") == []

    def test_the_window_shows_in_the_public_announcements(self, client):
        from datetime import datetime, timedelta, timezone

        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_id = self._add(client, start=soon, lead_hours=2).get_json()["id"]
        windows = client.get("/api/maintenance/announcements").get_json()["windows"]
        assert [w["id"] for w in windows] == [new_id]

    def test_cancel_removes_the_window(self, client, db_path):
        new_id = self._add(client).get_json()["id"]
        resp = client.post(f"{BASE}/window/cancel", headers=_auth(), json={"id": new_id})
        assert resp.status_code == 200
        assert _rows(db_path, "SELECT id FROM maintenance_windows WHERE status = 'active'") == []

    def test_cancel_refuses_a_window_an_admin_created(self, client, db_path):
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            "INSERT INTO maintenance_windows (name, task_type, schedule_type, next_run_at) "
            "VALUES ('Vacuum', 'db_vacuum', 'once', '2031-01-25T09:00:00.000Z')"
        )
        admin_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO maintenance_windows (name, task_type, schedule_type, next_run_at) "
            "VALUES ('Admin downtime', 'host_downtime', 'once', '2031-01-25T09:00:00.000Z')"
        )
        admin_downtime_id = cur.lastrowid
        conn.commit()
        conn.close()

        for wid in (admin_id, admin_downtime_id, 9999):
            resp = client.post(f"{BASE}/window/cancel", headers=_auth(), json={"id": wid})
            assert resp.status_code == 404
        assert len(_rows(db_path, "SELECT id FROM maintenance_windows WHERE status='active'")) == 2


class TestHostDowntimeTask:
    def test_registered_and_does_nothing(self, tmp_path):
        from api_modular.maintenance_tasks import registry

        task = registry.get("host_downtime")
        assert task is not None
        before = sorted(p.name for p in tmp_path.iterdir())
        params = {"db_path": str(tmp_path / "never-created.db"), "created_by": CLI}
        assert task.validate(params).ok is True
        result = task.execute(params)
        assert result.success is True
        assert sorted(p.name for p in tmp_path.iterdir()) == before


# ------------------------------------------------------------ poll recording


class TestPollRecording:
    def test_announcements_poll_without_a_session_records_nothing(self, client):
        from api_modular.websocket import poll_tracker

        client.get("/api/maintenance/announcements")
        assert poll_tracker.recent() == []


class TestWebSocketClientLeavesPolling:
    """library/web-v2/js/websocket.js (bd Audiobook-Manager-bd38.4).

    A browser that fell back to polling used to poll forever: nothing tried the
    WebSocket again, so a tab open across an API restart stayed invisible to
    the connection list. Source-level checks; the behaviour itself is proven
    in a real browser on the test VM.
    """

    SOURCE = (LIBRARY_DIR / "web-v2" / "js" / "websocket.js").read_text()

    def _function(self, name):
        start = self.SOURCE.index(f"function {name}(")
        depth, i = 0, self.SOURCE.index("{", start)
        for j in range(i, len(self.SOURCE)):
            depth += {"{": 1, "}": -1}.get(self.SOURCE[j], 0)
            if depth == 0:
                return self.SOURCE[start : j + 1]
        raise AssertionError(f"unterminated function {name}")

    def test_each_poll_tick_also_retries_the_websocket(self):
        assert "connect();" in self._function("pollTick")
        assert "pollForAnnouncements();" in self._function("pollTick")
        assert "setInterval(pollTick, POLL_INTERVAL)" in self._function("startPolling")

    def test_the_poll_reports_the_player_state(self):
        body = self._function("pollForAnnouncements")
        assert "getPlayerState()" in body
        assert "/api/maintenance/announcements?state=" in body
