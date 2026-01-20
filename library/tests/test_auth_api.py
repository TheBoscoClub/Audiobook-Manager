"""
Unit tests for auth API endpoints.

Tests cover:
- Login/logout flow with TOTP
- Session cookie handling
- Registration flow
- Authentication checks
- Protected endpoint access
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import AuthDatabase, User, AuthType, UserRepository
from auth.totp import TOTPAuthenticator, setup_totp


@pytest.fixture(scope="session")
def auth_temp_dir():
    """Session-scoped temp directory for auth tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="session")
def auth_app(auth_temp_dir):
    """Create a Flask app with auth enabled for testing (session-scoped)."""
    tmpdir = auth_temp_dir

    # Create temp databases
    main_db_path = Path(tmpdir) / "audiobooks.db"
    auth_db_path = Path(tmpdir) / "auth.db"
    auth_key_path = Path(tmpdir) / "auth.key"

    # Create main database with full schema
    import sqlite3
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
        -- Insert test audiobook
        INSERT INTO audiobooks (title, author, file_path, format, content_type)
        VALUES ('Test Audiobook', 'Test Author', '/test/path/book.opus', 'opus', 'Product');
    """)
    conn.close()

    # Initialize auth database
    auth_db = AuthDatabase(
        db_path=str(auth_db_path),
        key_path=str(auth_key_path),
        is_dev=True
    )
    auth_db.initialize()

    # Create test user
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
    app.config['AUTH_DEV_MODE'] = True
    app.config['TESTING'] = True

    # Store test data for tests to use
    app.test_user_secret = secret
    app.admin_secret = admin_secret
    app.auth_db = auth_db

    yield app


@pytest.fixture
def client(auth_app):
    """Create test client."""
    return auth_app.test_client()


class TestAuthCheck:
    """Tests for /auth/check endpoint."""

    def test_check_unauthenticated(self, client):
        """Test check returns false when not logged in."""
        r = client.get('/auth/check')
        assert r.status_code == 200
        data = r.get_json()
        assert data['authenticated'] is False

    def test_check_authenticated(self, client, auth_app):
        """Test check returns true when logged in."""
        # Login first
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        code = auth.current_code()

        client.post('/auth/login',
            json={"username": "testuser1", "code": code})

        r = client.get('/auth/check')
        assert r.status_code == 200
        data = r.get_json()
        assert data['authenticated'] is True
        assert data['username'] == 'testuser1'


class TestLogin:
    """Tests for /auth/login endpoint."""

    def test_login_success(self, client, auth_app):
        """Test successful login with valid TOTP."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        code = auth.current_code()

        r = client.post('/auth/login',
            json={"username": "testuser1", "code": code})

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert data['user']['username'] == 'testuser1'
        assert 'Set-Cookie' in r.headers

    def test_login_wrong_code(self, client):
        """Test login fails with wrong TOTP code."""
        r = client.post('/auth/login',
            json={"username": "testuser1", "code": "000000"})

        assert r.status_code == 401
        data = r.get_json()
        assert 'error' in data

    def test_login_wrong_username(self, client):
        """Test login fails with non-existent user."""
        r = client.post('/auth/login',
            json={"username": "nonexistent", "code": "123456"})

        assert r.status_code == 401
        data = r.get_json()
        assert 'error' in data
        # Should not reveal if user exists
        assert 'Invalid credentials' in data['error']

    def test_login_missing_fields(self, client):
        """Test login fails with missing fields."""
        r = client.post('/auth/login', json={"username": "testuser1"})
        assert r.status_code == 400

        r = client.post('/auth/login', json={"code": "123456"})
        assert r.status_code == 400

    def test_login_no_body(self, client):
        """Test login fails with no request body."""
        r = client.post('/auth/login')
        # 415 Unsupported Media Type when no JSON body
        assert r.status_code in (400, 415)


class TestLogout:
    """Tests for /auth/logout endpoint."""

    def test_logout_success(self, client, auth_app):
        """Test successful logout."""
        # Login first
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        # Logout
        r = client.post('/auth/logout')
        assert r.status_code == 200
        assert r.get_json()['success'] is True

        # Verify logged out
        r = client.get('/auth/check')
        assert r.get_json()['authenticated'] is False

    def test_logout_when_not_logged_in(self, client):
        """Test logout when not logged in (should still succeed)."""
        r = client.post('/auth/logout')
        assert r.status_code == 200
        assert r.get_json()['success'] is True


class TestCurrentUser:
    """Tests for /auth/me endpoint."""

    def test_me_authenticated(self, client, auth_app):
        """Test /auth/me returns user info when logged in."""
        auth = TOTPAuthenticator(auth_app.admin_secret)
        client.post('/auth/login',
            json={"username": "adminuser", "code": auth.current_code()})

        r = client.get('/auth/me')
        assert r.status_code == 200
        data = r.get_json()
        assert data['user']['username'] == 'adminuser'
        assert data['user']['is_admin'] is True
        assert data['user']['can_download'] is True
        assert 'session' in data
        assert 'notifications' in data

    def test_me_unauthenticated(self, client):
        """Test /auth/me returns 401 when not logged in."""
        r = client.get('/auth/me')
        assert r.status_code == 401


class TestRegistration:
    """Tests for registration endpoints."""

    def test_registration_start(self, client):
        """Test starting registration."""
        r = client.post('/auth/register/start',
            json={"username": "newuser12345"})

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert 'verify_token' in data  # Dev mode returns token

    def test_registration_username_validation(self, client):
        """Test username validation during registration."""
        # Too short
        r = client.post('/auth/register/start',
            json={"username": "abc"})
        assert r.status_code == 400
        assert 'at least 5' in r.get_json()['error']

        # Too long
        r = client.post('/auth/register/start',
            json={"username": "a" * 20})
        assert r.status_code == 400
        assert 'at most 16' in r.get_json()['error']

    def test_registration_duplicate_username(self, client):
        """Test registration fails for existing username."""
        r = client.post('/auth/register/start',
            json={"username": "testuser1"})
        assert r.status_code == 400
        assert 'already taken' in r.get_json()['error']

    def test_registration_full_flow(self, client):
        """Test complete registration flow."""
        # Start registration
        r = client.post('/auth/register/start',
            json={"username": "flowuser1"})
        assert r.status_code == 200
        token = r.get_json()['verify_token']

        # Verify and complete
        r = client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert data['username'] == 'flowuser1'
        assert 'totp_secret' in data
        assert 'totp_uri' in data

        # Login with new account
        from auth.totp import base32_to_secret
        secret = base32_to_secret(data['totp_secret'])
        auth = TOTPAuthenticator(secret)

        r = client.post('/auth/login',
            json={"username": "flowuser1", "code": auth.current_code()})
        assert r.status_code == 200
        assert r.get_json()['success'] is True

    def test_registration_invalid_token(self, client):
        """Test verification fails with invalid token."""
        r = client.post('/auth/register/verify',
            json={"token": "invalid_token", "auth_type": "totp"})
        assert r.status_code == 400
        assert 'Invalid' in r.get_json()['error']


class TestSessionManagement:
    """Tests for session management."""

    def test_single_session_enforcement(self, client, auth_app):
        """Test that new login invalidates old session."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)

        # First login
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        # Verify logged in
        r = client.get('/auth/check')
        assert r.get_json()['authenticated'] is True

        # Create second client and login
        client2 = auth_app.test_client()
        import time
        time.sleep(0.1)  # Ensure different TOTP window or same code
        client2.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        # Second client should be logged in
        r = client2.get('/auth/check')
        assert r.get_json()['authenticated'] is True

        # First client should be logged out (session invalidated)
        r = client.get('/auth/check')
        assert r.get_json()['authenticated'] is False


class TestAuthHealth:
    """Tests for /auth/health endpoint."""

    def test_health_check(self, client):
        """Test auth health endpoint."""
        r = client.get('/auth/health')
        assert r.status_code == 200
        data = r.get_json()
        assert data['status'] == 'ok'
        assert data['auth_db'] is True
        assert data['schema_version'] == 2


class TestRegistrationWithRecovery:
    """Tests for registration with recovery options."""

    def test_registration_with_recovery_email(self, client):
        """Test registration stores recovery email when provided."""
        # Start registration
        r = client.post('/auth/register/start',
            json={"username": "recovuser1"})
        token = r.get_json()['verify_token']

        # Verify with recovery email
        r = client.post('/auth/register/verify',
            json={
                "token": token,
                "auth_type": "totp",
                "recovery_email": "test@example.com"
            })

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert data['recovery_enabled'] is True
        assert 'backup_codes' in data
        assert len(data['backup_codes']) == 8

    def test_registration_without_recovery(self, client):
        """Test registration without recovery info gets backup codes only."""
        # Start registration
        r = client.post('/auth/register/start',
            json={"username": "norecov1"})
        token = r.get_json()['verify_token']

        # Verify without recovery info
        r = client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})

        assert r.status_code == 200
        data = r.get_json()
        assert data['recovery_enabled'] is False
        assert 'backup_codes' in data
        assert len(data['backup_codes']) == 8
        assert 'ONLY way to recover' in data['warning']


class TestBackupCodeRecovery:
    """Tests for backup code recovery endpoints."""

    def test_recover_with_valid_backup_code(self, client, auth_app):
        """Test account recovery with valid backup code."""
        # Create a user with backup codes
        r = client.post('/auth/register/start',
            json={"username": "rectest1"})
        token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})
        data = r.get_json()
        backup_codes = data['backup_codes']
        old_secret = data['totp_secret']

        # Recover using backup code
        r = client.post('/auth/recover/backup-code',
            json={
                "username": "rectest1",
                "backup_code": backup_codes[0]
            })

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert 'totp_secret' in data
        assert data['totp_secret'] != old_secret  # New secret generated
        assert 'backup_codes' in data
        assert len(data['backup_codes']) == 8

    def test_recover_with_invalid_backup_code(self, client, auth_app):
        """Test recovery fails with invalid backup code."""
        # Create a user
        r = client.post('/auth/register/start',
            json={"username": "rectest2"})
        token = r.get_json()['verify_token']

        client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})

        # Try invalid code
        r = client.post('/auth/recover/backup-code',
            json={
                "username": "rectest2",
                "backup_code": "XXXX-XXXX-XXXX-XXXX"
            })

        assert r.status_code == 401
        assert 'Invalid' in r.get_json()['error']

    def test_recover_backup_code_single_use(self, client, auth_app):
        """Test backup code can only be used once."""
        # Create a user
        r = client.post('/auth/register/start',
            json={"username": "rectest3"})
        token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})
        backup_codes = r.get_json()['backup_codes']

        # Use backup code for recovery
        r = client.post('/auth/recover/backup-code',
            json={
                "username": "rectest3",
                "backup_code": backup_codes[0]
            })
        assert r.status_code == 200

        # Try to use same code again (should fail)
        r = client.post('/auth/recover/backup-code',
            json={
                "username": "rectest3",
                "backup_code": backup_codes[0]
            })
        assert r.status_code == 401

    def test_recover_wrong_username(self, client):
        """Test recovery fails with non-existent username."""
        r = client.post('/auth/recover/backup-code',
            json={
                "username": "nonexistent",
                "backup_code": "XXXX-XXXX-XXXX-XXXX"
            })

        assert r.status_code == 401
        # Should not reveal if user exists
        assert 'Invalid username or backup code' in r.get_json()['error']


class TestBackupCodeManagement:
    """Tests for backup code management endpoints."""

    def test_get_remaining_codes_authenticated(self, client, auth_app):
        """Test getting remaining backup code count when logged in."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.post('/auth/recover/remaining-codes')
        assert r.status_code == 200
        data = r.get_json()
        assert 'remaining' in data

    def test_get_remaining_codes_unauthenticated(self, client):
        """Test getting remaining codes requires auth."""
        r = client.post('/auth/recover/remaining-codes')
        assert r.status_code == 401

    def test_regenerate_codes_authenticated(self, client, auth_app):
        """Test regenerating backup codes when logged in."""
        # Create and login as new user
        r = client.post('/auth/register/start',
            json={"username": "regenuser"})
        token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})
        old_codes = r.get_json()['backup_codes']
        secret = r.get_json()['totp_secret']

        # Login
        from auth.totp import base32_to_secret
        totp_secret = base32_to_secret(secret)
        auth = TOTPAuthenticator(totp_secret)
        client.post('/auth/login',
            json={"username": "regenuser", "code": auth.current_code()})

        # Regenerate codes
        r = client.post('/auth/recover/regenerate-codes')
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        new_codes = data['backup_codes']
        assert len(new_codes) == 8
        assert new_codes != old_codes

    def test_regenerate_codes_unauthenticated(self, client):
        """Test regenerating codes requires auth."""
        r = client.post('/auth/recover/regenerate-codes')
        assert r.status_code == 401


class TestRecoveryContactManagement:
    """Tests for recovery contact update endpoints."""

    def test_update_recovery_contact(self, client, auth_app):
        """Test updating recovery contact when logged in."""
        # Create user without recovery
        r = client.post('/auth/register/start',
            json={"username": "contactuser"})
        token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={"token": token, "auth_type": "totp"})
        secret = r.get_json()['totp_secret']

        # Login
        from auth.totp import base32_to_secret
        totp_secret = base32_to_secret(secret)
        auth = TOTPAuthenticator(totp_secret)
        client.post('/auth/login',
            json={"username": "contactuser", "code": auth.current_code()})

        # Add recovery email
        r = client.post('/auth/recover/update-contact',
            json={"recovery_email": "new@example.com"})

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert data['recovery_enabled'] is True

    def test_remove_recovery_contact(self, client, auth_app):
        """Test removing recovery contact."""
        # Create user with recovery
        r = client.post('/auth/register/start',
            json={"username": "rmcontact"})
        token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={
                "token": token,
                "auth_type": "totp",
                "recovery_email": "has@example.com"
            })
        secret = r.get_json()['totp_secret']

        # Login
        from auth.totp import base32_to_secret
        totp_secret = base32_to_secret(secret)
        auth = TOTPAuthenticator(totp_secret)
        client.post('/auth/login',
            json={"username": "rmcontact", "code": auth.current_code()})

        # Remove recovery email
        r = client.post('/auth/recover/update-contact',
            json={"recovery_email": None})

        assert r.status_code == 200
        data = r.get_json()
        assert data['recovery_enabled'] is False

    def test_update_contact_unauthenticated(self, client):
        """Test updating contact requires auth."""
        r = client.post('/auth/recover/update-contact',
            json={"recovery_email": "test@example.com"})
        assert r.status_code == 401


# =============================================================================
# Protected Endpoint Tests - Library API
# =============================================================================


class TestProtectedEndpointsUnauthenticated:
    """Test that protected endpoints require authentication when auth is enabled."""

    def test_audiobooks_requires_auth(self, client):
        """Test /api/audiobooks requires authentication."""
        r = client.get('/api/audiobooks')
        assert r.status_code == 401
        assert 'Authentication required' in r.get_json()['error']

    def test_stats_requires_auth(self, client):
        """Test /api/stats requires authentication."""
        r = client.get('/api/stats')
        assert r.status_code == 401

    def test_filters_requires_auth(self, client):
        """Test /api/filters requires authentication."""
        r = client.get('/api/filters')
        assert r.status_code == 401

    def test_collections_requires_auth(self, client):
        """Test /api/collections requires authentication."""
        r = client.get('/api/collections')
        assert r.status_code == 401

    def test_duplicates_requires_auth(self, client):
        """Test /api/duplicates requires authentication."""
        r = client.get('/api/duplicates')
        assert r.status_code == 401

    def test_position_status_requires_auth(self, client):
        """Test /api/position/status requires authentication."""
        r = client.get('/api/position/status')
        assert r.status_code == 401


class TestProtectedEndpointsAuthenticated:
    """Test that authenticated users can access protected endpoints."""

    def test_audiobooks_accessible_when_logged_in(self, client, auth_app):
        """Test /api/audiobooks works when authenticated."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.get('/api/audiobooks')
        assert r.status_code == 200

    def test_stats_accessible_when_logged_in(self, client, auth_app):
        """Test /api/stats works when authenticated."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.get('/api/stats')
        assert r.status_code == 200

    def test_collections_accessible_when_logged_in(self, client, auth_app):
        """Test /api/collections works when authenticated."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.get('/api/collections')
        assert r.status_code == 200


class TestAdminEndpointsNonAdmin:
    """Test that admin endpoints reject non-admin users."""

    def test_delete_requires_admin(self, client, auth_app):
        """Test DELETE /api/audiobooks/<id> requires admin."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.delete('/api/audiobooks/1')
        assert r.status_code == 403
        assert 'Admin privileges required' in r.get_json()['error']

    def test_vacuum_requires_admin(self, client, auth_app):
        """Test POST /api/utilities/vacuum requires admin."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.post('/api/utilities/vacuum')
        assert r.status_code == 403

    def test_delete_duplicates_requires_admin(self, client, auth_app):
        """Test POST /api/duplicates/delete requires admin."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.post('/api/duplicates/delete', json={"ids": []})
        assert r.status_code == 403


class TestAdminEndpointsWithAdmin:
    """Test that admin users can access admin endpoints."""

    def test_vacuum_allowed_for_admin(self, client, auth_app):
        """Test POST /api/utilities/vacuum works for admin."""
        auth = TOTPAuthenticator(auth_app.admin_secret)
        client.post('/auth/login',
            json={"username": "adminuser", "code": auth.current_code()})

        r = client.post('/api/utilities/vacuum')
        # Should succeed (200) or handle gracefully - not 401/403
        assert r.status_code in (200, 500)  # 500 if DB locked, but auth passed


class TestDownloadPermissionEndpoints:
    """Test endpoints that require download permission."""

    def test_stream_allowed_without_download_permission(self, client, auth_app):
        """Test streaming works for any authenticated user (no download permission needed)."""
        # testuser1 has can_download=False but should still be able to stream
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.get('/api/stream/1')
        # Should succeed or 404 (file doesn't exist on disk), but not 403
        assert r.status_code in (200, 404)

    def test_supplement_download_without_permission(self, client, auth_app):
        """Test supplement download requires download permission."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.get('/api/supplements/1/download')
        assert r.status_code == 403

    def test_supplement_download_with_permission(self, client, auth_app):
        """Test supplement download works with download permission."""
        # adminuser has can_download=True
        auth = TOTPAuthenticator(auth_app.admin_secret)
        client.post('/auth/login',
            json={"username": "adminuser", "code": auth.current_code()})

        r = client.get('/api/supplements/1/download')
        # Should succeed or 404 (supplement doesn't exist), but not 403
        assert r.status_code in (200, 404)

    def test_audiobook_download_without_permission(self, client, auth_app):
        """Test audiobook download requires download permission."""
        # testuser1 has can_download=False
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.get('/api/download/1')
        assert r.status_code == 403
        assert 'Download permission required' in r.get_json()['error']

    def test_audiobook_download_with_permission(self, client, auth_app):
        """Test audiobook download works with download permission."""
        # adminuser has can_download=True
        auth = TOTPAuthenticator(auth_app.admin_secret)
        client.post('/auth/login',
            json={"username": "adminuser", "code": auth.current_code()})

        r = client.get('/api/download/1')
        # Should succeed or 404 (file doesn't exist on disk), but not 403
        assert r.status_code in (200, 404)


class TestAudibleSyncAdminOnly:
    """Test that Audible sync endpoints are admin-only."""

    def test_position_sync_requires_admin(self, client, auth_app):
        """Test POST /api/position/sync/<id> requires admin."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.post('/api/position/sync/1')
        assert r.status_code == 403

    def test_position_sync_all_requires_admin(self, client, auth_app):
        """Test POST /api/position/sync-all requires admin."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.post('/api/position/sync-all')
        assert r.status_code == 403

    def test_download_audiobooks_requires_admin(self, client, auth_app):
        """Test POST /api/utilities/download-audiobooks-async requires admin."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.post('/api/utilities/download-audiobooks-async')
        assert r.status_code == 403


class TestPerUserPositionTracking:
    """Test per-user position tracking when auth is enabled."""

    def test_get_position_returns_user_position(self, client, auth_app):
        """Test GET /api/position/<id> returns user's personal position."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        # Getting position should work (returns 0 or saved position)
        r = client.get('/api/position/1')
        # Should succeed or 404 if audiobook doesn't exist
        assert r.status_code in (200, 404)

    def test_update_position_saves_per_user(self, client, auth_app):
        """Test PUT /api/position/<id> saves to user's personal position."""
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        client.post('/auth/login',
            json={"username": "testuser1", "code": auth.current_code()})

        r = client.put('/api/position/1',
            json={"position_ms": 120000})
        # Should succeed or 404 if audiobook doesn't exist
        assert r.status_code in (200, 404)


# =============================================================================
# Magic Link Recovery Tests
# =============================================================================


class TestMagicLinkRequest:
    """Tests for /auth/magic-link endpoint."""

    def test_magic_link_request_missing_username(self, client):
        """Test magic link request fails with missing username."""
        r = client.post('/auth/magic-link', json={"username": ""})
        assert r.status_code == 400
        assert 'Username is required' in r.get_json()['error']

    def test_magic_link_request_nonexistent_user(self, client):
        """Test magic link request for nonexistent user returns success (privacy)."""
        r = client.post('/auth/magic-link',
            json={"username": "nonexistent_user_12345"})

        # Should return success to prevent username enumeration
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert 'If an account exists' in data['message']

    def test_magic_link_request_user_without_recovery_email(self, client, auth_app):
        """Test magic link for user without recovery email returns success (privacy)."""
        # testuser1 was created without recovery email
        r = client.post('/auth/magic-link',
            json={"username": "testuser1"})

        # Should return success to prevent revealing whether user has recovery
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True

    def test_magic_link_request_user_with_recovery_email(self, client, auth_app):
        """Test magic link request for user with recovery email."""
        # Create a user with recovery email
        r = client.post('/auth/register/start',
            json={"username": "magicuser1"})
        token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={
                "token": token,
                "auth_type": "totp",
                "recovery_email": "magic@example.com"
            })
        assert r.status_code == 200

        # Request magic link
        r = client.post('/auth/magic-link',
            json={"username": "magicuser1"})

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        # Email won't actually send in test (no SMTP), but endpoint succeeds


class TestMagicLinkVerify:
    """Tests for /auth/magic-link/verify endpoint."""

    def test_magic_link_verify_missing_token(self, client):
        """Test magic link verify fails with missing token."""
        r = client.post('/auth/magic-link/verify', json={"token": ""})
        assert r.status_code == 400
        assert 'Token is required' in r.get_json()['error']

    def test_magic_link_verify_invalid_token(self, client):
        """Test magic link verify fails with invalid token."""
        r = client.post('/auth/magic-link/verify',
            json={"token": "invalid_token_12345"})
        assert r.status_code == 400
        assert 'Invalid or expired' in r.get_json()['error']

    def test_magic_link_verify_creates_session(self, client, auth_app):
        """Test successful magic link verification creates a session."""
        # Create user with recovery email
        r = client.post('/auth/register/start',
            json={"username": "verifuser1"})
        verify_token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={
                "token": verify_token,
                "auth_type": "totp",
                "recovery_email": "verify@example.com"
            })
        assert r.status_code == 200

        # Manually create a recovery token through the database
        from auth import PendingRecovery, PendingRecoveryRepository, UserRepository
        db = auth_app.auth_db
        user_repo = UserRepository(db)
        user = user_repo.get_by_username("verifuser1")

        recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=15)

        # Verify with the token
        r = client.post('/auth/magic-link/verify',
            json={"token": raw_token})

        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert data['username'] == 'verifuser1'
        assert 'Set-Cookie' in r.headers

        # Should be logged in now
        r = client.get('/auth/check')
        data = r.get_json()
        assert data['authenticated'] is True
        assert data['username'] == 'verifuser1'

    def test_magic_link_token_single_use(self, client, auth_app):
        """Test magic link token can only be used once."""
        # Create user with recovery email
        r = client.post('/auth/register/start',
            json={"username": "singleuse1"})
        verify_token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={
                "token": verify_token,
                "auth_type": "totp",
                "recovery_email": "single@example.com"
            })
        assert r.status_code == 200

        # Create recovery token
        from auth import PendingRecovery, UserRepository
        db = auth_app.auth_db
        user_repo = UserRepository(db)
        user = user_repo.get_by_username("singleuse1")

        recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=15)

        # First use - should succeed
        r = client.post('/auth/magic-link/verify',
            json={"token": raw_token})
        assert r.status_code == 200

        # Second use - should fail
        r = client.post('/auth/magic-link/verify',
            json={"token": raw_token})
        assert r.status_code == 400
        assert 'already been used' in r.get_json()['error']

    def test_magic_link_expired_token(self, client, auth_app):
        """Test magic link verify fails with expired token."""
        # Create user with recovery email
        r = client.post('/auth/register/start',
            json={"username": "expireuser"})
        verify_token = r.get_json()['verify_token']

        r = client.post('/auth/register/verify',
            json={
                "token": verify_token,
                "auth_type": "totp",
                "recovery_email": "expire@example.com"
            })
        assert r.status_code == 200

        # Create expired recovery token (0 minutes = immediate expiry)
        from auth import PendingRecovery, UserRepository
        from datetime import datetime, timedelta
        db = auth_app.auth_db
        user_repo = UserRepository(db)
        user = user_repo.get_by_username("expireuser")

        # Create token and manually set it as expired
        recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=0)

        # Verify with expired token
        r = client.post('/auth/magic-link/verify',
            json={"token": raw_token})
        assert r.status_code == 400
        assert 'expired' in r.get_json()['error'].lower()
