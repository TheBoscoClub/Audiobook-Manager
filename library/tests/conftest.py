"""
Pytest configuration and shared fixtures for Audiobooks Library tests.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

# Path to the database schema
SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"


def init_test_database(db_path: Path) -> None:
    """Initialize a test database with the schema.

    Creates all tables, indices, views, and triggers from schema.sql.
    """
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


# Session-scoped temp directory for the Flask app
# This persists across all tests in the session
@pytest.fixture(scope="session")
def session_temp_dir():
    """Create a session-scoped temporary directory for the Flask app."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# Session-scoped Flask app to avoid blueprint double-registration
@pytest.fixture(scope="session")
def flask_app(session_temp_dir):
    """Create a session-scoped Flask app.

    Flask blueprints can only be registered once. Using session scope
    ensures the app is created once and reused across all tests.
    """
    from backend.api_modular import create_app

    test_db = session_temp_dir / "test_audiobooks.db"

    # Initialize database with schema
    init_test_database(test_db)

    # Create supplements directory
    supplements_dir = session_temp_dir / "supplements"
    supplements_dir.mkdir(exist_ok=True)

    app = create_app(
        database_path=test_db,
        project_dir=session_temp_dir,
        supplements_dir=supplements_dir,
        api_port=5099,
    )
    app.config["TESTING"] = True

    return app


@pytest.fixture
def app_client(flask_app):
    """Create a test client for the Flask API.

    Uses the session-scoped app to avoid blueprint re-registration issues.
    Each test gets a fresh test client but shares the app instance.
    """
    with flask_app.test_client() as client:
        yield client


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config_env(temp_dir):
    """Create a mock config.env file."""
    config_file = temp_dir / "config.env"
    config_file.write_text("""
# Test configuration
AUDIOBOOKS_DATA=/test/audiobooks
AUDIOBOOKS_LIBRARY=/test/audiobooks/Library
AUDIOBOOKS_SOURCES=/test/audiobooks/Sources
AUDIOBOOKS_API_PORT=5001
""")
    return config_file


@pytest.fixture
def sample_audiobook_data():
    """Sample audiobook data for testing."""
    return {
        "id": 1,
        "title": "Test Audiobook",
        "author": "Test Author",
        "narrator": "Test Narrator",
        "duration_hours": 10.5,
        "file_path": "/test/path/audiobook.opus",
        "asin": "B00TEST123",
    }
