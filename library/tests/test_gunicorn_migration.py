"""Tests to verify Gunicorn migration doesn't break existing functionality."""

import pytest
from pathlib import Path

# Resolve project root from test file location (library/tests/ -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SYSTEMD_SERVICE = _PROJECT_ROOT / "systemd" / "audiobook-api.service"


def test_monkey_patch_is_first():
    """Verify gevent monkey-patching happens before other imports."""
    with open(_PROJECT_ROOT / "library/backend/api_server.py") as f:
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
    with open(_PROJECT_ROOT / "library/requirements.txt") as f:
        content = f.read().lower()
    assert "waitress" not in content, "waitress should be removed from requirements.txt"


def test_requirements_has_gunicorn_deps():
    """Verify all Gunicorn dependencies are listed."""
    with open(_PROJECT_ROOT / "library/requirements.txt") as f:
        content = f.read().lower()
    for dep in ["gunicorn", "gevent", "gevent-websocket", "flask-sock", "croniter"]:
        assert dep in content, f"{dep} missing from requirements.txt"


@pytest.mark.skipif(
    not _SYSTEMD_SERVICE.is_file(),
    reason="systemd service file not at project path (deployed installation)",
)
def test_systemd_uses_gunicorn():
    """Verify systemd service uses Gunicorn, not waitress."""
    with open(_SYSTEMD_SERVICE) as f:
        content = f.read()
    assert "gunicorn" in content, "Service should use gunicorn"
    assert "-k gevent" in content, "Service should use standard gevent worker"
    # Check ExecStart line specifically — comments may mention GeventWebSocketWorker as a warning
    exec_lines = [
        line for line in content.splitlines() if line.strip().startswith(("ExecStart=", "-k "))
    ]
    exec_text = " ".join(exec_lines)
    assert "GeventWebSocketWorker" not in exec_text, (
        "ExecStart must NOT use GeventWebSocketWorker — it double-handles WebSocket "
        "upgrades with flask-sock, sending two 101 responses that corrupt the stream"
    )
    assert "-w 1" in content, "Service must use single worker"


def test_api_server_has_module_level_app():
    """Verify api_server.py exposes module-level app for Gunicorn."""
    with open(_PROJECT_ROOT / "library/backend/api_server.py") as f:
        content = f.read()
    assert "app = _create_configured_app()" in content, (
        "api_server.py must have module-level app for gunicorn api_server:app"
    )


def test_api_modular_no_run_server():
    """Verify run_server was removed from api_modular."""
    with open(_PROJECT_ROOT / "library/backend/api_modular/__init__.py") as f:
        content = f.read()
    assert "def run_server(" not in content, "run_server should be removed"
    assert "from waitress" not in content, "waitress import should be removed"
