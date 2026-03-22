"""
Core API utilities - Database connection, CORS, and shared helpers.
"""

import os
import sqlite3
from pathlib import Path
from typing import Union

from flask import Response

# Type alias for Flask route return types
FlaskResponse = Union[Response, tuple[Response, int], tuple[str, int]]

# CORS allowed origin — set via CORS_ORIGIN env var for remote deployments
# Default: * (permissive, safe for standalone/localhost use)
# Remote: Set to your domain (e.g., https://library.example.com) in audiobooks.conf
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")


def get_db(db_path: Path) -> sqlite3.Connection:
    """Get database connection with Row factory and WAL mode.

    WAL (Write-Ahead Logging) allows concurrent readers and writers —
    position sync writes no longer block library page reads.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")  # 8MB cache (negative = KiB)
    conn.execute("PRAGMA busy_timeout=5000")  # Wait 5s on lock instead of failing
    return conn


def add_cors_headers(response: Response) -> Response:
    """
    Add CORS headers to all responses.
    Replaces flask-cors which has multiple CVEs (CVE-2024-6221, etc.)
    """
    response.headers["Access-Control-Allow-Origin"] = CORS_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = (
        "Content-Range, Accept-Ranges, Content-Length"
    )
    # Allow credentials only when not wildcard (spec forbids credentials with *)
    if CORS_ORIGIN != "*":
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def add_security_headers(response: Response) -> Response:
    """Add security headers to all responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self' wss: ws:; "
        "font-src 'self'; "
        "frame-ancestors 'self'; "
        "frame-src 'self'"
    )
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # HSTS only when serving over HTTPS
    if os.environ.get("AUDIOBOOKS_HTTPS_ENABLED", "true").lower() == "true":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response
