"""Tests to verify Gunicorn migration doesn't break existing functionality."""

from pathlib import Path

import pytest

# Resolve project root from test file location (library/tests/ -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SYSTEMD_SERVICE = _PROJECT_ROOT / "systemd" / "audiobook-api.service"
_SYSTEMD_PROXY_SERVICE = _PROJECT_ROOT / "systemd" / "audiobook-proxy.service"
_PROXY_GUNICORN_CONF = _PROJECT_ROOT / "library" / "web-v2" / "gunicorn_proxy.conf.py"


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


# ---------------------------------------------------------------------------
# Proxy service (v8.4.2.0 "Option B" — http.server → gunicorn)
# ---------------------------------------------------------------------------
#
# The API service had assertions from the day it migrated; the proxy service
# migrated in this release with none. The proxy is the process that terminates
# TLS and serves the whole web UI, so an ExecStart that silently reverted to
# `python proxy_server.py` (or dropped the config file, taking the TLS 1.2
# floor and the worker model with it) would be a bigger outage than anything
# the API assertions guard — and nothing would have noticed.


@pytest.mark.skipif(
    not _SYSTEMD_PROXY_SERVICE.is_file(),
    reason="systemd service file not at project path (deployed installation)",
)
def test_proxy_systemd_execstarts_gunicorn_with_the_canonical_config():
    """audiobook-proxy.service must exec gunicorn -c gunicorn_proxy.conf.py."""
    content = _SYSTEMD_PROXY_SERVICE.read_text()

    exec_start = ""
    capturing = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("ExecStart="):
            capturing = True
        elif not capturing:
            continue
        exec_start += " " + stripped.rstrip("\\").strip()
        if not stripped.endswith("\\"):
            break

    assert exec_start, "audiobook-proxy.service has no ExecStart"
    assert "/gunicorn" in exec_start, f"proxy must be started by gunicorn: {exec_start}"
    assert "-c" in exec_start.split(), f"proxy must be given a config file: {exec_start}"
    assert "gunicorn_proxy.conf.py" in exec_start, (
        "proxy must use the canonical gunicorn_proxy.conf.py — all server "
        f"settings live there (Single Canonical Source): {exec_start}"
    )
    assert "proxy_server:app" in exec_start, (
        f"proxy must serve the WSGI callable, not run the module: {exec_start}"
    )
    assert "python" not in exec_start.rsplit("/", 1)[-1], (
        f"proxy must not be run as `python proxy_server.py`: {exec_start}"
    )


@pytest.mark.skipif(
    not _PROXY_GUNICORN_CONF.is_file(), reason="proxy gunicorn config not at project path"
)
def test_proxy_gunicorn_config_declares_worker_model_and_tls_floor():
    """The settings the ExecStart delegates to must actually be in the config."""
    content = _PROXY_GUNICORN_CONF.read_text()
    assert 'worker_class = "gevent"' in content, "proxy needs cooperative I/O workers"
    assert "certfile" in content and "keyfile" in content, "proxy terminates TLS"
    assert "TLSv1_2" in content, "TLS 1.2 floor must be preserved from the pre-gunicorn proxy"
    assert "def ssl_context(" in content, "TLS floor is applied via the ssl_context hook"


@pytest.mark.skipif(
    not _SYSTEMD_PROXY_SERVICE.is_file(),
    reason="systemd service file not at project path (deployed installation)",
)
def test_proxy_service_has_no_inline_server_settings():
    """Server settings belong in the config file, not duplicated in the unit."""
    exec_lines = [
        line
        for line in _SYSTEMD_PROXY_SERVICE.read_text().splitlines()
        if line.strip().startswith("ExecStart=") or line.startswith("    ")
    ]
    exec_text = " ".join(exec_lines)
    for flag in ("--bind", "--certfile", "--keyfile", "--workers", "-k "):
        assert flag not in exec_text, (
            f"{flag} in ExecStart duplicates gunicorn_proxy.conf.py — one of the two will drift"
        )
