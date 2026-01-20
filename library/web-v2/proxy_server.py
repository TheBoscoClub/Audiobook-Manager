#!/usr/bin/env python3
"""
Reverse Proxy Server for Audiobooks Library
============================================
Serves as a unified HTTPS endpoint that:
- Proxies /api/* requests to the Flask backend (waitress on localhost:5001)
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
from config import (AUDIOBOOKS_API_PORT, AUDIOBOOKS_BIND_ADDRESS,
                    AUDIOBOOKS_CERTS, AUDIOBOOKS_WEB_PORT)

HTTPS_PORT = AUDIOBOOKS_WEB_PORT
API_PORT = AUDIOBOOKS_API_PORT
CERT_DIR = AUDIOBOOKS_CERTS
CERT_FILE = CERT_DIR / "server.crt"
KEY_FILE = CERT_DIR / "server.key"
BIND_ADDRESS = AUDIOBOOKS_BIND_ADDRESS


class ReverseProxyHandler(http.server.SimpleHTTPRequestHandler):
    """Handler that proxies API requests and serves static files."""

    def do_GET(self):
        if self.path.startswith("/api/") or self.path.startswith("/covers/"):
            self.proxy_to_api("GET")
        else:
            # Serve static files
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self.proxy_to_api("POST")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self.proxy_to_api("PUT")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self.proxy_to_api("DELETE")
        else:
            self.send_error(405, "Method Not Allowed")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header(
            "Access-Control-Expose-Headers",
            "Content-Range, Accept-Ranges, Content-Length",
        )
        self.end_headers()

    def proxy_to_api(self, method="GET"):
        """Proxy request to Flask API backend."""
        # Validate the path to prevent SSRF - only allow /api/ and /covers/ paths
        # and sanitize to prevent path traversal
        path = self.path
        if not (path.startswith("/api/") or path.startswith("/covers/")):
            self.send_error(403, "Forbidden - Invalid path")
            return

        # Sanitize path: remove any null bytes and normalize
        path = path.replace("\x00", "")
        # Construct URL to local backend only (never external)
        # CodeQL: SSRF safe - path validated above, connects to localhost only
        api_url = f"http://127.0.0.1:{API_PORT}{path}"  # lgtm[py/ssrf]

        try:
            # Prepare headers
            headers = {}
            for header in ["Content-Type", "Range", "Accept"]:
                if header in self.headers:
                    headers[header] = self.headers[header]

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

            with urllib.request.urlopen(req, timeout=30) as response:
                # Send response status
                self.send_response(response.status)

                # Copy headers from API response
                for header, value in response.headers.items():
                    self.send_header(header, value)

                # Add CORS headers
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                # Stream response body
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            # Forward HTTP errors from API
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error_body = json.dumps(
                {"error": e.reason, "code": e.code, "message": f"API error: {e.reason}"}
            ).encode()
            self.wfile.write(error_body)

        except urllib.error.URLError as e:
            # API server not reachable
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
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
            # Unexpected error
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            error_body = json.dumps(
                {"error": "Internal Server Error", "code": 500, "message": str(e)}
            ).encode()
            self.wfile.write(error_body)

    def log_message(self, format, *args):
        """Log with [PROXY] prefix."""
        print(f"[PROXY] {self.address_string()} - {format % args}")


class ReuseHTTPServer(http.server.HTTPServer):
    """HTTPServer with socket reuse enabled."""

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
