"""
Tests for the operation status tracking module.

The OperationTracker is a singleton that manages long-running operation state.
These tests cover the complete lifecycle: create -> start -> update -> complete/fail/cancel.
"""

import threading
import time
from datetime import datetime, timedelta

import pytest


class TestOperationState:
    """Test the OperationState enum."""

    def test_enum_values(self):
        """Test all expected enum values exist."""
        from backend.operation_status import OperationState

        assert OperationState.PENDING.value == "pending"
        assert OperationState.RUNNING.value == "running"
        assert OperationState.COMPLETED.value == "completed"
        assert OperationState.FAILED.value == "failed"
        assert OperationState.CANCELLED.value == "cancelled"

    def test_enum_is_str(self):
        """Test that OperationState inherits from str for JSON serialization."""
        from backend.operation_status import OperationState

        # str(Enum) behavior from str inheritance
        assert isinstance(OperationState.PENDING, str)


class TestOperationStatus:
    """Test the OperationStatus data class."""

    def test_initialization(self):
        """Test OperationStatus initializes with correct defaults."""
        from backend.operation_status import OperationState, OperationStatus

        status = OperationStatus("test-id", "test-type", "Test description")

        assert status.id == "test-id"
        assert status.type == "test-type"
        assert status.description == "Test description"
        assert status.state == OperationState.PENDING
        assert status.progress == 0
        assert status.message == "Initializing..."
        assert status.started_at is None
        assert status.completed_at is None
        assert status.result is None
        assert status.error is None

    def test_to_dict_pending(self):
        """Test to_dict output for pending operation."""
        from backend.operation_status import OperationStatus

        status = OperationStatus("abc123", "rescan", "Rescanning library")
        result = status.to_dict()

        assert result["id"] == "abc123"
        assert result["type"] == "rescan"
        assert result["description"] == "Rescanning library"
        assert result["state"] == "pending"
        assert result["progress"] == 0
        assert result["message"] == "Initializing..."
        assert result["started_at"] is None
        assert result["completed_at"] is None
        assert result["elapsed_seconds"] is None
        assert result["result"] is None
        assert result["error"] is None

    def test_to_dict_with_timestamps(self):
        """Test to_dict includes ISO formatted timestamps."""
        from backend.operation_status import OperationState, OperationStatus

        status = OperationStatus("test-id", "hash", "Computing hashes")
        status.state = OperationState.RUNNING
        status.started_at = datetime(2026, 1, 13, 10, 0, 0)

        result = status.to_dict()

        assert result["started_at"] == "2026-01-13T10:00:00"
        assert result["completed_at"] is None

    def test_elapsed_seconds_running(self):
        """Test elapsed time calculation for running operation."""
        from backend.operation_status import OperationState, OperationStatus

        status = OperationStatus("test-id", "test", "Test op")
        status.state = OperationState.RUNNING
        # Set started_at to 10 seconds ago
        status.started_at = datetime.now() - timedelta(seconds=10)

        result = status.to_dict()

        # Should be approximately 10 seconds (allow small variance)
        assert 9.5 <= result["elapsed_seconds"] <= 11.0

    def test_elapsed_seconds_completed(self):
        """Test elapsed time calculation uses completed_at when available."""
        from backend.operation_status import OperationState, OperationStatus

        status = OperationStatus("test-id", "test", "Test op")
        status.state = OperationState.COMPLETED
        status.started_at = datetime(2026, 1, 13, 10, 0, 0)
        status.completed_at = datetime(2026, 1, 13, 10, 0, 30)

        result = status.to_dict()

        assert result["elapsed_seconds"] == 30.0


@pytest.fixture
def fresh_tracker():
    """Reset the OperationTracker singleton for each test.

    The OperationTracker uses singleton pattern. To ensure test isolation,
    we reset the singleton before each test and restore it after.
    """
    from backend.operation_status import OperationTracker

    # Store original singleton
    original = OperationTracker._instance

    # Reset singleton
    OperationTracker._instance = None

    # Get a fresh instance
    tracker = OperationTracker()

    yield tracker

    # Restore original (or leave None for cleanup)
    OperationTracker._instance = original


class TestOperationTracker:
    """Test the OperationTracker singleton."""

    def test_singleton_pattern(self):
        """Test that OperationTracker returns the same instance."""
        from backend.operation_status import OperationTracker

        tracker1 = OperationTracker()
        tracker2 = OperationTracker()

        assert tracker1 is tracker2

    def test_create_operation(self, fresh_tracker):
        """Test creating a new operation."""
        op_id = fresh_tracker.create_operation("rescan", "Rescanning library")

        assert op_id is not None
        assert len(op_id) == 8  # UUID[:8]

        # Verify operation exists
        status = fresh_tracker.get_status(op_id)
        assert status is not None
        assert status["type"] == "rescan"
        assert status["description"] == "Rescanning library"
        assert status["state"] == "pending"

    def test_start_operation(self, fresh_tracker):
        """Test starting an operation."""
        op_id = fresh_tracker.create_operation("test", "Test operation")

        result = fresh_tracker.start_operation(op_id)

        assert result is True
        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "running"
        assert status["started_at"] is not None
        assert status["message"] == "Starting..."

    def test_start_operation_invalid_id(self, fresh_tracker):
        """Test starting non-existent operation returns False."""
        result = fresh_tracker.start_operation("nonexistent")
        assert result is False

    def test_update_progress(self, fresh_tracker):
        """Test updating operation progress."""
        op_id = fresh_tracker.create_operation("hash", "Computing hashes")
        fresh_tracker.start_operation(op_id)

        result = fresh_tracker.update_progress(op_id, 50, "Processing file 50/100")

        assert result is True
        status = fresh_tracker.get_status(op_id)
        assert status["progress"] == 50
        assert status["message"] == "Processing file 50/100"

    def test_update_progress_clamping(self, fresh_tracker):
        """Test that progress is clamped to 0-100 range."""
        op_id = fresh_tracker.create_operation("test", "Test")
        fresh_tracker.start_operation(op_id)

        # Test over 100
        fresh_tracker.update_progress(op_id, 150, "Over limit")
        status = fresh_tracker.get_status(op_id)
        assert status["progress"] == 100

        # Test under 0
        fresh_tracker.update_progress(op_id, -10, "Under limit")
        status = fresh_tracker.get_status(op_id)
        assert status["progress"] == 0

    def test_update_progress_invalid_id(self, fresh_tracker):
        """Test updating non-existent operation returns False."""
        result = fresh_tracker.update_progress("nonexistent", 50, "msg")
        assert result is False

    def test_complete_operation(self, fresh_tracker):
        """Test completing an operation successfully."""
        op_id = fresh_tracker.create_operation("rescan", "Rescan")
        fresh_tracker.start_operation(op_id)

        result_data = {"scanned": 100, "added": 5}
        result = fresh_tracker.complete_operation(op_id, result_data)

        assert result is True
        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "completed"
        assert status["progress"] == 100
        assert status["completed_at"] is not None
        assert status["result"] == result_data
        assert status["message"] == "Completed"

    def test_complete_operation_no_result(self, fresh_tracker):
        """Test completing operation without result data."""
        op_id = fresh_tracker.create_operation("test", "Test")
        fresh_tracker.start_operation(op_id)

        result = fresh_tracker.complete_operation(op_id)

        assert result is True
        status = fresh_tracker.get_status(op_id)
        assert status["result"] is None

    def test_complete_operation_invalid_id(self, fresh_tracker):
        """Test completing non-existent operation returns False."""
        result = fresh_tracker.complete_operation("nonexistent")
        assert result is False

    def test_fail_operation(self, fresh_tracker):
        """Test marking an operation as failed."""
        op_id = fresh_tracker.create_operation("scan", "Scanning")
        fresh_tracker.start_operation(op_id)

        result = fresh_tracker.fail_operation(op_id, "Disk full")

        assert result is True
        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "failed"
        assert status["completed_at"] is not None
        assert status["error"] == "Disk full"
        assert status["message"] == "Failed: Disk full"

    def test_fail_operation_invalid_id(self, fresh_tracker):
        """Test failing non-existent operation returns False."""
        result = fresh_tracker.fail_operation("nonexistent", "error")
        assert result is False

    def test_cancel_operation(self, fresh_tracker):
        """Test cancelling an operation."""
        op_id = fresh_tracker.create_operation("import", "Importing")
        fresh_tracker.start_operation(op_id)

        result = fresh_tracker.cancel_operation(op_id)

        assert result is True
        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "cancelled"
        assert status["completed_at"] is not None
        assert status["message"] == "Cancelled"

    def test_cancel_operation_invalid_id(self, fresh_tracker):
        """Test cancelling non-existent operation returns False."""
        result = fresh_tracker.cancel_operation("nonexistent")
        assert result is False

    def test_get_status_invalid_id(self, fresh_tracker):
        """Test getting status for non-existent operation."""
        result = fresh_tracker.get_status("nonexistent")
        assert result is None

    def test_get_operation(self, fresh_tracker):
        """Test getting the raw OperationStatus object."""
        from backend.operation_status import OperationStatus

        op_id = fresh_tracker.create_operation("test", "Test")

        op = fresh_tracker.get_operation(op_id)

        assert isinstance(op, OperationStatus)
        assert op.id == op_id

    def test_get_operation_invalid_id(self, fresh_tracker):
        """Test getting non-existent operation returns None."""
        result = fresh_tracker.get_operation("nonexistent")
        assert result is None

    def test_get_active_operations(self, fresh_tracker):
        """Test getting active (pending/running) operations."""
        # Create operations in different states
        pending_id = fresh_tracker.create_operation("pending", "Pending op")
        running_id = fresh_tracker.create_operation("running", "Running op")
        completed_id = fresh_tracker.create_operation("completed", "Completed op")

        fresh_tracker.start_operation(running_id)
        fresh_tracker.start_operation(completed_id)
        fresh_tracker.complete_operation(completed_id)

        active = fresh_tracker.get_active_operations()

        active_ids = [op["id"] for op in active]
        assert pending_id in active_ids
        assert running_id in active_ids
        assert completed_id not in active_ids

    def test_get_all_operations(self, fresh_tracker):
        """Test getting all operations regardless of state."""
        op1 = fresh_tracker.create_operation("op1", "Op 1")
        op2 = fresh_tracker.create_operation("op2", "Op 2")
        fresh_tracker.start_operation(op2)
        fresh_tracker.complete_operation(op2)

        all_ops = fresh_tracker.get_all_operations()

        assert len(all_ops) == 2
        all_ids = [op["id"] for op in all_ops]
        assert op1 in all_ids
        assert op2 in all_ids

    def test_is_operation_running(self, fresh_tracker):
        """Test checking if operation type is running."""
        op_id = fresh_tracker.create_operation("rescan", "Rescan")
        fresh_tracker.start_operation(op_id)

        result = fresh_tracker.is_operation_running("rescan")

        assert result == op_id

    def test_is_operation_running_not_running(self, fresh_tracker):
        """Test is_operation_running returns None when not running."""
        # Create but don't start
        fresh_tracker.create_operation("rescan", "Rescan")

        result = fresh_tracker.is_operation_running("rescan")

        assert result is None

    def test_is_operation_running_wrong_type(self, fresh_tracker):
        """Test is_operation_running returns None for different type."""
        op_id = fresh_tracker.create_operation("rescan", "Rescan")
        fresh_tracker.start_operation(op_id)

        result = fresh_tracker.is_operation_running("hash")

        assert result is None

    def test_cleanup_old_operations(self, fresh_tracker):
        """Test that old completed operations are cleaned up.

        Cleanup runs during create_operation() call, so we need to create
        new operations after completing existing ones to trigger cleanup.
        """
        # Set a small max_history for testing
        fresh_tracker._max_history = 3

        # Create and complete 5 operations
        completed_ids = []
        for i in range(5):
            op_id = fresh_tracker.create_operation(f"op{i}", f"Op {i}")
            fresh_tracker.start_operation(op_id)
            fresh_tracker.complete_operation(op_id, {"index": i})
            completed_ids.append(op_id)
            # Small delay to ensure different completion times
            time.sleep(0.01)

        # Now create one more to trigger cleanup of the 5 completed ops
        fresh_tracker.create_operation("trigger", "Trigger cleanup")

        # Should have max_history completed ops + 1 pending = 4 total
        all_ops = fresh_tracker.get_all_operations()
        completed_ops = [op for op in all_ops if op["state"] == "completed"]

        # Cleanup keeps max_history (3) completed ops
        assert len(completed_ops) == 3

        # The oldest ones should be removed
        remaining_ids = [op["id"] for op in completed_ops]
        assert completed_ids[0] not in remaining_ids  # Oldest removed
        assert completed_ids[1] not in remaining_ids  # Second oldest removed
        assert completed_ids[4] in remaining_ids  # Most recent kept

    def test_thread_safety(self, fresh_tracker):
        """Test that tracker operations are thread-safe."""
        results = {"created": [], "completed": []}
        errors = []

        def create_and_complete():
            try:
                for i in range(10):
                    op_id = fresh_tracker.create_operation("thread_test", f"Thread op {i}")
                    results["created"].append(op_id)
                    fresh_tracker.start_operation(op_id)
                    fresh_tracker.update_progress(op_id, 50, "Half done")
                    fresh_tracker.complete_operation(op_id)
                    results["completed"].append(op_id)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=create_and_complete) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results["created"]) == 40
        assert len(results["completed"]) == 40


class TestGetTracker:
    """Test the get_tracker helper function."""

    def test_returns_tracker_instance(self):
        """Test that get_tracker returns an OperationTracker."""
        from backend.operation_status import OperationTracker, get_tracker

        tracker = get_tracker()

        assert isinstance(tracker, OperationTracker)

    def test_returns_singleton(self):
        """Test that get_tracker returns the same instance."""
        from backend.operation_status import get_tracker

        tracker1 = get_tracker()
        tracker2 = get_tracker()

        assert tracker1 is tracker2


class TestCreateProgressCallback:
    """Test the create_progress_callback helper function."""

    def test_creates_callable(self, fresh_tracker):
        """Test that create_progress_callback returns a callable."""
        from backend.operation_status import create_progress_callback

        op_id = fresh_tracker.create_operation("test", "Test")
        fresh_tracker.start_operation(op_id)

        callback = create_progress_callback(op_id)

        assert callable(callback)

    def test_callback_updates_progress(self, fresh_tracker):
        """Test that callback updates operation progress."""
        from backend.operation_status import create_progress_callback

        op_id = fresh_tracker.create_operation("test", "Test")
        fresh_tracker.start_operation(op_id)

        callback = create_progress_callback(op_id)
        callback(50, 100, "Processing...")

        status = fresh_tracker.get_status(op_id)
        assert status["progress"] == 50
        assert status["message"] == "Processing..."

    def test_callback_calculates_percentage(self, fresh_tracker):
        """Test that callback calculates percentage from current/total."""
        from backend.operation_status import create_progress_callback

        op_id = fresh_tracker.create_operation("test", "Test")
        fresh_tracker.start_operation(op_id)

        callback = create_progress_callback(op_id)
        callback(25, 50, "Quarter done")  # 25/50 = 50%

        status = fresh_tracker.get_status(op_id)
        assert status["progress"] == 50

    def test_callback_handles_zero_total(self, fresh_tracker):
        """Test that callback handles total=0 by treating current as percentage."""
        from backend.operation_status import create_progress_callback

        op_id = fresh_tracker.create_operation("test", "Test")
        fresh_tracker.start_operation(op_id)

        callback = create_progress_callback(op_id)
        callback(75, 0, "Direct percentage")  # total=0, use current as %

        status = fresh_tracker.get_status(op_id)
        assert status["progress"] == 75


class TestOperationLifecycle:
    """Integration tests for complete operation lifecycle scenarios."""

    def test_successful_operation_lifecycle(self, fresh_tracker):
        """Test a complete successful operation from create to complete."""
        # Create
        op_id = fresh_tracker.create_operation("rescan", "Full library rescan")
        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "pending"

        # Start
        fresh_tracker.start_operation(op_id)
        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "running"
        assert status["started_at"] is not None

        # Progress updates
        fresh_tracker.update_progress(op_id, 25, "Scanning directory 1/4")
        fresh_tracker.update_progress(op_id, 50, "Scanning directory 2/4")
        fresh_tracker.update_progress(op_id, 75, "Scanning directory 3/4")
        fresh_tracker.update_progress(op_id, 100, "Finalizing")

        # Complete
        result = {"scanned": 1000, "added": 10, "updated": 5}
        fresh_tracker.complete_operation(op_id, result)

        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "completed"
        assert status["progress"] == 100
        assert status["result"]["scanned"] == 1000
        assert status["elapsed_seconds"] is not None

    def test_failed_operation_lifecycle(self, fresh_tracker):
        """Test an operation that fails during execution."""
        op_id = fresh_tracker.create_operation("import", "Import new audiobooks")
        fresh_tracker.start_operation(op_id)
        fresh_tracker.update_progress(op_id, 30, "Importing...")

        # Simulate failure
        fresh_tracker.fail_operation(op_id, "Permission denied: /mnt/audiobooks")

        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "failed"
        assert status["error"] == "Permission denied: /mnt/audiobooks"
        assert status["completed_at"] is not None

    def test_cancelled_operation_lifecycle(self, fresh_tracker):
        """Test an operation that gets cancelled by user."""
        op_id = fresh_tracker.create_operation("hash", "Generate file hashes")
        fresh_tracker.start_operation(op_id)
        fresh_tracker.update_progress(op_id, 10, "Computing hash 100/1000")

        # User cancels
        fresh_tracker.cancel_operation(op_id)

        status = fresh_tracker.get_status(op_id)
        assert status["state"] == "cancelled"
        assert status["message"] == "Cancelled"
