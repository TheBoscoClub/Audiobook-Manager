#!/usr/bin/env python3
"""
HTTP to HTTPS Redirect Server
==============================
Redirects all HTTP traffic to HTTPS endpoint.
"""

import http.server
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    AUDIOBOOKS_WEB_PORT,
    AUDIOBOOKS_HTTP_REDIRECT_PORT,
    AUDIOBOOKS_BIND_ADDRESS
)

HTTPS_PORT = AUDIOBOOKS_WEB_PORT
HTTP_PORT = AUDIOBOOKS_HTTP_REDIRECT_PORT
BIND_ADDRESS = AUDIOBOOKS_BIND_ADDRESS


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
<head><title>Redirecting to HTTPS...</title></head>
<body>
<p>Redirecting to <a href="{https_url}">{https_url}</a></p>
</body>
</html>'''
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        """Log with [REDIRECT] prefix to distinguish from HTTPS logs."""
        print(f"[REDIRECT] {self.address_string()} - {format % args}")


class ReuseHTTPServer(http.server.HTTPServer):
    """HTTPServer with socket reuse enabled."""
    def server_bind(self):
        import socket
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


def main():
    """Run HTTP redirect server."""
    try:
        server_address = (BIND_ADDRESS, HTTP_PORT)
        httpd = ReuseHTTPServer(server_address, HTTPToHTTPSRedirectHandler)
        print(f"HTTP→HTTPS Redirect Server")
        print(f"==========================")
        print(f"Listening on: http://{BIND_ADDRESS}:{HTTP_PORT}/")
        print(f"Redirecting to: https://...:{HTTPS_PORT}/")
        print()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()
    except Exception as e:
        print(f"Error starting redirect server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
