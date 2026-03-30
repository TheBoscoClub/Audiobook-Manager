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
    collections_bp,
    get_collections_lookup,
    init_collections_routes,
    invalidate_collections_cache,
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
from .suggestions import suggestions_bp, init_suggestions_routes
from .user_state import init_user_state_routes, user_bp
from .preferences import preferences_bp
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


def _configure_app(flask_app, database_path, project_dir, supplements_dir,
                   api_port, auth_db_path, auth_key_path, auth_dev_mode):
    """Set Flask app configuration values."""
    flask_app.config["SESSION_COOKIE_SECURE"] = True
    flask_app.config["SESSION_COOKIE_HTTPONLY"] = True
    flask_app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    flask_app.config["DATABASE_PATH"] = database_path
    flask_app.config["PROJECT_DIR"] = project_dir
    flask_app.config["SUPPLEMENTS_DIR"] = supplements_dir
    flask_app.config["API_PORT"] = api_port
    flask_app.config["AUTH_DEV_MODE"] = auth_dev_mode

    if auth_db_path and auth_key_path:
        flask_app.config["AUTH_DB_PATH"] = auth_db_path
        flask_app.config["AUTH_KEY_PATH"] = auth_key_path
        flask_app.config["AUTH_ENABLED"] = True
    else:
        flask_app.config["AUTH_ENABLED"] = False


def _init_once(bp, init_fn, *args):
    """Initialize a blueprint's routes only once (idempotency guard)."""
    if not getattr(bp, "_routes_initialized", False):
        init_fn(*args)
        bp._routes_initialized = True


def _init_route_modules(flask_app, database_path, project_root, supplements_dir,
                        auth_dev_mode):
    """Initialize all route modules with their dependencies."""
    init_audiobooks_routes(database_path, project_root, database_path)
    _init_once(collections_bp, init_collections_routes, database_path)
    _init_once(editions_bp, init_editions_routes, database_path)
    _init_once(duplicates_bp, init_duplicates_routes, database_path)
    _init_once(supplements_bp, init_supplements_routes, database_path, supplements_dir)
    _init_once(utilities_bp, init_utilities_routes, database_path, project_root)
    _init_once(position_bp, init_position_routes, database_path)
    init_grouped_routes(database_path)
    init_admin_authors_routes(str(database_path))

    if flask_app.config["AUTH_ENABLED"]:
        init_auth_routes(
            auth_db_path=flask_app.config["AUTH_DB_PATH"],
            auth_key_path=flask_app.config["AUTH_KEY_PATH"],
            is_dev=auth_dev_mode,
        )
        init_user_state_routes(str(database_path))
        init_admin_activity_routes(str(database_path))


def _register_core_blueprints(flask_app):
    """Register all non-auth blueprints."""
    for bp in (audiobooks_bp, collections_bp, editions_bp, duplicates_bp,
               supplements_bp, utilities_bp, position_bp, grouped_bp,
               admin_authors_bp):
        flask_app.register_blueprint(bp)


def _register_auth_blueprints(flask_app):
    """Register auth-related blueprints if auth is enabled."""
    if flask_app.config["AUTH_ENABLED"]:
        for bp in (auth_bp, user_bp, preferences_bp, admin_activity_bp):
            flask_app.register_blueprint(bp)


def _register_extension_blueprints(flask_app, database_path):
    """Register maintenance, roadmap, and suggestions blueprints."""
    from .maintenance import maintenance_bp, init_maintenance_routes
    init_maintenance_routes(database_path)
    flask_app.register_blueprint(maintenance_bp)

    from .roadmap import roadmap_bp, init_roadmap_routes
    init_roadmap_routes(database_path)
    flask_app.register_blueprint(roadmap_bp)

    init_suggestions_routes(database_path)
    flask_app.register_blueprint(suggestions_bp)


def _setup_websocket(flask_app, database_path):
    """Configure WebSocket endpoint and admin connections route."""
    from flask_sock import Sock
    from .websocket import connection_manager
    import json as _json

    sock = Sock(flask_app)

    @sock.route("/api/ws")
    def ws_handler(ws):
        """WebSocket handler for heartbeat and push notifications."""
        auth_enabled = flask_app.config.get("AUTH_ENABLED", False)
        session_id = request.cookies.get("audiobooks_session", "anon-" + str(id(ws)))
        username = "anonymous"

        if auth_enabled:
            user = get_current_user()
            if user is None:
                ws.close(1008, "Authentication required")
                return
            username = user.username
            session_id = request.cookies.get("audiobooks_session", session_id)

        connection_manager.register(session_id, ws, username=username)
        from .websocket import init_notification_poller
        init_notification_poller(database_path)
        try:
            while True:
                data = ws.receive(timeout=15)
                if data is None:
                    break
                try:
                    msg = _json.loads(data)
                    if msg.get("type") == "heartbeat":
                        connection_manager.heartbeat(
                            session_id, state=msg.get("state", "idle")
                        )
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass
        finally:
            connection_manager.unregister(session_id)

    @flask_app.route("/api/admin/connections")
    @admin_if_enabled
    def get_connections():
        return jsonify(connection_manager.admin_connections_list())


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
    database_path = database_path or DATABASE_PATH
    project_dir = project_dir or PROJECT_DIR
    supplements_dir = supplements_dir or SUPPLEMENTS_DIR
    api_port = api_port or API_PORT

    flask_app = Flask(__name__)
    _configure_app(
        flask_app, database_path, project_dir, supplements_dir,
        api_port, auth_db_path, auth_key_path, auth_dev_mode,
    )

    project_root = project_dir / "library"

    # Register CORS and security headers
    @flask_app.after_request
    def apply_cors(response: Response) -> Response:
        return add_cors_headers(response)

    @flask_app.after_request
    def apply_security_headers(response: Response) -> Response:
        return add_security_headers(response)

    @flask_app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
    @flask_app.route("/<path:path>", methods=["OPTIONS"])
    def handle_options(path: str) -> tuple[str, int]:
        """Handle CORS preflight requests"""
        return "", 204

    _init_route_modules(flask_app, database_path, project_root, supplements_dir,
                        auth_dev_mode)
    _register_core_blueprints(flask_app)
    _register_auth_blueprints(flask_app)
    _register_extension_blueprints(flask_app, database_path)
    _setup_websocket(flask_app, database_path)

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
    "get_collections_lookup",
    "invalidate_collections_cache",
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
    "suggestions_bp",
    # Auth decorators
    "login_required",
    "admin_required",
    "localhost_only",
    "get_current_user",
    "auth_if_enabled",
    "download_permission_required",
    "admin_if_enabled",
]
