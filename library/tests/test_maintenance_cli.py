"""audiobook-maintenance (library/backend/maintenance_cli.py).

The exit code of `status` is what a host-restart script acts on, so the
tests pin it from both sides: 0 only for "nobody" and for an app that is
verifiably not running; everything doubtful is non-zero (bd Audiobook-Manager-bd38.1).
"""

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import maintenance_cli as cli  # noqa: E402  # type: ignore[import-not-found]

TOKEN = "c" * 64


class _Door(BaseHTTPRequestHandler):
    """Stand-in for the API's local door; answers what the test queued."""

    def _answer(self):
        self.server.seen.append(
            (
                self.command,
                self.path,
                self.headers.get("Authorization"),
                self.rfile.read(int(self.headers.get("Content-Length") or 0)),
            )
        )
        status, body = self.server.answer
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def door():
    server = HTTPServer(("127.0.0.1", 0), _Door)
    server.seen = []
    server.answer = (200, _status_body())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "maintenance-token"
    path.write_text(TOKEN)
    return path


@pytest.fixture
def closed_port():
    """A loopback port with nothing listening on it."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run(port, token_file, *verb):
    return cli.main(["--port", str(port), "--token-file", str(token_file), *verb])


def _status_body(*states, uptime=100000):
    users = [{"username": f"u{i}", "state": s} for i, s in enumerate(states)]
    return {
        "uptime_seconds": uptime,
        "connected": len(users),
        "listening": sum(1 for s in states if s == "listening"),
        "polling": sum(1 for s in states if s == "polling"),
        "users": users,
    }


class TestStatusExitCode:
    def test_nobody_is_zero(self, door, token_file, capsys):
        assert _run(door.server_port, token_file, "status") == 0
        assert capsys.readouterr().out.splitlines() == ["connected=0 listening=0 polling=0"]
        assert door.seen[0][:3] == ("GET", "/api/local/maintenance/status", f"Bearer {TOKEN}")

    @pytest.mark.parametrize(
        "states, expected",
        [
            (["browsing"], 10),
            (["connected", "browsing"], 10),
            (["listening"], 20),
            (["connected", "listening"], 20),
            (["polling"], 20),
            (["something-new"], 20),  # a state this program does not know: not "quiet"
        ],
    )
    def test_states(self, door, token_file, states, expected, capsys):
        door.answer = (200, _status_body(*states))
        assert _run(door.server_port, token_file, "status") == expected
        out = capsys.readouterr().out.splitlines()
        assert out[1:] == [f"u{i} {s}" for i, s in enumerate(states)]

    @pytest.mark.parametrize(
        "answer",
        [
            (401, {"error": "Invalid maintenance token"}),
            (503, {"error": "Maintenance token not issued"}),
            (404, {"error": "Not found"}),
            (500, b"<html>boom</html>"),
            (200, b"not json"),
            (200, []),
            (200, {"connected": 0}),
            # no uptime: cannot tell whether browsers have had time to reconnect
            (200, {"connected": 0, "listening": 0, "polling": 0, "users": []}),
            (200, {"connected": 0, "listening": 0, "polling": 0, "users": None}),
            (200, {"connected": -1, "listening": 0, "polling": 0, "users": []}),
            (200, {"connected": True, "listening": 0, "polling": 0, "users": []}),
            # says nobody, lists somebody
            (200, {"connected": 0, "listening": 0, "polling": 0, "users": [{"username": "x"}]}),
        ],
    )
    def test_an_answer_that_cannot_be_relied_on_is_unknown(self, door, token_file, answer):
        door.answer = answer
        assert _run(door.server_port, token_file, "status") == cli.EXIT_UNKNOWN

    @pytest.mark.parametrize("uptime", [0, 5, 89])
    def test_nobody_right_after_the_app_started_is_unknown(self, door, token_file, uptime, capsys):
        # The connection list is empty after a restart until browsers reconnect.
        door.answer = (200, _status_body(uptime=uptime))
        assert _run(door.server_port, token_file, "status") == cli.EXIT_UNKNOWN
        assert "started" in capsys.readouterr().err

    def test_nobody_once_the_app_has_settled_is_zero(self, door, token_file):
        door.answer = (200, _status_body(uptime=90))
        assert _run(door.server_port, token_file, "status") == 0

    def test_a_listener_right_after_start_is_still_a_listener(self, door, token_file):
        door.answer = (200, _status_body("listening", uptime=5))
        assert _run(door.server_port, token_file, "status") == 20

    def test_unreadable_token_with_the_app_up_is_unknown(self, door, tmp_path):
        assert _run(door.server_port, tmp_path / "absent", "status") == cli.EXIT_UNKNOWN
        assert door.seen == []

    def test_empty_token_file_is_unknown(self, door, tmp_path):
        empty = tmp_path / "empty"
        empty.write_text("\n")
        assert _run(door.server_port, empty, "status") == cli.EXIT_UNKNOWN


class TestAppNotRunning:
    @pytest.mark.parametrize("state", ["inactive", "failed"])
    def test_refused_port_and_stopped_service_is_zero(
        self, closed_port, token_file, monkeypatch, capsys, state
    ):
        monkeypatch.setattr(cli, "service_state", lambda unit: state)
        assert _run(closed_port, token_file, "status") == 0
        assert capsys.readouterr().out.strip() == "app=down connected=0"

    @pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading", None])
    def test_refused_port_without_a_stopped_service_is_unknown(
        self, closed_port, token_file, monkeypatch, state
    ):
        monkeypatch.setattr(cli, "service_state", lambda unit: state)
        assert _run(closed_port, token_file, "status") == cli.EXIT_UNKNOWN

    def test_stopped_service_but_something_answers_the_port_is_not_down(
        self, door, token_file, monkeypatch
    ):
        # The unit reads inactive, yet a process serves the port: trust the port.
        monkeypatch.setattr(cli, "service_state", lambda unit: "inactive")
        door.answer = (200, _status_body("listening"))
        assert _run(door.server_port, token_file, "status") == 20

    def test_down_app_needs_no_token(self, closed_port, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "service_state", lambda unit: "inactive")
        assert _run(closed_port, tmp_path / "absent", "status") == 0
        assert capsys.readouterr().out.strip() == "app=down connected=0"

    def test_service_state_without_a_service_manager_is_none(self, monkeypatch):
        def no_systemctl(*args, **kwargs):
            raise FileNotFoundError("systemctl")

        monkeypatch.setattr(cli.subprocess, "run", no_systemctl)
        assert cli.service_state("audiobook-api.service") is None


class TestWriteVerbs:
    def test_announce_prints_the_id(self, door, token_file, capsys):
        door.answer = (201, {"id": 7})
        assert _run(door.server_port, token_file, "announce", "Back at 3") == 0
        assert capsys.readouterr().out.strip() == "7"
        method, path, _, raw = door.seen[0]
        assert (method, path) == ("POST", "/api/local/maintenance/announce")
        assert json.loads(raw) == {"message": "Back at 3"}

    def test_clear_all_and_by_id(self, door, token_file, capsys):
        door.answer = (200, {"cleared": 2, "ids": [1, 2]})
        assert _run(door.server_port, token_file, "clear") == 0
        assert _run(door.server_port, token_file, "clear", "5") == 0
        assert [json.loads(s[3]) for s in door.seen] == [{}, {"id": 5}]
        assert capsys.readouterr().out.split() == ["cleared=2", "cleared=2"]

    def test_window_add_sends_a_start_with_a_zone(self, door, token_file, capsys):
        door.answer = (201, {"id": 3, "start": "x"})
        code = _run(
            door.server_port, token_file, "window", "add", "--start", "2031-01-25T03:00",
            "--minutes", "10", "--lead-hours", "2", "--message", "Host restart",
        )  # fmt: skip
        assert code == 0
        assert capsys.readouterr().out.strip() == "3"
        sent = json.loads(door.seen[0][3])
        assert sent["minutes"] == 10 and sent["lead_hours"] == 2
        assert sent["message"] == "Host restart"
        from datetime import datetime

        assert datetime.fromisoformat(sent["start"]).tzinfo is not None
        assert sent["start"].startswith("2031-01-25T03:00:00")

    def test_window_add_keeps_a_given_zone(self):
        assert cli.start_with_zone("2031-01-25T03:00:00-06:00") == "2031-01-25T03:00:00-06:00"
        assert cli.start_with_zone("2031-01-25T09:00:00Z") == "2031-01-25T09:00:00+00:00"

    def test_window_add_refuses_a_bad_start_without_calling(self, door, token_file):
        code = _run(
            door.server_port, token_file, "window", "add", "--start", "tomorrow",
            "--minutes", "10", "--message", "x",
        )  # fmt: skip
        assert code == 1
        assert door.seen == []

    def test_window_cancel(self, door, token_file, capsys):
        door.answer = (200, {"cancelled": 3})
        assert _run(door.server_port, token_file, "window", "cancel", "3") == 0
        assert json.loads(door.seen[0][3]) == {"id": 3}

    @pytest.mark.parametrize("verb", [("announce", "x"), ("clear",), ("window", "cancel", "1")])
    def test_a_refusal_is_a_failure(self, door, token_file, verb, capsys):
        door.answer = (404, {"error": "No such thing"})
        assert _run(door.server_port, token_file, *verb) == 1
        assert "No such thing" in capsys.readouterr().err

    def test_write_verb_with_the_app_down_fails(self, closed_port, token_file):
        assert _run(closed_port, token_file, "announce", "x") == 1
