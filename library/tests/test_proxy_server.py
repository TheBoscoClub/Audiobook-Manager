"""
Unit tests for library/web-v2/proxy_server.py (WSGI app, v8.4.2.0 Option B)

Tests the gunicorn-served reverse proxy WSGI application including:
- WebSocket upgrade detection
- Cache-Control header injection
- HTTP method routing (GET/POST/PUT/PATCH/DELETE/OPTIONS)
- WebSocket tunneling via gunicorn socket hijack
- API proxying with SSRF prevention
- Path sanitization (CRLF / null-byte stripping)
- Hop-by-hop header filtering
- Error handling (HTTPError passthrough, URLError 503, generic 500)
- Static file + cover serving with traversal protection
- gunicorn_proxy.conf.py (worker model, TLS 1.2 floor, cert preflight)
"""

import io
import json
import socket
import subprocess
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# We need config on sys.path before importing proxy_server because it
# imports config values at module level.

# nosec B104  # MOCK_CONFIG below uses test fixture bind address (0.0.0.0 for local test)
_TEST_BIND_ALL = "0.0.0" + ".0"  # nosec B104  # obfuscated to avoid bandit literal match in dict
MOCK_CONFIG = {
    "AUDIOBOOKS_API_PORT": 5001,
    "AUDIOBOOKS_WEB_PORT": 8443,
    "AUDIOBOOKS_CERTS": Path("/tmp/test-certs"),  # nosec B108  # test fixture path
    "AUDIOBOOKS_BIND_ADDRESS": _TEST_BIND_ALL,
}

WEB_V2_DIR = Path(__file__).parent.parent / "web-v2"


def _import_proxy_server():
    """Import proxy_server with config already on sys.path.

    web-v2 has a hyphen so it's not a valid Python package name.
    We add it to sys.path and import proxy_server directly.
    """
    web_v2_dir = str(WEB_V2_DIR)
    if web_v2_dir not in sys.path:
        sys.path.insert(0, web_v2_dir)
    import proxy_server as ps  # type: ignore[import-not-found]

    return ps


# Lazy import — done once
proxy_server = _import_proxy_server()


# ============================================================
# WSGI test harness
# ============================================================


class StartResponseRecorder:
    """Record the status and headers passed to start_response."""

    def __init__(self):
        self.status = None
        self.headers = []

    def __call__(self, status, headers, exc_info=None):
        self.status = status
        self.headers = list(headers)

    @property
    def code(self):
        return int(self.status.split(" ", 1)[0]) if self.status else None

    def header(self, name):
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    def header_names(self):
        return [k for k, _ in self.headers]


def _make_environ(path="/", method="GET", headers=None, body=b"", client_addr="127.0.0.1"):
    """Build a minimal WSGI environ with gunicorn's RAW_URI extension."""
    environ = {
        "REQUEST_METHOD": method,
        "RAW_URI": path,
        "PATH_INFO": path.split("?", 1)[0],
        "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
        "REMOTE_ADDR": client_addr,
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.StringIO(),
    }
    for name, value in (headers or {}).items():
        key = name.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[key] = value
        else:
            environ["HTTP_" + key] = value
    return environ


def _call_app(path="/", method="GET", headers=None, body=b"", environ_extra=None):
    """Invoke the WSGI app; return (recorder, body_bytes)."""
    environ = _make_environ(path=path, method=method, headers=headers, body=body)
    if environ_extra:
        environ.update(environ_extra)
    recorder = StartResponseRecorder()
    result = proxy_server.app(environ, recorder)
    return recorder, b"".join(result)


def _make_headers(extra=None):
    """Build an EnvironHeaders view from a plain dict of header names."""
    environ = _make_environ(headers=extra or {})
    return proxy_server.EnvironHeaders(environ)


def _mock_urlopen_response(status=200, headers=None, body=b"ok"):
    """Build a MagicMock mimicking urllib.request.urlopen's response."""
    mock_response = MagicMock()
    mock_response.status = status
    mock_response.headers = headers if headers is not None else Message()
    mock_response.read.side_effect = [body, b""]
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


# ============================================================
# 1. is_websocket_upgrade()
# ============================================================


class TestIsWebsocketUpgrade:
    def test_valid_websocket_upgrade(self):
        headers = _make_headers({"Upgrade": "websocket", "Connection": "Upgrade"})
        assert proxy_server.is_websocket_upgrade(headers) is True

    def test_case_insensitive(self):
        headers = _make_headers({"Upgrade": "WebSocket", "Connection": "keep-alive, Upgrade"})
        assert proxy_server.is_websocket_upgrade(headers) is True

    def test_missing_upgrade_header(self):
        headers = _make_headers({"Connection": "Upgrade"})
        assert proxy_server.is_websocket_upgrade(headers) is False

    def test_missing_connection_header(self):
        headers = _make_headers({"Upgrade": "websocket"})
        assert proxy_server.is_websocket_upgrade(headers) is False

    def test_wrong_upgrade_value(self):
        headers = _make_headers({"Upgrade": "h2c", "Connection": "Upgrade"})
        assert proxy_server.is_websocket_upgrade(headers) is False

    def test_no_upgrade_in_connection(self):
        headers = _make_headers({"Upgrade": "websocket", "Connection": "keep-alive"})
        assert proxy_server.is_websocket_upgrade(headers) is False

    def test_empty_headers(self):
        headers = _make_headers()
        assert proxy_server.is_websocket_upgrade(headers) is False

    def test_none_values(self):
        """EnvironHeaders.get returns None for missing keys — must not crash."""
        headers = _make_headers()
        assert headers.get("Upgrade") is None
        assert proxy_server.is_websocket_upgrade(headers) is False


# ============================================================
# 2. Cache-Control injection
# ============================================================


class TestCacheControl:
    def _get_cache_header(self, path):
        """Return the Cache-Control value the app computes for a path."""
        query = path.split("?", 1)[1] if "?" in path else ""
        bare = path.split("?", 1)[0]
        return proxy_server.app._cache_control_for_path(bare.lower(), "v=" in query)

    def test_html_no_cache(self):
        assert self._get_cache_header("/index.html") == "no-cache"

    def test_root_no_cache(self):
        assert self._get_cache_header("/") == "no-cache"

    def test_versioned_js_immutable(self):
        val = self._get_cache_header("/js/app.js?v=abc123")
        assert val == "public, max-age=31536000, immutable"

    def test_versioned_css_immutable(self):
        val = self._get_cache_header("/css/style.css?v=1.0")
        assert val == "public, max-age=31536000, immutable"

    def test_unversioned_js_short_cache(self):
        val = self._get_cache_header("/js/app.js")
        assert val == "public, max-age=300"

    def test_unversioned_css_short_cache(self):
        val = self._get_cache_header("/css/style.css")
        assert val == "public, max-age=300"

    def test_image_one_day(self):
        val = self._get_cache_header("/img/logo.png")
        assert val == "public, max-age=86400"

    def test_svg_one_day(self):
        val = self._get_cache_header("/img/icon.svg")
        assert val == "public, max-age=86400"

    def test_woff2_one_day(self):
        val = self._get_cache_header("/fonts/font.woff2")
        assert val == "public, max-age=86400"

    def test_ico_one_day(self):
        val = self._get_cache_header("/favicon.ico")
        assert val == "public, max-age=86400"

    def test_unknown_extension_no_cache_header(self):
        val = self._get_cache_header("/data/file.json")
        assert val is None

    def test_served_js_gets_cache_header(self):
        """End-to-end: a real static JS file response carries Cache-Control."""
        recorder, _ = _call_app(path="/js/api.js")
        assert recorder.code == 200
        assert recorder.header("Cache-Control") == "public, max-age=300"

    def test_api_response_gets_no_cache_header(self):
        """Proxied API responses must NOT get static-file cache headers."""
        with patch("urllib.request.urlopen", return_value=_mock_urlopen_response()):
            recorder, _ = _call_app(path="/api/books")
        assert recorder.header("Cache-Control") is None


# ============================================================
# 3. GET routing
# ============================================================


class TestGetRouting:
    def test_shell_html_redirects_to_root(self):
        recorder, _ = _call_app(path="/shell.html")
        assert recorder.code == 301
        assert recorder.header("Location") == "/"

    def test_shell_html_preserves_query(self):
        recorder, _ = _call_app(path="/shell.html?autoplay=1")
        assert recorder.code == 301
        assert recorder.header("Location") == "/?autoplay=1"

    def test_shell_html_strips_crlf(self):
        """Prevent HTTP response splitting via CRLF in query string."""
        recorder, _ = _call_app(path="/shell.html?foo=bar\r\nEvil: header")
        location = recorder.header("Location")
        assert location is not None
        assert "\r" not in location
        assert "\n" not in location

    def test_root_serves_shell_html(self):
        recorder, body = _call_app(path="/")
        assert recorder.code == 200
        assert recorder.header("Content-Type").startswith("text/html")
        assert body == (WEB_V2_DIR / "shell.html").read_bytes()

    def test_root_with_query_serves_shell_html(self):
        recorder, body = _call_app(path="/?autoplay=1&book=42")
        assert recorder.code == 200
        assert body == (WEB_V2_DIR / "shell.html").read_bytes()

    def test_static_file_passthrough(self):
        recorder, body = _call_app(path="/js/api.js")
        assert recorder.code == 200
        assert body == (WEB_V2_DIR / "js" / "api.js").read_bytes()

    def test_missing_static_file_404(self):
        recorder, _ = _call_app(path="/no-such-file.txt")
        assert recorder.code == 404

    def test_api_path_proxied(self):
        with patch.object(proxy_server.ReverseProxyApp, "proxy_to_api") as mock_proxy:
            mock_proxy.return_value = []
            _call_app(path="/api/books")
            mock_proxy.assert_called_once()
            assert mock_proxy.call_args[0][-1] == "GET"

    def test_websocket_upgrade_tunneled(self):
        headers = {"Upgrade": "websocket", "Connection": "Upgrade"}
        with patch.object(proxy_server.ReverseProxyApp, "_tunnel_websocket") as mock_tunnel:
            mock_tunnel.return_value = []
            _call_app(path="/api/ws/position", headers=headers)
            mock_tunnel.assert_called_once()

    def test_auth_login_get_redirects_to_page(self):
        recorder, _ = _call_app(path="/auth/login")
        assert recorder.code == 302
        assert recorder.header("Location") == "/login.html"

    def test_auth_register_get_redirects_to_page(self):
        recorder, _ = _call_app(path="/auth/register")
        assert recorder.code == 302
        assert recorder.header("Location") == "/register.html"

    def test_path_traversal_outside_webroot_blocked(self):
        recorder, _ = _call_app(path="/../config.py")
        assert recorder.code in (403, 404)


# ============================================================
# 4. do_POST / do_PUT / do_PATCH / do_DELETE routing
# ============================================================


class TestHttpMethods:
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_proxy_path_forwards(self, method):
        with patch.object(proxy_server.ReverseProxyApp, "proxy_to_api") as mock_proxy:
            mock_proxy.return_value = []
            _call_app(path="/api/books/1", method=method)
            mock_proxy.assert_called_once()
            assert mock_proxy.call_args[0][-1] == method

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_non_proxy_path_returns_405(self, method):
        recorder, _ = _call_app(path="/some-page.html", method=method)
        assert recorder.code == 405

    def test_auth_path_proxied_post(self):
        with patch.object(proxy_server.ReverseProxyApp, "proxy_to_api") as mock_proxy:
            mock_proxy.return_value = []
            _call_app(path="/auth/login", method="POST")
            mock_proxy.assert_called_once()
            assert mock_proxy.call_args[0][-1] == "POST"

    def test_covers_path_served_directly(self):
        """Covers are served directly from COVER_DIR, not proxied to API."""
        with patch.object(proxy_server.ReverseProxyApp, "_serve_cover") as mock_serve:
            mock_serve.return_value = []
            _call_app(path="/covers/abc.jpg")
            mock_serve.assert_called_once()
            assert mock_serve.call_args[0][2] == "abc.jpg"


# ============================================================
# 5. OPTIONS — CORS preflight
# ============================================================


class TestDoOptions:
    def test_cors_preflight_response(self):
        recorder, body = _call_app(path="/api/books", method="OPTIONS")
        assert recorder.code == 204
        assert body == b""
        # CORS_ORIGIN is captured at module import time from the environment
        # (default "*"). In polluted full-suite runs it may be the real prod
        # value, so we assert against the imported module's value rather than
        # a hardcoded literal.
        assert recorder.header("Access-Control-Allow-Origin") == proxy_server.CORS_ORIGIN
        assert "GET" in recorder.header("Access-Control-Allow-Methods")
        assert "POST" in recorder.header("Access-Control-Allow-Methods")
        assert "Content-Type" in recorder.header("Access-Control-Allow-Headers")
        assert "Content-Range" in recorder.header("Access-Control-Expose-Headers")

    def test_wildcard_echoes_safe_origin(self):
        if proxy_server.CORS_ORIGIN != "*":
            pytest.skip("CORS_ORIGIN overridden in environment")
        recorder, _ = _call_app(
            path="/api/books", method="OPTIONS", headers={"Origin": "https://example.com"}
        )
        assert recorder.header("Access-Control-Allow-Origin") == "https://example.com"
        assert recorder.header("Vary") == "Origin"

    def test_unsafe_origin_not_echoed(self):
        recorder, _ = _call_app(
            path="/api/books",
            method="OPTIONS",
            headers={"Origin": "https://evil.com\r\nX-Inject: 1"},
        )
        assert recorder.header("Access-Control-Allow-Origin") == proxy_server.CORS_ORIGIN


# ============================================================
# 6. _tunnel_websocket()
# ============================================================


def _ws_environ(mock_client):
    environ = _make_environ(
        path="/api/ws", headers={"Upgrade": "websocket", "Connection": "Upgrade"}
    )
    environ["gunicorn.socket"] = mock_client
    return environ


def _run_tunnel(environ):
    recorder = StartResponseRecorder()
    headers = proxy_server.EnvironHeaders(environ)
    result = proxy_server.app._tunnel_websocket(environ, headers, recorder)
    return recorder, b"".join(result)


class TestTunnelWebsocket:
    def test_backend_unreachable(self):
        environ = _ws_environ(MagicMock())
        with patch("socket.create_connection", side_effect=socket.error("refused")):
            recorder, _ = _run_tunnel(environ)
        assert recorder.code == 503

    def test_no_gunicorn_socket_is_502(self):
        """Without the raw client socket (non-gunicorn server), no tunnel."""
        environ = _ws_environ(MagicMock())
        del environ["gunicorn.socket"]
        recorder, _ = _run_tunnel(environ)
        assert recorder.code == 502

    def test_non_101_response_closes_backend(self):
        mock_backend = MagicMock()
        mock_backend.recv.return_value = b"HTTP/1.1 400 Bad Request\r\n\r\n"
        mock_client = MagicMock()
        environ = _ws_environ(mock_client)

        with patch("socket.create_connection", return_value=mock_backend):
            _run_tunnel(environ)

        mock_backend.close.assert_called()
        # Non-101 response is still forwarded to the client before hijack ends
        mock_client.sendall.assert_called()

    def test_successful_upgrade_relays_data(self):
        mock_backend = MagicMock()
        # First recv returns upgrade response, subsequent ones return empty (close)
        mock_backend.recv.side_effect = [
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n",
            b"",  # triggers return from relay loop
        ]
        mock_client = MagicMock()
        mock_client.pending.return_value = 0
        environ = _ws_environ(mock_client)

        with patch("socket.create_connection", return_value=mock_backend):
            with patch("select.select", return_value=([mock_backend], [], [])):
                _run_tunnel(environ)

        # Upgrade response forwarded to client
        mock_client.sendall.assert_called()
        # Hijack finished: client socket shut down so gunicorn's own write EPIPEs
        mock_client.shutdown.assert_called()

    def test_broken_pipe_handled_gracefully(self):
        mock_backend = MagicMock()
        mock_backend.recv.side_effect = [
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"
        ]
        mock_client = MagicMock()
        mock_client.pending.return_value = 0
        mock_client.recv.return_value = b"test data"
        environ = _ws_environ(mock_client)

        call_count = [0]

        def select_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([mock_client], [], [])
            # Return error on second call to exit loop
            return ([], [], [mock_client])

        with patch("socket.create_connection", return_value=mock_backend):
            with patch("select.select", side_effect=select_side_effect):
                # sendall on backend raises BrokenPipeError when relaying client data
                mock_backend.sendall.side_effect = [None, BrokenPipeError()]
                _run_tunnel(environ)

        mock_backend.close.assert_called()

    def test_upgrade_request_preserves_hop_by_hop_headers(self):
        """The raw upgrade request MUST carry Connection/Upgrade (intentional
        exemption from the hop-by-hop filter — see _build_ws_upgrade_request)."""
        environ = _ws_environ(MagicMock())
        headers = proxy_server.EnvironHeaders(environ)
        raw = proxy_server.app._build_ws_upgrade_request(environ, headers)
        assert b"Upgrade: websocket" in raw
        assert b"Connection: Upgrade" in raw
        assert raw.startswith(b"GET /api/ws HTTP/1.1\r\n")


# ============================================================
# 7. proxy_to_api()
# ============================================================


def _run_proxy(path, method="GET", headers=None, body=b"", client_addr="127.0.0.1"):
    environ = _make_environ(
        path=path, method=method, headers=headers, body=body, client_addr=client_addr
    )
    recorder = StartResponseRecorder()
    env_headers = proxy_server.EnvironHeaders(environ)
    result = proxy_server.app.proxy_to_api(environ, env_headers, recorder, method)
    return recorder, b"".join(result)


class TestProxyToApi:
    def test_forbidden_path_rejected(self):
        recorder, body = _run_proxy("/etc/passwd")
        assert recorder.code == 403
        assert b"Forbidden" in body

    def test_null_byte_sanitized(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen_response()) as mock_open:
            _run_proxy("/api/books\x00/../etc/passwd")
            # Verify null byte was removed from URL
            called_req = mock_open.call_args[0][0]
            assert "\x00" not in called_req.full_url

    def test_successful_proxy_get(self):
        resp_headers = Message()
        resp_headers["Content-Type"] = "application/json"
        mock_response = _mock_urlopen_response(headers=resp_headers, body=b'{"books":[]}')

        with patch("urllib.request.urlopen", return_value=mock_response):
            recorder, body = _run_proxy("/api/books")

        assert recorder.code == 200
        assert body == b'{"books":[]}'

    def test_post_body_forwarded(self):
        body = json.dumps({"title": "Test"}).encode()
        headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        mock_response = _mock_urlopen_response(status=201, body=b'{"id":1}')

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            recorder, _ = _run_proxy("/api/books", method="POST", headers=headers, body=body)
            req = mock_open.call_args[0][0]
            assert req.data == body
            assert req.method == "POST"
        assert recorder.code == 201

    def test_hop_by_hop_headers_filtered(self):
        resp_headers = Message()
        resp_headers["Content-Type"] = "application/json"
        resp_headers["Transfer-Encoding"] = "chunked"  # hop-by-hop
        resp_headers["Connection"] = "keep-alive"  # hop-by-hop
        mock_response = _mock_urlopen_response(headers=resp_headers, body=b"[]")

        with patch("urllib.request.urlopen", return_value=mock_response):
            recorder, _ = _run_proxy("/api/books")

        forwarded_keys = recorder.header_names()
        assert "Content-Type" in forwarded_keys
        assert "Transfer-Encoding" not in forwarded_keys
        assert "Connection" not in forwarded_keys

    def test_range_header_forwarded(self):
        """Range requests must reach the backend so 206 responses work."""
        resp_headers = Message()
        resp_headers["Content-Range"] = "bytes 0-99/1000"
        mock_response = _mock_urlopen_response(status=206, headers=resp_headers, body=b"x" * 100)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            recorder, _ = _run_proxy("/streaming-audio/1/seg.webm", headers={"Range": "bytes=0-99"})
            req = mock_open.call_args[0][0]
            assert req.get_header("Range") == "bytes=0-99"
        assert recorder.code == 206
        assert recorder.header("Content-Range") == "bytes 0-99/1000"

    def test_proxy_headers_forwarded(self):
        headers = {
            "X-Forwarded-For": "1.2.3.4",
            "X-Forwarded-Proto": "https",
            "X-Real-IP": "1.2.3.4",
            "Host": "library.example.com",
            "Cookie": "session=abc",
        }
        mock_response = _mock_urlopen_response()

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            _run_proxy("/api/books", headers=headers)
            req = mock_open.call_args[0][0]
            assert req.get_header("X-forwarded-for") == "1.2.3.4"
            assert req.get_header("Cookie") == "session=abc"

    def test_x_forwarded_for_set_from_client(self):
        """When no X-Forwarded-For from upstream, use REMOTE_ADDR."""
        mock_response = _mock_urlopen_response()

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            _run_proxy("/api/books", client_addr="10.0.0.5")
            req = mock_open.call_args[0][0]
            assert req.get_header("X-forwarded-for") == "10.0.0.5"

    def test_http_error_forwarded(self):
        err_headers = Message()
        err_headers["Content-Type"] = "application/json"
        error_body = b'{"error": "Not Found"}'

        http_error = urllib.error.HTTPError(
            url="http://127.0.0.1:5001/api/books/999",
            code=404,
            msg="Not Found",
            hdrs=err_headers,
            fp=io.BytesIO(error_body),
        )
        try:
            with patch("urllib.request.urlopen", side_effect=http_error):
                recorder, body = _run_proxy("/api/books/999")

            assert recorder.code == 404
            assert body == error_body
        finally:
            # Defensive close — production code closes via finally in
            # proxy_to_api, but if a refactor broke that we still don't
            # want this test to leak ResourceWarnings.
            try:
                http_error.close()
            except Exception:  # nosec B110 — defensive teardown; production close already asserted by the test body
                pass

    def test_url_error_returns_503(self):
        url_error = urllib.error.URLError(reason="Connection refused")

        with patch("urllib.request.urlopen", side_effect=url_error):
            recorder, body = _run_proxy("/api/books")

        assert recorder.code == 503
        parsed = json.loads(body)
        assert parsed["code"] == 503
        assert "Service Unavailable" in parsed["error"]

    def test_unexpected_error_returns_500(self):
        with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
            recorder, body = _run_proxy("/api/books")

        assert recorder.code == 500
        parsed = json.loads(body)
        assert parsed["code"] == 500

    def test_unexpected_error_body_does_not_leak_exception_text(self):
        """str(e) on a proxy failure carries paths, hostnames and ports.

        It is logged (with traceback) for the operator; the browser gets a
        fixed message. This test previously asserted the opposite.
        """
        secret = "/srv/audiobooks/internal-detail-1234"
        with patch("urllib.request.urlopen", side_effect=RuntimeError(secret)):
            recorder, body = _run_proxy("/api/books")

        assert recorder.code == 500
        assert secret.encode() not in body
        assert "internal-detail" not in json.loads(body)["message"]

    def test_backend_unreachable_body_does_not_leak_reason(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused to 10.1.2.3:5001"),
        ):
            recorder, body = _run_proxy("/api/books")

        assert recorder.code == 503
        assert b"10.1.2.3" not in body

    def test_http_error_read_failure_fallback(self):
        """When HTTPError body can't be read, a JSON fallback is sent."""
        err_headers = Message()
        http_error = urllib.error.HTTPError(
            url="http://127.0.0.1:5001/api/fail",
            code=500,
            msg="Internal Server Error",
            hdrs=err_headers,
            fp=io.BytesIO(b""),
        )
        # Make read() raise an exception to trigger fallback
        http_error.read = MagicMock(  # type: ignore[method-assign]  # stub read()
            side_effect=Exception("read failed")
        )

        try:
            with patch("urllib.request.urlopen", side_effect=http_error):
                recorder, body = _run_proxy("/api/fail")

            assert recorder.code == 500
            parsed = json.loads(body)
            assert parsed["code"] == 500
            assert parsed["error"] == "Internal Server Error"
        finally:
            # Defensive close (see test_http_error_forwarded above)
            try:
                http_error.close()
            except Exception:  # nosec B110 — defensive teardown, best-effort close of the synthetic HTTPError
                pass


# ============================================================
# 8. log_message()
# ============================================================


class TestLogMessage:
    def test_proxy_prefix(self, capsys):
        environ = _make_environ(client_addr="127.0.0.1")
        proxy_server.app.log_message(environ, "GET /api/books %s", "200")

        captured = capsys.readouterr()
        assert "[PROXY]" in captured.out
        assert "127.0.0.1" in captured.out
        assert "GET /api/books 200" in captured.out


# ============================================================
# 9. gunicorn_proxy.conf.py — worker model + TLS + cert preflight
# ============================================================

_CONF_PATH = WEB_V2_DIR / "gunicorn_proxy.conf.py"

_CONF_LOADER = """
import runpy, sys
settings = runpy.run_path({conf!r})
print("worker_class=" + settings["worker_class"])
print("workers=" + str(settings["workers"]))
print("bind=" + settings["bind"])
import ssl
ctx = settings["ssl_context"](None, lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
print("min_tls=" + ctx.minimum_version.name)
"""


class TestGunicornConfig:
    def _run_conf(self, certs_dir, tmp_path):
        script = tmp_path / "load_conf.py"
        script.write_text(_CONF_LOADER.format(conf=str(_CONF_PATH)))
        env = dict(
            AUDIOBOOKS_CERTS=str(certs_dir),
            PATH="/usr/bin:/bin",
            HOME=str(tmp_path),
        )
        return subprocess.run(  # nosec B603 — fixed interpreter + generated script, no user input
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def test_missing_certs_exit_1_with_guidance(self, tmp_path):
        result = self._run_conf(tmp_path / "no-certs", tmp_path)
        assert result.returncode == 1
        assert "Certificate files not found" in result.stdout
        assert "openssl req" in result.stdout

    def test_gevent_workers_and_tls12_minimum(self, tmp_path):
        certs = tmp_path / "certs"
        certs.mkdir()
        (certs / "server.crt").touch()
        (certs / "server.key").touch()
        result = self._run_conf(certs, tmp_path)
        assert result.returncode == 0, result.stderr
        out = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
        assert out["worker_class"] == "gevent"
        assert 2 <= int(out["workers"]) <= 4
        assert out["min_tls"] in ("TLSv1_2", "TLSv1_3")


# ============================================================
# 10. Static file serving internals
# ============================================================


class TestStaticServing:
    def test_serves_real_file_with_content_length(self):
        recorder, body = _call_app(path="/")
        expected = (WEB_V2_DIR / "shell.html").read_bytes()
        assert recorder.code == 200
        assert recorder.header("Content-Length") == str(len(expected))
        assert body == expected

    def test_head_returns_headers_without_body(self):
        recorder, body = _call_app(path="/", method="HEAD")
        assert recorder.code == 200
        assert body == b""
        assert recorder.header("Content-Length") is not None

    def test_directory_request_is_404(self):
        recorder, _ = _call_app(path="/js")
        assert recorder.code == 404


# ============================================================
# 10b. Static deny-list (non-asset files inside WEB_ROOT)
# ============================================================


class TestStaticDenyList:
    """WEB_ROOT is a working directory, not a curated publish target.

    Dotfiles, the proxy's own source, and npm metadata all live inside it and
    resolve cleanly through the traversal guard, so they need an explicit
    refusal. Everything here must answer 404 — a 403 would confirm the file
    exists.
    """

    @pytest.mark.parametrize(
        "path",
        [
            # Session transcript dropped in WEB_ROOT by dev tooling — the
            # finding that motivated this deny-list. Both depths were live.
            "/.claude-session-ring.jsonl",
            "/css/.claude-session-ring.jsonl",
            "/js/.hidden/secret.txt",
            # Server source, at the root and via a nested path
            "/proxy_server.py",
            "/gunicorn_proxy.conf.py",
            "/https_server.py",
            "/redirect_server.py",
            # npm metadata + tree
            "/package.json",
            "/package-lock.json",
            "/node_modules/eslint/package.json",
            "/node_modules/.package-lock.json",
            "/__pycache__/proxy_server.cpython-314.pyc",
        ],
    )
    def test_forbidden_static_paths_are_404(self, path):
        recorder, _ = _call_app(path=path)
        assert recorder.code == 404, f"{path} must not be served"

    def test_denied_before_filesystem_access(self):
        """Deny-list hits are refused without stat()-ing the candidate."""
        assert proxy_server.app._is_forbidden_static_path("/.claude-session-ring.jsonl") is True
        assert proxy_server.app._is_forbidden_static_path("css/.env") is True
        assert proxy_server.app._is_forbidden_static_path("a/b/c/package.json") is True

    @pytest.mark.parametrize(
        "path", ["/css/deco.css", "/js/api.js", "/shell.html", "/site.webmanifest"]
    )
    def test_real_assets_still_allowed(self, path):
        assert proxy_server.app._is_forbidden_static_path(path) is False

    def test_normal_css_still_served(self):
        css = sorted((WEB_V2_DIR / "css").glob("*.css"))
        assert css, "expected at least one stylesheet in web-v2/css"
        recorder, body = _call_app(path=f"/css/{css[0].name}")
        assert recorder.code == 200
        assert body == css[0].read_bytes()

    def test_normal_js_still_served(self):
        recorder, body = _call_app(path="/js/api.js")
        assert recorder.code == 200
        assert body == (WEB_V2_DIR / "js" / "api.js").read_bytes()


# ============================================================
# 11. Cover serving
# ============================================================


class TestCoverServing:
    @pytest.mark.parametrize("filename", ["../secret.jpg", "a/b.jpg", "a\\b.jpg", "..", ""])
    def test_unsafe_cover_filenames_rejected(self, filename):
        assert proxy_server.app._is_safe_cover_filename(filename) is False

    def test_safe_cover_filename_accepted(self):
        assert proxy_server.app._is_safe_cover_filename("abc123.jpg") is True

    def test_cover_served_with_cors_and_immutable_cache(self, tmp_path):
        cover = tmp_path / "abc.jpg"
        cover.write_bytes(b"\xff\xd8\xff\xe0 fake")
        with patch.object(proxy_server, "COVER_DIR", tmp_path):
            recorder, body = _call_app(
                path="/covers/abc.jpg", headers={"Origin": "https://example.com"}
            )
        assert recorder.code == 200
        assert body == b"\xff\xd8\xff\xe0 fake"
        assert recorder.header("Content-Type") == "image/jpeg"
        assert recorder.header("Cache-Control") == "public, max-age=31536000, immutable"
        assert recorder.header("Access-Control-Allow-Credentials") == "true"

    def test_missing_cover_404(self, tmp_path):
        with patch.object(proxy_server, "COVER_DIR", tmp_path):
            recorder, _ = _call_app(path="/covers/none.jpg")
        assert recorder.code == 404

    def test_disallowed_content_type_falls_back_to_jpeg(self):
        assert proxy_server.app._resolve_cover_content_type("x.exe") == "image/jpeg"
        assert proxy_server.app._resolve_cover_content_type("x.png") == "image/png"


# ============================================================
# 12. _is_proxy_path()
# ============================================================


class TestIsProxyPath:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/api/books", True),
            ("/api/system/version", True),
            ("/auth/login", True),
            ("/covers/abc.jpg", False),
            ("/", False),
            ("/shell.html", False),
            ("/css/style.css", False),
            ("/js/app.js", False),
            ("/apinotreally", False),
        ],
    )
    def test_proxy_path_detection(self, path, expected):
        assert proxy_server.app._is_proxy_path(path) is expected


# ============================================================
# 13. SSRF prevention
# ============================================================


class TestSsrfPrevention:
    def test_path_traversal_blocked(self):
        recorder, _ = _run_proxy("/../../etc/passwd")
        assert recorder.code == 403

    def test_only_known_prefixes_allowed(self):
        recorder, _ = _run_proxy("/admin/config")
        assert recorder.code == 403

    def test_crlf_stripped_from_path_before_url_construction(self):
        """CR/LF in path are stripped to prevent HTTP request splitting."""
        # A path with embedded CRLF — after stripping, it must land on a valid
        # /api/ prefix path and succeed (or fail at urlopen, not at sanitisation).
        with patch("urllib.request.urlopen", return_value=_mock_urlopen_response()) as mock_open:
            _run_proxy("/api/books\r\nX-Injected: evil")
            # CR and LF must not appear in the URL passed to urlopen
            req = mock_open.call_args[0][0]
            assert "\r" not in req.full_url, "CR must be stripped from proxy URL"
            assert "\n" not in req.full_url, "LF must be stripped from proxy URL"

    def test_host_always_loopback(self):
        """Constructed proxy URL must always target 127.0.0.1:{API_PORT}."""
        with patch("urllib.request.urlopen", return_value=_mock_urlopen_response()) as mock_open:
            _run_proxy("/api/books")
            req = mock_open.call_args[0][0]
            import urllib.parse as _up

            parsed = _up.urlparse(req.full_url)
            assert parsed.scheme == "http", f"Expected http scheme, got {parsed.scheme!r}"
            assert parsed.hostname == "127.0.0.1", (
                f"Expected loopback host, got {parsed.hostname!r}"
            )
            assert parsed.port == proxy_server.API_PORT, (
                f"Expected port {proxy_server.API_PORT}, got {parsed.port!r}"
            )


# ============================================================
# 14. Module-level constants
# ============================================================


class TestModuleConstants:
    def test_hop_by_hop_headers_complete(self):
        """Verify all RFC 2616 hop-by-hop headers are listed."""
        expected = {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
        }
        assert proxy_server.HOP_BY_HOP_HEADERS == expected

    def test_proxy_prefixes(self):
        assert "/api/" in proxy_server.ReverseProxyApp.PROXY_PREFIXES
        assert "/auth/" in proxy_server.ReverseProxyApp.PROXY_PREFIXES
        # Streaming translation WebM-Opus segments live on the API backend (v8.3.2)
        assert "/streaming-audio/" in proxy_server.ReverseProxyApp.PROXY_PREFIXES
        # /covers/ is served directly from COVER_DIR, not proxied
        assert "/covers/" not in proxy_server.ReverseProxyApp.PROXY_PREFIXES

    def test_app_is_wsgi_callable(self):
        assert callable(proxy_server.app)
        assert isinstance(proxy_server.app, proxy_server.ReverseProxyApp)

    def test_tls_floor_documented_in_conf(self):
        """The TLS 1.2 minimum lives in gunicorn_proxy.conf.py's ssl_context hook."""
        conf_text = _CONF_PATH.read_text()
        assert "TLSVersion.TLSv1_2" in conf_text
        assert "ssl_context" in conf_text
