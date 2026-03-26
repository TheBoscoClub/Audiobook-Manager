"""
Tests for the AuditLog model and AuditLogRepository.

Covers:
- log() creates entry with correct fields
- log() stores details as JSON string
- list() returns newest first (ORDER BY id DESC)
- list() filters by action
- list() filters by user (actor or target)
- list() paginates with limit/offset
- count_unseen() counts entries above a given ID
- Entries survive user deletion (actor_id becomes NULL via ON DELETE SET NULL)
"""

import json
import sys
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth.audit import AuditLogRepository  # noqa: E402
from auth.models import AuditLog  # noqa: E402

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def audit_repo(auth_app):
    """Return an AuditLogRepository backed by the shared auth_db."""
    return AuditLogRepository(auth_app.auth_db)


@pytest.fixture
def sample_user(auth_app):
    """Create a temporary test user and return its ID.

    Yields user_id; deletes the user after the test so state doesn't
    bleed into subsequent tests that rely on auth_app session scope.
    """
    from auth.models import AuthType, User

    auth_db = auth_app.auth_db
    user = User(
        username="audit_test_user",
        auth_type=AuthType.TOTP,
        auth_credential=b"testsecret",
    )
    user = user.save(auth_db)
    yield user.id

    # Cleanup: delete user so the session-scoped auth_db stays clean
    from auth.models import UserRepository

    repo = UserRepository(auth_db)
    repo.delete(user.id)


@pytest.fixture
def second_user(auth_app):
    """Create a second temporary test user and return its ID."""
    from auth.models import AuthType, User

    auth_db = auth_app.auth_db
    user = User(
        username="audit_test_user2",
        auth_type=AuthType.TOTP,
        auth_credential=b"testsecret2",
    )
    user = user.save(auth_db)
    yield user.id

    from auth.models import UserRepository

    repo = UserRepository(auth_db)
    repo.delete(user.id)


# ============================================================
# AuditLog dataclass tests
# ============================================================


class TestAuditLogDataclass:
    def test_from_row_creates_instance(self):
        """from_row() should populate all fields from a positional tuple."""
        row = (1, "2026-03-23T12:00:00Z", 10, 20, "user.create", '{"key": "val"}')
        entry = AuditLog.from_row(row)
        assert entry.id == 1
        assert entry.timestamp == "2026-03-23T12:00:00Z"
        assert entry.actor_id == 10
        assert entry.target_id == 20
        assert entry.action == "user.create"
        assert entry.details == '{"key": "val"}'

    def test_from_row_none_returns_none(self):
        """from_row(None) should return None."""
        assert AuditLog.from_row(None) is None

    def test_from_row_nullable_fields(self):
        """from_row() should handle NULL actor_id and target_id."""
        row = (5, "2026-03-23T12:00:00Z", None, None, "user.deleted", None)
        entry = AuditLog.from_row(row)
        assert entry.actor_id is None
        assert entry.target_id is None
        assert entry.details is None


# ============================================================
# AuditLogRepository tests
# ============================================================


class TestAuditLogRepositoryLog:
    def test_log_creates_entry(self, audit_repo, sample_user, second_user):
        """log() should create an entry and return it with all fields set."""
        entry = audit_repo.log(
            actor_id=sample_user,
            target_id=second_user,
            action="user.create",
        )
        assert entry is not None
        assert entry.id is not None
        assert entry.actor_id == sample_user
        assert entry.target_id == second_user
        assert entry.action == "user.create"
        assert entry.timestamp is not None

    def test_log_stores_details_as_json(self, audit_repo, sample_user, second_user):
        """log() should store details dict as a JSON string."""
        details = {"reason": "test", "count": 42}
        entry = audit_repo.log(
            actor_id=sample_user,
            target_id=second_user,
            action="user.update",
            details=details,
        )
        assert entry.details is not None
        # details should be a JSON string in the database
        parsed = json.loads(entry.details)
        assert parsed["reason"] == "test"
        assert parsed["count"] == 42

    def test_log_without_details(self, audit_repo, sample_user):
        """log() with no details should store NULL (details=None)."""
        entry = audit_repo.log(
            actor_id=sample_user,
            target_id=None,
            action="session.login",
        )
        assert entry.details is None

    def test_log_returns_persisted_entry(self, audit_repo, sample_user):
        """log() should return an entry that can be retrieved by ID."""
        entry = audit_repo.log(
            actor_id=sample_user,
            target_id=None,
            action="session.logout",
        )
        fetched = audit_repo.get_by_id(entry.id)
        assert fetched is not None
        assert fetched.id == entry.id
        assert fetched.action == "session.logout"


class TestAuditLogRepositoryList:
    def test_list_newest_first(self, audit_repo, sample_user):
        """list() should return entries ordered by id DESC (newest first)."""
        # Create 3 entries in order
        e1 = audit_repo.log(actor_id=sample_user, target_id=None, action="order.test.a")
        e2 = audit_repo.log(actor_id=sample_user, target_id=None, action="order.test.b")
        e3 = audit_repo.log(actor_id=sample_user, target_id=None, action="order.test.c")

        # Filter to just our entries to avoid interference from other tests
        our_ids = {e1.id, e2.id, e3.id}

        # Grab all order.test.* entries
        all_order_entries = audit_repo.list(limit=100)
        our_entries = [e for e in all_order_entries if e.id in our_ids]

        # They should be returned in descending ID order (newest first)
        assert len(our_entries) == 3
        assert our_entries[0].id == e3.id
        assert our_entries[1].id == e2.id
        assert our_entries[2].id == e1.id

    def test_list_filter_by_action(self, audit_repo, sample_user):
        """list() with action_filter should only return matching entries."""
        unique_action = "unique.test.action.xyz123"
        audit_repo.log(actor_id=sample_user, target_id=None, action=unique_action)
        audit_repo.log(actor_id=sample_user, target_id=None, action="other.action")

        results = audit_repo.list(action_filter=unique_action)
        assert all(e.action == unique_action for e in results)
        assert len(results) >= 1

    def test_list_filter_by_user_as_actor(self, audit_repo, sample_user, second_user):
        """list() with user_filter should return entries where user is actor."""
        unique_action = "actor.filter.test.xyz456"
        audit_repo.log(
            actor_id=sample_user, target_id=second_user, action=unique_action
        )

        results = audit_repo.list(user_filter=sample_user, action_filter=unique_action)
        assert len(results) >= 1
        assert all(
            e.actor_id == sample_user or e.target_id == sample_user for e in results
        )

    def test_list_filter_by_user_as_target(self, audit_repo, sample_user, second_user):
        """list() with user_filter should return entries where user is target."""
        unique_action = "target.filter.test.xyz789"
        audit_repo.log(
            actor_id=second_user, target_id=sample_user, action=unique_action
        )

        results = audit_repo.list(user_filter=sample_user, action_filter=unique_action)
        assert len(results) >= 1
        assert all(
            e.actor_id == sample_user or e.target_id == sample_user for e in results
        )

    def test_list_pagination(self, audit_repo, sample_user):
        """list() should respect limit/offset for pagination."""
        page_action = "pagination.test.xyz000"
        # Create 5 entries
        for i in range(5):
            audit_repo.log(actor_id=sample_user, target_id=None, action=page_action)

        page1 = audit_repo.list(action_filter=page_action, limit=2, offset=0)
        page2 = audit_repo.list(action_filter=page_action, limit=2, offset=2)
        page3 = audit_repo.list(action_filter=page_action, limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1  # Only 1 remaining

        # No duplicates across pages
        ids_p1 = {e.id for e in page1}
        ids_p2 = {e.id for e in page2}
        assert ids_p1.isdisjoint(ids_p2)


class TestAuditLogRepositoryCount:
    def test_count_unseen(self, audit_repo, sample_user):
        """count_unseen() should count entries with id > last_seen_id."""
        anchor = audit_repo.log(
            actor_id=sample_user, target_id=None, action="unseen.anchor"
        )
        last_seen = anchor.id

        # Create 3 more entries after the anchor
        audit_repo.log(actor_id=sample_user, target_id=None, action="unseen.new1")
        audit_repo.log(actor_id=sample_user, target_id=None, action="unseen.new2")
        audit_repo.log(actor_id=sample_user, target_id=None, action="unseen.new3")

        count = audit_repo.count_unseen(last_seen)
        assert count >= 3  # At least 3 unseen (other tests may add more)

    def test_count_unseen_zero_when_nothing_new(self, audit_repo, sample_user):
        """count_unseen() should return 0 if no entries newer than the ID."""
        # Get the maximum current ID
        entries = audit_repo.list(limit=1)
        if entries:
            last_id = entries[0].id
        else:
            last_id = 999999

        count = audit_repo.count_unseen(last_id)
        assert count == 0


class TestAuditLogUserDeletion:
    def test_entry_survives_user_deletion(self, audit_repo, auth_app):
        """Entries should persist after actor/target user is deleted.

        The audit_log table uses ON DELETE SET NULL for actor_id and
        target_id, so rows are preserved with NULL foreign keys.
        """
        from auth.models import AuthType, User, UserRepository

        auth_db = auth_app.auth_db

        # Create a temporary user
        ephemeral = User(
            username="ephemeral_audit_user",
            auth_type=AuthType.TOTP,
            auth_credential=b"ephemeralkey",
        )
        ephemeral = ephemeral.save(auth_db)
        ephemeral_id = ephemeral.id

        # Log an action with this user as actor
        entry = audit_repo.log(
            actor_id=ephemeral_id,
            target_id=None,
            action="ephemeral.action",
        )
        entry_id = entry.id

        # Delete the user
        repo = UserRepository(auth_db)
        repo.delete(ephemeral_id)

        # Audit entry should still exist with actor_id = NULL
        fetched = audit_repo.get_by_id(entry_id)
        assert fetched is not None
        assert fetched.id == entry_id
        assert fetched.action == "ephemeral.action"
        assert fetched.actor_id is None  # SET NULL after user deletion
