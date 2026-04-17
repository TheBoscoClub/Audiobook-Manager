"""Shared test helper functions and utilities."""

import time


def wait_for_thread_completion(tracker_mock, timeout=10.0):
    """Wait until tracker's complete_operation or fail_operation is called.

    Used by all utilities_ops extended test modules to wait for
    background thread completion without busy-waiting.

    Raises AssertionError if the thread does not complete within ``timeout``
    seconds. The previous silent-False return hid real thread races behind
    downstream ``call_args[0][1]`` AttributeError/TypeError failures — a
    symptom the /test phase-7 finding called out as flakiness in
    ``test_genre_sync_timeout`` and its narrator counterpart. Raising here
    surfaces the actual timeout, and the longer default window (10s, up
    from 2s) absorbs CPU-bound coverage-instrumented suites where the GIL
    can starve a daemon worker past the old threshold.

    Args:
        tracker_mock: The MagicMock tracker instance the worker writes to.
        timeout: Max seconds to wait for completion before failing.

    Returns:
        True on completion. Never returns False — raises instead so that
        the failure points at the actual thread-completion timeout rather
        than at a downstream None dereference.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tracker_mock.complete_operation.called or tracker_mock.fail_operation.called:
            return True
        time.sleep(0.02)
    raise AssertionError(
        "Background worker did not call complete_operation/fail_operation "
        f"within {timeout}s — thread is either stuck or running slower than "
        "expected under load. Increase timeout or inspect the worker."
    )
