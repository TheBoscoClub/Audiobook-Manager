#!/usr/bin/env python3
"""Simple HTTPS server for serving static files with HTTP redirect."""

import http.server
import ssl
import os
import sys
import threading
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    AUDIOBOOKS_WEB_PORT,
    AUDIOBOOKS_HTTP_REDIRECT_PORT,
    AUDIOBOOKS_HTTP_REDIRECT_ENABLED,
    AUDIOBOOKS_CERTS
)

HTTPS_PORT = AUDIOBOOKS_WEB_PORT
HTTP_PORT = AUDIOBOOKS_HTTP_REDIRECT_PORT
HTTP_REDIRECT_ENABLED = AUDIOBOOKS_HTTP_REDIRECT_ENABLED
CERT_DIR = AUDIOBOOKS_CERTS
CERT_FILE = CERT_DIR / 'server.crt'
KEY_FILE = CERT_DIR / 'server.key'


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
        host = self.headers.get('Host', 'localhost')
        # Remove port if present
        if ':' in host:
            host = host.split(':')[0]

        https_url = f"https://{host}:{HTTPS_PORT}{self.path}"
        self.send_response(301)
        self.send_header('Location', https_url)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        body = f'''<!DOCTYPE html>
<html>
<head><title>Redirecting...</title></head>
<body>
<p>Redirecting to <a href="{https_url}">{https_url}</a></p>
</body>
</html>'''
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        """Log with [HTTP] prefix to distinguish from HTTPS logs."""
        print(f"[HTTP] {self.address_string()} - {format % args}")


def run_http_redirect_server():
    """Run HTTP server that redirects to HTTPS."""
    try:
        server = http.server.HTTPServer(('0.0.0.0', HTTP_PORT), HTTPToHTTPSRedirectHandler)
        print(f"HTTP redirect server on http://0.0.0.0:{HTTP_PORT}/ -> https://...:{HTTPS_PORT}/")
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

    handler = http.server.SimpleHTTPRequestHandler

    # Create SSL context
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

    # Create HTTPS server
    server = http.server.HTTPServer(('0.0.0.0', HTTPS_PORT), handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    print(f"Serving HTTPS on https://0.0.0.0:{HTTPS_PORT}/ ...")
    print(f"Certificate: {CERT_FILE}")
    print(f"Key: {KEY_FILE}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == '__main__':
    main()
