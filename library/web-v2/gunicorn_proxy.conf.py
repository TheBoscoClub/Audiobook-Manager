"""
Gunicorn configuration for the audiobook reverse proxy (audiobook-proxy.service).

Single canonical server configuration — both the systemd unit and the
`python proxy_server.py` standalone entry point run gunicorn with this file:

    gunicorn -c gunicorn_proxy.conf.py proxy_server:app

Worker model (v8.4.2.0, Audiobook-Manager-nfx "Option B"):
- gevent workers: cooperative I/O so long-lived WebSocket tunnels and
  streaming-audio forwards never starve covers/API traffic. Mirrors
  audiobook-api.service's worker class.
- 2 workers: the proxy is I/O-bound and lightweight; each gevent worker
  multiplexes many concurrent requests, and two processes remove the
  single-process cold-start thundering-herd bottleneck that produced
  transient 502s via Caddy's dial_timeout.
- Unlike the API (hard 1-worker constraint for its in-memory WebSocket
  connection manager), the proxy is stateless — worker count is free to
  scale.

TLS is terminated here (certfile/keyfile below); the ssl_context hook
enforces TLS 1.2 minimum, matching the pre-gunicorn proxy's
``ssl.TLSVersion.TLSv1_2`` floor.
"""

import os
import ssl
import sys
from pathlib import Path

# Make proxy_server (and the shared config module) importable regardless of
# the caller's working directory.
_WEB_V2_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_WEB_V2_DIR))
sys.path.insert(0, str(_WEB_V2_DIR.parent))

from config import (  # noqa: E402
    AUDIOBOOKS_BIND_ADDRESS,
    AUDIOBOOKS_CERTS,
    AUDIOBOOKS_WEB_PORT,
)

_CERT_FILE = AUDIOBOOKS_CERTS / "server.crt"
_KEY_FILE = AUDIOBOOKS_CERTS / "server.key"

# Fail fast with actionable guidance when certs are missing (the friendly
# message the pre-gunicorn proxy_server.main() printed).
if not _CERT_FILE.exists() or not _KEY_FILE.exists():
    print(f"Error: Certificate files not found in {AUDIOBOOKS_CERTS}")
    print(f"  Expected: {_CERT_FILE}")
    print(f"  Expected: {_KEY_FILE}")
    print()
    print("Generate certificates with:")
    print(f"  mkdir -p {AUDIOBOOKS_CERTS}")
    print("  openssl req -x509 -newkey rsa:4096 -nodes \\")
    print(f"    -keyout {_KEY_FILE} \\")
    print(f"    -out {_CERT_FILE} \\")
    print("    -days 365 -subj '/CN=localhost'")
    sys.exit(1)

# --- Core server settings ---
bind = f"{AUDIOBOOKS_BIND_ADDRESS}:{AUDIOBOOKS_WEB_PORT}"
workers = int(os.environ.get("AUDIOBOOKS_PROXY_WORKERS", "2"))
worker_class = "gevent"
chdir = str(_WEB_V2_DIR)

# Long timeout mirrors audiobook-api.service — streaming-audio forwards and
# WebSocket tunnels are long-lived; gevent workers heartbeat cooperatively.
timeout = 1800
graceful_timeout = 60
keepalive = 5

# --- TLS ---
certfile = str(_CERT_FILE)
keyfile = str(_KEY_FILE)


def ssl_context(config, default_ssl_context_factory):
    """Enforce TLS 1.2 minimum (parity with the pre-gunicorn proxy)."""
    context = default_ssl_context_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


# --- Logging (systemd journal picks up stdout/stderr) ---
accesslog = None  # request logging is handled by ReverseProxyApp.log_message
errorlog = "-"
proc_name = "audiobook-proxy"
