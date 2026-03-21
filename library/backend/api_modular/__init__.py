"""
Audiobook Library API - Flask Backend Package

This package provides a modular Flask API for the audiobook library.
Routes are organized into blueprints by functionality:
- audiobooks: Main listing, filtering, streaming, single book
- collections: Predefined genre-based collections
- editions: Edition detection and grouping
- duplicates: Duplicate detection (hash and title based)
- supplements: PDF, ebook, and other companion files
- utilities: CRUD, imports, exports, maintenance

For backward compatibility, this module also exports:
- app: The Flask application instance
- get_db: Function to get a database connection
- All the constants from the old api.py
"""

import os
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, request

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
# Type alias for Flask route return types (backward compatibility)
from typing import Optional, Union

from config import API_PORT, DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR

from .audiobooks import audiobooks_bp, init_audiobooks_routes
from .collections import (
    COLLECTIONS,
    collections_bp,
    genre_query,
    init_collections_routes,
    multi_genre_query,
)
from .core import add_cors_headers, add_security_headers
from .core import get_db as _get_db_with_path
from .duplicates import duplicates_bp, init_duplicates_routes
from .editions import (
    editions_bp,
    has_edition_marker,
    init_editions_routes,
    normalize_base_title,
)
from .position_sync import init_position_routes, position_bp
from .supplements import init_supplements_routes, supplements_bp
from .grouped import grouped_bp, init_grouped_routes
from .admin_activity import admin_activity_bp, init_admin_activity_routes
from .admin_authors import admin_authors_bp, init_admin_authors_routes
from .user_state import init_user_state_routes, user_bp
from .utilities import init_utilities_routes, utilities_bp
from .auth import (
    auth_bp,
    init_auth_routes,
    login_required,
    admin_required,
    localhost_only,
    get_current_user,
    auth_if_enabled,
    download_permission_required,
    admin_if_enabled,
)

FlaskResponse = Union[Response, tuple[Response, int], tuple[str, int]]

# Backward compatibility: Global database path and project root
DB_PATH = DATABASE_PATH
PROJECT_ROOT = PROJECT_DIR / "library"


def get_db():
    """Get database connection - backward compatible wrapper."""
    return _get_db_with_path(DB_PATH)


def create_app(
    database_path: Optional[Path] = None,
    project_dir: Optional[Path] = None,
    supplements_dir: Optional[Path] = None,
    api_port: Optional[int] = None,
    auth_db_path: Optional[Path] = None,
    auth_key_path: Optional[Path] = None,
    auth_dev_mode: bool = False,
):
    """
    Create and configure the Flask application.

    Args:
        database_path: Path to the SQLite database file (default: from config)
        project_dir: Path to the project root directory (default: from config)
        supplements_dir: Path to the supplements directory (default: from config)
        api_port: Port to run the API on (default: from config)

    Returns:
        Configured Flask application
    """
    # Use defaults from config if not provided
    database_path = database_path or DATABASE_PATH
    project_dir = project_dir or PROJECT_DIR
    supplements_dir = supplements_dir or SUPPLEMENTS_DIR
    api_port = api_port or API_PORT

    flask_app = Flask(__name__)

    # Session cookie security hardening
    flask_app.config["SESSION_COOKIE_SECURE"] = True
    flask_app.config["SESSION_COOKIE_HTTPONLY"] = True
    flask_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Store configuration
    flask_app.config["DATABASE_PATH"] = database_path
    flask_app.config["PROJECT_DIR"] = project_dir
    flask_app.config["SUPPLEMENTS_DIR"] = supplements_dir
    flask_app.config["API_PORT"] = api_port
    flask_app.config["AUTH_DEV_MODE"] = auth_dev_mode

    project_root = project_dir / "library"

    # Auth database paths (optional - auth endpoints disabled if not configured)
    if auth_db_path and auth_key_path:
        flask_app.config["AUTH_DB_PATH"] = auth_db_path
        flask_app.config["AUTH_KEY_PATH"] = auth_key_path
        flask_app.config["AUTH_ENABLED"] = True
    else:
        flask_app.config["AUTH_ENABLED"] = False

    # Register CORS and security headers
    @flask_app.after_request
    def apply_cors(response: Response) -> Response:
        return add_cors_headers(response)

    @flask_app.after_request
    def apply_security_headers(response: Response) -> Response:
        return add_security_headers(response)

    # Handle OPTIONS preflight requests
    @flask_app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
    @flask_app.route("/<path:path>", methods=["OPTIONS"])
    def handle_options(path: str) -> tuple[str, int]:
        """Handle CORS preflight requests"""
        return "", 204

    # Initialize route modules with their dependencies.
    # Modules using current_app.config (audiobooks, grouped, admin_authors) are
    # no-ops but called for API compatibility. Closure-based modules need guards.
    init_audiobooks_routes(database_path, project_root, database_path)
    if not getattr(collections_bp, "_routes_initialized", False):
        init_collections_routes(database_path)
        collections_bp._routes_initialized = True
    if not getattr(editions_bp, "_routes_initialized", False):
        init_editions_routes(database_path)
        editions_bp._routes_initialized = True
    if not getattr(duplicates_bp, "_routes_initialized", False):
        init_duplicates_routes(database_path)
        duplicates_bp._routes_initialized = True
    if not getattr(supplements_bp, "_routes_initialized", False):
        init_supplements_routes(database_path, supplements_dir)
        supplements_bp._routes_initialized = True
    if not getattr(utilities_bp, "_routes_initialized", False):
        init_utilities_routes(database_path, project_root)
        utilities_bp._routes_initialized = True
    if not getattr(position_bp, "_routes_initialized", False):
        init_position_routes(database_path)
        position_bp._routes_initialized = True
    init_grouped_routes(database_path)
    init_admin_authors_routes(database_path)

    # Initialize auth routes if configured
    if flask_app.config["AUTH_ENABLED"]:
        init_auth_routes(
            auth_db_path=flask_app.config["AUTH_DB_PATH"],
            auth_key_path=flask_app.config["AUTH_KEY_PATH"],
            is_dev=auth_dev_mode,
        )
        init_user_state_routes(database_path)
        init_admin_activity_routes(database_path)

    # Register blueprints
    flask_app.register_blueprint(audiobooks_bp)
    flask_app.register_blueprint(collections_bp)
    flask_app.register_blueprint(editions_bp)
    flask_app.register_blueprint(duplicates_bp)
    flask_app.register_blueprint(supplements_bp)
    flask_app.register_blueprint(utilities_bp)
    flask_app.register_blueprint(position_bp)
    flask_app.register_blueprint(grouped_bp)
    flask_app.register_blueprint(admin_authors_bp)

    # Register auth blueprint if configured
    if flask_app.config["AUTH_ENABLED"]:
        flask_app.register_blueprint(auth_bp)
        flask_app.register_blueprint(user_bp)
        flask_app.register_blueprint(admin_activity_bp)

    return flask_app


# Export public API - including backward-compatible names
__all__ = [
    # Factory functions
    "create_app",
    "get_db",
    "DB_PATH",
    "PROJECT_ROOT",
    "FlaskResponse",
    # Helper functions (backward compatibility)
    "has_edition_marker",
    "normalize_base_title",
    "genre_query",
    "multi_genre_query",
    # Constants
    "COLLECTIONS",
    # Blueprints
    "audiobooks_bp",
    "collections_bp",
    "editions_bp",
    "duplicates_bp",
    "supplements_bp",
    "utilities_bp",
    "position_bp",
    "admin_activity_bp",
    "admin_authors_bp",
    "grouped_bp",
    "auth_bp",
    "user_bp",
    # Auth decorators
    "login_required",
    "admin_required",
    "localhost_only",
    "get_current_user",
    "auth_if_enabled",
    "download_permission_required",
    "admin_if_enabled",
]
