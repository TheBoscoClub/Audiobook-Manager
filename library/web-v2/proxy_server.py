#!/usr/bin/env python3
"""
Reverse Proxy Server for Audiobooks Library
============================================
Serves as a unified HTTPS endpoint that:
- Proxies /api/* requests to the Flask backend (Gunicorn+geventwebsocket on localhost:5001)
- Serves static files (HTML/CSS/JS) from web-v2/ directory
- Handles SSL/TLS with existing certificates
- Supports range requests for audio streaming
"""

import http.server
import json
import logging
import os
import ssl
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
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


def is_websocket_upgrade(headers):
    """Detect WebSocket upgrade request."""
    upgrade = (headers.get("Upgrade", "") or "").lower()
    connection = (headers.get("Connection", "") or "").lower()
    return upgrade == "websocket" and "upgrade" in connection


class ReverseProxyHandler(http.server.SimpleHTTPRequestHandler):
    """Handler that proxies API requests and serves static files."""

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

    def end_headers(self):
        """Inject Cache-Control headers for static files."""
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        # Only set cache headers for static file responses (not proxied API)
        if not any(self.path.startswith(p) for p in self.PROXY_PREFIXES):
            cache_val = self._cache_control_for_path(
                parsed.path.lower(), "v=" in (parsed.query or "")
            )
            if cache_val:
                self.send_header("Cache-Control", cache_val)

        super().end_headers()

    def _is_proxy_path(self):
        return any(self.path.startswith(p) for p in self.PROXY_PREFIXES)

    # Map API-like GET paths to their static HTML pages.
    # Browsers hitting /auth/login expect a page, not a POST-only API endpoint.
    _PAGE_REDIRECTS = {"/auth/login": "/login.html", "/auth/register": "/register.html"}

    def do_GET(self):
        if self._is_proxy_path() and is_websocket_upgrade(self.headers):
            self._tunnel_websocket()
            return
        # Redirect browser GETs for page-like /auth/ paths to static HTML
        from urllib.parse import urlparse

        bare = urlparse(self.path).path
        if bare in self._PAGE_REDIRECTS:
            self.send_response(302)
            self.send_header("Location", self._PAGE_REDIRECTS[bare])
            self.end_headers()
            return
        if self._is_proxy_path():
            self.proxy_to_api("GET")
            return

        # Parse path and query string separately for clean URL routing
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        bare_path = parsed.path

        # Serve cover images directly from the covers directory (bypasses Flask)
        if bare_path.startswith("/covers/"):
            self._serve_cover(bare_path[8:])  # strip "/covers/"
            return

        if bare_path == "/":
            # Serve shell.html directly at / so the browser address bar shows
            # the clean URL (e.g., https://library.example.com/) with no
            # shell.html visible. Preserve query string (e.g., ?autoplay=...).
            self.path = "/shell.html" + ("?" + parsed.query if parsed.query else "")
            super().do_GET()
        elif bare_path == "/shell.html":
            # Canonical URL is /; redirect direct shell.html access there.
            # Preserve query string across the redirect.
            # Strip CRLF to prevent HTTP response splitting (CodeQL #315)
            query = parsed.query.replace("\r", "").replace("\n", "")
            location = "/" + ("?" + query if query else "")
            self.send_response(301)
            self.send_header("Location", location)
            self.end_headers()
        else:
            # Serve static files
            super().do_GET()

    # Allowlist of content types for cover images
    _ALLOWED_COVER_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}

    @staticmethod
    def _is_safe_cover_filename(filename: str) -> bool:
        """Check that a cover filename has no path traversal characters."""
        return (
            bool(filename) and "/" not in filename and "\\" not in filename and ".." not in filename
        )

    def _resolve_cover_content_type(self, filename: str) -> str:
        """Determine the safe content type for a cover image."""
        import mimetypes

        guessed = mimetypes.guess_type(filename)[0] or "image/jpeg"
        return guessed if guessed in self._ALLOWED_COVER_TYPES else "image/jpeg"

    def _send_cover_response(self, cover_path: Path, content_type: str):
        """Send the HTTP response with cover data and cache headers."""
        file_size = cover_path.stat().st_size

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.end_headers()

        with open(cover_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _serve_cover(self, filename: str):
        """Serve a cover image directly from the covers directory.

        Bypasses the Flask proxy hop — covers are static files that need no
        auth or logic. Content-addressed filenames (MD5 hashes) are immutable,
        so we set aggressive cache headers.
        """
        if not self._is_safe_cover_filename(filename):
            self.send_error(400, "Bad Request")
            return

        cover_path = COVER_DIR / filename
        if not cover_path.is_file():
            self.send_error(404, "Cover not found")
            return

        try:
            content_type = self._resolve_cover_content_type(filename)
            self._send_cover_response(cover_path, content_type)
        except (OSError, BrokenPipeError):  # fmt: skip
            pass

    def do_POST(self):
        if self._is_proxy_path():
            self.proxy_to_api("POST")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PUT(self):
        if self._is_proxy_path():
            self.proxy_to_api("PUT")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PATCH(self):
        if self._is_proxy_path():
            self.proxy_to_api("PATCH")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_DELETE(self):
        if self._is_proxy_path():
            self.proxy_to_api("DELETE")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header(
            "Access-Control-Expose-Headers", "Content-Range, Accept-Ranges, Content-Length"
        )
        self.end_headers()

    def _build_ws_upgrade_request(self) -> bytes:
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
        request_line = f"{self.command} {self.path} HTTP/1.1\r\n"
        header_lines = ""
        for key, value in self.headers.items():
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

    def _tunnel_websocket(self):
        """Tunnel a WebSocket upgrade request to the API backend via raw TCP."""
        import select
        import socket

        raw_request = self._build_ws_upgrade_request()

        try:
            backend = socket.create_connection(("127.0.0.1", API_PORT), timeout=10)
        except (socket.error, OSError) as e:
            self.send_error(503, f"Backend unreachable: {e}")
            return

        try:
            backend.sendall(raw_request)
            client_sock = self.request
            buf = self._ws_read_upgrade_response(backend)
            client_sock.sendall(buf)

            if not buf.startswith(b"HTTP/1.1 101"):
                backend.close()
                return

            sockets = [client_sock, backend]
            while True:
                if not self._ws_drain_ssl_pending(client_sock, backend):
                    return

                readable, _, errored = select.select(sockets, [], sockets, 30)
                if errored or not readable:
                    break
                if not self._ws_relay_readable(readable, client_sock, backend):
                    return
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.debug("backend proxy streaming interrupted (non-fatal): %s", e)
        finally:
            try:
                backend.close()
            except Exception as e:
                logger.debug("backend close failed (non-fatal): %s", e)

    # Headers to forward from client to Flask backend
    _CLIENT_HEADERS = ("Content-Type", "Range", "Accept", "Cookie")
    _PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Proto", "X-Real-IP", "Host")

    def _collect_proxy_headers(self) -> dict:
        """Collect headers to forward to the Flask backend."""
        headers = {}
        for header in self._CLIENT_HEADERS:
            if header in self.headers:
                headers[header] = self.headers[header]
        for header in self._PROXY_HEADERS:
            if header in self.headers:
                headers[header] = self.headers[header]
        if "X-Forwarded-For" not in headers:
            headers["X-Forwarded-For"] = self.client_address[0]
        return headers

    def _read_request_body(self, method: str) -> bytes | None:
        """Read request body for POST/PUT/PATCH methods."""
        if method not in ("POST", "PUT", "PATCH"):
            return None
        content_length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(content_length) if content_length > 0 else None

    def _forward_response_headers(self, response_headers) -> None:
        """Forward response headers, filtering hop-by-hop."""
        for header, value in response_headers.items():
            if header.lower() not in HOP_BY_HOP_HEADERS:
                self.send_header(header, value)

    def _send_json_error(self, code: int, error: str, message: str) -> None:
        """Send a JSON error response."""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({"error": error, "code": code, "message": message}).encode()
        self.wfile.write(body)

    def proxy_to_api(self, method="GET"):
        """Proxy request to Flask API backend."""
        path = self.path
        if not any(path.startswith(p) for p in self.PROXY_PREFIXES):
            self.send_error(403, "Forbidden - Invalid path")
            return

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
            self._send_json_error(403, "Forbidden", "Proxy target validation failed")
            return

        try:
            headers = self._collect_proxy_headers()
            body = self._read_request_body(method)
            req = urllib.request.Request(
                api_url, data=body, headers=headers, method=method
            )  # noqa: S310 — Request for proxied API call; URL validated against internal 127.0.0.1 host only

            with urllib.request.urlopen(  # noqa: S310 — urlopen for proxied localhost API; URL built from internal 127.0.0.1 base  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                req, timeout=30
            ) as response:
                self.send_response(response.status)
                self._forward_response_headers(response.headers)
                self.end_headers()

                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self._forward_response_headers(e.headers)
            self.end_headers()
            try:
                error_body = e.read()
            except Exception:
                error_body = json.dumps({"error": e.reason, "code": e.code}).encode()
            self.wfile.write(error_body)

        except urllib.error.URLError as e:
            self.log_message("URLError proxying %s %s: %s", method, api_url, e.reason)
            self._send_json_error(
                503, "Service Unavailable", f"API server not reachable: {str(e.reason)}"
            )

        except (BrokenPipeError, ConnectionResetError) as e:
            # Client closed the TCP socket mid-response (page nav, tab close,
            # mobile backgrounding). The response was already partially sent;
            # nothing more we can or should write. Log at info level without
            # a traceback — this is expected client-driven behavior, not an
            # internal error.
            self.log_message(
                "client disconnected while proxying %s %s: %s", method, api_url, type(e).__name__
            )

        except Exception as e:
            self.log_message(
                "Unhandled exception proxying %s %s: %s\n%s",
                method,
                api_url,
                e,
                traceback.format_exc(),
            )
            self._send_json_error(500, "Internal Server Error", str(e))

    def log_message(self, format, *args):
        """Log with [PROXY] prefix."""
        print(f"[PROXY] {self.address_string()} - {format % args}")


class ReuseHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded HTTPServer with socket reuse.

    Threading is required because WebSocket tunnels block the handler
    for the duration of the connection. Without threading, a single
    active WebSocket would block all other HTTP requests.
    """

    daemon_threads = True

    def server_bind(self):
        import socket

        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


def main():
    """Start the HTTPS reverse proxy server."""
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        print(f"Error: Certificate files not found in {CERT_DIR}")
        print(f"  Expected: {CERT_FILE}")
        print(f"  Expected: {KEY_FILE}")
        print()
        print("Generate certificates with:")
        print(f"  mkdir -p {CERT_DIR}")
        print("  openssl req -x509 -newkey rsa:4096 -nodes \\")
        print(f"    -keyout {KEY_FILE} \\")
        print(f"    -out {CERT_FILE} \\")
        print("    -days 365 -subj '/CN=localhost'")
        sys.exit(1)

    # Change to web directory to serve static files
    web_dir = Path(__file__).parent
    os.chdir(web_dir)

    # Create SSL context with secure defaults
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # Enforce TLS 1.2 minimum to prevent use of insecure protocols
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

    # Create HTTPS server
    server_address = (BIND_ADDRESS, HTTPS_PORT)
    httpd = ReuseHTTPServer(server_address, ReverseProxyHandler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

    print("Audiobooks Library Reverse Proxy (HTTPS)")
    print("=========================================")
    print(f"Listening on: https://{BIND_ADDRESS}:{HTTPS_PORT}/")
    print(f"API backend:  http://localhost:{API_PORT}/")
    print(f"Certificate:  {CERT_FILE}")
    print(f"Key:          {KEY_FILE}")
    print()
    print(f"Access the library at: https://localhost:{HTTPS_PORT}/")
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
