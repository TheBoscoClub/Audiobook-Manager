"""
Performance tests for auth system.

Tests measure:
- Database operation latency
- Concurrent session handling
- Token hashing performance
- Bulk user operations

Timing methodology (loaded-runner robustness):
- Assertions use the median of many samples, never the mean or p95 — tail
  latency on a loaded shared CI runner measures the runner, not the code
- Ceilings are ~10x the local baseline, so they tolerate scheduler
  contention while still failing on order-of-magnitude regressions
  (per-call key derivation, full-table rewrites, sync-per-statement I/O)
- CPU-bound work (token hashing) is measured with time.process_time(),
  which excludes time spent descheduled
- Unrepeatable single-shot operations use generous absolute ceilings;
  repeatable ones use best-of-N, which is immune to transient load
- avg/max/p95 are still printed for diagnostics; they are not asserted
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

from auth import (  # noqa: E402
    AuthDatabase,
    AuthType,
    Notification,
    NotificationRepository,
    NotificationType,
    Session,
    SessionRepository,
    User,
    UserRepository,
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
            user = User(username=username, auth_type=AuthType.TOTP, auth_credential=b"secret")

            start = time.perf_counter()
            user.save(temp_db)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg_time = statistics.mean(times)
        max_time = max(times)
        p95_time = sorted(times)[int(len(times) * 0.95)]
        median_time = statistics.median(times)

        print(
            f"\nUser creation: avg={avg_time * 1000:.2f}ms,"
            f" median={median_time * 1000:.2f}ms,"
            f" max={max_time * 1000:.2f}ms,"
            f" p95={p95_time * 1000:.2f}ms"
        )

        # Median with a generous ceiling (~10x local baseline): robust to
        # scheduler noise, still fails if per-insert cost turns pathological
        assert median_time < 0.5, f"Median user creation too slow: {median_time * 1000:.2f}ms"

    def test_user_lookup_latency(self, temp_db):
        """Test user lookup time is acceptable."""
        # Create users first
        for i in range(100):
            User(username=f"look{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret").save(
                temp_db
            )

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
        median_time = statistics.median(times)

        print(
            f"\nUser lookup: avg={avg_time * 1000:.2f}ms,"
            f" median={median_time * 1000:.2f}ms,"
            f" max={max_time * 1000:.2f}ms,"
            f" p95={p95_time * 1000:.2f}ms"
        )

        # Median with a generous ceiling (~10x local baseline): robust to
        # scheduler noise, still fails if indexed lookups turn pathological
        assert median_time < 0.1, f"Median lookup too slow: {median_time * 1000:.2f}ms"

    def test_session_token_lookup_latency(self, temp_db):
        """Test session token lookup performance."""
        # Create users and sessions
        tokens = []
        for i in range(100):
            user = User(username=f"sess{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret")
            user.save(temp_db)
            assert user.id is not None
            _session, token = Session.create_for_user(temp_db, user.id)
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
        median_time = statistics.median(times)

        print(
            f"\nToken lookup: avg={avg_time * 1000:.2f}ms,"
            f" median={median_time * 1000:.2f}ms,"
            f" max={max_time * 1000:.2f}ms,"
            f" p95={p95_time * 1000:.2f}ms"
        )

        # Token lookup includes hashing, so allow slightly more time.
        # Median with a generous ceiling (~10x local baseline): robust to
        # scheduler noise, still fails on a pathological lookup path
        assert median_time < 0.2, f"Median token lookup too slow: {median_time * 1000:.2f}ms"


class TestTokenHashingPerformance:
    """Tests for token hashing performance."""

    def test_hash_token_speed(self):
        """Test token hashing is fast enough."""
        token = "sample_session_token_abc123xyz789"  # nosec B105 # noqa: S105 — test fixture, not a real credential
        iterations = 1000

        # Hashing is CPU-bound: measure process CPU time over the whole
        # loop, which excludes time spent descheduled on a loaded runner
        start = time.process_time()
        for _ in range(iterations):
            hash_token(token)
        cpu_elapsed = time.process_time() - start
        avg_time = cpu_elapsed / iterations

        print(f"\nToken hashing: avg={avg_time * 1000000:.2f}μs CPU per hash")

        # Hashing should be very fast (< 1ms CPU); would fail if hash_token
        # were accidentally switched to a slow KDF (scrypt/bcrypt-class)
        assert avg_time < 0.001, f"Token hashing too slow: {avg_time * 1000:.2f}ms"

    def test_hash_token_consistency(self):
        """Verify same token produces same hash."""
        token = "consistent_token_test"  # nosec B105 # noqa: S105 — test fixture, not a real credential
        hashes = [hash_token(token) for _ in range(100)]

        # All hashes should be identical
        assert len(set(hashes)) == 1, "Hash inconsistency detected"


class TestConcurrentOperations:
    """Tests for concurrent database access."""

    def test_concurrent_user_lookups(self, temp_db):
        """Test concurrent user lookups don't cause issues."""
        # Create users
        for i in range(50):
            User(username=f"conc{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret").save(
                temp_db
            )

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
                username=f"csess{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret"
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
            futures = [executor.submit(create_session, user.id) for user in users]

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if not result[1]:
                    errors.append(result)

        assert len(errors) == 0, f"Session creation errors: {errors}"

        # Verify each user has a session
        repo = SessionRepository(temp_db)
        for user in users:
            assert user.id is not None
            session = repo.get_by_user_id(user.id)
            assert session is not None, f"User {user.id} has no session"


class TestBulkOperations:
    """Tests for bulk data operations."""

    def test_notification_bulk_create(self, temp_db):
        """Test bulk notification creation performance."""
        start = time.perf_counter()

        for i in range(100):
            Notification(
                message=f"Notification {i}", type=NotificationType.INFO, priority=i % 10
            ).save(temp_db)

        elapsed = time.perf_counter() - start
        print(f"\n100 notifications created in {elapsed * 1000:.2f}ms ({elapsed * 10:.2f}ms each)")

        # Generous ceiling (~10x local baseline, unrepeatable single shot):
        # still fails if per-insert cost turns pathological (>100ms each)
        assert elapsed < 10.0, f"Bulk notification creation too slow: {elapsed:.2f}s"

    def test_notification_query_performance(self, temp_db):
        """Test notification query with many items."""
        # Create test user
        user = User(username="nquery", auth_type=AuthType.TOTP, auth_credential=b"secret")
        user.save(temp_db)
        assert user.id is not None

        # Create many notifications
        for i in range(200):
            Notification(
                message=f"Notification {i}", type=NotificationType.INFO, priority=i % 10
            ).save(temp_db)

        repo = NotificationRepository(temp_db)
        times = []
        active: list = []

        # Measure query performance
        for _ in range(50):
            start = time.perf_counter()
            active = repo.get_active_for_user(user.id)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        avg_time = statistics.mean(times)
        median_time = statistics.median(times)
        print(
            f"\nActive notifications query: avg={avg_time * 1000:.2f}ms,"
            f" median={median_time * 1000:.2f}ms, count={len(active)}"
        )

        # Median with a generous ceiling (~10x local baseline): robust to
        # scheduler noise, still fails if the query turns pathological
        assert median_time < 0.5, f"Notification query too slow: {median_time * 1000:.2f}ms"

    def test_session_cleanup_performance(self, temp_db):
        """Test stale session cleanup performance."""
        # Create many users with sessions
        for i in range(100):
            user = User(
                username=f"clean{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret"
            )
            user.save(temp_db)
            assert user.id is not None
            Session.create_for_user(temp_db, user.id)

        # Make half the sessions stale
        with temp_db.connection() as conn:
            # Use SQLite-compatible format to match DEFAULT CURRENT_TIMESTAMP
            old_time = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE sessions SET last_seen = ? WHERE id % 2 = 0", (old_time,))

        repo = SessionRepository(temp_db)

        # Measure cleanup performance
        start = time.perf_counter()
        deleted = repo.cleanup_stale(grace_minutes=30)
        elapsed = time.perf_counter() - start

        print(f"\nSession cleanup: deleted {deleted} in {elapsed * 1000:.2f}ms")

        assert deleted == 50, f"Expected 50 deleted, got {deleted}"
        # Generous ceiling (~10x local baseline, unrepeatable single shot):
        # still fails if cleanup degrades to per-row round trips
        assert elapsed < 1.0, f"Cleanup too slow: {elapsed * 1000:.2f}ms"


class TestDatabaseScaling:
    """Tests for database behavior at scale."""

    def test_user_table_scaling(self, temp_db):
        """Test performance with many users."""
        # Create 500 users
        start = time.perf_counter()
        for i in range(500):
            User(username=f"scale{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret").save(
                temp_db
            )
        create_time = time.perf_counter() - start

        print(f"\n500 users created in {create_time:.2f}s ({create_time * 2:.2f}ms each)")

        repo = UserRepository(temp_db)

        # Test lookup at various points, sampled repeatedly so the median
        # is meaningful on a loaded runner
        lookup_times = []
        for i in [0, 100, 250, 400, 499]:
            for _ in range(5):
                start = time.perf_counter()
                user = repo.get_by_username(f"scale{i:04d}")
                elapsed = time.perf_counter() - start
                lookup_times.append(elapsed)
                assert user is not None

        avg_lookup = statistics.mean(lookup_times)
        median_lookup = statistics.median(lookup_times)
        print(
            f"Lookups in 500-user table: avg={avg_lookup * 1000:.2f}ms,"
            f" median={median_lookup * 1000:.2f}ms"
        )

        # Lookup should still be fast with index. Median with a generous
        # ceiling (~10x local baseline): robust to scheduler noise, still
        # fails if lookups degrade badly with table size
        assert median_lookup < 0.1, f"Lookup degraded with scale: {median_lookup * 1000:.2f}ms"

    def test_list_all_users_performance(self, temp_db):
        """Test listing all users performance."""
        # Create users
        for i in range(200):
            User(username=f"list{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret").save(
                temp_db
            )

        repo = UserRepository(temp_db)

        # Measure list_all best-of-5: the fastest run reflects intrinsic
        # cost and is immune to transient contention on a loaded runner
        run_times = []
        users: list = []
        for _ in range(5):
            start = time.perf_counter()
            users = repo.list_all()
            elapsed = time.perf_counter() - start
            run_times.append(elapsed)
        best_time = min(run_times)

        print(f"\nlist_all for {len(users)} users: best={best_time * 1000:.2f}ms of 5 runs")

        assert len(users) == 200
        # Generous ceiling (~5x local baseline) on the best run: still
        # fails if listing 200 users turns pathological
        assert best_time < 0.5, f"list_all too slow: {best_time * 1000:.2f}ms"


class TestEncryptionOverhead:
    """Tests for SQLCipher encryption overhead."""

    def test_encryption_vs_operations(self, temp_db):
        """Measure encryption overhead on operations."""
        # This test just measures baseline with encryption
        # (we can't easily compare without encryption in this setup)

        times: dict[str, list[float]] = {"insert": [], "select": [], "update": []}

        # Measure insert
        for i in range(50):
            user = User(username=f"enc{i:04d}", auth_type=AuthType.TOTP, auth_credential=b"secret")
            start = time.perf_counter()
            user.save(temp_db)
            times["insert"].append(time.perf_counter() - start)

        repo = UserRepository(temp_db)

        # Measure select
        for i in range(50):
            start = time.perf_counter()
            repo.get_by_username(f"enc{i:04d}")
            times["select"].append(time.perf_counter() - start)

        # Measure update
        users = repo.list_all()
        for user in users[:50]:
            user.can_download = True
            start = time.perf_counter()
            user.save(temp_db)
            times["update"].append(time.perf_counter() - start)

        print("\nEncrypted DB operation times:")
        medians = {}
        for op, t in times.items():
            avg = statistics.mean(t)
            medians[op] = statistics.median(t)
            print(f"  {op}: avg={avg * 1000:.3f}ms, median={medians[op] * 1000:.3f}ms")

        # All operations should be reasonably fast despite encryption.
        # Medians with generous ceilings (~10x local baseline): robust to
        # scheduler noise, still fail if encryption overhead turns
        # pathological (e.g. per-operation key derivation)
        assert medians["insert"] < 0.5
        assert medians["select"] < 0.1
        assert medians["update"] < 0.5
