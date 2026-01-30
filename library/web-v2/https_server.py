#!/usr/bin/env python3
"""HTTPS server for serving static files with API proxying and HTTP redirect."""

import http.server
import http.client
import os
import ssl
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (AUDIOBOOKS_CERTS, AUDIOBOOKS_HTTP_REDIRECT_ENABLED,
                    AUDIOBOOKS_HTTP_REDIRECT_PORT, AUDIOBOOKS_WEB_PORT,
                    AUDIOBOOKS_API_PORT)

HTTPS_PORT = AUDIOBOOKS_WEB_PORT
HTTP_PORT = AUDIOBOOKS_HTTP_REDIRECT_PORT
HTTP_REDIRECT_ENABLED = AUDIOBOOKS_HTTP_REDIRECT_ENABLED
API_PORT = AUDIOBOOKS_API_PORT
CERT_DIR = AUDIOBOOKS_CERTS
CERT_FILE = CERT_DIR / "server.crt"
KEY_FILE = CERT_DIR / "server.key"

# Paths that should be proxied to the API server
API_PREFIXES = ('/auth/', '/auth', '/api/', '/api')


class APIProxyHandler(http.server.SimpleHTTPRequestHandler):
    """Handler that serves static files and proxies API requests."""

    def do_GET(self):
        if self._is_api_request():
            self._proxy_to_api()
        else:
            super().do_GET()

    def do_POST(self):
        if self._is_api_request():
            self._proxy_to_api()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_DELETE(self):
        if self._is_api_request():
            self._proxy_to_api()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PUT(self):
        if self._is_api_request():
            self._proxy_to_api()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PATCH(self):
        if self._is_api_request():
            self._proxy_to_api()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_OPTIONS(self):
        if self._is_api_request():
            self._proxy_to_api()
        else:
            self.send_error(405, "Method Not Allowed")

    def _is_api_request(self):
        """Check if this request should be proxied to the API."""
        return self.path.startswith(API_PREFIXES)

    def _proxy_to_api(self):
        """Proxy the request to the Flask API server."""
        try:
            # Read request body if present
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            # Connect to API server
            conn = http.client.HTTPConnection('127.0.0.1', API_PORT, timeout=30)

            # Forward headers (filter out hop-by-hop headers)
            headers = {}
            hop_by_hop = {'connection', 'keep-alive', 'proxy-authenticate',
                          'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
                          'upgrade', 'host'}
            for key, value in self.headers.items():
                if key.lower() not in hop_by_hop:
                    headers[key] = value

            # Add forwarding headers
            client_ip = self.client_address[0]
            headers['X-Forwarded-For'] = client_ip
            headers['X-Forwarded-Proto'] = 'https'
            headers['X-Real-IP'] = client_ip

            # Make the request to the API
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()

            # Send response status
            self.send_response(response.status)

            # Forward response headers (filter hop-by-hop)
            for key, value in response.getheaders():
                if key.lower() not in hop_by_hop:
                    self.send_header(key, value)
            self.end_headers()

            # Forward response body
            self.wfile.write(response.read())
            conn.close()

        except ConnectionRefusedError:
            self.send_error(502, "API server unavailable (connection refused)")
        except Exception as e:
            self.send_error(502, f"API proxy error: {str(e)}")


class HTTPToHTTPSRedirectHandler(http.server.BaseHTTPRequestHandler):
    """Handler that redirects all HTTP requests to HTTPS."""

    def do_GET(self):
        self.send_redirect()

    def do_POST(self):
        self.send_redirect()

    def do_HEAD(self):
        self.send_redirect()

    def send_redirect(self):
        """Send 301 redirect to HTTPS version of the URL."""
        # Get the host from the request, default to localhost
        host = self.headers.get("Host", "localhost")
        # Remove port if present
        if ":" in host:
            host = host.split(":")[0]

        # Sanitize host and path to prevent HTTP response splitting
        # Remove any newlines, carriage returns, or null bytes
        # CodeQL: safe_host/safe_path sanitized - no CRLF characters
        safe_host = "".join(c for c in host if c not in "\r\n\0")
        safe_path = "".join(c for c in self.path if c not in "\r\n\0")

        https_url = f"https://{safe_host}:{HTTPS_PORT}{safe_path}"
        self.send_response(301)
        self.send_header("Location", https_url)  # lgtm[py/http-response-splitting]
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = f"""<!DOCTYPE html>
<html>
<head><title>Redirecting...</title></head>
<body>
<p>Redirecting to <a href="{https_url}">{https_url}</a></p>
</body>
</html>"""
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        """Log with [HTTP] prefix to distinguish from HTTPS logs."""
        print(f"[HTTP] {self.address_string()} - {format % args}")


def run_http_redirect_server():
    """Run HTTP server that redirects to HTTPS."""
    try:
        server = http.server.HTTPServer(
            ("0.0.0.0", HTTP_PORT), HTTPToHTTPSRedirectHandler
        )
        print(
            f"HTTP redirect server on http://0.0.0.0:{HTTP_PORT}/ -> https://...:{HTTPS_PORT}/"
        )
        server.serve_forever()
    except Exception as e:
        print(f"HTTP redirect server error: {e}")


def main():
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        print(f"Error: Certificate files not found in {CERT_DIR}")
        print(f"  Expected: {CERT_FILE}")
        print(f"  Expected: {KEY_FILE}")
        sys.exit(1)

    # Change to web directory
    web_dir = Path(__file__).parent
    os.chdir(web_dir)

    # Start HTTP redirect server in background thread (if enabled)
    if HTTP_REDIRECT_ENABLED:
        http_thread = threading.Thread(target=run_http_redirect_server, daemon=True)
        http_thread.start()

    handler = APIProxyHandler

    # Create SSL context with TLS 1.2+ minimum
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2  # CodeQL: Enforce TLS 1.2+
    context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

    # Create HTTPS server
    server = http.server.HTTPServer(("0.0.0.0", HTTPS_PORT), handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    print(f"Serving HTTPS on https://0.0.0.0:{HTTPS_PORT}/ ...")
    print(f"API proxy: /auth/* and /api/* -> http://127.0.0.1:{API_PORT}/")
    print(f"Certificate: {CERT_FILE}")
    print(f"Key: {KEY_FILE}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
