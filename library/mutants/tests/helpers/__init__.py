"""Shared test helper functions and utilities."""

import time


def wait_for_thread_completion(tracker_mock, timeout=10.0, expect=None):
    """Wait until the worker thread finishes and assert which method it called.

    Used by all utilities_ops extended test modules to wait for background
    thread completion without busy-waiting. The helper previously returned
    True as soon as EITHER ``complete_operation`` OR ``fail_operation`` was
    called. That either-or semantic silently hid an entire class of race —
    if the worker hit an unexpected failure path under load and called
    ``fail_operation``, the test would proceed to its assertion on
    ``complete_operation.call_args[0][1]``, which evaluates ``None`` and
    crashes with a misleading ``TypeError: 'NoneType' object is not
    subscriptable`` that gives the maintainer no signal about WHICH method
    the worker actually invoked.

    The ``expect`` parameter closes that race:

    * ``expect="complete"`` — wait for ``complete_operation`` specifically.
      If ``fail_operation`` fires first (or concurrently), raise
      ``AssertionError`` naming the actual call so the maintainer knows the
      worker failed rather than the assertion being broken.
    * ``expect="fail"`` — mirror of the above, for tests that expect the
      worker to call ``fail_operation``.
    * ``expect=None`` (default) — legacy either-or behavior; preserves
      backwards compatibility for existing tests that don't care which
      method was called (e.g. tests asserting on ``update_progress`` or
      ``Popen.call_args``).

    Raises ``AssertionError`` in all failure modes. The longer default
    window (10s, up from 2s) absorbs CPU-bound coverage-instrumented suites
    where the GIL can starve a daemon worker past the old threshold.

    ``MagicMock._increment_mock_call`` sets ``.called = True`` OUTSIDE the
    internal lock, then assigns ``.call_args`` INSIDE the lock. Under
    concurrent load (daemon worker thread vs polling test thread with
    coverage instrumentation), the polling thread can observe
    ``.called is True`` while ``.call_args`` is still ``None`` — a genuine
    CPython stdlib race. Returning at that point causes the caller's
    ``mock.complete_operation.call_args[0][1]`` to crash with
    ``TypeError: 'NoneType' object is not subscriptable``. The fix: also
    require ``.call_args is not None`` before declaring completion, so the
    call is guaranteed fully committed by the time we return.

    Args:
        tracker_mock: The MagicMock tracker instance the worker writes to.
        timeout: Max seconds to wait for completion before failing.
        expect: Either ``"complete"``, ``"fail"``, or ``None``.

    Returns:
        True on completion.
    """
    if expect not in (None, "complete", "fail"):
        raise ValueError(f"expect must be None, 'complete', or 'fail'; got {expect!r}")

    def _fully_committed(mock_method):
        """Return True only when both .called AND .call_args are set.

        Guards against the MagicMock ordering race described above.
        """
        return mock_method.called and mock_method.call_args is not None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        complete_called = _fully_committed(tracker_mock.complete_operation)
        fail_called = _fully_committed(tracker_mock.fail_operation)

        if expect == "complete":
            if fail_called and not complete_called:
                raise AssertionError(
                    "Expected complete_operation but worker called fail_operation: "
                    f"{tracker_mock.fail_operation.call_args!r}"
                )
            if complete_called:
                return True
        elif expect == "fail":
            if complete_called and not fail_called:
                raise AssertionError(
                    "Expected fail_operation but worker called complete_operation: "
                    f"{tracker_mock.complete_operation.call_args!r}"
                )
            if fail_called:
                return True
        else:
            if complete_called or fail_called:
                return True
        time.sleep(0.02)

    raise AssertionError(
        "Background worker did not call complete_operation/fail_operation "
        f"within {timeout}s — thread is either stuck or running slower than "
        "expected under load. Increase timeout or inspect the worker."
    )
