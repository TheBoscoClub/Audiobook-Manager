"""
Pytest configuration and shared fixtures for Audiobooks Library tests.
"""

import encodings.idna  # noqa: F401 — force-load idna codec for Python 3.14 + werkzeug
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

HARDWARE_TOUCH_TIMEOUT = 90  # total seconds for up to 3 hardware touch attempts
HARDWARE_TOUCH_MAX_ATTEMPTS = 3  # max touch opportunities within the timeout


def pytest_addoption(parser):
    parser.addoption(
        "--fido2",
        action="store_true",
        default=False,
        help=(
            "Run FIDO2 auth tests with a physical hardware key (e.g., YubiKey). "
            "If omitted, FIDO2 tests run automatically with a software "
            "authenticator — no prompt, no hardware needed."
        ),
    )
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help=(
            "Run tests that require non-FIDO2 physical hardware. "
            "FIDO2 auth tests are controlled exclusively by --fido2."
        ),
    )
    parser.addoption(
        "--vm",
        action="store_true",
        default=False,
        help=(
            "Run integration tests that require the test VM "
            "(test-audiobook-cachyos). Does NOT include FIDO2 auth tests."
        ),
    )
    parser.addoption(
        "--docker",
        action="store_true",
        default=False,
        help="Run Docker container tests (require Docker daemon running)",
    )


def pytest_configure(config):
    """Early configuration - runs before test collection."""
    import os

    # Register custom markers to suppress PytestUnknownMarkWarning
    config.addinivalue_line("markers", "integration: tests requiring the test VM")
    config.addinivalue_line("markers", "fido2: tests requiring a FIDO2 hardware key")
    config.addinivalue_line("markers", "hardware: tests requiring non-FIDO2 hardware")
    config.addinivalue_line("markers", "docker: tests requiring Docker daemon")
    config.addinivalue_line(
        "markers", "v8: tests for v8 features (auto-skipped when VERSION major < 8)"
    )

    # Set VM_TESTS env var early so modules can check it at import time
    if config.getoption("--vm", default=False):
        os.environ["VM_TESTS"] = "1"

    # FIDO2 mode: --fido2 means hardware key, omitted means software.
    # No prompt — software is the automatic default.
    if not config.getoption("--fido2", default=False):
        os.environ["FIDO2_SOFTWARE"] = "1"


def _get_project_major_version():
    """Read major version from VERSION file at project root."""
    version_file = Path(__file__).resolve().parent.parent.parent / "VERSION"
    try:
        version_str = version_file.read_text().strip()
        return int(version_str.split(".")[0])
    except (FileNotFoundError, ValueError, IndexError):
        return 0


def _apply_skip_marker(items, keyword: str, reason: str):
    """Add a skip marker to all items matching a keyword."""
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        if keyword in item.keywords:
            item.add_marker(marker)


def pytest_collection_modifyitems(config, items):
    # Version-gated markers: auto-skip @pytest.mark.v8 tests when major < 8
    major = _get_project_major_version()
    if major < 8:
        _apply_skip_marker(items, "v8", f"v8 feature (current version major={major})")

    # Flag-gated markers: skip tests unless corresponding CLI flag is given
    flag_gates = {
        "--hardware": ("hardware", "needs --hardware flag to run (non-FIDO2 hardware)"),
        "--vm": ("integration", "needs --vm flag to run (test VM)"),
        "--docker": ("docker", "needs --docker flag to run (Docker daemon)"),
    }
    for flag, (keyword, reason) in flag_gates.items():
        if not config.getoption(flag):
            _apply_skip_marker(items, keyword, reason)


HARDWARE_SKIP_MSG = (
    "hardware authentication skipped; "
    "user not present or did not respond to the prompt."
)


def hardware_touch_attempt(fido2_callable, *args, **kwargs):
    """Call a FIDO2 operation with up to 3 touch attempts within 90 seconds.

    FIDO2 hardware keys (e.g., YubiKey) have a built-in touch timeout,
    typically ~30 seconds.  This wrapper gives the user up to 3 attempts
    to touch the key, constrained by a 90-second overall deadline.

    If the user touches the key on any attempt and no other hardware
    errors occur, the result is returned immediately (test passes).
    If all 3 attempts expire or 90 seconds elapse without a successful
    touch, the test is skipped and remaining tests continue.

    Args:
        fido2_callable: A function that triggers a FIDO2 touch
            (e.g., ``client.make_credential`` or ``client.get_assertion``).
        *args, **kwargs: Forwarded to *fido2_callable*.

    Returns:
        The result of the FIDO2 operation on success.

    Raises:
        pytest.skip: If the user never touches the key within the budget.
        Exception: Any non-timeout FIDO2/CTAP error is re-raised immediately.
    """
    from fido2.client import ClientError
    from fido2.ctap import CtapError

    deadline = time.monotonic() + HARDWARE_TOUCH_TIMEOUT

    for attempt in range(1, HARDWARE_TOUCH_MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        print(
            f"\n  >>> Attempt {attempt}/{HARDWARE_TOUCH_MAX_ATTEMPTS}: "
            f"Touch your hardware key now "
            f"({int(remaining)}s remaining)... <<<"
        )

        try:
            return fido2_callable(*args, **kwargs)
        except ClientError as exc:
            if exc.code == ClientError.ERR.TIMEOUT:
                continue
            raise
        except CtapError as exc:
            if exc.code in (
                CtapError.ERR.USER_ACTION_TIMEOUT,
                CtapError.ERR.ACTION_TIMEOUT,
                CtapError.ERR.KEEPALIVE_CANCEL,
            ):
                continue
            raise
        except OSError:
            # Device communication failure (USB disconnect, etc.)
            continue

    pytest.skip(HARDWARE_SKIP_MSG)


# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

# Project root (two levels up from library/tests/)
PROJECT_ROOT = LIBRARY_DIR.parent

# Path to the database schema
SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"

# VM connection details — override via environment for your own test VM
# Example: VM_HOST=10.0.0.50 VM_NAME=my-test-vm pytest ...
VM_HOST = os.environ.get("VM_HOST", "192.168.122.104")
VM_API_PORT = int(os.environ.get("VM_API_PORT", "5001"))
VM_NAME = os.environ.get("VM_NAME", "test-audiobook-cachyos")


@pytest.fixture(scope="session", autouse=False)
def ensure_vm_running():
    """Start the test VM if it's powered off.

    Checks VM state via virsh and starts it if needed, then waits
    for SSH connectivity before allowing tests to proceed.
    """
    try:
        result = subprocess.run(
            ["sudo", "virsh", "domstate", VM_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("virsh not available or timed out")
        return

    if result.returncode != 0:
        pytest.skip(f"{VM_NAME} not found in libvirt")
        return

    state = result.stdout.strip()

    if state == "running":
        return

    # Start the VM
    start_result = subprocess.run(
        ["sudo", "virsh", "start", VM_NAME],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if start_result.returncode != 0:
        pytest.fail(f"Failed to start VM: {start_result.stderr}")

    # Wait for SSH connectivity (up to 60s)
    ssh_key = os.path.expanduser("~/.ssh/id_ed25519")
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
    """Deploy latest code to test-audiobook-cachyos via upgrade.sh --remote.

    Runs the full upgrade lifecycle (stop, backup, sync, venv, restart, validate).
    Skip with SKIP_VM_DEPLOY=1 for rapid iteration when code is already deployed.
    Depends on ensure_vm_running to guarantee VM is up first.
    """
    if os.environ.get("SKIP_VM_DEPLOY", "").strip() == "1":
        return

    upgrade_script = PROJECT_ROOT / "upgrade.sh"
    if not upgrade_script.exists():
        pytest.skip("upgrade.sh not found at project root")

    result = subprocess.run(
        [
            str(upgrade_script),
            "--from-project",
            str(PROJECT_ROOT),
            "--remote",
            VM_HOST,
            "--yes",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        pytest.fail(f"upgrade.sh --remote failed:\n{result.stderr}\n{result.stdout}")


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

        -- Normalized author/narrator tables
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            sort_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS narrators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            sort_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS book_authors (
            book_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (book_id, author_id),
            FOREIGN KEY (book_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
            FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS book_narrators (
            book_id INTEGER NOT NULL,
            narrator_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (book_id, narrator_id),
            FOREIGN KEY (book_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
            FOREIGN KEY (narrator_id) REFERENCES narrators(id) ON DELETE CASCADE
        );

        -- Test audiobooks (used by auth API and user state tests)
        INSERT INTO audiobooks
            (id, title, author, file_path, format, duration_hours,
             content_type, created_at)
        VALUES (1, 'The Fellowship of the Ring', 'J.R.R. Tolkien',
            '/test/fellowship.opus', 'opus', 19.0,
            'Product', '2026-01-01 00:00:00');

        INSERT INTO audiobooks
            (id, title, author, file_path, format, duration_hours,
             content_type, created_at)
        VALUES (2, 'The Two Towers', 'J.R.R. Tolkien',
            '/test/towers.opus', 'opus', 16.0,
            'Product', '2026-01-15 00:00:00');

        INSERT INTO audiobooks
            (id, title, author, file_path, format, duration_hours,
             content_type, created_at)
        VALUES (3, 'Return of the King', 'J.R.R. Tolkien',
            '/test/return.opus', 'opus', 14.5,
            'Product', '2026-02-01 00:00:00');

        INSERT INTO audiobooks
            (id, title, author, file_path, format, duration_hours,
             content_type, created_at)
        VALUES (4, 'The Hobbit', 'J.R.R. Tolkien',
            '/test/hobbit.opus', 'opus', 11.0,
            'Product', '2026-02-20 00:00:00');

        -- Normalized author data for test audiobooks
        INSERT INTO authors (id, name, sort_name)
            VALUES (1, 'J.R.R. Tolkien', 'Tolkien, J.R.R.');
        INSERT INTO book_authors (book_id, author_id, position) VALUES (1, 1, 0);
        INSERT INTO book_authors (book_id, author_id, position) VALUES (2, 1, 0);
        INSERT INTO book_authors (book_id, author_id, position) VALUES (3, 1, 0);
        INSERT INTO book_authors (book_id, author_id, position) VALUES (4, 1, 0);
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


@pytest.fixture(scope="session")
def auth_db(auth_app):
    """Expose the AuthDatabase instance from the session-scoped auth app.

    Used by tests that need direct database access without going through
    the HTTP layer (e.g., repository unit tests).
    """
    return auth_app.auth_db


# ============================================================
# Auth client fixtures for endpoint testing
# ============================================================
# The auth system uses a cookie-based session token
# ("audiobooks_session") that maps to a hashed token in the
# sessions table — NOT Flask's built-in session dict.
#
# To produce an authenticated test client we:
#   1. Create a User in the auth DB
#   2. Create a Session via Session.create() → get the raw token
#   3. Set the raw token as the audiobooks_session cookie
#
# All fixtures are function-scoped (default) so each test gets
# a fresh user + session.  Usernames are suffixed with "_fix" to
# avoid collisions with session-scoped seed data ("testuser1",
# "adminuser") created in the auth_app fixture.
# ============================================================


def _make_session_cookie(auth_db_instance, user_id: int) -> str:
    """Create a Session record and return the raw token for use as a cookie."""
    from auth.models import Session

    _session, raw_token = Session.create_for_user(
        db=auth_db_instance,
        user_id=user_id,
        user_agent="pytest",
        ip_address="127.0.0.1",
    )
    return raw_token


@pytest.fixture
def admin_client(auth_app, auth_db):
    """Test client authenticated as an admin user."""
    from auth.models import User, AuthType, UserRepository

    user_repo = UserRepository(auth_db)
    admin = user_repo.get_by_username("testadmin_fix")
    if admin is None:
        admin = User(
            username="testadmin_fix",
            auth_type=AuthType.TOTP,
            auth_credential=b"testsecret",
            is_admin=True,
            can_download=True,
        ).save(auth_db)
    raw_token = _make_session_cookie(auth_db, admin.id)
    client = auth_app.test_client()
    client.set_cookie("audiobooks_session", raw_token)
    client._test_admin = admin  # Store ref for tests that need it
    return client


@pytest.fixture
def test_user(auth_db):
    """A regular (non-admin) TOTP user."""
    from auth.models import User, AuthType, UserRepository

    user_repo = UserRepository(auth_db)
    existing = user_repo.get_by_username("regularuser_fix")
    if existing is not None:
        return existing
    return User(
        username="regularuser_fix",
        auth_type=AuthType.TOTP,
        auth_credential=b"testsecret",
        is_admin=False,
        can_download=True,
    ).save(auth_db)


@pytest.fixture
def user_client(auth_app, auth_db, test_user):
    """Test client authenticated as a regular (non-admin) user."""
    raw_token = _make_session_cookie(auth_db, test_user.id)
    client = auth_app.test_client()
    client.set_cookie("audiobooks_session", raw_token)
    return client


@pytest.fixture
def anon_client(auth_app):
    """Test client with no session (unauthenticated)."""
    return auth_app.test_client()


@pytest.fixture
def sole_admin(auth_db):
    """An admin user for sole-admin guard tests.

    Note: the session-scoped auth_db already contains "adminuser",
    so tests relying on this being the *only* admin must either delete
    that seed user first or query the real admin count.
    """
    from auth.models import User, AuthType

    return User(
        username="soleadmin_fix",
        auth_type=AuthType.TOTP,
        auth_credential=b"testsecret",
        is_admin=True,
        can_download=True,
    ).save(auth_db)


@pytest.fixture
def sole_admin_client(auth_app, auth_db, sole_admin):
    """Test client authenticated as the sole-admin fixture user."""
    raw_token = _make_session_cookie(auth_db, sole_admin.id)
    client = auth_app.test_client()
    client.set_cookie("audiobooks_session", raw_token)
    return client


@pytest.fixture
def test_magic_link_user(auth_db):
    """A Magic Link user with a recovery email."""
    from auth.models import User, AuthType, UserRepository

    user = User(
        username="mluser_fix",
        auth_type=AuthType.MAGIC_LINK,
        auth_credential=b"",
    ).save(auth_db)
    UserRepository(auth_db).update_email(user.id, "ml@test.com")
    return UserRepository(auth_db).get_by_id(user.id)


@pytest.fixture
def magic_link_user_client(auth_app, auth_db, test_magic_link_user):
    """Test client authenticated as a magic link user."""
    raw_token = _make_session_cookie(auth_db, test_magic_link_user.id)
    client = auth_app.test_client()
    client.set_cookie("audiobooks_session", raw_token)
    return client


@pytest.fixture
def logged_in_user(auth_db):
    """A user whose last_login is set (not NULL)."""
    from auth.models import User, AuthType
    from datetime import datetime

    return User(
        username="loggedinuser_fix",
        auth_type=AuthType.TOTP,
        auth_credential=b"testsecret",
        is_admin=False,
        can_download=True,
        last_login=datetime.now(),
    ).save(auth_db)
