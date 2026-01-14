"""
Core API utilities - Database connection, CORS, and shared helpers.
"""

import sqlite3
from pathlib import Path
from typing import Union

from flask import Response

# Type alias for Flask route return types
FlaskResponse = Union[Response, tuple[Response, int], tuple[str, int]]


def get_db(db_path: Path) -> sqlite3.Connection:
    """Get database connection with Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def add_cors_headers(response: Response) -> Response:
    """
    Add CORS headers to all responses.
    This is a simple implementation suitable for localhost/personal use.
    Replaces flask-cors which has multiple CVEs (CVE-2024-6221, etc.)
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = (
        "Content-Range, Accept-Ranges, Content-Length"
    )
    return response
