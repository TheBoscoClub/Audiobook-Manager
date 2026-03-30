"""Shared test helper functions and utilities."""

import time


def wait_for_thread_completion(tracker_mock, timeout=2.0):
    """Wait until tracker's complete_operation or fail_operation is called.

    Used by all utilities_ops extended test modules to wait for
    background thread completion without busy-waiting.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tracker_mock.complete_operation.called or tracker_mock.fail_operation.called:
            return True
        time.sleep(0.02)
    return False
