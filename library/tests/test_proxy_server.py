"""
Unit tests for library/web-v2/proxy_server.py

Tests the HTTPS reverse proxy handler including:
- WebSocket upgrade detection
- Cache-Control header injection
- HTTP method routing (GET/POST/PUT/DELETE/OPTIONS)
- WebSocket tunneling
- API proxying with SSRF prevention
- Path sanitization
- Error handling
- Server configuration
- main() TLS setup
"""

import http.server
import io
import json
import socket
import ssl
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# We need to mock the config imports before importing proxy_server
# because it imports config values at module level.

MOCK_CONFIG = {
    "AUDIOBOOKS_API_PORT": 5001,
    "AUDIOBOOKS_WEB_PORT": 8443,
    "AUDIOBOOKS_CERTS": Path("/tmp/test-certs"),
    "AUDIOBOOKS_BIND_ADDRESS": "0.0.0.0",
}


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    """Ensure proxy_server module-level config is sane for tests."""
    # proxy_server reads these at import time; they're already set
    # by conftest adding library/ to sys.path. We patch at the module
    # level to avoid side effects.
    pass


def _import_proxy_server():
    """Import proxy_server with config already on sys.path.

    web-v2 has a hyphen so it's not a valid Python package name.
    We add it to sys.path and import proxy_server directly.
    """
    web_v2_dir = str(Path(__file__).parent.parent / "web-v2")
    if web_v2_dir not in sys.path:
        sys.path.insert(0, web_v2_dir)
    import proxy_server as ps
    return ps


# Lazy import — done once
proxy_server = _import_proxy_server()


def _make_headers(extra=None):
    """Build an http.client.HTTPMessage (email.message.Message) with optional extras."""
    msg = Message()
    if extra:
        for k, v in extra.items():
            msg[k] = v
    return msg


def _make_handler(path="/", method="GET", headers=None, body=None):
    """Create a mock ReverseProxyHandler without starting a real server.

    We bypass __init__ entirely and set the attributes the handler
    methods actually read.
    """
    handler = object.__new__(proxy_server.ReverseProxyHandler)
    handler.path = path
    handler.command = method
    handler.headers = headers or _make_headers()
    handler.client_address = ("127.0.0.1", 54321)
    handler.request = MagicMock()  # raw socket
    handler.rfile = io.BytesIO(body or b"")
    handler.wfile = io.BytesIO()
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.server = MagicMock()
    handler.close_connection = True
    handler.directory = "."

    # Track headers sent via send_header / end_headers
    handler._sent_headers = []
    handler._response_code = None
    handler._headers_finished = False

    original_send_header = http.server.BaseHTTPRequestHandler.send_header

    def mock_send_header(self, keyword, value):
        self._sent_headers.append((keyword, value))

    def mock_send_response(self, code, message=None):
        self._response_code = code

    def mock_end_headers(self):
        self._headers_finished = True

    handler.send_header = lambda k, v: mock_send_header(handler, k, v)
    handler.send_response = lambda c, m=None: mock_send_response(handler, c, m)
    # Don't replace end_headers — we want the real one for cache tests
    # but we need to prevent the parent from writing to wfile.
    # We'll override per-test as needed.

    return handler


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
        """Headers returning None should not crash."""
        headers = _make_headers()
        # Message.__getitem__ returns None for missing keys
        assert proxy_server.is_websocket_upgrade(headers) is False


# ============================================================
# 2. end_headers() — Cache-Control injection
# ============================================================


class TestEndHeadersCacheControl:
    def _get_cache_header(self, path):
        """Return the Cache-Control value set by end_headers for a given path."""
        handler = _make_handler(path=path)
        # Replace end_headers' parent call to avoid writing to wfile
        with patch.object(http.server.SimpleHTTPRequestHandler, "end_headers"):
            handler.end_headers = proxy_server.ReverseProxyHandler.end_headers.__get__(handler)
            handler.end_headers()
        for k, v in handler._sent_headers:
            if k == "Cache-Control":
                return v
        return None

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

    def test_api_path_no_cache_header(self):
        """API paths should NOT get cache headers (Flask handles them)."""
        val = self._get_cache_header("/api/books")
        assert val is None

    def test_auth_path_no_cache_header(self):
        val = self._get_cache_header("/auth/login")
        assert val is None

    def test_unknown_extension_no_cache_header(self):
        val = self._get_cache_header("/data/file.json")
        assert val is None


# ============================================================
# 3. do_GET() — routing
# ============================================================


class TestDoGet:
    def test_shell_html_redirects_to_root(self):
        handler = _make_handler(path="/shell.html")
        handler.end_headers = MagicMock()
        handler.do_GET()
        assert handler._response_code == 301
        locations = [v for k, v in handler._sent_headers if k == "Location"]
        assert locations == ["/"]

    def test_shell_html_preserves_query(self):
        handler = _make_handler(path="/shell.html?autoplay=1")
        handler.end_headers = MagicMock()
        handler.do_GET()
        assert handler._response_code == 301
        locations = [v for k, v in handler._sent_headers if k == "Location"]
        assert locations == ["/?autoplay=1"]

    def test_shell_html_strips_crlf(self):
        """Prevent HTTP response splitting via CRLF in query string."""
        handler = _make_handler(path="/shell.html?foo=bar\r\nEvil: header")
        handler.end_headers = MagicMock()
        handler.do_GET()
        locations = [v for k, v in handler._sent_headers if k == "Location"]
        assert len(locations) == 1
        assert "\r" not in locations[0]
        assert "\n" not in locations[0]

    def test_root_rewrites_to_shell_html(self):
        handler = _make_handler(path="/")
        with patch.object(http.server.SimpleHTTPRequestHandler, "do_GET") as mock_get:
            handler.do_GET()
            assert handler.path == "/shell.html"
            mock_get.assert_called_once()

    def test_root_preserves_query_string(self):
        handler = _make_handler(path="/?autoplay=1&book=42")
        with patch.object(http.server.SimpleHTTPRequestHandler, "do_GET") as mock_get:
            handler.do_GET()
            assert handler.path == "/shell.html?autoplay=1&book=42"
            mock_get.assert_called_once()

    def test_static_file_passthrough(self):
        handler = _make_handler(path="/css/style.css")
        with patch.object(http.server.SimpleHTTPRequestHandler, "do_GET") as mock_get:
            handler.do_GET()
            mock_get.assert_called_once()

    def test_api_path_proxied(self):
        handler = _make_handler(path="/api/books")
        with patch.object(
            proxy_server.ReverseProxyHandler, "proxy_to_api"
        ) as mock_proxy:
            handler.do_GET()
            mock_proxy.assert_called_once_with("GET")

    def test_websocket_upgrade_tunneled(self):
        headers = _make_headers({"Upgrade": "websocket", "Connection": "Upgrade"})
        handler = _make_handler(path="/api/ws/position", headers=headers)
        with patch.object(
            proxy_server.ReverseProxyHandler, "_tunnel_websocket"
        ) as mock_tunnel:
            handler.do_GET()
            mock_tunnel.assert_called_once()


# ============================================================
# 4. do_POST / do_PUT / do_DELETE
# ============================================================


class TestHttpMethods:
    @pytest.mark.parametrize("method_name", ["do_POST", "do_PUT", "do_DELETE"])
    def test_proxy_path_forwards(self, method_name):
        handler = _make_handler(path="/api/books/1")
        with patch.object(
            proxy_server.ReverseProxyHandler, "proxy_to_api"
        ) as mock_proxy:
            getattr(handler, method_name)()
            expected_method = method_name.replace("do_", "")
            mock_proxy.assert_called_once_with(expected_method)

    @pytest.mark.parametrize("method_name", ["do_POST", "do_PUT", "do_DELETE"])
    def test_non_proxy_path_returns_405(self, method_name):
        handler = _make_handler(path="/some-page.html")
        handler.end_headers = MagicMock()
        # send_error writes to wfile, mock it
        handler.send_error = MagicMock()
        getattr(handler, method_name)()
        handler.send_error.assert_called_once_with(405, "Method Not Allowed")

    def test_auth_path_proxied_post(self):
        handler = _make_handler(path="/auth/login")
        with patch.object(
            proxy_server.ReverseProxyHandler, "proxy_to_api"
        ) as mock_proxy:
            handler.do_POST()
            mock_proxy.assert_called_once_with("POST")

    def test_covers_path_proxied_get(self):
        handler = _make_handler(path="/covers/abc.jpg")
        with patch.object(
            proxy_server.ReverseProxyHandler, "proxy_to_api"
        ) as mock_proxy:
            handler.do_GET()
            mock_proxy.assert_called_once_with("GET")


# ============================================================
# 5. do_OPTIONS() — CORS preflight
# ============================================================


class TestDoOptions:
    def test_cors_preflight_response(self):
        handler = _make_handler(path="/api/books")
        # end_headers needs to work but not write to socket
        with patch.object(http.server.SimpleHTTPRequestHandler, "end_headers"):
            handler.end_headers = proxy_server.ReverseProxyHandler.end_headers.__get__(handler)
            handler.do_OPTIONS()

        assert handler._response_code == 204
        header_dict = dict(handler._sent_headers)
        assert header_dict["Access-Control-Allow-Origin"] == "*"
        assert "GET" in header_dict["Access-Control-Allow-Methods"]
        assert "POST" in header_dict["Access-Control-Allow-Methods"]
        assert "Content-Type" in header_dict["Access-Control-Allow-Headers"]
        assert "Content-Range" in header_dict["Access-Control-Expose-Headers"]


# ============================================================
# 6. _tunnel_websocket()
# ============================================================


class TestTunnelWebsocket:
    def test_backend_unreachable(self):
        handler = _make_handler(path="/api/ws/position")
        handler.command = "GET"
        handler.send_error = MagicMock()

        with patch("socket.create_connection", side_effect=socket.error("refused")):
            handler._tunnel_websocket()
        handler.send_error.assert_called_once()
        assert handler.send_error.call_args[0][0] == 503

    def test_non_101_response_closes_backend(self):
        handler = _make_handler(
            path="/api/ws",
            headers=_make_headers({"Upgrade": "websocket", "Connection": "Upgrade"}),
        )
        handler.command = "GET"

        mock_backend = MagicMock()
        # Return a non-101 response
        mock_backend.recv.return_value = b"HTTP/1.1 400 Bad Request\r\n\r\n"

        mock_client = MagicMock()
        handler.request = mock_client

        with patch("socket.create_connection", return_value=mock_backend):
            handler._tunnel_websocket()

        mock_backend.close.assert_called()

    def test_successful_upgrade_relays_data(self):
        handler = _make_handler(
            path="/api/ws",
            headers=_make_headers({"Upgrade": "websocket", "Connection": "Upgrade"}),
        )
        handler.command = "GET"

        mock_backend = MagicMock()
        # First recv returns upgrade response, subsequent ones return empty (close)
        mock_backend.recv.side_effect = [
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n",
            b"",  # triggers return from relay loop
        ]

        mock_client = MagicMock()
        mock_client.pending.return_value = 0
        handler.request = mock_client

        with patch("socket.create_connection", return_value=mock_backend):
            with patch("select.select", return_value=([mock_backend], [], [])):
                handler._tunnel_websocket()

        # Upgrade response forwarded to client
        mock_client.sendall.assert_called()

    def test_broken_pipe_handled_gracefully(self):
        handler = _make_handler(
            path="/api/ws",
            headers=_make_headers({"Upgrade": "websocket", "Connection": "Upgrade"}),
        )
        handler.command = "GET"

        mock_backend = MagicMock()
        # First recv for upgrade response, subsequent for relay
        mock_backend.recv.side_effect = [
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n",
        ]

        mock_client = MagicMock()
        mock_client.pending.return_value = 0
        # First sendall succeeds (upgrade response), second raises BrokenPipe
        mock_client.recv.return_value = b"test data"
        handler.request = mock_client

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
                handler._tunnel_websocket()

        mock_backend.close.assert_called()


# ============================================================
# 7. proxy_to_api()
# ============================================================


class TestProxyToApi:
    def test_forbidden_path_rejected(self):
        handler = _make_handler(path="/etc/passwd")
        handler.send_error = MagicMock()
        handler.proxy_to_api("GET")
        handler.send_error.assert_called_once_with(403, "Forbidden - Invalid path")

    def test_null_byte_sanitized(self):
        handler = _make_handler(path="/api/books\x00/../etc/passwd")
        handler.end_headers = MagicMock()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = Message()
        mock_response.read.side_effect = [b"OK", b""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            handler.proxy_to_api("GET")
            # Verify null byte was removed from URL
            called_req = mock_open.call_args[0][0]
            assert "\x00" not in called_req.full_url

    def test_successful_proxy_get(self):
        handler = _make_handler(path="/api/books")
        handler.end_headers = MagicMock()

        resp_headers = Message()
        resp_headers["Content-Type"] = "application/json"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = resp_headers
        mock_response.read.side_effect = [b'{"books":[]}', b""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            handler.proxy_to_api("GET")

        assert handler._response_code == 200
        assert handler.wfile.getvalue() == b'{"books":[]}'

    def test_post_body_forwarded(self):
        body = json.dumps({"title": "Test"}).encode()
        headers = _make_headers({"Content-Length": str(len(body)), "Content-Type": "application/json"})
        handler = _make_handler(path="/api/books", method="POST", headers=headers, body=body)
        handler.end_headers = MagicMock()

        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.headers = Message()
        mock_response.read.side_effect = [b'{"id":1}', b""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            handler.proxy_to_api("POST")
            req = mock_open.call_args[0][0]
            assert req.data == body
            assert req.method == "POST"

    def test_hop_by_hop_headers_filtered(self):
        handler = _make_handler(path="/api/books")
        handler.end_headers = MagicMock()

        resp_headers = Message()
        resp_headers["Content-Type"] = "application/json"
        resp_headers["Transfer-Encoding"] = "chunked"  # hop-by-hop
        resp_headers["Connection"] = "keep-alive"  # hop-by-hop

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = resp_headers
        mock_response.read.side_effect = [b"[]", b""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            handler.proxy_to_api("GET")

        forwarded_keys = [k for k, v in handler._sent_headers]
        assert "Content-Type" in forwarded_keys
        assert "Transfer-Encoding" not in forwarded_keys
        assert "Connection" not in forwarded_keys

    def test_proxy_headers_forwarded(self):
        headers = _make_headers({
            "X-Forwarded-For": "1.2.3.4",
            "X-Forwarded-Proto": "https",
            "X-Real-IP": "1.2.3.4",
            "Host": "library.thebosco.club",
            "Cookie": "session=abc",
        })
        handler = _make_handler(path="/api/books", headers=headers)
        handler.end_headers = MagicMock()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = Message()
        mock_response.read.side_effect = [b"ok", b""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            handler.proxy_to_api("GET")
            req = mock_open.call_args[0][0]
            assert req.get_header("X-forwarded-for") == "1.2.3.4"
            assert req.get_header("Cookie") == "session=abc"

    def test_x_forwarded_for_set_from_client(self):
        """When no X-Forwarded-For from upstream, use client_address."""
        handler = _make_handler(path="/api/books")
        handler.client_address = ("10.0.0.5", 12345)
        handler.end_headers = MagicMock()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = Message()
        mock_response.read.side_effect = [b"ok", b""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
            handler.proxy_to_api("GET")
            req = mock_open.call_args[0][0]
            assert req.get_header("X-forwarded-for") == "10.0.0.5"

    def test_http_error_forwarded(self):
        handler = _make_handler(path="/api/books/999")
        handler.end_headers = MagicMock()

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

        with patch("urllib.request.urlopen", side_effect=http_error):
            handler.proxy_to_api("GET")

        assert handler._response_code == 404
        assert handler.wfile.getvalue() == error_body

    def test_url_error_returns_503(self):
        handler = _make_handler(path="/api/books")
        handler.end_headers = MagicMock()

        url_error = urllib.error.URLError(reason="Connection refused")

        with patch("urllib.request.urlopen", side_effect=url_error):
            handler.proxy_to_api("GET")

        assert handler._response_code == 503
        body = json.loads(handler.wfile.getvalue())
        assert body["code"] == 503
        assert "Service Unavailable" in body["error"]

    def test_unexpected_error_returns_500(self):
        handler = _make_handler(path="/api/books")
        handler.end_headers = MagicMock()

        with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
            handler.proxy_to_api("GET")

        assert handler._response_code == 500
        body = json.loads(handler.wfile.getvalue())
        assert body["code"] == 500
        assert "unexpected" in body["message"]

    def test_http_error_read_failure_fallback(self):
        """When HTTPError body can't be read, a JSON fallback is sent."""
        handler = _make_handler(path="/api/fail")
        handler.end_headers = MagicMock()

        err_headers = Message()
        http_error = urllib.error.HTTPError(
            url="http://127.0.0.1:5001/api/fail",
            code=500,
            msg="Internal Server Error",
            hdrs=err_headers,
            fp=io.BytesIO(b""),
        )
        # Make read() raise an exception to trigger fallback
        http_error.read = MagicMock(side_effect=Exception("read failed"))

        with patch("urllib.request.urlopen", side_effect=http_error):
            handler.proxy_to_api("GET")

        assert handler._response_code == 500
        body = json.loads(handler.wfile.getvalue())
        assert body["code"] == 500
        assert body["error"] == "Internal Server Error"


# ============================================================
# 8. log_message()
# ============================================================


class TestLogMessage:
    def test_proxy_prefix(self, capsys):
        handler = _make_handler()
        handler.address_string = MagicMock(return_value="127.0.0.1")
        handler.log_message("GET /api/books %s", "200")

        captured = capsys.readouterr()
        assert "[PROXY]" in captured.out
        assert "127.0.0.1" in captured.out
        assert "GET /api/books 200" in captured.out


# ============================================================
# 9. ReuseHTTPServer
# ============================================================


class TestReuseHTTPServer:
    def test_daemon_threads_enabled(self):
        assert proxy_server.ReuseHTTPServer.daemon_threads is True

    def test_so_reuseaddr_set(self):
        """server_bind should set SO_REUSEADDR on the socket."""
        server = object.__new__(proxy_server.ReuseHTTPServer)
        mock_socket = MagicMock()
        server.socket = mock_socket
        server.server_address = ("0.0.0.0", 8443)

        with patch.object(http.server.ThreadingHTTPServer, "server_bind"):
            server.server_bind()

        mock_socket.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )


# ============================================================
# 10. main()
# ============================================================


class TestMain:
    def test_missing_cert_exits(self, tmp_path):
        """main() should sys.exit(1) if cert files are missing."""
        with patch.object(proxy_server, "CERT_FILE", tmp_path / "no.crt"):
            with patch.object(proxy_server, "KEY_FILE", tmp_path / "no.key"):
                with pytest.raises(SystemExit) as exc_info:
                    proxy_server.main()
                assert exc_info.value.code == 1

    def test_tls_context_configured(self, tmp_path):
        """main() should create a TLS context with TLS 1.2 minimum."""
        cert = tmp_path / "server.crt"
        key = tmp_path / "server.key"
        cert.touch()
        key.touch()

        mock_context = MagicMock(spec=ssl.SSLContext)
        mock_server = MagicMock()

        with patch.object(proxy_server, "CERT_FILE", cert), \
             patch.object(proxy_server, "KEY_FILE", key), \
             patch("ssl.SSLContext", return_value=mock_context), \
             patch.object(proxy_server, "ReuseHTTPServer", return_value=mock_server), \
             patch("os.chdir"):

            # Make serve_forever raise to exit the function
            mock_server.serve_forever.side_effect = KeyboardInterrupt()

            proxy_server.main()

            # Verify TLS 1.2 minimum was set
            assert mock_context.minimum_version == ssl.TLSVersion.TLSv1_2
            mock_context.load_cert_chain.assert_called_once_with(
                str(cert), str(key)
            )
            mock_context.wrap_socket.assert_called_once()


# ============================================================
# 11. _is_proxy_path()
# ============================================================


class TestIsProxyPath:
    @pytest.mark.parametrize("path,expected", [
        ("/api/books", True),
        ("/api/system/version", True),
        ("/auth/login", True),
        ("/covers/abc.jpg", True),
        ("/", False),
        ("/shell.html", False),
        ("/css/style.css", False),
        ("/js/app.js", False),
        ("/apinotreally", False),
    ])
    def test_proxy_path_detection(self, path, expected):
        handler = _make_handler(path=path)
        assert handler._is_proxy_path() is expected


# ============================================================
# 12. SSRF prevention
# ============================================================


class TestSsrfPrevention:
    def test_path_traversal_blocked(self):
        handler = _make_handler(path="/../../etc/passwd")
        handler.send_error = MagicMock()
        handler.proxy_to_api("GET")
        handler.send_error.assert_called_once_with(403, "Forbidden - Invalid path")

    def test_only_known_prefixes_allowed(self):
        handler = _make_handler(path="/admin/config")
        handler.send_error = MagicMock()
        handler.proxy_to_api("GET")
        handler.send_error.assert_called_once_with(403, "Forbidden - Invalid path")


# ============================================================
# 13. Module-level constants
# ============================================================


class TestModuleConstants:
    def test_hop_by_hop_headers_complete(self):
        """Verify all RFC 2616 hop-by-hop headers are listed."""
        expected = {
            "connection", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailers",
            "transfer-encoding", "upgrade",
        }
        assert proxy_server.HOP_BY_HOP_HEADERS == expected

    def test_proxy_prefixes(self):
        assert "/api/" in proxy_server.ReverseProxyHandler.PROXY_PREFIXES
        assert "/auth/" in proxy_server.ReverseProxyHandler.PROXY_PREFIXES
        assert "/covers/" in proxy_server.ReverseProxyHandler.PROXY_PREFIXES
