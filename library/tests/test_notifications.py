"""
Comprehensive tests for Notification model and NotificationRepository.

Tests cover:
- Notification CRUD operations
- All notification types (info, maintenance, outage, personal)
- Priority ordering
- Time-based filtering (starts_at, expires_at)
- User targeting (global vs personal)
- Dismissal functionality
- Repository methods
"""

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
    Notification,
    NotificationType,
    NotificationRepository,
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


class TestNotificationTypes:
    """Tests for different notification types."""

    def test_info_notification(self, temp_db, test_user):
        """Test INFO notification type."""
        notif = Notification(
            message="Welcome to the library!",
            type=NotificationType.INFO,
        )
        notif.save(temp_db)

        assert notif.type == NotificationType.INFO
        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert len(active) == 1
        assert active[0].type == NotificationType.INFO

    def test_maintenance_notification(self, temp_db, test_user):
        """Test MAINTENANCE notification type."""
        notif = Notification(
            message="Scheduled maintenance Saturday at 2am",
            type=NotificationType.MAINTENANCE,
        )
        notif.save(temp_db)

        assert notif.type == NotificationType.MAINTENANCE
        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert active[0].type == NotificationType.MAINTENANCE

    def test_outage_notification(self, temp_db, test_user):
        """Test OUTAGE notification type."""
        notif = Notification(
            message="System currently experiencing issues",
            type=NotificationType.OUTAGE,
        )
        notif.save(temp_db)

        assert notif.type == NotificationType.OUTAGE
        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert active[0].type == NotificationType.OUTAGE

    def test_personal_notification(self, temp_db, test_user, second_user):
        """Test PERSONAL notification only visible to target user."""
        notif = Notification(
            message="Your request has been fulfilled!",
            type=NotificationType.PERSONAL,
            target_user_id=test_user.id,
        )
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)

        # Target user should see it
        active_target = repo.get_active_for_user(test_user.id)
        assert len(active_target) == 1
        assert active_target[0].type == NotificationType.PERSONAL

        # Other user should NOT see it
        active_other = repo.get_active_for_user(second_user.id)
        assert len(active_other) == 0


class TestNotificationPriority:
    """Tests for notification priority ordering."""

    def test_priority_ordering(self, temp_db, test_user):
        """Test notifications are ordered by priority (higher first)."""
        Notification(message="Low priority", type=NotificationType.INFO, priority=0).save(temp_db)
        Notification(message="High priority", type=NotificationType.INFO, priority=10).save(temp_db)
        Notification(message="Medium priority", type=NotificationType.INFO, priority=5).save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)

        assert len(active) == 3
        assert active[0].message == "High priority"
        assert active[1].message == "Medium priority"
        assert active[2].message == "Low priority"

    def test_same_priority_both_returned(self, temp_db, test_user):
        """Test same priority notifications are both returned."""
        # Create notifications with same priority
        n1 = Notification(message="First", type=NotificationType.INFO, priority=5)
        n1.save(temp_db)

        n2 = Notification(message="Second", type=NotificationType.INFO, priority=5)
        n2.save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)

        # Both should be returned (order may vary when timestamps are same)
        assert len(active) == 2
        messages = {n.message for n in active}
        assert messages == {"First", "Second"}


class TestNotificationTiming:
    """Tests for notification time-based filtering."""

    def test_starts_at_future(self, temp_db, test_user):
        """Test notification with future starts_at is not shown."""
        notif = Notification(
            message="Coming soon",
            type=NotificationType.INFO,
            starts_at=datetime.now() + timedelta(hours=1),
        )
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert len(active) == 0

    def test_starts_at_past(self, temp_db, test_user):
        """Test notification with past starts_at is shown."""
        notif = Notification(
            message="Already started",
            type=NotificationType.INFO,
            starts_at=datetime.now() - timedelta(hours=1),
        )
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert len(active) == 1

    def test_expires_at_future(self, temp_db, test_user):
        """Test notification with future expires_at is shown."""
        notif = Notification(
            message="Still valid",
            type=NotificationType.INFO,
            expires_at=datetime.now() + timedelta(hours=1),
        )
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert len(active) == 1

    def test_expires_at_past(self, temp_db, test_user):
        """Test notification with past expires_at is not shown."""
        notif = Notification(
            message="Expired",
            type=NotificationType.INFO,
            expires_at=datetime.now() - timedelta(hours=1),
        )
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert len(active) == 0

    def test_valid_time_window(self, temp_db, test_user):
        """Test notification within valid time window is shown."""
        notif = Notification(
            message="In window",
            type=NotificationType.MAINTENANCE,
            starts_at=datetime.now() - timedelta(hours=1),
            expires_at=datetime.now() + timedelta(hours=1),
        )
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)
        assert len(active) == 1

    def test_is_active_method(self, temp_db):
        """Test the is_active() method on Notification."""
        # Active notification
        active_notif = Notification(message="Active", type=NotificationType.INFO)
        assert active_notif.is_active() is True

        # Future notification
        future_notif = Notification(
            message="Future",
            type=NotificationType.INFO,
            starts_at=datetime.now() + timedelta(hours=1),
        )
        assert future_notif.is_active() is False

        # Expired notification
        expired_notif = Notification(
            message="Expired",
            type=NotificationType.INFO,
            expires_at=datetime.now() - timedelta(hours=1),
        )
        assert expired_notif.is_active() is False


class TestNotificationDismissal:
    """Tests for notification dismissal functionality."""

    def test_dismiss_notification(self, temp_db, test_user):
        """Test dismissing a notification."""
        notif = Notification(message="Dismissable", type=NotificationType.INFO, dismissable=True)
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)

        # Should see notification
        assert len(repo.get_active_for_user(test_user.id)) == 1

        # Dismiss it
        result = repo.dismiss(notif.id, test_user.id)
        assert result is True

        # Should not see it anymore
        assert len(repo.get_active_for_user(test_user.id)) == 0

    def test_dismiss_idempotent(self, temp_db, test_user):
        """Test dismissing same notification twice is safe."""
        notif = Notification(message="Dismiss twice", type=NotificationType.INFO)
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)

        # First dismiss succeeds
        assert repo.dismiss(notif.id, test_user.id) is True

        # Second dismiss returns False (already dismissed)
        assert repo.dismiss(notif.id, test_user.id) is False

    def test_dismiss_per_user(self, temp_db, test_user, second_user):
        """Test dismissal is per-user."""
        notif = Notification(message="Per user dismiss", type=NotificationType.INFO)
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)

        # First user dismisses
        repo.dismiss(notif.id, test_user.id)

        # First user should not see it
        assert len(repo.get_active_for_user(test_user.id)) == 0

        # Second user should still see it
        assert len(repo.get_active_for_user(second_user.id)) == 1


class TestNotificationCRUD:
    """Tests for notification CRUD operations."""

    def test_create_notification(self, temp_db):
        """Test creating a notification."""
        notif = Notification(
            message="New notification",
            type=NotificationType.INFO,
            created_by="admin",
        )
        notif.save(temp_db)

        assert notif.id is not None
        assert notif.created_at is not None
        assert notif.created_by == "admin"

    def test_update_notification(self, temp_db):
        """Test updating a notification."""
        notif = Notification(message="Original", type=NotificationType.INFO)
        notif.save(temp_db)

        # Update
        notif.message = "Updated"
        notif.priority = 5
        notif.save(temp_db)

        # Verify
        repo = NotificationRepository(temp_db)
        all_notifs = repo.list_all()
        assert len(all_notifs) == 1
        assert all_notifs[0].message == "Updated"
        assert all_notifs[0].priority == 5

    def test_delete_notification(self, temp_db):
        """Test deleting a notification."""
        notif = Notification(message="To delete", type=NotificationType.INFO)
        notif.save(temp_db)

        repo = NotificationRepository(temp_db)
        assert len(repo.list_all()) == 1

        notif.delete(temp_db)

        assert len(repo.list_all()) == 0

    def test_delete_unsaved_notification(self, temp_db):
        """Test deleting unsaved notification returns False."""
        notif = Notification(message="Unsaved", type=NotificationType.INFO)
        result = notif.delete(temp_db)
        assert result is False


class TestNotificationRepository:
    """Tests for NotificationRepository methods."""

    def test_list_all(self, temp_db):
        """Test listing all notifications."""
        Notification(message="First", type=NotificationType.INFO).save(temp_db)
        Notification(message="Second", type=NotificationType.MAINTENANCE).save(temp_db)
        Notification(message="Third", type=NotificationType.OUTAGE).save(temp_db)

        repo = NotificationRepository(temp_db)
        all_notifs = repo.list_all()

        assert len(all_notifs) == 3

    def test_list_all_returns_all(self, temp_db):
        """Test list_all returns all notifications."""
        Notification(message="First", type=NotificationType.INFO).save(temp_db)
        Notification(message="Second", type=NotificationType.INFO).save(temp_db)

        repo = NotificationRepository(temp_db)
        all_notifs = repo.list_all()

        assert len(all_notifs) == 2
        messages = {n.message for n in all_notifs}
        assert messages == {"First", "Second"}

    def test_get_active_excludes_expired(self, temp_db, test_user):
        """Test get_active_for_user excludes expired notifications."""
        # Active
        Notification(message="Active", type=NotificationType.INFO).save(temp_db)

        # Expired
        Notification(
            message="Expired",
            type=NotificationType.INFO,
            expires_at=datetime.now() - timedelta(hours=1),
        ).save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)

        assert len(active) == 1
        assert active[0].message == "Active"

    def test_get_active_excludes_future(self, temp_db, test_user):
        """Test get_active_for_user excludes future notifications."""
        # Active
        Notification(message="Active", type=NotificationType.INFO).save(temp_db)

        # Future
        Notification(
            message="Future",
            type=NotificationType.INFO,
            starts_at=datetime.now() + timedelta(hours=1),
        ).save(temp_db)

        repo = NotificationRepository(temp_db)
        active = repo.get_active_for_user(test_user.id)

        assert len(active) == 1
        assert active[0].message == "Active"
