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

from flask import Flask, Response

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
# Type alias for Flask route return types (backward compatibility)
from typing import Optional, Union

from config import API_PORT, DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR

from .audiobooks import audiobooks_bp, init_audiobooks_routes
from .collections import (COLLECTIONS, collections_bp, genre_query,
                          init_collections_routes, multi_genre_query)
from .core import add_cors_headers
from .core import get_db as _get_db_with_path
from .duplicates import duplicates_bp, init_duplicates_routes
from .editions import (editions_bp, has_edition_marker, init_editions_routes,
                       normalize_base_title)
from .position_sync import init_position_routes, position_bp
from .supplements import init_supplements_routes, supplements_bp
from .utilities import init_utilities_routes, utilities_bp
from .auth import auth_bp, init_auth_routes, login_required, admin_required, localhost_only, get_current_user

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

    # Register CORS handler
    @flask_app.after_request
    def apply_cors(response: Response) -> Response:
        return add_cors_headers(response)

    # Handle OPTIONS preflight requests
    @flask_app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
    @flask_app.route("/<path:path>", methods=["OPTIONS"])
    def handle_options(path: str) -> tuple[str, int]:
        """Handle CORS preflight requests"""
        return "", 204

    # Initialize all route modules with their dependencies
    init_audiobooks_routes(database_path, project_root, database_path)
    init_collections_routes(database_path)
    init_editions_routes(database_path)
    init_duplicates_routes(database_path)
    init_supplements_routes(database_path, supplements_dir)
    init_utilities_routes(database_path, project_root)
    init_position_routes(database_path)

    # Initialize auth routes if configured
    if flask_app.config["AUTH_ENABLED"]:
        init_auth_routes(
            auth_db_path=flask_app.config["AUTH_DB_PATH"],
            auth_key_path=flask_app.config["AUTH_KEY_PATH"],
            is_dev=auth_dev_mode,
        )

    # Register blueprints
    flask_app.register_blueprint(audiobooks_bp)
    flask_app.register_blueprint(collections_bp)
    flask_app.register_blueprint(editions_bp)
    flask_app.register_blueprint(duplicates_bp)
    flask_app.register_blueprint(supplements_bp)
    flask_app.register_blueprint(utilities_bp)
    flask_app.register_blueprint(position_bp)

    # Register auth blueprint if configured
    if flask_app.config["AUTH_ENABLED"]:
        flask_app.register_blueprint(auth_bp)

    return flask_app


# Note: Global app instance removed to prevent double-registration issues
# when api_server.py calls create_app(). For backward compatibility,
# callers should use create_app() directly.
app = None  # Placeholder for backward compatibility checks


def run_server(
    flask_app: Optional[Flask] = None,
    port: Optional[int] = None,
    debug: bool = False,
    use_waitress: bool = False,
):
    """
    Run the Flask application.

    Args:
        flask_app: Flask application instance (default: global app)
        port: Port to run on (default: from config)
        debug: Enable debug mode
        use_waitress: Use waitress production server instead of Flask dev server
    """
    if flask_app is None:
        flask_app = app
    if flask_app is None:
        raise RuntimeError(
            "No Flask application provided and global app is not initialized. Call create_app() first."
        )
    port = port or API_PORT

    print("Starting Audiobook Library API...")
    print(f"Database: {flask_app.config.get('DATABASE_PATH', DATABASE_PATH)}")
    print("\nEndpoints:")
    print("  GET /api/stats - Library statistics")
    print("  GET /api/audiobooks - Paginated audiobooks")
    print("  GET /api/audiobooks/<id> - Single audiobook")
    print("  GET /api/filters - Available filter options")
    print("  GET /api/collections - Predefined collections")
    print("  GET /api/stream/<id> - Stream audiobook file")
    print("  GET /api/supplements - All supplements")
    print("  GET /api/supplements/stats - Supplement statistics")
    print("  GET /api/audiobooks/<id>/supplements - Supplements for audiobook")
    print("  GET /api/audiobooks/<id>/editions - Editions of a book")
    print("  GET /api/duplicates - Hash-based duplicates")
    print("  GET /api/duplicates/by-title - Title-based duplicates")
    print("  POST /api/supplements/scan - Scan and import supplements")
    print("  GET /covers/<filename> - Cover images")
    print("\nExample queries:")
    print("  /api/audiobooks?page=1&per_page=50")
    print("  /api/audiobooks?search=tolkien")
    print("  /api/audiobooks?author=sanderson&sort=duration_hours&order=desc")
    print()

    if use_waitress:
        try:
            from waitress import serve

            bind_address = os.environ.get("AUDIOBOOKS_BIND_ADDRESS", "127.0.0.1")
            print("Running in production mode (waitress)")
            print(f"Listening on: http://{bind_address}:{port}")
            print()
            serve(flask_app, host=bind_address, port=port, threads=4)
        except ImportError:
            print("Error: waitress not installed. Install with: pip install waitress")
            print("Falling back to Flask development server...")
            print(f"API running on: http://0.0.0.0:{port}")
            print()
            flask_app.run(debug=debug, host="0.0.0.0", port=port)
    else:
        # Development mode (Flask dev server)
        print("Running in development mode (Flask dev server)")
        print(f"API running on: http://0.0.0.0:{port}")
        print()
        flask_app.run(debug=debug, host="0.0.0.0", port=port)


# Export public API - including backward-compatible names
__all__ = [
    # Factory functions
    "create_app",
    "run_server",
    # Global instances (backward compatibility)
    "app",
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
    "auth_bp",
    # Auth decorators
    "login_required",
    "admin_required",
    "localhost_only",
    "get_current_user",
]
