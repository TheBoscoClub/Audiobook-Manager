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
    """Get database connection with Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    # Allow credentials only when origin is not wildcard (spec forbids credentials with *)
    if CORS_ORIGIN != "*":
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response
