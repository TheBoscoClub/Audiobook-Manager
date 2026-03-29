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
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    AUDIOBOOKS_API_PORT,
    AUDIOBOOKS_BIND_ADDRESS,
    AUDIOBOOKS_CERTS,
    AUDIOBOOKS_WEB_PORT,
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
    PROXY_PREFIXES = ("/api/", "/auth/", "/covers/")

    def end_headers(self):
        """Inject Cache-Control headers for static files.

        Strategy:
        - HTML: no-cache (revalidate every request; ~200ms conditional GET).
          HTML files are small and reference versioned JS/CSS via ?v= params,
          so they must always reflect current asset versions.
        - JS/CSS with ?v=: immutable, cache for 1 year.  The ?v= param changes
          on each release, busting the cache automatically.
        - JS/CSS without ?v=: short cache (5 min) to avoid stale scripts
          while still reducing repeat requests.
        - Images/fonts: cache for 1 day.
        - API responses: not touched here (proxied responses have their own
          headers from Flask).
        """
        from urllib.parse import urlparse

        parsed = urlparse(self.path)
        path = parsed.path.lower()
        has_version = "v=" in (parsed.query or "")

        # Only set cache headers for static file responses (not proxied API)
        if not any(self.path.startswith(p) for p in self.PROXY_PREFIXES):
            if path.endswith(".html") or path == "/":
                self.send_header("Cache-Control", "no-cache")
            elif (path.endswith(".js") or path.endswith(".css")) and has_version:
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            elif path.endswith(".js") or path.endswith(".css"):
                self.send_header("Cache-Control", "public, max-age=300")
            elif any(
                path.endswith(ext)
                for ext in (
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
            ):
                self.send_header("Cache-Control", "public, max-age=86400")

        super().end_headers()

    def _is_proxy_path(self):
        return any(self.path.startswith(p) for p in self.PROXY_PREFIXES)

    # Map API-like GET paths to their static HTML pages.
    # Browsers hitting /auth/login expect a page, not a POST-only API endpoint.
    _PAGE_REDIRECTS = {
        "/auth/login": "/login.html",
        "/auth/register": "/register.html",
    }

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

        if bare_path == "/":
            # Serve shell.html directly at / so the browser address bar shows
            # the clean URL (e.g., https://library.thebosco.club/) with no
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

    def do_DELETE(self):
        if self._is_proxy_path():
            self.proxy_to_api("DELETE")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header(
            "Access-Control-Expose-Headers",
            "Content-Range, Accept-Ranges, Content-Length",
        )
        self.end_headers()

    def _tunnel_websocket(self):
        """Tunnel a WebSocket upgrade request to the API backend via raw TCP.

        Note: All headers are forwarded verbatim (no hop-by-hop filtering).
        This is intentional per RFC 6455 — WebSocket upgrades require
        Connection: Upgrade and Upgrade: websocket to pass through intact.
        """
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
            #
            # client_sock is an ssl.SSLSocket (TLS-terminated here).
            # select() only sees the underlying TCP fd, NOT data already
            # decrypted into the SSL buffer.  We must check pending()
            # before each select() to avoid starving the backend of
            # heartbeats that are sitting in the SSL read buffer.
            sockets = [client_sock, backend]
            while True:
                # Drain any data already decrypted in the SSL buffer
                # before asking select() about the raw TCP fd.
                if hasattr(client_sock, "pending") and client_sock.pending() > 0:
                    data = client_sock.recv(65536)
                    if not data:
                        return
                    backend.sendall(data)
                    continue

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

    def proxy_to_api(self, method="GET"):
        """Proxy request to Flask API backend."""
        # Validate the path to prevent SSRF - only allow known prefixes
        # and sanitize to prevent path traversal
        path = self.path
        if not any(path.startswith(p) for p in self.PROXY_PREFIXES):
            self.send_error(403, "Forbidden - Invalid path")
            return

        # Sanitize path: remove any null bytes and normalize
        path = path.replace("\x00", "")
        # Construct URL to local backend only (never external)
        # CodeQL: SSRF safe - path validated above, connects to localhost only
        api_url = f"http://127.0.0.1:{API_PORT}{path}"

        try:
            # Prepare headers - forward client headers to Flask
            headers = {}
            for header in ["Content-Type", "Range", "Accept", "Cookie"]:
                if header in self.headers:
                    headers[header] = self.headers[header]

            # Forward proxy headers so Flask sees real client info
            # These are set by the upstream Caddy reverse proxy
            for proxy_header in [
                "X-Forwarded-For",
                "X-Forwarded-Proto",
                "X-Real-IP",
                "Host",
            ]:
                if proxy_header in self.headers:
                    headers[proxy_header] = self.headers[proxy_header]

            # If no X-Forwarded-For from upstream, set it from the connecting client
            if "X-Forwarded-For" not in headers:
                headers["X-Forwarded-For"] = self.client_address[0]

            # Read request body for POST/PUT
            body = None
            if method in ("POST", "PUT"):
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length > 0:
                    body = self.rfile.read(content_length)

            # Make request to API
            req = urllib.request.Request(
                api_url, data=body, headers=headers, method=method
            )

            with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310 — connects to hardcoded 127.0.0.1 only
                # Send response status
                self.send_response(response.status)

                # Copy headers from API response, filtering hop-by-hop headers
                for header, value in response.headers.items():
                    if header.lower() not in HOP_BY_HOP_HEADERS:
                        self.send_header(header, value)
                self.end_headers()

                # Stream response body
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            # Forward HTTP errors from API, preserving the original response body
            self.send_response(e.code)
            for header, value in e.headers.items():
                if header.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(header, value)
            self.end_headers()
            # Read and forward the actual error body from Flask
            try:
                error_body = e.read()
            except Exception:
                error_body = json.dumps({"error": e.reason, "code": e.code}).encode()
            self.wfile.write(error_body)

        except urllib.error.URLError as e:
            # API server not reachable (no CORS here — Flask handles it on success)
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            error_body = json.dumps(
                {
                    "error": "Service Unavailable",
                    "code": 503,
                    "message": f"API server not reachable: {str(e.reason)}",
                }
            ).encode()
            self.wfile.write(error_body)

        except Exception as e:
            # Unexpected error (no CORS here — Flask handles it on success)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            error_body = json.dumps(
                {"error": "Internal Server Error", "code": 500, "message": str(e)}
            ).encode()
            self.wfile.write(error_body)

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
