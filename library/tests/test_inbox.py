"""
Comprehensive tests for InboxMessage model and InboxRepository.

Tests cover:
- Inbox message CRUD operations
- All inbox statuses (unread, read, replied, archived)
- Reply methods (in-app, email)
- PII handling (email cleared on reply)
- Status transitions
- Repository methods
- Contact log audit trail
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import (
    AuthDatabase,
    AuthType,
    User,
    InboxMessage,
    InboxRepository,
    InboxStatus,
    ReplyMethod,
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
    user = User(username="testuser", auth_type=AuthType.TOTP, auth_credential=b"secret")
    user.save(temp_db)
    return user


@pytest.fixture
def second_user(temp_db):
    """Create a second test user."""
    user = User(username="seconduser", auth_type=AuthType.TOTP, auth_credential=b"secret2")
    user.save(temp_db)
    return user


class TestInboxMessageCreation:
    """Tests for inbox message creation."""

    def test_create_inapp_message(self, temp_db, test_user):
        """Test creating a message with in-app reply preference."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Please add more sci-fi books!",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        assert msg.id is not None
        assert msg.status == InboxStatus.UNREAD
        assert msg.reply_via == ReplyMethod.IN_APP
        assert msg.reply_email is None
        assert msg.created_at is not None

    def test_create_email_message(self, temp_db, test_user):
        """Test creating a message with email reply preference."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Please contact me via email",
            reply_via=ReplyMethod.EMAIL,
            reply_email="user@example.com",
        )
        msg.save(temp_db)

        assert msg.id is not None
        assert msg.reply_via == ReplyMethod.EMAIL
        assert msg.reply_email == "user@example.com"

    def test_create_logs_contact(self, temp_db, test_user):
        """Test creating message logs to contact_log table."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test message",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        # Check contact_log
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM contact_log WHERE user_id = ?",
                (test_user.id,)
            )
            count = cursor.fetchone()[0]
            assert count == 1


class TestInboxStatusTransitions:
    """Tests for inbox message status transitions."""

    def test_mark_read(self, temp_db, test_user):
        """Test marking a message as read."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        assert msg.status == InboxStatus.UNREAD
        assert msg.read_at is None

        msg.mark_read(temp_db)

        assert msg.status == InboxStatus.READ
        assert msg.read_at is not None
        assert isinstance(msg.read_at, datetime)

    def test_mark_replied(self, temp_db, test_user):
        """Test marking a message as replied."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        msg.mark_replied(temp_db)

        assert msg.status == InboxStatus.REPLIED
        assert msg.replied_at is not None

    def test_mark_replied_clears_email(self, temp_db, test_user):
        """Test that marking as replied clears email (PII protection)."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Please email me",
            reply_via=ReplyMethod.EMAIL,
            reply_email="sensitive@example.com",
        )
        msg.save(temp_db)

        assert msg.reply_email == "sensitive@example.com"

        msg.mark_replied(temp_db)

        # Email should be cleared
        assert msg.reply_email is None

        # Verify in database
        repo = InboxRepository(temp_db)
        loaded = repo.get_by_id(msg.id)
        assert loaded.reply_email is None

    def test_archive_message(self, temp_db, test_user):
        """Test archiving a message."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="To archive",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        # Archive it
        msg.status = InboxStatus.ARCHIVED
        msg.save(temp_db)

        repo = InboxRepository(temp_db)
        loaded = repo.get_by_id(msg.id)
        assert loaded.status == InboxStatus.ARCHIVED


class TestInboxRepository:
    """Tests for InboxRepository methods."""

    def test_get_by_id(self, temp_db, test_user):
        """Test getting a message by ID."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Find me",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        repo = InboxRepository(temp_db)
        found = repo.get_by_id(msg.id)

        assert found is not None
        assert found.message == "Find me"
        assert found.from_user_id == test_user.id

    def test_get_by_id_not_found(self, temp_db):
        """Test getting nonexistent message returns None."""
        repo = InboxRepository(temp_db)
        found = repo.get_by_id(99999)
        assert found is None

    def test_list_unread(self, temp_db, test_user):
        """Test listing unread messages."""
        # Create unread messages
        InboxMessage(
            from_user_id=test_user.id,
            message="Unread 1",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        InboxMessage(
            from_user_id=test_user.id,
            message="Unread 2",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        # Create read message
        read_msg = InboxMessage(
            from_user_id=test_user.id,
            message="Already read",
            reply_via=ReplyMethod.IN_APP,
        )
        read_msg.save(temp_db)
        read_msg.mark_read(temp_db)

        repo = InboxRepository(temp_db)
        unread = repo.list_unread()

        assert len(unread) == 2
        assert all(m.status == InboxStatus.UNREAD for m in unread)

    def test_list_all_excludes_archived(self, temp_db, test_user):
        """Test list_all excludes archived by default."""
        # Create normal message
        InboxMessage(
            from_user_id=test_user.id,
            message="Normal",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        # Create and archive message
        archived = InboxMessage(
            from_user_id=test_user.id,
            message="Archived",
            reply_via=ReplyMethod.IN_APP,
        )
        archived.save(temp_db)
        archived.status = InboxStatus.ARCHIVED
        archived.save(temp_db)

        repo = InboxRepository(temp_db)

        # Default: excludes archived
        messages = repo.list_all()
        assert len(messages) == 1
        assert messages[0].message == "Normal"

    def test_list_all_includes_archived(self, temp_db, test_user):
        """Test list_all can include archived."""
        # Create normal message
        InboxMessage(
            from_user_id=test_user.id,
            message="Normal",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        # Create and archive message
        archived = InboxMessage(
            from_user_id=test_user.id,
            message="Archived",
            reply_via=ReplyMethod.IN_APP,
        )
        archived.save(temp_db)
        archived.status = InboxStatus.ARCHIVED
        archived.save(temp_db)

        repo = InboxRepository(temp_db)

        # With include_archived=True
        messages = repo.list_all(include_archived=True)
        assert len(messages) == 2

    def test_count_unread(self, temp_db, test_user):
        """Test counting unread messages."""
        repo = InboxRepository(temp_db)

        # Initially zero
        assert repo.count_unread() == 0

        # Add unread messages
        InboxMessage(
            from_user_id=test_user.id,
            message="Unread 1",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        assert repo.count_unread() == 1

        InboxMessage(
            from_user_id=test_user.id,
            message="Unread 2",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        assert repo.count_unread() == 2

        # Mark one as read
        messages = repo.list_unread()
        messages[0].mark_read(temp_db)

        assert repo.count_unread() == 1

    def test_list_all_returns_all(self, temp_db, test_user):
        """Test list_all returns all messages."""
        InboxMessage(
            from_user_id=test_user.id,
            message="First",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        InboxMessage(
            from_user_id=test_user.id,
            message="Second",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        repo = InboxRepository(temp_db)
        messages = repo.list_all()

        assert len(messages) == 2
        msg_texts = {m.message for m in messages}
        assert msg_texts == {"First", "Second"}


class TestReplyMethods:
    """Tests for different reply methods."""

    def test_inapp_reply_method(self, temp_db, test_user):
        """Test IN_APP reply method."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Reply in app",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(temp_db)

        assert msg.reply_via == ReplyMethod.IN_APP
        assert msg.reply_via.value == "in-app"

    def test_email_reply_method(self, temp_db, test_user):
        """Test EMAIL reply method."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Reply via email",
            reply_via=ReplyMethod.EMAIL,
            reply_email="test@example.com",
        )
        msg.save(temp_db)

        assert msg.reply_via == ReplyMethod.EMAIL
        assert msg.reply_via.value == "email"


class TestMultipleUsers:
    """Tests involving multiple users."""

    def test_messages_from_different_users(self, temp_db, test_user, second_user):
        """Test messages from different users."""
        InboxMessage(
            from_user_id=test_user.id,
            message="From first user",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        InboxMessage(
            from_user_id=second_user.id,
            message="From second user",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        repo = InboxRepository(temp_db)
        messages = repo.list_all()

        assert len(messages) == 2
        user_ids = {m.from_user_id for m in messages}
        assert test_user.id in user_ids
        assert second_user.id in user_ids

    def test_contact_log_multiple_users(self, temp_db, test_user, second_user):
        """Test contact log tracks all users."""
        # Messages from both users
        InboxMessage(
            from_user_id=test_user.id,
            message="First",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        InboxMessage(
            from_user_id=test_user.id,
            message="Second from same user",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        InboxMessage(
            from_user_id=second_user.id,
            message="From other user",
            reply_via=ReplyMethod.IN_APP,
        ).save(temp_db)

        # Check contact_log
        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT user_id, COUNT(*) FROM contact_log GROUP BY user_id")
            counts = {row[0]: row[1] for row in cursor.fetchall()}

        assert counts[test_user.id] == 2
        assert counts[second_user.id] == 1


class TestInboxFromRow:
    """Tests for InboxMessage.from_row deserialization."""

    def test_from_row_all_fields(self, temp_db, test_user):
        """Test from_row correctly deserializes all fields."""
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test message",
            reply_via=ReplyMethod.EMAIL,
            reply_email="test@example.com",
        )
        msg.save(temp_db)
        msg.mark_read(temp_db)

        repo = InboxRepository(temp_db)
        loaded = repo.get_by_id(msg.id)

        assert loaded.id == msg.id
        assert loaded.from_user_id == test_user.id
        assert loaded.message == "Test message"
        assert loaded.reply_via == ReplyMethod.EMAIL
        assert loaded.reply_email == "test@example.com"
        assert loaded.status == InboxStatus.READ
        assert isinstance(loaded.created_at, datetime)
        assert isinstance(loaded.read_at, datetime)
