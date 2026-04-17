"""Regression guard: prevent `library/None` stray SQLite artifact.

A prior bug in `localization/queue._get_db()` called `sqlite3.connect(str(_db_path))`
without verifying `_db_path` was initialized. When `_db_path is None`, `str(None)`
evaluates to the literal string ``"None"`` and SQLite happily creates a database
file at that path. When `enqueue_book_all_locales()` was invoked from the
`_run_post_insert_hooks` path during test runs, the cwd was `library/`, so a
stray empty `library/None` SQLite file appeared.

The fix raises ``RuntimeError`` in `_get_db()` when `_db_path` is ``None``. This
test ensures the artifact never reappears silently — if a future refactor
reintroduces an unguarded ``sqlite3.connect(None)`` anywhere whose cwd is
``library/``, this test fails and pinpoints the regression.
"""

from __future__ import annotations

import pathlib


def test_no_stray_none_file_in_library() -> None:
    """Assert that `library/None` does not exist.

    Runs at collection time with every pytest invocation. If this fails, a test
    or production call is invoking `sqlite3.connect(None)` (or similar) with cwd
    set to the ``library/`` directory. See ``localization/queue._get_db()`` for
    the original fix template.
    """
    library_dir = pathlib.Path(__file__).resolve().parent.parent
    stray = library_dir / "None"
    assert not stray.exists(), (
        f"Stray `None` file found at {stray} — a test or production code path is "
        "calling sqlite3.connect(None) (or str(None) → 'None'). Add a guard in the "
        "offending function, similar to localization.queue._get_db()."
    )
