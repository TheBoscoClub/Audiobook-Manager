#!/usr/bin/env python3
"""
Reverse Proxy WSGI App for Audiobooks Library
=============================================
WSGI application served by gunicorn (gevent workers) that:
- Proxies /api/*, /auth/*, /streaming-audio/* requests to the Flask backend
  (Gunicorn+flask-sock on localhost:5001), preserving range requests (206)
- Serves static files (HTML/CSS/JS) from the web-v2/ directory
- Serves cover images directly from the covers directory (bypasses Flask)
- Tunnels WebSocket upgrades (/streaming-translate et al.) to the backend
  via raw-socket hijack of ``environ["gunicorn.socket"]``
- TLS termination is handled by gunicorn (certfile/keyfile + ssl_context
  hook in gunicorn_proxy.conf.py — TLS 1.2 minimum)

v8.4.2.0 (Audiobook-Manager-nfx, "Option B"): replaced the former
``http.server.ThreadingHTTPServer`` single-process server with a gunicorn
worker pool. The threaded server was a latent concurrency bottleneck —
1-in-10 parallel requests could hit Caddy's dial_timeout under cold-state
thundering-herd (post-upgrade, post-CF-cache-purge). Gevent workers give
cooperative I/O for many concurrent covers/API/streaming requests.

Run under gunicorn:
    gunicorn -c gunicorn_proxy.conf.py proxy_server:app
Or standalone (execs gunicorn with the same config):
    python proxy_server.py
"""

import fnmatch
import http.client
import json
import logging
import mimetypes
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Add parent directory to path for config/common imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import is_loopback_address as _is_loopback_address  # noqa: E402

from config import (  # noqa: E402
    AUDIOBOOKS_API_PORT,
    AUDIOBOOKS_BIND_ADDRESS,
    AUDIOBOOKS_CERTS,
    AUDIOBOOKS_WEB_PORT,
    COVER_DIR,
)

HTTPS_PORT = AUDIOBOOKS_WEB_PORT
API_PORT = AUDIOBOOKS_API_PORT
CERT_DIR = AUDIOBOOKS_CERTS
CERT_FILE = CERT_DIR / "server.crt"
KEY_FILE = CERT_DIR / "server.key"
BIND_ADDRESS = AUDIOBOOKS_BIND_ADDRESS
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")

# Directory static files are served from (this file lives in web-v2/)
WEB_ROOT = Path(__file__).parent.resolve()

# Reject any Origin header containing CR, LF, NUL, or other control chars.
# These would enable HTTP response splitting if echoed into Access-Control-
# Allow-Origin (CodeQL py/http-response-splitting). Length cap prevents
# pathological inputs.
_ORIGIN_CRLF_PATTERN = re.compile(r"[\r\n\x00-\x1f\x7f]")
_ORIGIN_MAX_LENGTH = 256

# CR/LF/NUL in any outbound header value would enable response splitting.
_HEADER_CRLF_PATTERN = re.compile(r"[\r\n\x00]")


def _origin_is_safe(origin: str) -> bool:
    """Reject Origin values that would enable response-header splitting."""
    return (
        bool(origin)
        and len(origin) <= _ORIGIN_MAX_LENGTH
        and _ORIGIN_CRLF_PATTERN.search(origin) is None
    )


# Hop-by-hop headers that must not be forwarded by proxies (RFC 2616 Section 13.5.1)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_HTTP_STATUS_REASONS = {
    200: "OK",
    204: "No Content",
    206: "Partial Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _status_line(code: int) -> str:
    """Build a WSGI status string ('200 OK') from a numeric code."""
    reason = _HTTP_STATUS_REASONS.get(code) or http.client.responses.get(code, "Unknown")
    return f"{code} {reason}"


def is_websocket_upgrade(headers):
    """Detect WebSocket upgrade request."""
    upgrade = (headers.get("Upgrade", "") or "").lower()
    connection = (headers.get("Connection", "") or "").lower()
    return upgrade == "websocket" and "upgrade" in connection


class EnvironHeaders:
    """Read-only request-header view over a WSGI environ.

    Presents the ``.get(name, default)`` / ``.items()`` interface the
    proxy helpers use, reconstructing header names from ``HTTP_*`` keys
    (plus the two CGI specials ``CONTENT_TYPE`` / ``CONTENT_LENGTH``).
    """

    def __init__(self, environ: dict):
        self._headers: dict[str, str] = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                name = key[5:].replace("_", "-").title()
                self._headers[name.lower()] = value
        if environ.get("CONTENT_TYPE"):
            self._headers["content-type"] = environ["CONTENT_TYPE"]
        if environ.get("CONTENT_LENGTH"):
            self._headers["content-length"] = environ["CONTENT_LENGTH"]

    def get(self, name: str, default=None):
        return self._headers.get(name.lower(), default)

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._headers

    def items(self):
        """Yield (Title-Cased-Name, value) pairs."""
        for name, value in self._headers.items():
            yield name.title(), value


class ReverseProxyApp:
    """WSGI application that proxies API requests and serves static files."""

    # Paths that get proxied to the Flask API backend
    PROXY_PREFIXES = ("/api/", "/auth/", "/streaming-audio/")

    # Static asset extensions that get 1-day cache
    _ASSET_EXTENSIONS = (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
    )

    # Map API-like GET paths to their static HTML pages.
    # Browsers hitting /auth/login expect a page, not a POST-only API endpoint.
    _PAGE_REDIRECTS = {"/auth/login": "/login.html", "/auth/register": "/register.html"}

    # Allowlist of content types for cover images
    _ALLOWED_COVER_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}

    # Names the static server must never hand out, at ANY depth under WEB_ROOT.
    #
    # WEB_ROOT is a live working directory, not a curated publish target: in a
    # deployed install it is the rsync destination of the project's web-v2/
    # tree, and in development it is a git working tree. Files that are not
    # web assets do land in it — the proxy's own source (proxy_server.py,
    # gunicorn_proxy.conf.py), npm's node_modules/ + package*.json, and
    # tooling dotfiles such as .claude-session-ring.jsonl (which contains raw
    # session transcript). Path-traversal containment does not help here: all
    # of these resolve *inside* WEB_ROOT, so `relative_to(WEB_ROOT)` accepts
    # them and mimetypes happily serves them as text/plain.
    #
    # Denied requests get 404, not 403 — a 403 confirms the file exists.
    _FORBIDDEN_STATIC_DIRS = frozenset({"node_modules", "__pycache__"})
    _FORBIDDEN_STATIC_GLOBS = ("*.py", "*.pyc", "*.pyo", "*.pyi", "package*.json")

    # Headers forwarded verbatim from the client to the Flask backend.
    #
    # X-Forwarded-For / X-Real-IP / X-Forwarded-Proto / Host are deliberately
    # NOT in this list: they are authored by the proxy in
    # _collect_proxy_headers() and any client-supplied value is dropped. The
    # backend's localhost_only / admin_or_localhost decorators authorize on
    # the forwarded client address, so a forwardable X-Forwarded-For is a
    # direct privilege-escalation primitive.
    _CLIENT_HEADERS = ("Content-Type", "Range", "Accept", "Cookie")

    # ------------------------------------------------------------------
    # WSGI entry point
    # ------------------------------------------------------------------

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = self._raw_path(environ)
        headers = EnvironHeaders(environ)

        try:
            if method == "OPTIONS":
                return self._handle_options(headers, start_response)

            if self._is_proxy_path(path):
                if is_websocket_upgrade(headers):
                    return self._tunnel_websocket(environ, headers, start_response)
                bare = urllib.parse.urlparse(path).path
                if method in ("GET", "HEAD") and bare in self._PAGE_REDIRECTS:
                    # Redirect browser GETs for page-like /auth/ paths to static HTML
                    return self._redirect(start_response, 302, self._PAGE_REDIRECTS[bare])
                return self.proxy_to_api(environ, headers, start_response, method)

            if method in ("GET", "HEAD"):
                return self._handle_get(environ, headers, start_response, method)

            return self._send_error(start_response, 405, "Method Not Allowed")
        except (BrokenPipeError, ConnectionResetError) as e:
            # Client closed the TCP socket before the response was staged.
            self.log_message(
                environ, "client disconnected during %s %s: %s", method, path, type(e).__name__
            )
            return []

    # ------------------------------------------------------------------
    # Request parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_path(environ: dict) -> str:
        """Return the original request target (path + query), un-decoded.

        gunicorn exposes the exact bytes of the request line as RAW_URI;
        fall back to re-assembling from PATH_INFO/QUERY_STRING for servers
        (and tests) that don't provide it.
        """
        raw = environ.get("RAW_URI") or environ.get("REQUEST_URI")
        if raw:
            return raw
        path = urllib.parse.quote(environ.get("PATH_INFO", "/"))
        query = environ.get("QUERY_STRING", "")
        return path + ("?" + query if query else "")

    def _is_proxy_path(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.PROXY_PREFIXES)

    def _cache_control_for_path(self, path: str, has_version: bool) -> str | None:
        """Return the Cache-Control value for a static file path, or None."""
        if path.endswith(".html") or path == "/":
            return "no-cache"
        if path.endswith((".js", ".css")):
            if has_version:
                return "public, max-age=31536000, immutable"
            return "public, max-age=300"
        if path.endswith(self._ASSET_EXTENSIONS):
            return "public, max-age=86400"
        return None

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_header_value(value: str) -> str:
        """Strip CR/LF/NUL from an outbound header value (response splitting)."""
        return _HEADER_CRLF_PATTERN.sub("", value)

    def _redirect(self, start_response, code: int, location: str):
        """Send a redirect. Location is CRLF-stripped (response splitting)."""
        location = self._sanitize_header_value(location)
        headers = [("Location", location)]
        cache_val = self._cache_control_for_path(
            urllib.parse.urlparse(location).path.lower() or "/", False
        )
        if cache_val:
            headers.append(("Cache-Control", cache_val))
        start_response(_status_line(code), headers)
        return []

    @staticmethod
    def _send_error(start_response, code: int, message: str):
        body = f"{code} {message}\n".encode()
        start_response(
            _status_line(code),
            [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
        )
        return [body]

    def _send_json_error(self, start_response, code: int, error: str, message: str):
        """Send a JSON error response."""
        body = json.dumps({"error": error, "code": code, "message": message}).encode()
        start_response(
            _status_line(code),
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]

    def _resolve_cors_origin(self, headers: EnvironHeaders) -> str:
        """Determine the value to send for Access-Control-Allow-Origin.

        When ``CORS_ORIGIN`` is the wildcard ``*`` AND the request carries
        an ``Origin`` header, echo that origin instead. The wildcard is
        invalid alongside ``Access-Control-Allow-Credentials: true`` per
        the CORS spec, and we always pair the two for credentialed
        requests (cookies-based auth). Echoing the request Origin is the
        spec-compliant way to support credentialed cross-origin requests.

        Origin values containing CR/LF/NUL/control chars are rejected to
        prevent HTTP response-header splitting (CodeQL #531
        py/http-response-splitting). Unsafe values fall back to the
        configured ``CORS_ORIGIN`` default.

        If no Origin header is present (e.g., same-origin or non-browser
        clients) we keep the configured value (which is the wildcard by
        default — harmless without credentials).
        """
        origin_header = headers.get("Origin")
        if CORS_ORIGIN == "*" and origin_header and _origin_is_safe(origin_header):
            return origin_header
        return CORS_ORIGIN

    def _cors_headers(self, headers: EnvironHeaders) -> list[tuple[str, str]]:
        """Build the canonical CORS header pair for credentialed requests.

        Always paired: ``Access-Control-Allow-Origin`` + ``Access-Control-
        Allow-Credentials: true``. The fetch() calls in the web UI use
        ``credentials: "include"`` to ride the session cookie across the
        proxy boundary, and Allow-Credentials must be true for the browser
        to expose the response to JS.

        When echoing a specific origin (not wildcard), Vary: Origin tells
        caches that the response varies by Origin so they don't serve a
        cached response with the wrong A-C-A-O header to a different
        cross-origin caller.
        """
        return [
            ("Access-Control-Allow-Origin", self._resolve_cors_origin(headers)),
            ("Access-Control-Allow-Credentials", "true"),
            ("Vary", "Origin"),
        ]

    def _handle_options(self, headers: EnvironHeaders, start_response):
        """Handle CORS preflight requests."""
        response_headers = self._cors_headers(headers) + [
            ("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type, Range"),
            ("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges, Content-Length"),
        ]
        start_response(_status_line(204), response_headers)
        return []

    # ------------------------------------------------------------------
    # Static file + cover serving
    # ------------------------------------------------------------------

    def _handle_get(self, environ, headers: EnvironHeaders, start_response, method: str):
        """Route non-proxy GET/HEAD requests: covers, shell, static files."""
        raw = self._raw_path(environ)
        parsed = urllib.parse.urlparse(raw)
        bare_path = urllib.parse.unquote(parsed.path)

        # Serve cover images directly from the covers directory (bypasses Flask)
        if bare_path.startswith("/covers/"):
            return self._serve_cover(headers, start_response, bare_path[8:], method)

        if bare_path == "/":
            # Serve shell.html directly at / so the browser address bar shows
            # the clean URL (e.g., https://library.example.com/) with no
            # shell.html visible. Query string (e.g., ?autoplay=...) is
            # client-side only — file content is identical.
            return self._serve_static(start_response, "/shell.html", "/", parsed.query, method)

        if bare_path == "/shell.html":
            # Canonical URL is /; redirect direct shell.html access there.
            # Preserve query string across the redirect.
            # Strip CRLF to prevent HTTP response splitting (CodeQL #315)
            query = parsed.query.replace("\r", "").replace("\n", "")
            location = "/" + ("?" + query if query else "")
            return self._redirect(start_response, 301, location)

        # Serve static files
        return self._serve_static(start_response, bare_path, bare_path, parsed.query, method)

    @classmethod
    def _is_forbidden_static_path(cls, path: str) -> bool:
        """Return True if a WEB_ROOT-relative path must never be served.

        Refuses, at any depth: dot-prefixed names (dotfiles AND dot-dirs),
        the directories in ``_FORBIDDEN_STATIC_DIRS``, and final components
        matching ``_FORBIDDEN_STATIC_GLOBS``. See those constants for why.
        """
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
        if not parts:
            return False
        for part in parts:
            if part.startswith("."):
                return True
            if part.lower() in cls._FORBIDDEN_STATIC_DIRS:
                return True
        name = parts[-1].lower()
        return any(fnmatch.fnmatch(name, pattern) for pattern in cls._FORBIDDEN_STATIC_GLOBS)

    def _serve_static(self, start_response, file_path: str, cache_path: str, query: str, method):
        """Serve a static file from WEB_ROOT with path-traversal protection."""
        # Refuse non-asset names before touching the filesystem.
        if self._is_forbidden_static_path(file_path):
            return self._send_error(start_response, 404, "File not found")

        # Normalize and contain within WEB_ROOT (defeats ../ traversal).
        candidate = (WEB_ROOT / file_path.lstrip("/")).resolve()
        try:
            relative = candidate.relative_to(WEB_ROOT)
        except ValueError:
            return self._send_error(start_response, 403, "Forbidden")

        # Re-check after resolution: `..` segments and symlinks can land on a
        # forbidden name that the raw request path did not spell out.
        if self._is_forbidden_static_path(str(relative)):
            return self._send_error(start_response, 404, "File not found")

        if not candidate.is_file():
            return self._send_error(start_response, 404, "File not found")

        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        file_size = candidate.stat().st_size

        response_headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(file_size)),
        ]
        cache_val = self._cache_control_for_path(cache_path.lower(), "v=" in (query or ""))
        if cache_val:
            response_headers.append(("Cache-Control", cache_val))

        start_response(_status_line(200), response_headers)
        if method == "HEAD":
            return []
        return self._file_chunks(candidate)

    @staticmethod
    def _file_chunks(path: Path, chunk_size: int = 65536):
        """Yield a file's content in chunks (streaming, low memory)."""
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except (OSError, BrokenPipeError):  # fmt: skip
            # Client disconnect mid-transfer or file vanished under us —
            # nothing more can be written; stop the stream quietly.
            return

    @staticmethod
    def _is_safe_cover_filename(filename: str) -> bool:
        """Check that a cover filename has no path traversal characters."""
        return (
            bool(filename) and "/" not in filename and "\\" not in filename and ".." not in filename
        )

    def _resolve_cover_content_type(self, filename: str) -> str:
        """Determine the safe content type for a cover image."""
        guessed = mimetypes.guess_type(filename)[0] or "image/jpeg"
        return guessed if guessed in self._ALLOWED_COVER_TYPES else "image/jpeg"

    def _serve_cover(self, headers: EnvironHeaders, start_response, filename: str, method: str):
        """Serve a cover image directly from the covers directory.

        Bypasses the Flask proxy hop — covers are static files that need no
        auth or logic. Content-addressed filenames (MD5 hashes) are immutable,
        so we set aggressive cache headers.
        """
        if not self._is_safe_cover_filename(filename):
            return self._send_error(start_response, 400, "Bad Request")

        cover_path = COVER_DIR / filename
        if not cover_path.is_file():
            return self._send_error(start_response, 404, "Cover not found")

        content_type = self._resolve_cover_content_type(filename)
        file_size = cover_path.stat().st_size

        response_headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(file_size)),
            ("Cache-Control", "public, max-age=31536000, immutable"),
        ] + self._cors_headers(headers)

        start_response(_status_line(200), response_headers)
        if method == "HEAD":
            return []
        return self._file_chunks(cover_path)

    # ------------------------------------------------------------------
    # WebSocket tunneling
    # ------------------------------------------------------------------

    def _build_ws_upgrade_request(self, environ: dict, headers: EnvironHeaders) -> bytes:
        """Build raw HTTP upgrade request bytes for the backend.

        Raw header passthrough is INTENTIONAL here. Unlike normal HTTP
        forwarding (which strips hop-by-hop headers via HOP_BY_HOP_HEADERS),
        a WebSocket upgrade requires ``Connection: Upgrade`` and ``Upgrade:
        websocket`` — the very headers the hop-by-hop filter would strip.
        After the 101 Switching Protocols response this socket becomes a
        raw bidirectional tunnel, not a WSGI-forwarded request, so the
        HOP_BY_HOP_HEADERS filter must not be applied here.

        Do not "fix" this to use the filter — it will break WebSocket.
        """
        method = environ.get("REQUEST_METHOD", "GET").upper()
        request_line = f"{method} {self._raw_path(environ)} HTTP/1.1\r\n"
        header_lines = ""
        for key, value in headers.items():
            header_lines += f"{key}: {value}\r\n"
        header_lines += "\r\n"
        return (request_line + header_lines).encode("latin-1")

    def _ws_read_upgrade_response(self, backend) -> bytes:
        """Read the HTTP upgrade response from backend until headers end."""
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = backend.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf

    @staticmethod
    def _ws_drain_ssl_pending(client_sock, backend) -> bool:
        """Drain data from SSL read buffer. Returns False if connection closed."""
        if hasattr(client_sock, "pending") and client_sock.pending() > 0:
            data = client_sock.recv(65536)
            if not data:
                return False
            backend.sendall(data)
        return True

    @staticmethod
    def _ws_relay_readable(readable, client_sock, backend) -> bool:
        """Relay data between readable sockets. Returns False if connection closed."""
        for sock in readable:
            data = sock.recv(65536)
            if not data:
                return False
            target = backend if sock is client_sock else client_sock
            target.sendall(data)
        return True

    def _tunnel_websocket(self, environ, headers: EnvironHeaders, start_response):
        """Tunnel a WebSocket upgrade request to the API backend via raw TCP.

        Hijacks the client connection from gunicorn (``gunicorn.socket`` in
        the WSGI environ). Blocking the handler for the tunnel's lifetime is
        fine under the gevent worker — socket I/O is cooperative, so other
        requests keep flowing (the old threaded server needed a thread per
        tunnel for the same reason).
        """
        import select
        import socket

        client_sock = environ.get("gunicorn.socket")
        if client_sock is None:
            # Not running under gunicorn (e.g. wsgiref dev server) — the raw
            # client socket is unavailable, so the tunnel cannot be built.
            return self._send_error(start_response, 502, "WebSocket tunnel unavailable")

        raw_request = self._build_ws_upgrade_request(environ, headers)

        try:
            backend = socket.create_connection(("127.0.0.1", API_PORT), timeout=10)
        except (socket.error, OSError) as e:
            return self._send_error(start_response, 503, f"Backend unreachable: {e}")

        hijacked = False
        try:
            backend.sendall(raw_request)
            buf = self._ws_read_upgrade_response(backend)
            hijacked = True  # bytes now flow on the raw socket, not via WSGI
            client_sock.sendall(buf)

            if not buf.startswith(b"HTTP/1.1 101"):
                backend.close()
                return self._finish_hijacked(environ, start_response)

            sockets = [client_sock, backend]
            while True:
                if not self._ws_drain_ssl_pending(client_sock, backend):
                    return self._finish_hijacked(environ, start_response)

                readable, _, errored = select.select(sockets, [], sockets, 30)
                if errored or not readable:
                    break
                if not self._ws_relay_readable(readable, client_sock, backend):
                    return self._finish_hijacked(environ, start_response)
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.debug("backend proxy streaming interrupted (non-fatal): %s", e)
        finally:
            try:
                backend.close()
            except Exception as e:
                logger.debug("backend close failed (non-fatal): %s", e)

        if hijacked:
            return self._finish_hijacked(environ, start_response)
        return self._send_error(start_response, 502, "WebSocket upgrade failed")

    @staticmethod
    def _finish_hijacked(environ, start_response):
        """Terminate a WSGI call whose socket was hijacked for tunneling.

        The upgrade response (and any tunneled frames) were already written
        to the raw socket, so gunicorn must not write a second HTTP response.
        shutdown(SHUT_RDWR) — not close() — makes gunicorn's post-request
        header write hit a half-closed connection and raise EPIPE, which its
        async worker ignores as a routine client disconnect. (close() would
        invalidate the fd and surface a noisy EBADF traceback per tunnel
        instead.) gunicorn still owns and closes the fd afterwards.
        start_response is called to satisfy the WSGI contract for the
        (never-sent) response.
        """
        import socket

        try:
            environ["gunicorn.socket"].shutdown(socket.SHUT_RDWR)
        except (OSError, KeyError):  # fmt: skip
            pass
        start_response(_status_line(200), [])
        return []

    # ------------------------------------------------------------------
    # HTTP proxying
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_client_address(environ: dict, headers: EnvironHeaders) -> str:
        """Determine the client address to advertise to the Flask backend.

        Trust exactly one hop.

        The socket peer (``REMOTE_ADDR``) is the only unforgeable address the
        proxy has, so it is the answer for any request that arrived directly.
        Client-supplied ``X-Forwarded-For`` is ignored outright in that case —
        it is attacker-controlled, and the backend authorizes localhost-only
        endpoints on this value.

        A loopback peer means the request came through the same-host front
        door (Caddy — see caddy/audiobooks.conf, which proxies :8084/:8085 to
        this server). Caddy *appends* the address it saw to any inbound
        X-Forwarded-For, so the RIGHT-most entry is Caddy's own observation
        and the only entry its client could not author. The left-most entry
        is exactly what an attacker sets to spoof ``127.0.0.1``; reading it
        was the original defect.

        Residual, documented: if a tunnel daemon (cloudflared) reaches Caddy
        over loopback, Caddy's observation is itself ``127.0.0.1`` and the
        chain becomes indistinguishable from a genuine local call. Deployments
        that expose localhost-only endpoints through such a tunnel must rely on
        AUTH_ENABLED=true (``admin_or_localhost`` then requires an authenticated
        admin and never consults an address at all).
        """
        peer = environ.get("REMOTE_ADDR", "") or ""
        if not _is_loopback_address(peer):
            return peer
        forwarded_for = headers.get("X-Forwarded-For") or ""
        entries = [entry.strip() for entry in forwarded_for.split(",") if entry.strip()]
        return entries[-1] if entries else peer

    def _collect_proxy_headers(self, environ: dict, headers: EnvironHeaders) -> dict:
        """Collect headers to forward to the Flask backend.

        Client-supplied forwarding/routing headers are dropped and re-authored
        here (see ``_CLIENT_HEADERS``); values are CRLF-stripped because they
        are written into the outbound request line.
        """
        forwarded = {}
        for header in self._CLIENT_HEADERS:
            value = headers.get(header)
            if value is not None:
                forwarded[header] = value

        client_address = self._sanitize_header_value(
            self._effective_client_address(environ, headers)
        )
        forwarded["X-Forwarded-For"] = client_address
        forwarded["X-Real-IP"] = client_address
        # This proxy terminates TLS (gunicorn certfile/keyfile), so the client
        # leg is always https regardless of what the client claimed.
        forwarded["X-Forwarded-Proto"] = "https"
        # Explicit upstream Host: forwarding the client's Host verbatim let a
        # crafted value reach the backend's URL/redirect building.
        forwarded["Host"] = f"127.0.0.1:{API_PORT}"
        return forwarded

    @staticmethod
    def _read_request_body(environ: dict, method: str) -> bytes | None:
        """Read request body for POST/PUT/PATCH methods."""
        if method not in ("POST", "PUT", "PATCH"):
            return None
        try:
            content_length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            content_length = 0
        if content_length <= 0:
            return None
        return environ["wsgi.input"].read(content_length)

    def _filter_response_headers(self, response_headers) -> list[tuple[str, str]]:
        """Forward response headers, filtering hop-by-hop."""
        return [
            (header, self._sanitize_header_value(value))
            for header, value in response_headers.items()
            if header.lower() not in HOP_BY_HOP_HEADERS
        ]

    @staticmethod
    def _response_chunks(response, first_chunk: bytes | None = None):
        """Stream a backend response body in chunks toward the client."""
        try:
            with response:
                if first_chunk:
                    yield first_chunk
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    yield chunk
        except (BrokenPipeError, ConnectionResetError) as e:
            # Client closed the TCP socket mid-response (page nav, tab close,
            # mobile backgrounding). The response was already partially sent;
            # nothing more we can or should write. Log without a traceback —
            # this is expected client-driven behavior, not an internal error.
            logger.info(
                "client disconnected while streaming proxied response: %s", type(e).__name__
            )

    def proxy_to_api(self, environ, headers: EnvironHeaders, start_response, method="GET"):
        """Proxy request to Flask API backend."""
        path = self._raw_path(environ)
        if not any(path.startswith(p) for p in self.PROXY_PREFIXES):
            return self._send_error(start_response, 403, "Forbidden - Invalid path")

        # Strip HTTP request-splitting characters (null bytes, CR, LF) that
        # could manipulate headers in the forwarded request.  The host is always
        # the loopback address 127.0.0.1 with a fixed port from config — it is
        # never derived from user input — so SSRF to external hosts is
        # structurally impossible here (py/partial-ssrf mitigation).
        path = path.replace("\x00", "").replace("\r", "").replace("\n", "")
        api_url = f"http://127.0.0.1:{API_PORT}{path}"

        # Belt-and-suspenders: verify the constructed URL targets only the
        # loopback API backend.  This catches any future refactor that
        # accidentally makes the host dynamic.
        _parsed = urllib.parse.urlparse(api_url)
        _expected_netloc = f"127.0.0.1:{API_PORT}"
        if _parsed.scheme != "http" or _parsed.netloc != _expected_netloc:
            return self._send_json_error(
                start_response, 403, "Forbidden", "Proxy target validation failed"
            )

        try:
            forwarded_headers = self._collect_proxy_headers(environ, headers)
            body = self._read_request_body(environ, method)
            req = urllib.request.Request(
                api_url, data=body, headers=forwarded_headers, method=method
            )  # noqa: S310 — Request for proxied API call; URL validated against internal 127.0.0.1 host only

            response = urllib.request.urlopen(  # noqa: S310 — urlopen for proxied localhost API; URL built from internal 127.0.0.1 base  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                req, timeout=30
            )
            start_response(
                _status_line(response.status), self._filter_response_headers(response.headers)
            )
            return self._response_chunks(response)

        except urllib.error.HTTPError as e:
            try:
                error_body = e.read()
            except Exception:
                error_body = json.dumps({"error": e.reason, "code": e.code}).encode()
            finally:
                # HTTPError holds an underlying http.client.HTTPResponse / fp
                # that must be closed explicitly. Without close(), Python 3.14
                # emits "ResourceWarning: Implicitly cleaning up <HTTPError ...>"
                # at GC time. close() is idempotent and safe to call.
                try:
                    e.close()
                except Exception:  # nosec B110 — best-effort close of HTTPError fp; close() failure only re-risks the ResourceWarning this guards against
                    pass
            start_response(_status_line(e.code), self._filter_response_headers(e.headers))
            return [error_body]

        except urllib.error.URLError as e:
            self.log_message(environ, "URLError proxying %s %s: %s", method, api_url, e.reason)
            # Reason is logged above; the client is told only that the
            # backend is down, not which socket error or address produced it.
            return self._send_json_error(
                start_response,
                503,
                "Service Unavailable",
                "API server not reachable.",
            )

        except Exception as e:
            self.log_message(
                environ,
                "Unhandled exception proxying %s %s: %s\n%s",
                method,
                api_url,
                e,
                traceback.format_exc(),
            )
            # The exception text is logged above (with traceback) but never
            # returned: str(e) on an unexpected proxy failure leaks filesystem
            # paths, internal hostnames and port numbers straight to the
            # browser. The client gets a fixed message; the operator gets the
            # detail in the journal.
            return self._send_json_error(
                start_response, 500, "Internal Server Error", "The server encountered an error."
            )

    @staticmethod
    def log_message(environ, format, *args):
        """Log with [PROXY] prefix."""
        client = environ.get("REMOTE_ADDR", "-") if isinstance(environ, dict) else "-"
        print(f"[PROXY] {client} - {format % args}")


# WSGI callable for gunicorn: `gunicorn -c gunicorn_proxy.conf.py proxy_server:app`
app = ReverseProxyApp()


def _find_gunicorn() -> Path | None:
    """Locate the gunicorn binary for the standalone entry point.

    Search order: the sibling library venv (covers the installed-app wrapper,
    which runs system python3), the interpreter's own bin dir (covers running
    from an activated venv), then PATH.
    """
    import shutil

    candidates = [
        Path(__file__).parent.parent / "venv" / "bin" / "gunicorn",
        Path(sys.executable).parent / "gunicorn",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    which = shutil.which("gunicorn")
    return Path(which) if which else None


def main():
    """Exec gunicorn with the canonical proxy config (dev/standalone entry).

    The systemd unit (audiobook-proxy.service) runs gunicorn directly; this
    entry point exists so `python proxy_server.py` (and the installed
    audiobooks-proxy wrapper) behaves identically — single canonical
    configuration in gunicorn_proxy.conf.py, no duplicated server setup here.
    """
    conf = Path(__file__).parent / "gunicorn_proxy.conf.py"
    gunicorn_bin = _find_gunicorn()
    if gunicorn_bin is None:
        print(
            "Error: gunicorn not found (looked in the library venv, "
            f"{Path(sys.executable).parent}, and PATH)"
        )
        print("Install requirements first: pip install -r requirements.txt")
        sys.exit(1)
    os.execv(str(gunicorn_bin), [str(gunicorn_bin), "-c", str(conf), "proxy_server:app"])  # nosec B606 — fixed venv-local/PATH gunicorn binary + fixed args; no user input


if __name__ == "__main__":
    main()
