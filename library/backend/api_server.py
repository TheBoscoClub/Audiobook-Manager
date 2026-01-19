#!/usr/bin/env python3
"""
Audiobook Library API Server

This is the main entry point for the audiobook library API.
Uses the modular api package for all route handling.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from api_modular import create_app, run_server
from config import API_PORT, DATABASE_PATH, PROJECT_DIR, SUPPLEMENTS_DIR


def main():
    """Main entry point for the API server."""
    if not DATABASE_PATH.exists():
        print(f"Error: Database not found at {DATABASE_PATH}")
        print("Please run: python3 backend/import_to_db.py")
        sys.exit(1)

    # Auth database configuration (optional)
    auth_db_path = os.environ.get("AUTH_DATABASE")
    auth_key_path = os.environ.get("AUTH_KEY_FILE")
    auth_dev_mode = os.environ.get("AUDIOBOOKS_DEV_MODE", "false").lower() in ("true", "1", "yes")

    # Create the Flask application
    app = create_app(
        database_path=DATABASE_PATH,
        project_dir=PROJECT_DIR,
        supplements_dir=SUPPLEMENTS_DIR,
        api_port=API_PORT,
        auth_db_path=Path(auth_db_path) if auth_db_path else None,
        auth_key_path=Path(auth_key_path) if auth_key_path else None,
        auth_dev_mode=auth_dev_mode,
    )

    # Check if running with waitress (production mode)
    use_waitress = os.environ.get("AUDIOBOOKS_USE_WAITRESS", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    debug = os.environ.get("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")

    # Run the server
    run_server(app, port=API_PORT, debug=debug, use_waitress=use_waitress)


if __name__ == "__main__":
    main()
