"""
Unit tests for the auth module (SQLCipher encrypted database).

Tests cover:
- Database encryption and key management
- User CRUD operations
- Session management (single-session enforcement)
- Position tracking
- Notifications and dismissals
- Inbox messages
- Pending registrations
"""

import os
import sys
import tempfile
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
    UserPosition,
    PositionRepository,
    Notification,
    NotificationType,
    NotificationRepository,
    InboxMessage,
    InboxRepository,
    ReplyMethod,
    InboxStatus,
    PendingRegistration,
    PendingRegistrationRepository,
    hash_token,
    generate_session_token,
    # Backup codes
    BackupCode,
    BackupCodeRepository,
    generate_backup_code,
    generate_backup_codes,
    hash_backup_code,
    normalize_backup_code,
    format_codes_for_display,
    NUM_BACKUP_CODES,
)


@pytest.fixture
def temp_db():
    """Create a temporary encrypted database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test-auth.db')
        key_path = os.path.join(tmpdir, 'test.key')
        db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
        db.initialize()
        yield db


class TestAuthDatabase:
    """Tests for AuthDatabase class."""

    def test_database_creation(self, temp_db):
        """Test database is created with correct schema."""
        status = temp_db.verify()
        assert status['db_exists']
        assert status['key_exists']
        assert status['can_connect']
        assert status['schema_version'] == 2
        assert status['table_count'] == 12  # Including schema_version, backup_codes, pending_recovery

    def test_database_encryption(self):
        """Test database file is actually encrypted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'encrypted.db')
            key_path = os.path.join(tmpdir, 'test.key')

            db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
            db.initialize()

            # Insert test data
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO users (username, auth_type, auth_credential) VALUES (?, ?, ?)",
                    ('testuser', 'totp', b'secret')
                )

            # Read raw file - should NOT contain plain SQLite header
            with open(db_path, 'rb') as f:
                header = f.read(16)

            assert not header.startswith(b'SQLite format 3'), "Database should be encrypted"

    def test_wrong_key_rejected(self):
        """Test that wrong encryption key is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'encrypted.db')
            key_path = os.path.join(tmpdir, 'test.key')
            wrong_key_path = os.path.join(tmpdir, 'wrong.key')

            # Create database with correct key
            db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
            db.initialize()

            # Create wrong key
            with open(wrong_key_path, 'w') as f:
                f.write('0' * 64)

            # Try to access with wrong key
            bad_db = AuthDatabase(db_path=db_path, key_path=wrong_key_path, is_dev=True)

            from auth.database import AuthDatabaseError
            with pytest.raises(AuthDatabaseError, match="decrypt"):
                with bad_db.connection() as conn:
                    conn.execute("SELECT * FROM users")

    def test_key_generation(self):
        """Test encryption key is generated if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            key_path = os.path.join(tmpdir, 'generated.key')

            assert not os.path.exists(key_path)

            db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
            db.initialize()

            assert os.path.exists(key_path)
            with open(key_path) as f:
                key = f.read().strip()
            assert len(key) == 64  # 256 bits as hex


class TestUserModel:
    """Tests for User model and repository."""

    def test_user_creation(self, temp_db):
        """Test creating a user."""
        user = User(
            username='alice123',
            auth_type=AuthType.TOTP,
            auth_credential=b'test_secret',
            can_download=True
        )
        user.save(temp_db)

        assert user.id is not None
        assert user.created_at is not None
        assert user.last_login is None

    def test_username_uniqueness(self, temp_db):
        """Test that duplicate usernames are rejected."""
        user1 = User(username='unique1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user1.save(temp_db)

        user2 = User(username='unique1', auth_type=AuthType.TOTP, auth_credential=b'secret2')

        with pytest.raises(Exception):  # Integrity error
            user2.save(temp_db)

    def test_user_repository_get_by_username(self, temp_db):
        """Test fetching user by username."""
        user = User(username='findme1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        repo = UserRepository(temp_db)
        found = repo.get_by_username('findme1')
        assert found is not None
        assert found.username == 'findme1'

        not_found = repo.get_by_username('nonexistent')
        assert not_found is None

    def test_username_case_sensitive(self, temp_db):
        """Test that usernames are case-sensitive."""
        user = User(username='CaseSensitive', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        repo = UserRepository(temp_db)
        assert repo.get_by_username('CaseSensitive') is not None
        assert repo.get_by_username('casesensitive') is None
        assert repo.get_by_username('CASESENSITIVE') is None

    def test_user_update_last_login(self, temp_db):
        """Test updating last login time."""
        user = User(username='login1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)
        assert user.last_login is None

        user.update_last_login(temp_db)
        assert user.last_login is not None

        # Verify persisted
        repo = UserRepository(temp_db)
        loaded = repo.get_by_id(user.id)
        assert loaded.last_login is not None

    def test_user_delete(self, temp_db):
        """Test deleting a user."""
        user = User(username='todelete', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)
        user_id = user.id

        user.delete(temp_db)

        repo = UserRepository(temp_db)
        assert repo.get_by_id(user_id) is None


class TestSessionModel:
    """Tests for Session model and single-session enforcement."""

    def test_session_creation(self, temp_db):
        """Test creating a session."""
        user = User(username='session1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        session, token = Session.create_for_user(temp_db, user.id, user_agent='TestAgent/1.0')

        assert session.id is not None
        assert session.user_id == user.id
        assert session.user_agent == 'TestAgent/1.0'
        assert len(token) > 30  # URL-safe base64 token

    def test_single_session_enforcement(self, temp_db):
        """Test that only one session per user is allowed."""
        user = User(username='single1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        # Create first session
        session1, token1 = Session.create_for_user(temp_db, user.id)

        # Create second session (should invalidate first)
        session2, token2 = Session.create_for_user(temp_db, user.id)

        repo = SessionRepository(temp_db)

        # First token should no longer work
        old_session = repo.get_by_token(token1)
        assert old_session is None

        # Second token should work
        new_session = repo.get_by_token(token2)
        assert new_session is not None
        assert new_session.id == session2.id

    def test_session_lookup_by_token(self, temp_db):
        """Test looking up session by raw token."""
        user = User(username='lookup1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        session, token = Session.create_for_user(temp_db, user.id)
        repo = SessionRepository(temp_db)

        found = repo.get_by_token(token)
        assert found is not None
        assert found.id == session.id

        not_found = repo.get_by_token('invalid_token')
        assert not_found is None

    def test_session_touch(self, temp_db):
        """Test updating last_seen timestamp."""
        user = User(username='touchuser1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        session, _ = Session.create_for_user(temp_db, user.id)

        import time
        time.sleep(0.05)  # Small delay to ensure time passes

        session.touch(temp_db)

        # Verify last_seen was updated by checking it was set recently
        assert session.last_seen is not None
        now = datetime.now()
        # Should be within 1 second of now
        diff = abs((now - session.last_seen).total_seconds())
        assert diff < 1.0

    def test_session_invalidate(self, temp_db):
        """Test session invalidation (logout)."""
        user = User(username='logout1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        session, token = Session.create_for_user(temp_db, user.id)
        session.invalidate(temp_db)

        repo = SessionRepository(temp_db)
        assert repo.get_by_token(token) is None


class TestPositionModel:
    """Tests for UserPosition model."""

    def test_position_save(self, temp_db):
        """Test saving a position."""
        user = User(username='posuser1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        pos = UserPosition(user_id=user.id, audiobook_id=42, position_ms=150000)
        pos.save(temp_db)

        assert pos.updated_at is not None

    def test_position_update(self, temp_db):
        """Test updating a position (upsert)."""
        user = User(username='posuser2', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        # First save
        pos = UserPosition(user_id=user.id, audiobook_id=42, position_ms=100000)
        pos.save(temp_db)

        # Update via new object (simulating upsert)
        pos2 = UserPosition(user_id=user.id, audiobook_id=42, position_ms=200000)
        pos2.save(temp_db)

        # Verify only one record exists with new position
        repo = PositionRepository(temp_db)
        loaded = repo.get(user.id, 42)
        assert loaded.position_ms == 200000

    def test_position_isolation(self, temp_db):
        """Test that positions are isolated per user."""
        user1 = User(username='isouser1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user1.save(temp_db)
        user2 = User(username='isouser2', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user2.save(temp_db)

        # Different positions for same audiobook
        UserPosition(user_id=user1.id, audiobook_id=42, position_ms=100000).save(temp_db)
        UserPosition(user_id=user2.id, audiobook_id=42, position_ms=200000).save(temp_db)

        repo = PositionRepository(temp_db)
        assert repo.get(user1.id, 42).position_ms == 100000
        assert repo.get(user2.id, 42).position_ms == 200000


class TestNotificationModel:
    """Tests for Notification model."""

    def test_notification_creation(self, temp_db):
        """Test creating a notification."""
        notif = Notification(
            message='Welcome to the library!',
            type=NotificationType.INFO,
            dismissable=True
        )
        notif.save(temp_db)

        assert notif.id is not None
        assert notif.created_at is not None

    def test_notification_active_for_user(self, temp_db):
        """Test getting active notifications for a user."""
        user = User(username='notif1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        # Global notification
        Notification(message='Global notice', type=NotificationType.INFO).save(temp_db)

        # Personal notification for this user
        Notification(
            message='Personal notice',
            type=NotificationType.PERSONAL,
            target_user_id=user.id
        ).save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(user.id)

        assert len(active) == 2

    def test_notification_dismiss(self, temp_db):
        """Test dismissing a notification."""
        user = User(username='dismiss1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        notif = Notification(message='Dismissable', type=NotificationType.INFO, dismissable=True)
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)

        # Should see notification
        assert len(repo.get_active_for_user(user.id)) == 1

        # Dismiss it
        repo.dismiss(notif.id, user.id)

        # Should not see it anymore
        assert len(repo.get_active_for_user(user.id)) == 0

    def test_notification_expiry(self, temp_db):
        """Test notification expiry filtering."""
        user = User(username='expire1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        # Expired notification
        Notification(
            message='Expired',
            type=NotificationType.INFO,
            expires_at=datetime.now() - timedelta(hours=1)
        ).save(temp_db)

        # Future notification
        Notification(
            message='Future',
            type=NotificationType.INFO,
            starts_at=datetime.now() + timedelta(hours=1)
        ).save(temp_db)

        # Active notification
        Notification(message='Active', type=NotificationType.INFO).save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(user.id)

        assert len(active) == 1
        assert active[0].message == 'Active'


class TestInboxModel:
    """Tests for InboxMessage model."""

    def test_inbox_creation(self, temp_db):
        """Test creating an inbox message."""
        user = User(username='inbox1', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        msg = InboxMessage(
            from_user_id=user.id,
            message='Please add more sci-fi books!',
            reply_via=ReplyMethod.IN_APP
        )
        msg.save(temp_db)

        assert msg.id is not None
        assert msg.status == InboxStatus.UNREAD

    def test_inbox_mark_read(self, temp_db):
        """Test marking message as read."""
        user = User(username='inbox2', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        msg = InboxMessage(from_user_id=user.id, message='Test', reply_via=ReplyMethod.IN_APP)
        msg.save(temp_db)

        repo = InboxRepository(temp_db)
        assert repo.count_unread() == 1

        msg.mark_read(temp_db)
        assert repo.count_unread() == 0
        assert msg.read_at is not None

    def test_inbox_mark_replied_clears_email(self, temp_db):
        """Test that marking as replied clears the email (PII)."""
        user = User(username='inbox3', auth_type=AuthType.TOTP, auth_credential=b'secret')
        user.save(temp_db)

        msg = InboxMessage(
            from_user_id=user.id,
            message='Test',
            reply_via=ReplyMethod.EMAIL,
            reply_email='test@example.com'
        )
        msg.save(temp_db)

        assert msg.reply_email == 'test@example.com'

        msg.mark_replied(temp_db)

        # Email should be cleared
        assert msg.reply_email is None

        # Verify in database
        repo = InboxRepository(temp_db)
        loaded = repo.get_by_id(msg.id)
        assert loaded.reply_email is None


class TestPendingRegistration:
    """Tests for PendingRegistration model."""

    def test_pending_creation(self, temp_db):
        """Test creating a pending registration."""
        reg, token = PendingRegistration.create(temp_db, 'newuser', expiry_minutes=15)

        assert reg.id is not None
        assert reg.username == 'newuser'
        assert len(token) > 30
        assert not reg.is_expired()

    def test_pending_token_lookup(self, temp_db):
        """Test looking up pending registration by token."""
        reg, token = PendingRegistration.create(temp_db, 'lookup1', expiry_minutes=15)

        repo = PendingRegistrationRepository(temp_db)
        found = repo.get_by_token(token)
        assert found is not None
        assert found.username == 'lookup1'

        not_found = repo.get_by_token('invalid')
        assert not_found is None

    def test_pending_consume(self, temp_db):
        """Test consuming (single-use) a pending registration."""
        reg, token = PendingRegistration.create(temp_db, 'consume1', expiry_minutes=15)

        repo = PendingRegistrationRepository(temp_db)

        # Should exist
        assert repo.get_by_token(token) is not None

        # Consume it
        reg.consume(temp_db)

        # Should no longer exist
        assert repo.get_by_token(token) is None

    def test_pending_expiry(self, temp_db):
        """Test pending registration expiry."""
        # Create with 0 minute expiry (already expired)
        reg, token = PendingRegistration.create(temp_db, 'expire1', expiry_minutes=0)

        assert reg.is_expired()


class TestTokenHashing:
    """Tests for token hashing utilities."""

    def test_hash_consistency(self):
        """Test that hash function is consistent."""
        token = 'test_token_value'
        hash1 = hash_token(token)
        hash2 = hash_token(token)
        assert hash1 == hash2

    def test_hash_different_inputs(self):
        """Test that different inputs produce different hashes."""
        hash1 = hash_token('token1')
        hash2 = hash_token('token2')
        assert hash1 != hash2

    def test_session_token_generation(self):
        """Test session token generation."""
        token, token_hash = generate_session_token()

        assert len(token) > 30
        assert len(token_hash) == 64  # SHA-256 produces 64 hex chars
        assert hash_token(token) == token_hash


class TestBackupCodes:
    """Tests for backup code generation and verification."""

    def test_generate_backup_code_format(self):
        """Test backup code format: XXXX-XXXX-XXXX-XXXX."""
        code = generate_backup_code()
        parts = code.split('-')

        assert len(parts) == 4
        for part in parts:
            assert len(part) == 4
            assert part.isalnum()
            assert part.isupper()

    def test_generate_backup_code_no_confusing_chars(self):
        """Test backup codes exclude confusing characters (0, O, 1, I)."""
        # Generate many codes to ensure we'd hit these chars if they were included
        for _ in range(50):
            code = generate_backup_code()
            assert '0' not in code
            assert 'O' not in code
            assert '1' not in code
            assert 'I' not in code

    def test_generate_backup_codes_count(self):
        """Test generating default number of backup codes."""
        raw_codes, hashes = generate_backup_codes()

        assert len(raw_codes) == NUM_BACKUP_CODES
        assert len(hashes) == NUM_BACKUP_CODES
        assert len(raw_codes) == 8  # Default

    def test_generate_backup_codes_unique(self):
        """Test that generated codes are unique."""
        raw_codes, hashes = generate_backup_codes()

        assert len(set(raw_codes)) == len(raw_codes)
        assert len(set(hashes)) == len(hashes)

    def test_normalize_code(self):
        """Test code normalization removes formatting."""
        code = "ABCD-1234-EFGH-5678"

        # Different input formats should normalize to the same thing
        assert normalize_backup_code(code) == "ABCD1234EFGH5678"
        assert normalize_backup_code("abcd-1234-efgh-5678") == "ABCD1234EFGH5678"
        assert normalize_backup_code("ABCD 1234 EFGH 5678") == "ABCD1234EFGH5678"
        assert normalize_backup_code("abcd1234efgh5678") == "ABCD1234EFGH5678"

    def test_hash_backup_code(self):
        """Test backup code hashing is consistent."""
        code = "ABCD-1234-EFGH-5678"

        hash1 = hash_backup_code(code)
        hash2 = hash_backup_code(code)
        hash3 = hash_backup_code("abcd-1234-efgh-5678")  # Different case

        assert hash1 == hash2
        assert hash1 == hash3  # Normalized before hashing
        assert len(hash1) == 64  # SHA-256

    def test_format_codes_for_display(self):
        """Test display formatting includes all codes."""
        codes = ["AAAA-BBBB-CCCC-DDDD", "EEEE-FFFF-GGGG-HHHH"]
        display = format_codes_for_display(codes)

        assert "AAAA-BBBB-CCCC-DDDD" in display
        assert "EEEE-FFFF-GGGG-HHHH" in display
        assert "BACKUP" in display
        assert "1." in display
        assert "2." in display


class TestBackupCodeRepository:
    """Tests for backup code database operations."""

    def test_create_codes_for_user(self, temp_db):
        """Test creating backup codes for a user."""
        # Create user first
        user = User(
            username="backup1",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes = repo.create_codes_for_user(user.id)

        assert len(codes) == NUM_BACKUP_CODES
        assert repo.get_remaining_count(user.id) == NUM_BACKUP_CODES

    def test_verify_and_consume_valid_code(self, temp_db):
        """Test verifying and consuming a valid backup code."""
        user = User(
            username="backup2",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes = repo.create_codes_for_user(user.id)

        # Verify first code
        assert repo.verify_and_consume(user.id, codes[0]) is True
        assert repo.get_remaining_count(user.id) == NUM_BACKUP_CODES - 1

    def test_verify_code_case_insensitive(self, temp_db):
        """Test backup code verification is case-insensitive."""
        user = User(
            username="backup3",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes = repo.create_codes_for_user(user.id)

        # Use lowercase
        lower_code = codes[0].lower()
        assert repo.verify_and_consume(user.id, lower_code) is True

    def test_verify_code_without_dashes(self, temp_db):
        """Test backup code verification works without dashes."""
        user = User(
            username="backup4",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes = repo.create_codes_for_user(user.id)

        # Remove dashes
        no_dash_code = codes[0].replace('-', '')
        assert repo.verify_and_consume(user.id, no_dash_code) is True

    def test_verify_invalid_code(self, temp_db):
        """Test verifying an invalid backup code fails."""
        user = User(
            username="backup5",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        repo.create_codes_for_user(user.id)

        # Try invalid code
        assert repo.verify_and_consume(user.id, "XXXX-XXXX-XXXX-XXXX") is False
        assert repo.get_remaining_count(user.id) == NUM_BACKUP_CODES  # No change

    def test_code_single_use(self, temp_db):
        """Test that backup codes can only be used once."""
        user = User(
            username="backup6",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes = repo.create_codes_for_user(user.id)

        # Use a code
        assert repo.verify_and_consume(user.id, codes[0]) is True

        # Try to use same code again
        assert repo.verify_and_consume(user.id, codes[0]) is False

    def test_code_wrong_user(self, temp_db):
        """Test backup code doesn't work for wrong user."""
        user1 = User(
            username="backup7a",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user1.save(temp_db)

        user2 = User(
            username="backup7b",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user2.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes1 = repo.create_codes_for_user(user1.id)
        repo.create_codes_for_user(user2.id)

        # User1's code should not work for user2
        assert repo.verify_and_consume(user2.id, codes1[0]) is False

    def test_regenerate_codes_replaces_old(self, temp_db):
        """Test regenerating codes invalidates old unused codes."""
        user = User(
            username="backup8",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        old_codes = repo.create_codes_for_user(user.id)

        # Use one code
        repo.verify_and_consume(user.id, old_codes[0])
        assert repo.get_remaining_count(user.id) == NUM_BACKUP_CODES - 1

        # Regenerate codes
        new_codes = repo.create_codes_for_user(user.id)
        assert repo.get_remaining_count(user.id) == NUM_BACKUP_CODES

        # Old codes should no longer work
        assert repo.verify_and_consume(user.id, old_codes[1]) is False

        # New codes should work
        assert repo.verify_and_consume(user.id, new_codes[0]) is True

    def test_get_all_for_user(self, temp_db):
        """Test getting all backup codes for a user (admin view)."""
        user = User(
            username="backup9",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        codes = repo.create_codes_for_user(user.id)

        # Use a code
        repo.verify_and_consume(user.id, codes[0])

        # Get all codes
        all_codes = repo.get_all_for_user(user.id)
        assert len(all_codes) == NUM_BACKUP_CODES

        # Check one is used
        used_count = sum(1 for c in all_codes if c.is_used)
        assert used_count == 1

    def test_delete_all_for_user(self, temp_db):
        """Test deleting all backup codes for a user."""
        user = User(
            username="backup10",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret"
        )
        user.save(temp_db)

        repo = BackupCodeRepository(temp_db)
        repo.create_codes_for_user(user.id)
        assert repo.get_remaining_count(user.id) == NUM_BACKUP_CODES

        # Delete all
        deleted = repo.delete_all_for_user(user.id)
        assert deleted == NUM_BACKUP_CODES
        assert repo.get_remaining_count(user.id) == 0


class TestUserRecoveryFields:
    """Tests for user recovery fields."""

    def test_user_with_recovery_email(self, temp_db):
        """Test creating user with recovery email."""
        user = User(
            username="recov1",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            recovery_email="test@example.com",
            recovery_enabled=True,
        )
        user.save(temp_db)

        repo = UserRepository(temp_db)
        loaded = repo.get_by_username("recov1")

        assert loaded.recovery_email == "test@example.com"
        assert loaded.recovery_phone is None
        assert loaded.recovery_enabled is True

    def test_user_with_recovery_phone(self, temp_db):
        """Test creating user with recovery phone."""
        user = User(
            username="recov2",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            recovery_phone="+1234567890",
            recovery_enabled=True,
        )
        user.save(temp_db)

        repo = UserRepository(temp_db)
        loaded = repo.get_by_username("recov2")

        assert loaded.recovery_email is None
        assert loaded.recovery_phone == "+1234567890"
        assert loaded.recovery_enabled is True

    def test_user_without_recovery(self, temp_db):
        """Test creating user without recovery info."""
        user = User(
            username="recov3",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)

        repo = UserRepository(temp_db)
        loaded = repo.get_by_username("recov3")

        assert loaded.recovery_email is None
        assert loaded.recovery_phone is None
        assert loaded.recovery_enabled is False

    def test_update_recovery_info(self, temp_db):
        """Test updating user recovery info."""
        user = User(
            username="recov4",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)

        # Update with recovery info
        user.recovery_email = "updated@example.com"
        user.recovery_enabled = True
        user.save(temp_db)

        repo = UserRepository(temp_db)
        loaded = repo.get_by_username("recov4")

        assert loaded.recovery_email == "updated@example.com"
        assert loaded.recovery_enabled is True
