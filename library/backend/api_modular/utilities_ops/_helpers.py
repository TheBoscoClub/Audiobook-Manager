"""
Shared helpers for async operation endpoints.

Provides the boilerplate that every async endpoint repeats:
conflict checking, operation creation, thread management, result handling.
"""

import threading

from flask import jsonify
from operation_status import get_tracker

from ..core import FlaskResponse


def run_async_operation(
    operation_type: str, description: str, conflict_error: str, success_message: str, work_fn
) -> FlaskResponse:
    """Run a background operation with standard boilerplate.

    Checks for conflicts, creates the operation, starts a daemon thread,
    and returns the appropriate Flask response.

    Args:
        operation_type: Tracker key (e.g. "rescan", "hash").
        description: Human-readable operation description for the tracker.
        conflict_error: Error message if the operation is already running.
        success_message: Message returned in the success JSON response.
        work_fn: Callable(tracker, operation_id) executed in a background
            thread.  Must call tracker.complete_operation() or
            tracker.fail_operation() before returning.  Uncaught exceptions
            are automatically caught and reported as failures.

    Returns:
        Flask JSON response — 200 on success, 409 if already running.
    """
    tracker = get_tracker()

    existing = tracker.is_operation_running(operation_type)
    if existing:
        return (jsonify({"success": False, "error": conflict_error, "operation_id": existing}), 409)

    operation_id = tracker.create_operation(operation_type, description)

    def _thread_target():
        tracker.start_operation(operation_id)
        try:
            work_fn(tracker, operation_id)
        except Exception as e:
            tracker.fail_operation(operation_id, str(e))

    thread = threading.Thread(target=_thread_target, daemon=True)
    thread.start()

    return jsonify({"success": True, "message": success_message, "operation_id": operation_id})


def handle_result(tracker, operation_id, result, success_data, fallback_error):
    """Handle the standard run_with_progress result dict.

    Maps the three outcomes (timeout, success, failure) to the appropriate
    tracker calls.  Pairs with ``run_with_progress`` from ``_subprocess``.

    Args:
        tracker: OperationTracker instance.
        operation_id: Current operation ID.
        result: Dict returned by ``run_with_progress``.
        success_data: Dict passed to ``tracker.complete_operation`` on success.
        fallback_error: Error message used when result has no error string.
    """
    if result["timed_out"]:
        tracker.fail_operation(operation_id, result["error"])
    elif result["success"]:
        tracker.complete_operation(operation_id, success_data)
    else:
        tracker.fail_operation(operation_id, result["error"] or fallback_error)
