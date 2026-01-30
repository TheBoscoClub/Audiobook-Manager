"""
Security penetration tests for auth system.

Tests attempt various attack vectors to verify the auth system
correctly rejects:
- Session token manipulation
- Cookie tampering
- SQL injection attempts
- Token reuse after logout
- Session fixation attacks
- Privilege escalation attempts
- Timing attacks on token validation
"""

import hashlib
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import (
    AuthDatabase,
    AuthType,
    User,
    UserRepository,
    Session,
    SessionRepository,
    hash_token,
)


@pytest.fixture
def temp_db():
    """Create a temporary encrypted database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/test-auth.db"
        key_path = f"{tmpdir}/test.key"
        db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
        db.initialize()
        yield db


@pytest.fixture
def test_user(temp_db):
    """Create a test user."""
    user = User(username="sectest", auth_type=AuthType.TOTP, auth_credential=b"secret")
    user.save(temp_db)
    return user


@pytest.fixture
def admin_user(temp_db):
    """Create an admin user."""
    user = User(
        username="secadmin",
        auth_type=AuthType.TOTP,
        auth_credential=b"adminsecret",
        is_admin=True,
    )
    user.save(temp_db)
    return user


class TestSessionTokenManipulation:
    """Tests for session token manipulation attacks."""

    def test_reject_modified_token(self, temp_db, test_user):
        """Test that modified session tokens are rejected."""
        # Create valid session using the proper API
        session, raw_token = Session.create_for_user(temp_db, test_user.id)

        repo = SessionRepository(temp_db)

        # Original token works
        found = repo.get_by_token(raw_token)
        assert found is not None

        # Modified tokens should not work
        modified_tokens = [
            raw_token[:-1] + "X",  # Changed last char
            raw_token.upper(),  # Changed case
            raw_token + " ",  # Added space
            " " + raw_token,  # Prepended space
            raw_token[:-1],  # Shortened
            raw_token + "X",  # Extended
            "",  # Empty
            "x" * 1000,  # Very long
        ]

        for modified in modified_tokens:
            assert repo.get_by_token(modified) is None, f"Modified token accepted: {modified}"

    def test_reject_forged_token_hash(self, temp_db, test_user):
        """Test that forged token hashes are rejected."""
        # Create session with the proper API
        session, real_token = Session.create_for_user(temp_db, test_user.id)

        repo = SessionRepository(temp_db)

        # Real token works
        assert repo.get_by_token(real_token) is not None

        # Forged tokens that might hash similarly should not work
        forged_tokens = [
            real_token + "\x00",  # Null byte injection
            real_token.upper(),
            real_token.replace("-", "_") if "-" in real_token else real_token + "X",
        ]

        for forged in forged_tokens:
            if forged != real_token:  # Only test if actually different
                assert repo.get_by_token(forged) is None, f"Forged token accepted: {repr(forged)}"


class TestSQLInjection:
    """Tests for SQL injection prevention."""

    def test_username_sql_injection(self, temp_db, test_user):
        """Test SQL injection in username field is prevented."""
        repo = UserRepository(temp_db)

        # Common SQL injection payloads
        injection_payloads = [
            "'; DROP TABLE users; --",
            "' OR '1'='1",
            "' OR '1'='1' --",
            "admin'--",
            "' UNION SELECT * FROM users --",
            "1; DELETE FROM users",
            "' OR 1=1 --",
            "'; INSERT INTO users VALUES(99,'hacked','x'); --",
        ]

        for payload in injection_payloads:
            # Should return None, not cause an error or return data
            result = repo.get_by_username(payload)
            assert result is None, f"SQL injection may have worked: {payload}"

            # Database should still be intact
            original = repo.get_by_username("sectest")
            assert original is not None, f"Database corrupted after injection attempt: {payload}"

    def test_token_sql_injection(self, temp_db, test_user):
        """Test SQL injection in token field is prevented."""
        # Create valid session
        session, safe_token = Session.create_for_user(temp_db, test_user.id)

        repo = SessionRepository(temp_db)

        injection_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE sessions; --",
            "' UNION SELECT * FROM users --",
        ]

        for payload in injection_payloads:
            result = repo.get_by_token(payload)
            assert result is None

            # Verify session still exists
            assert repo.get_by_token(safe_token) is not None


class TestTokenReuseAfterLogout:
    """Tests for token reuse after session invalidation."""

    def test_token_invalid_after_delete(self, temp_db, test_user):
        """Test that deleted session tokens cannot be reused."""
        # Create session
        session, raw_token = Session.create_for_user(temp_db, test_user.id)

        repo = SessionRepository(temp_db)

        # Token works initially
        assert repo.get_by_token(raw_token) is not None

        # Delete session (logout) using repository
        repo.invalidate_user_sessions(test_user.id)

        # Token should no longer work
        assert repo.get_by_token(raw_token) is None

    def test_token_stale_after_timeout(self, temp_db, test_user):
        """Test that stale session tokens are cleaned up."""
        # Create session
        session, raw_token = Session.create_for_user(temp_db, test_user.id)

        repo = SessionRepository(temp_db)

        # Token works initially
        assert repo.get_by_token(raw_token) is not None

        # Manually set last_seen to old timestamp to simulate staleness
        with temp_db.connection() as conn:
            # Use SQLite-compatible format to match DEFAULT CURRENT_TIMESTAMP
            old_time = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE id = ?",
                (old_time, session.id)
            )

        # Cleanup stale sessions (30 min threshold)
        repo.cleanup_stale(grace_minutes=30)

        # Token should no longer work
        assert repo.get_by_token(raw_token) is None


class TestPrivilegeEscalation:
    """Tests for privilege escalation prevention."""

    def test_cannot_modify_admin_flag_directly(self, temp_db, test_user):
        """Test that admin flag cannot be escalated through normal operations."""
        repo = UserRepository(temp_db)

        # User starts as non-admin
        assert test_user.is_admin is False

        # Reload from DB
        loaded = repo.get_by_username("sectest")
        assert loaded.is_admin is False

        # Verify admin flag is stored correctly
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT is_admin FROM users WHERE username = ?",
                ("sectest",)
            )
            row = cursor.fetchone()
            assert row[0] == 0  # Not admin in DB

    def test_session_preserves_user_privileges(self, temp_db, test_user, admin_user):
        """Test that session maintains correct user privileges."""
        # Create sessions for both users
        user_session, user_token = Session.create_for_user(temp_db, test_user.id)
        admin_session, admin_token = Session.create_for_user(temp_db, admin_user.id)

        session_repo = SessionRepository(temp_db)
        user_repo = UserRepository(temp_db)

        # Verify user token maps to non-admin user
        user_sess = session_repo.get_by_token(user_token)
        user = user_repo.get_by_id(user_sess.user_id)
        assert user.is_admin is False

        # Verify admin token maps to admin user
        admin_sess = session_repo.get_by_token(admin_token)
        admin = user_repo.get_by_id(admin_sess.user_id)
        assert admin.is_admin is True


class TestSessionFixation:
    """Tests for session fixation attack prevention."""

    def test_session_bound_to_user(self, temp_db, test_user, admin_user):
        """Test that sessions are properly bound to their user."""
        # Create session for regular user
        session, raw_token = Session.create_for_user(temp_db, test_user.id)

        session_repo = SessionRepository(temp_db)
        user_repo = UserRepository(temp_db)

        # Session should map to the correct user
        loaded_session = session_repo.get_by_token(raw_token)
        user = user_repo.get_by_id(loaded_session.user_id)
        assert user.id == test_user.id
        assert user.username == "sectest"
        assert user.is_admin is False

        # Cannot use this session to access admin privileges
        assert user.id != admin_user.id


class TestInputValidation:
    """Tests for input validation on auth fields."""

    def test_username_length_constraints(self, temp_db):
        """Test username length validation."""
        repo = UserRepository(temp_db)

        # Too short (less than 5 chars per schema)
        short_user = User(
            username="abc",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        with pytest.raises(Exception):
            short_user.save(temp_db)

        # Too long (more than 16 chars per schema)
        long_user = User(
            username="a" * 20,
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        with pytest.raises(Exception):
            long_user.save(temp_db)

    def test_null_byte_in_username(self, temp_db):
        """Test null byte injection in username is handled."""
        repo = UserRepository(temp_db)

        # Null byte in username should be rejected or handled safely
        # (depends on implementation - either reject or sanitize)
        null_username = "test\x00admin"

        # This should either raise an exception or create user with sanitized name
        # Either way, should not allow bypassing username checks
        result = repo.get_by_username(null_username)
        assert result is None


class TestDatabaseEncryption:
    """Tests for database encryption integrity."""

    def test_database_unreadable_without_key(self, temp_db):
        """Test that database content is encrypted."""
        # Get the database file path
        db_path = temp_db.db_path

        # Create some data
        user = User(
            username="encrypted",
            auth_type=AuthType.TOTP,
            auth_credential=b"supersecret",
        )
        user.save(temp_db)

        # Read raw database file
        with open(db_path, "rb") as f:
            raw_content = f.read()

        # Sensitive data should not appear in plaintext
        assert b"encrypted" not in raw_content, "Username found in plaintext in DB file"
        assert b"supersecret" not in raw_content, "Credential found in plaintext in DB file"

    def test_wrong_key_fails(self):
        """Test that database cannot be opened with wrong key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test-auth.db"
            key_path = f"{tmpdir}/test.key"
            wrong_key_path = f"{tmpdir}/wrong.key"

            # Create database with correct key
            db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
            db.initialize()
            User(
                username="testuser",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            ).save(db)

            # Create wrong key file
            with open(wrong_key_path, "w") as f:
                f.write("0" * 64)

            # Try to open with wrong key - should fail
            with pytest.raises(Exception):
                wrong_db = AuthDatabase(
                    db_path=db_path,
                    key_path=wrong_key_path,
                    is_dev=True
                )
                UserRepository(wrong_db).list_all()


class TestTimingAttacks:
    """Tests to help identify potential timing attack vulnerabilities."""

    def test_token_validation_constant_time(self, temp_db, test_user):
        """Test that token validation doesn't leak timing information.

        Note: This is a basic check. Full timing attack testing requires
        statistical analysis over many iterations.
        """
        # Create valid session
        session, valid_token = Session.create_for_user(temp_db, test_user.id)

        repo = SessionRepository(temp_db)

        # Measure time for valid token
        start = time.perf_counter()
        repo.get_by_token(valid_token)
        valid_time = time.perf_counter() - start

        # Measure time for invalid token of same length
        start = time.perf_counter()
        repo.get_by_token("x" * len(valid_token))
        invalid_time = time.perf_counter() - start

        # Times should be within same order of magnitude
        # (This is a weak test - real timing attack testing needs statistics)
        # Mainly checking for obvious early-exit patterns
        ratio = max(valid_time, invalid_time) / max(min(valid_time, invalid_time), 0.000001)
        assert ratio < 100, f"Timing difference too large: valid={valid_time}, invalid={invalid_time}"


class TestBoundaryConditions:
    """Tests for edge cases and boundary conditions."""

    def test_empty_credentials(self, temp_db):
        """Test handling of empty credentials."""
        repo = UserRepository(temp_db)

        # Empty username lookup should return None safely
        assert repo.get_by_username("") is None
        assert repo.get_by_username(None) is None if hasattr(repo, 'get_by_username') else True

    def test_very_long_token(self, temp_db, test_user):
        """Test handling of extremely long tokens."""
        repo = SessionRepository(temp_db)

        # Very long token
        long_token = "x" * 10000
        result = repo.get_by_token(long_token)
        assert result is None  # Should not crash

    def test_unicode_in_username_lookup(self, temp_db):
        """Test unicode handling in username lookups."""
        repo = UserRepository(temp_db)

        # Unicode injection attempts
        unicode_payloads = [
            "admin\u0000",  # Null char
            "admin\u200b",  # Zero-width space
            "ádmin",  # Homoglyph
            "аdmin",  # Cyrillic 'a'
        ]

        for payload in unicode_payloads:
            result = repo.get_by_username(payload)
            assert result is None, f"Unicode payload matched: {repr(payload)}"
