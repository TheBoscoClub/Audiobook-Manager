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

    # Create minimal main database
    import sqlite3
    conn = sqlite3.connect(main_db_path)
    conn.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY)")
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
