#!/usr/bin/env python3
"""
Audiobook Library API Server

IMPORTANT: gevent monkey-patching MUST be the first executable code.
It patches stdlib I/O (including sqlite3) for cooperative scheduling.
Without this, SQLite queries block the entire greenlet loop.
"""

from gevent import monkey

monkey.patch_all()

import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from api_modular import create_app  # noqa: E402

from config import API_PORT, DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR  # noqa: E402


def _create_configured_app():
    """Create and return the configured Flask application."""
    if not DATABASE_PATH.exists():
        print(f"Error: Database not found at {DATABASE_PATH}")
        print("Please run: python3 backend/import_to_db.py")
        sys.exit(1)

    auth_enabled = os.environ.get("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
    auth_db_path = os.environ.get("AUTH_DATABASE") if auth_enabled else None
    auth_key_path = os.environ.get("AUTH_KEY_FILE") if auth_enabled else None
    auth_dev_mode = os.environ.get("AUDIOBOOKS_DEV_MODE", "false").lower() in ("true", "1", "yes")

    return create_app(
        database_path=DATABASE_PATH,
        project_dir=PROJECT_DIR,
        supplements_dir=SUPPLEMENTS_DIR,
        api_port=API_PORT,
        auth_db_path=Path(auth_db_path) if auth_db_path else None,
        auth_key_path=Path(auth_key_path) if auth_key_path else None,
        auth_dev_mode=auth_dev_mode,
    )


# Module-level app object for Gunicorn: `gunicorn api_server:app`
app = _create_configured_app()


if __name__ == "__main__":
    # Direct execution for development/testing only
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")
    if debug:
        app.run(host="127.0.0.1", port=API_PORT, debug=True)  # nosec B201 — dev-only path behind __main__ guard; production uses Gunicorn
    else:
        from gevent.pywsgi import WSGIServer

        server = WSGIServer(
            ("0.0.0.0", API_PORT),  # noqa: S104  # nosec B104 — bind 0.0.0.0 intentional; service is fronted by Caddy/TLS reverse proxy, not exposed directly
            app,
        )
        print(f"Serving on http://0.0.0.0:{API_PORT}")
        server.serve_forever()
