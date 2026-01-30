"""
Performance tests for auth system.

Tests measure:
- Database operation latency
- Concurrent session handling
- Token hashing performance
- Bulk user operations
"""

import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    Notification,
    NotificationType,
    NotificationRepository,
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


class TestDatabasePerformance:
    """Tests for database operation performance."""

    def test_user_creation_latency(self, temp_db):
        """Test user creation time is acceptable."""
        times = []

        for i in range(50):
            username = f"perf{i:04d}"
            user = User(
                username=username,
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            )

            start = time.perf_counter()
            user.save(temp_db)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg_time = statistics.mean(times)
        max_time = max(times)
        p95_time = sorted(times)[int(len(times) * 0.95)]

        print(f"\nUser creation: avg={avg_time*1000:.2f}ms, max={max_time*1000:.2f}ms, p95={p95_time*1000:.2f}ms")

        # Assertions: should be fast for SQLite
        assert avg_time < 0.05, f"Average user creation too slow: {avg_time*1000:.2f}ms"
        assert p95_time < 0.1, f"p95 user creation too slow: {p95_time*1000:.2f}ms"

    def test_user_lookup_latency(self, temp_db):
        """Test user lookup time is acceptable."""
        # Create users first
        for i in range(100):
            User(
                username=f"look{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            ).save(temp_db)

        repo = UserRepository(temp_db)
        times = []

        # Measure lookups
        for i in range(100):
            username = f"look{i:04d}"
            start = time.perf_counter()
            user = repo.get_by_username(username)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            assert user is not None

        avg_time = statistics.mean(times)
        max_time = max(times)
        p95_time = sorted(times)[int(len(times) * 0.95)]

        print(f"\nUser lookup: avg={avg_time*1000:.2f}ms, max={max_time*1000:.2f}ms, p95={p95_time*1000:.2f}ms")

        assert avg_time < 0.01, f"Average lookup too slow: {avg_time*1000:.2f}ms"
        assert p95_time < 0.02, f"p95 lookup too slow: {p95_time*1000:.2f}ms"

    def test_session_token_lookup_latency(self, temp_db):
        """Test session token lookup performance."""
        # Create users and sessions
        tokens = []
        for i in range(100):
            user = User(
                username=f"sess{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            )
            user.save(temp_db)
            session, token = Session.create_for_user(temp_db, user.id)
            tokens.append(token)

        repo = SessionRepository(temp_db)
        times = []

        # Measure token lookups
        for token in tokens:
            start = time.perf_counter()
            session = repo.get_by_token(token)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            assert session is not None

        avg_time = statistics.mean(times)
        max_time = max(times)
        p95_time = sorted(times)[int(len(times) * 0.95)]

        print(f"\nToken lookup: avg={avg_time*1000:.2f}ms, max={max_time*1000:.2f}ms, p95={p95_time*1000:.2f}ms")

        # Token lookup includes hashing, so allow slightly more time
        assert avg_time < 0.02, f"Average token lookup too slow: {avg_time*1000:.2f}ms"
        assert p95_time < 0.05, f"p95 token lookup too slow: {p95_time*1000:.2f}ms"


class TestTokenHashingPerformance:
    """Tests for token hashing performance."""

    def test_hash_token_speed(self):
        """Test token hashing is fast enough."""
        times = []
        token = "sample_session_token_abc123xyz789"

        for _ in range(1000):
            start = time.perf_counter()
            hash_token(token)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg_time = statistics.mean(times)
        max_time = max(times)

        print(f"\nToken hashing: avg={avg_time*1000000:.2f}μs, max={max_time*1000000:.2f}μs")

        # Hashing should be very fast (< 1ms)
        assert avg_time < 0.001, f"Token hashing too slow: {avg_time*1000:.2f}ms"

    def test_hash_token_consistency(self):
        """Verify same token produces same hash."""
        token = "consistent_token_test"
        hashes = [hash_token(token) for _ in range(100)]

        # All hashes should be identical
        assert len(set(hashes)) == 1, "Hash inconsistency detected"


class TestConcurrentOperations:
    """Tests for concurrent database access."""

    def test_concurrent_user_lookups(self, temp_db):
        """Test concurrent user lookups don't cause issues."""
        # Create users
        for i in range(50):
            User(
                username=f"conc{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            ).save(temp_db)

        repo = UserRepository(temp_db)
        errors = []
        results = []

        def lookup_user(username):
            try:
                start = time.perf_counter()
                user = repo.get_by_username(username)
                elapsed = time.perf_counter() - start
                return (username, user is not None, elapsed)
            except Exception as e:
                return (username, False, str(e))

        # Run concurrent lookups
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(lookup_user, f"conc{i:04d}")
                for i in range(50)
                for _ in range(3)  # Each user looked up 3 times
            ]

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if not result[1]:
                    errors.append(result)

        # No errors should occur
        assert len(errors) == 0, f"Concurrent lookup errors: {errors}"

        # All lookups should succeed
        success_count = sum(1 for r in results if r[1])
        assert success_count == 150, f"Only {success_count}/150 lookups succeeded"

    def test_concurrent_session_creation(self, temp_db):
        """Test concurrent session creation for different users."""
        # Create users
        users = []
        for i in range(20):
            user = User(
                username=f"csess{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            )
            user.save(temp_db)
            users.append(user)

        results = []
        errors = []

        def create_session(user_id):
            try:
                session, token = Session.create_for_user(temp_db, user_id)
                return (user_id, True, token)
            except Exception as e:
                return (user_id, False, str(e))

        # Create sessions concurrently
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(create_session, user.id)
                for user in users
            ]

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if not result[1]:
                    errors.append(result)

        assert len(errors) == 0, f"Session creation errors: {errors}"

        # Verify each user has a session
        repo = SessionRepository(temp_db)
        for user in users:
            session = repo.get_by_user_id(user.id)
            assert session is not None, f"User {user.id} has no session"


class TestBulkOperations:
    """Tests for bulk data operations."""

    def test_notification_bulk_create(self, temp_db):
        """Test bulk notification creation performance."""
        start = time.perf_counter()

        for i in range(100):
            Notification(
                message=f"Notification {i}",
                type=NotificationType.INFO,
                priority=i % 10,
            ).save(temp_db)

        elapsed = time.perf_counter() - start
        print(f"\n100 notifications created in {elapsed*1000:.2f}ms ({elapsed*10:.2f}ms each)")

        assert elapsed < 2.0, f"Bulk notification creation too slow: {elapsed:.2f}s"

    def test_notification_query_performance(self, temp_db):
        """Test notification query with many items."""
        # Create test user
        user = User(
            username="nquery",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)

        # Create many notifications
        for i in range(200):
            Notification(
                message=f"Notification {i}",
                type=NotificationType.INFO,
                priority=i % 10,
            ).save(temp_db)

        repo = NotificationRepository(temp_db)
        times = []

        # Measure query performance
        for _ in range(50):
            start = time.perf_counter()
            active = repo.get_active_for_user(user.id)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg_time = statistics.mean(times)
        print(f"\nActive notifications query: avg={avg_time*1000:.2f}ms, count={len(active)}")

        assert avg_time < 0.05, f"Notification query too slow: {avg_time*1000:.2f}ms"

    def test_session_cleanup_performance(self, temp_db):
        """Test stale session cleanup performance."""
        # Create many users with sessions
        for i in range(100):
            user = User(
                username=f"clean{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            )
            user.save(temp_db)
            Session.create_for_user(temp_db, user.id)

        # Make half the sessions stale
        with temp_db.connection() as conn:
            # Use SQLite-compatible format to match DEFAULT CURRENT_TIMESTAMP
            old_time = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE id % 2 = 0",
                (old_time,)
            )

        repo = SessionRepository(temp_db)

        # Measure cleanup performance
        start = time.perf_counter()
        deleted = repo.cleanup_stale(grace_minutes=30)
        elapsed = time.perf_counter() - start

        print(f"\nSession cleanup: deleted {deleted} in {elapsed*1000:.2f}ms")

        assert deleted == 50, f"Expected 50 deleted, got {deleted}"
        assert elapsed < 0.1, f"Cleanup too slow: {elapsed*1000:.2f}ms"


class TestDatabaseScaling:
    """Tests for database behavior at scale."""

    def test_user_table_scaling(self, temp_db):
        """Test performance with many users."""
        # Create 500 users
        start = time.perf_counter()
        for i in range(500):
            User(
                username=f"scale{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            ).save(temp_db)
        create_time = time.perf_counter() - start

        print(f"\n500 users created in {create_time:.2f}s ({create_time*2:.2f}ms each)")

        repo = UserRepository(temp_db)

        # Test lookup at various points
        lookup_times = []
        for i in [0, 100, 250, 400, 499]:
            start = time.perf_counter()
            user = repo.get_by_username(f"scale{i:04d}")
            elapsed = time.perf_counter() - start
            lookup_times.append(elapsed)
            assert user is not None

        avg_lookup = statistics.mean(lookup_times)
        print(f"Lookups in 500-user table: avg={avg_lookup*1000:.2f}ms")

        # Lookup should still be fast with index
        assert avg_lookup < 0.01, f"Lookup degraded with scale: {avg_lookup*1000:.2f}ms"

    def test_list_all_users_performance(self, temp_db):
        """Test listing all users performance."""
        # Create users
        for i in range(200):
            User(
                username=f"list{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            ).save(temp_db)

        repo = UserRepository(temp_db)

        # Measure list_all
        start = time.perf_counter()
        users = repo.list_all()
        elapsed = time.perf_counter() - start

        print(f"\nlist_all for {len(users)} users: {elapsed*1000:.2f}ms")

        assert len(users) == 200
        assert elapsed < 0.1, f"list_all too slow: {elapsed*1000:.2f}ms"


class TestEncryptionOverhead:
    """Tests for SQLCipher encryption overhead."""

    def test_encryption_vs_operations(self, temp_db):
        """Measure encryption overhead on operations."""
        # This test just measures baseline with encryption
        # (we can't easily compare without encryption in this setup)

        times = {
            'insert': [],
            'select': [],
            'update': [],
        }

        # Measure insert
        for i in range(50):
            user = User(
                username=f"enc{i:04d}",
                auth_type=AuthType.TOTP,
                auth_credential=b"secret",
            )
            start = time.perf_counter()
            user.save(temp_db)
            times['insert'].append(time.perf_counter() - start)

        repo = UserRepository(temp_db)

        # Measure select
        for i in range(50):
            start = time.perf_counter()
            repo.get_by_username(f"enc{i:04d}")
            times['select'].append(time.perf_counter() - start)

        # Measure update
        users = repo.list_all()
        for user in users[:50]:
            user.can_download = True
            start = time.perf_counter()
            user.save(temp_db)
            times['update'].append(time.perf_counter() - start)

        print("\nEncrypted DB operation times:")
        for op, t in times.items():
            avg = statistics.mean(t)
            print(f"  {op}: avg={avg*1000:.3f}ms")

        # All operations should be reasonably fast despite encryption
        assert statistics.mean(times['insert']) < 0.05
        assert statistics.mean(times['select']) < 0.01
        assert statistics.mean(times['update']) < 0.05
