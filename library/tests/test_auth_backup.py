"""
Tests for auth database backup and restore functionality.

Tests cover:
- Backup creation with encryption preserved
- Restore from backup
- Data integrity verification
- Key file handling
- Error conditions
"""

import os
import shutil
import sys
import tempfile
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
    Notification,
    NotificationType,
    NotificationRepository,
    InboxMessage,
    InboxRepository,
    ReplyMethod,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestAuthBackup:
    """Tests for auth database backup operations."""

    def test_backup_and_restore_users(self, temp_dir):
        """Test backing up and restoring user data."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"
        backup_db_path = temp_dir / "auth-backup.db"
        backup_key_path = temp_dir / "auth-backup.key"

        # Create database and add test users
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        user1 = User(
            username="bkuser1",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret1",
            can_download=True,
            is_admin=False,
        )
        user1.save(db)

        user2 = User(
            username="bkadmin1",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret2",
            can_download=True,
            is_admin=True,
        )
        user2.save(db)

        # Create backup by copying files
        shutil.copy(db_path, backup_db_path)
        shutil.copy(key_path, backup_key_path)

        # Modify original database
        user1.username = "moduser1"
        user1.save(db)

        user_repo = UserRepository(db)
        assert user_repo.get_by_username("moduser1") is not None
        assert user_repo.get_by_username("bkuser1") is None

        # Restore from backup
        shutil.copy(backup_db_path, db_path)

        # Re-open database and verify restored data
        db2 = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        user_repo2 = UserRepository(db2)

        restored_user = user_repo2.get_by_username("bkuser1")
        assert restored_user is not None
        assert restored_user.can_download is True

        restored_admin = user_repo2.get_by_username("bkadmin1")
        assert restored_admin is not None
        assert restored_admin.is_admin is True

    def test_backup_preserves_encryption(self, temp_dir):
        """Test that backup file remains encrypted."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"
        backup_db_path = temp_dir / "auth-backup.db"

        # Create encrypted database
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        User(
            username="encrypted_user",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        ).save(db)

        # Create backup
        shutil.copy(db_path, backup_db_path)

        # Verify backup is encrypted (can't open with wrong key)
        wrong_key_path = temp_dir / "wrong.key"
        with open(wrong_key_path, "w") as f:
            f.write("0" * 64)  # Wrong key

        with pytest.raises(Exception):
            wrong_db = AuthDatabase(
                db_path=str(backup_db_path),
                key_path=str(wrong_key_path),
                is_dev=True
            )
            # Try to access data - this should fail
            UserRepository(wrong_db).list_all()

    def test_backup_with_notifications(self, temp_dir):
        """Test backup includes notifications."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"
        backup_db_path = temp_dir / "auth-backup.db"

        # Create database with notifications
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        notif = Notification(
            message="Important announcement",
            type=NotificationType.MAINTENANCE,
            priority=10,
        )
        notif.save(db)

        # Create backup
        shutil.copy(db_path, backup_db_path)
        shutil.copy(key_path, temp_dir / "backup.key")

        # Delete notification from original
        notif.delete(db)
        assert len(NotificationRepository(db).list_all()) == 0

        # Restore from backup
        shutil.copy(backup_db_path, db_path)

        # Verify notification is restored
        db2 = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        notifications = NotificationRepository(db2).list_all()
        assert len(notifications) == 1
        assert notifications[0].message == "Important announcement"
        assert notifications[0].type == NotificationType.MAINTENANCE

    def test_backup_with_inbox_messages(self, temp_dir):
        """Test backup includes inbox messages."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"
        backup_db_path = temp_dir / "auth-backup.db"

        # Create database with user and inbox message
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        user = User(
            username="inbox_test_user",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(db)

        msg = InboxMessage(
            from_user_id=user.id,
            message="Please add more books!",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(db)

        # Create backup
        shutil.copy(db_path, backup_db_path)

        # Mark message as read (modify original)
        msg.mark_read(db)
        inbox_repo = InboxRepository(db)
        assert inbox_repo.count_unread() == 0

        # Restore from backup
        shutil.copy(backup_db_path, db_path)

        # Verify message is unread again
        db2 = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        inbox_repo2 = InboxRepository(db2)
        assert inbox_repo2.count_unread() == 1

    def test_key_file_required_for_restore(self, temp_dir):
        """Test that restore fails without correct key file."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"

        # Create encrypted database
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        User(
            username="key_test_user",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        ).save(db)

        # Delete key file
        os.remove(key_path)

        # Create new key file (different key)
        new_key_path = temp_dir / "new.key"

        # Should fail to open with new/different key
        with pytest.raises(Exception):
            new_db = AuthDatabase(
                db_path=str(db_path),
                key_path=str(new_key_path),
                is_dev=True
            )
            # Try to access data
            UserRepository(new_db).list_all()

    def test_backup_database_integrity(self, temp_dir):
        """Test that backup maintains database integrity."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"
        backup_db_path = temp_dir / "auth-backup.db"

        # Create database with various data
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        # Add user
        user = User(
            username="integrity_test",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret123",
            can_download=True,
            is_admin=True,
        )
        user.save(db)

        # Add notification targeting user
        notif = Notification(
            message="Personal message",
            type=NotificationType.PERSONAL,
            target_user_id=user.id,
        )
        notif.save(db)

        # Add inbox message from user
        msg = InboxMessage(
            from_user_id=user.id,
            message="Test message",
            reply_via=ReplyMethod.EMAIL,
            reply_email="test@example.com",
        )
        msg.save(db)

        # Get counts before backup
        user_count = len(UserRepository(db).list_all())
        notif_count = len(NotificationRepository(db).list_all())
        inbox_count = len(InboxRepository(db).list_all(include_archived=True))

        # Create backup
        shutil.copy(db_path, backup_db_path)

        # Open backup and verify counts match
        backup_db = AuthDatabase(
            db_path=str(backup_db_path),
            key_path=str(key_path),
            is_dev=True
        )

        assert len(UserRepository(backup_db).list_all()) == user_count
        assert len(NotificationRepository(backup_db).list_all()) == notif_count
        assert len(InboxRepository(backup_db).list_all(include_archived=True)) == inbox_count

        # Verify user data integrity
        backup_user = UserRepository(backup_db).get_by_username("integrity_test")
        assert backup_user.auth_credential == b"secret123"
        assert backup_user.can_download is True
        assert backup_user.is_admin is True


class TestDatabaseRecovery:
    """Tests for database recovery scenarios."""

    def test_recover_from_corrupted_database(self, temp_dir):
        """Test recovery when main database is corrupted."""
        db_path = temp_dir / "auth.db"
        key_path = temp_dir / "auth.key"
        backup_db_path = temp_dir / "auth-backup.db"

        # Create and populate database
        db = AuthDatabase(db_path=str(db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        User(
            username="recover_user",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        ).save(db)

        # Create backup
        shutil.copy(db_path, backup_db_path)

        # Corrupt the main database
        with open(db_path, "wb") as f:
            f.write(b"corrupted data")

        # Main database should fail to open
        with pytest.raises(Exception):
            bad_db = AuthDatabase(
                db_path=str(db_path),
                key_path=str(key_path),
                is_dev=True
            )
            UserRepository(bad_db).list_all()

        # Restore from backup
        shutil.copy(backup_db_path, db_path)

        # Should work now
        recovered_db = AuthDatabase(
            db_path=str(db_path),
            key_path=str(key_path),
            is_dev=True
        )
        user = UserRepository(recovered_db).get_by_username("recover_user")
        assert user is not None
