"""
Pytest configuration and shared fixtures for Audiobooks Library tests.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

# Project root (two levels up from library/tests/)
PROJECT_ROOT = LIBRARY_DIR.parent

# Path to the database schema
SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"

# VM connection details
VM_HOST = "192.168.122.100"
VM_API_PORT = 5001


@pytest.fixture(scope="session")
def deploy_to_vm():
    """Deploy latest code to test-vm-cachyos before integration tests.

    Runs ./deploy-vm.sh --full --restart and waits for the API health check.
    Skip with SKIP_VM_DEPLOY=1 for rapid iteration when code is already deployed.
    """
    if os.environ.get("SKIP_VM_DEPLOY", "").strip() == "1":
        return

    deploy_script = PROJECT_ROOT / "deploy-vm.sh"
    if not deploy_script.exists():
        pytest.skip("deploy-vm.sh not found at project root")

    result = subprocess.run(
        [str(deploy_script), "--full", "--restart"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        pytest.fail(f"deploy-vm.sh failed:\n{result.stderr}\n{result.stdout}")

    # Wait for API to become healthy
    import requests

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"http://{VM_HOST}:{VM_API_PORT}/api/system/version", timeout=3
            )
            if resp.status_code in (200, 401, 403):
                # 401/403 means auth is required but API is up
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)

    pytest.fail("API did not become healthy within 30s after deploy")


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
