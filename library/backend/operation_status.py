#!/usr/bin/env python3
"""
Operation Status Tracker
========================
Thread-safe tracking of long-running operations with progress reporting.

Provides a shared status store that can be updated from background threads
and polled from the API endpoints.
"""

import threading
from datetime import datetime
from enum import Enum
from typing import Callable, Optional
from uuid import uuid4


class OperationState(str, Enum):
    """Operation states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationStatus:
    """Represents the status of a single operation."""

    def __init__(self, operation_id: str, operation_type: str, description: str):
        self.id = operation_id
        self.type = operation_type
        self.description = description
        self.state = OperationState.PENDING
        self.progress = 0  # 0-100
        self.message = "Initializing..."
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "state": self.state.value,
            "progress": self.progress,
            "message": self.message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "elapsed_seconds": self._elapsed_seconds(),
            "result": self.result,
            "error": self.error,
        }

    def _elapsed_seconds(self) -> Optional[float]:
        """Calculate elapsed time in seconds."""
        if not self.started_at:
            return None
        end_time = self.completed_at or datetime.now()
        return (end_time - self.started_at).total_seconds()


class OperationTracker:
    """
    Thread-safe operation status tracker.

    Singleton pattern - use get_tracker() to get the global instance.
    """

    _instance: Optional["OperationTracker"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize the tracker."""
        self._operations: dict[str, OperationStatus] = {}
        self._op_lock = threading.Lock()
        self._max_history = 50  # Keep last N completed operations

    def create_operation(self, operation_type: str, description: str) -> str:
        """
        Create a new operation and return its ID.

        Args:
            operation_type: Type identifier (e.g., "rescan", "add_new", "hash")
            description: Human-readable description

        Returns:
            Unique operation ID
        """
        operation_id = str(uuid4())[:8]

        with self._op_lock:
            status = OperationStatus(operation_id, operation_type, description)
            self._operations[operation_id] = status
            self._cleanup_old_operations()

        return operation_id

    def start_operation(self, operation_id: str) -> bool:
        """Mark operation as started."""
        with self._op_lock:
            if operation_id not in self._operations:
                return False
            op = self._operations[operation_id]
            op.state = OperationState.RUNNING
            op.started_at = datetime.now()
            op.message = "Starting..."
            return True

    def update_progress(self, operation_id: str, progress: int, message: str) -> bool:
        """
        Update operation progress.

        Args:
            operation_id: Operation ID
            progress: Progress percentage (0-100)
            message: Current status message
        """
        with self._op_lock:
            if operation_id not in self._operations:
                return False
            op = self._operations[operation_id]
            op.progress = max(0, min(100, progress))
            op.message = message
            return True

    def complete_operation(
        self, operation_id: str, result: Optional[dict] = None
    ) -> bool:
        """Mark operation as completed successfully."""
        with self._op_lock:
            if operation_id not in self._operations:
                return False
            op = self._operations[operation_id]
            op.state = OperationState.COMPLETED
            op.progress = 100
            op.completed_at = datetime.now()
            op.result = result
            op.message = "Completed"
            return True

    def fail_operation(self, operation_id: str, error: str) -> bool:
        """Mark operation as failed."""
        with self._op_lock:
            if operation_id not in self._operations:
                return False
            op = self._operations[operation_id]
            op.state = OperationState.FAILED
            op.completed_at = datetime.now()
            op.error = error
            op.message = f"Failed: {error}"
            return True

    def cancel_operation(self, operation_id: str) -> bool:
        """Mark operation as cancelled."""
        with self._op_lock:
            if operation_id not in self._operations:
                return False
            op = self._operations[operation_id]
            op.state = OperationState.CANCELLED
            op.completed_at = datetime.now()
            op.message = "Cancelled"
            return True

    def get_status(self, operation_id: str) -> Optional[dict]:
        """Get operation status as dict."""
        with self._op_lock:
            if operation_id not in self._operations:
                return None
            return self._operations[operation_id].to_dict()

    def get_operation(self, operation_id: str) -> Optional[OperationStatus]:
        """Get operation status object."""
        with self._op_lock:
            return self._operations.get(operation_id)

    def get_active_operations(self) -> list[dict]:
        """Get all active (pending or running) operations."""
        with self._op_lock:
            return [
                op.to_dict()
                for op in self._operations.values()
                if op.state in (OperationState.PENDING, OperationState.RUNNING)
            ]

    def get_all_operations(self) -> list[dict]:
        """Get all operations."""
        with self._op_lock:
            return [op.to_dict() for op in self._operations.values()]

    def is_operation_running(self, operation_type: str) -> Optional[str]:
        """
        Check if an operation of given type is already running.

        Returns operation_id if running, None otherwise.
        """
        with self._op_lock:
            for op in self._operations.values():
                if op.type == operation_type and op.state == OperationState.RUNNING:
                    return op.id
            return None

    def _cleanup_old_operations(self):
        """Remove old completed operations to prevent memory growth."""
        completed = [
            (op.completed_at, op_id)
            for op_id, op in self._operations.items()
            if op.state
            in (
                OperationState.COMPLETED,
                OperationState.FAILED,
                OperationState.CANCELLED,
            )
            and op.completed_at is not None
        ]

        if len(completed) > self._max_history:
            # Sort by completion time, oldest first
            completed.sort(key=lambda x: x[0])
            to_remove = len(completed) - self._max_history

            for _, op_id in completed[:to_remove]:
                del self._operations[op_id]


def get_tracker() -> OperationTracker:
    """Get the global operation tracker instance."""
    return OperationTracker()


def create_progress_callback(operation_id: str) -> Callable[[int, int, str], None]:
    """
    Create a progress callback function for use with long-running operations.

    Returns a callback(current, total, message) that updates the operation status.
    """
    tracker = get_tracker()

    def callback(current: int, total: int, message: str):
        if total > 0:
            progress = int((current / total) * 100)
        else:
            progress = current  # Assume current is already percentage
        tracker.update_progress(operation_id, progress, message)

    return callback
