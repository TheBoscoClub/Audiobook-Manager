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


def pytest_addoption(parser):
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help="Run tests that require physical hardware (e.g., YubiKey touch)",
    )
    parser.addoption(
        "--vm",
        action="store_true",
        default=False,
        help="Run integration tests that require the test VM (test-audiobook-cachyos)",
    )


def pytest_configure(config):
    """Early configuration - runs before test collection."""
    import os

    # Set VM_TESTS env var early so modules can check it at import time
    if config.getoption("--vm", default=False):
        os.environ["VM_TESTS"] = "1"


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--hardware"):
        skip_hw = pytest.mark.skip(reason="needs --hardware flag to run")
        for item in items:
            if "hardware" in item.keywords:
                item.add_marker(skip_hw)
    if not config.getoption("--vm"):
        skip_vm = pytest.mark.skip(reason="needs --vm flag to run (test VM)")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_vm)


# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

# Project root (two levels up from library/tests/)
PROJECT_ROOT = LIBRARY_DIR.parent

# Path to the database schema
SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"

# VM connection details - test-audiobook-cachyos dedicated isolation VM
VM_HOST = "192.168.122.104"
VM_API_PORT = 5001
VM_NAME = "test-audiobook-cachyos"


VM_STARTED_BY_TESTS = False


@pytest.fixture(scope="session", autouse=False)
def ensure_vm_running():
    """Start test-audiobook-cachyos if it's powered off.

    Checks VM state via virsh and starts it if needed, then waits
    for SSH connectivity before allowing tests to proceed.
    """
    global VM_STARTED_BY_TESTS

    try:
        result = subprocess.run(
            ["sudo", "virsh", "domstate", "test-audiobook-cachyos"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("virsh not available or timed out")
        return

    if result.returncode != 0:
        pytest.skip("test-audiobook-cachyos not found in libvirt")
        return

    state = result.stdout.strip()

    if state == "running":
        return

    # Start the VM
    start_result = subprocess.run(
        ["sudo", "virsh", "start", "test-audiobook-cachyos"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if start_result.returncode != 0:
        pytest.fail(f"Failed to start VM: {start_result.stderr}")

    VM_STARTED_BY_TESTS = True

    # Wait for SSH connectivity (up to 60s)
    ssh_key = os.path.expanduser("~/.claude/ssh/id_ed25519")
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            r = subprocess.run(
                [
                    "ssh",
                    "-i",
                    ssh_key,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=3",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"claude@{VM_HOST}",
                    "echo",
                    "ok",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(3)

    pytest.fail("VM started but SSH not available within 60s")


@pytest.fixture(scope="session")
def deploy_to_vm(ensure_vm_running):
    """Deploy latest code to test-audiobook-cachyos before integration tests.

    Runs ./deploy-vm.sh --full --restart and waits for the API health check.
    Skip with SKIP_VM_DEPLOY=1 for rapid iteration when code is already deployed.
    Depends on ensure_vm_running to guarantee VM is up first.
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


# ============================================================
# Shared auth-enabled Flask app fixtures
# ============================================================
# Session-scoped so Flask blueprints are only registered once.
# Used by test_auth_api.py, test_user_state_api.py, and any
# future test files that need an auth-enabled Flask app.
# ============================================================


@pytest.fixture(scope="session")
def auth_temp_dir():
    """Session-scoped temp directory for auth tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="session")
def auth_app(auth_temp_dir):
    """Create a Flask app with auth enabled for testing (session-scoped).

    Shared across all auth-related test files to avoid Flask blueprint
    re-registration errors (blueprints are global singletons).
    """
    from auth import AuthDatabase, AuthType, User
    from auth.totp import setup_totp

    tmpdir = auth_temp_dir
    main_db_path = Path(tmpdir) / "audiobooks.db"
    auth_db_path = Path(tmpdir) / "auth.db"
    auth_key_path = Path(tmpdir) / "auth.key"

    # Create main database with full schema + test audiobooks
    conn = sqlite3.connect(main_db_path)
    conn.executescript("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            narrator TEXT,
            publisher TEXT,
            series TEXT,
            duration_hours REAL,
            duration_formatted TEXT,
            file_size_mb REAL,
            file_path TEXT UNIQUE NOT NULL,
            cover_path TEXT,
            format TEXT,
            quality TEXT,
            published_year INTEGER,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sha256_hash TEXT,
            hash_verified_at TIMESTAMP,
            author_last_name TEXT,
            author_first_name TEXT,
            narrator_last_name TEXT,
            narrator_first_name TEXT,
            series_sequence REAL,
            edition TEXT,
            asin TEXT,
            published_date TEXT,
            acquired_date TEXT,
            isbn TEXT,
            source TEXT DEFAULT 'test',
            playback_position_ms INTEGER DEFAULT 0,
            playback_position_updated TIMESTAMP,
            audible_position_ms INTEGER,
            audible_position_updated TIMESTAMP,
            position_synced_at TIMESTAMP,
            content_type TEXT DEFAULT 'Product',
            source_asin TEXT
        );
        CREATE TABLE collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE collection_items (
            collection_id INTEGER NOT NULL,
            audiobook_id INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (collection_id, audiobook_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        );
        CREATE TABLE genres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE audiobook_genres (
            audiobook_id INTEGER,
            genre_id INTEGER,
            PRIMARY KEY (audiobook_id, genre_id),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
            FOREIGN KEY (genre_id) REFERENCES genres(id) ON DELETE CASCADE
        );
        CREATE TABLE eras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE audiobook_eras (
            audiobook_id INTEGER,
            era_id INTEGER,
            PRIMARY KEY (audiobook_id, era_id),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
            FOREIGN KEY (era_id) REFERENCES eras(id) ON DELETE CASCADE
        );
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE audiobook_topics (
            audiobook_id INTEGER,
            topic_id INTEGER,
            PRIMARY KEY (audiobook_id, topic_id),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE
        );
        CREATE TABLE supplements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER,
            asin TEXT,
            type TEXT NOT NULL DEFAULT 'pdf',
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size_mb REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE SET NULL
        );
        CREATE TABLE playback_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            position_ms INTEGER NOT NULL,
            source TEXT DEFAULT 'local',
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        );

        -- Test audiobooks (used by auth API and user state tests)
        INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type, created_at)
        VALUES (1, 'The Fellowship of the Ring', 'J.R.R. Tolkien', '/test/fellowship.opus', 'opus', 19.0, 'Product', '2026-01-01 00:00:00');

        INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type, created_at)
        VALUES (2, 'The Two Towers', 'J.R.R. Tolkien', '/test/towers.opus', 'opus', 16.0, 'Product', '2026-01-15 00:00:00');

        INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type, created_at)
        VALUES (3, 'Return of the King', 'J.R.R. Tolkien', '/test/return.opus', 'opus', 14.5, 'Product', '2026-02-01 00:00:00');

        INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type, created_at)
        VALUES (4, 'The Hobbit', 'J.R.R. Tolkien', '/test/hobbit.opus', 'opus', 11.0, 'Product', '2026-02-20 00:00:00');
    """)
    conn.close()

    # Initialize auth database
    auth_db = AuthDatabase(
        db_path=str(auth_db_path), key_path=str(auth_key_path), is_dev=True
    )
    auth_db.initialize()

    # Create test user (regular user, can_download=False for auth tests)
    secret, base32, uri = setup_totp("testuser1")
    user = User(
        username="testuser1",
        auth_type=AuthType.TOTP,
        auth_credential=secret,
        can_download=False,
        is_admin=False,
    )
    user.save(auth_db)

    # Create admin user
    admin_secret, _, _ = setup_totp("adminuser")
    admin = User(
        username="adminuser",
        auth_type=AuthType.TOTP,
        auth_credential=admin_secret,
        can_download=True,
        is_admin=True,
    )
    admin.save(auth_db)

    # Create Flask app
    sys.path.insert(0, str(LIBRARY_DIR / "backend"))
    from api_modular import create_app

    app = create_app(
        database_path=main_db_path,
        project_dir=LIBRARY_DIR.parent,
        supplements_dir=LIBRARY_DIR / "testdata" / "Supplements",
        api_port=6001,
        auth_db_path=auth_db_path,
        auth_key_path=auth_key_path,
        auth_dev_mode=True,
    )
    app.config["AUTH_DEV_MODE"] = True
    app.config["TESTING"] = True

    # Store test data for tests to use
    app.test_user_secret = secret
    app.admin_secret = admin_secret
    app.auth_db = auth_db
    app.test_user_id = user.id
    app.admin_user_id = admin.id

    yield app
