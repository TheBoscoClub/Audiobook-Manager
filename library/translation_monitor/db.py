"""DB connection helpers for the translation monitor.

The monitor scripts run as the audiobooks system user under a systemd timer.
They open a short-lived sqlite3 connection, perform a single pass of
detection + reset, then exit. This module centralises path resolution and
PRAGMA tuning so both scripts stay symmetric.

Path resolution priority:
    1. Explicit ``db_path`` argument (used by tests)
    2. ``AUDIOBOOKS_DATABASE`` environment variable (set by systemd unit)
    3. Canonical default from :mod:`library.config` (``AUDIOBOOKS_DATABASE``)

Connection PRAGMAs:
    - ``foreign_keys = ON`` (enforce CASCADE on audiobook delete)
    - ``busy_timeout = 5000`` (5s — survives a brief writer contention with
      the live worker without blocking the timer-driven monitor)
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator


def _canonical_default_db() -> str:
    """Resolve the canonical DB path via the config module.

    Importing :mod:`library.config` lazily avoids pulling Flask/SQLite
    schema initialisation into the monitor's import path. If the import
    fails (e.g. monitor running before deps are wired), we fall back to
    the environment variable directly — never embed a literal path here
    (project rule, see ``rules/paths-and-separation.md``).
    """
    fallback = os.environ.get("AUDIOBOOKS_DATABASE", "")
    try:
        # pylint: disable=import-outside-toplevel
        from config import AUDIOBOOKS_DATABASE  # type: ignore[import-not-found]

        return str(AUDIOBOOKS_DATABASE)
    except (ImportError, AttributeError):  # fmt: skip
        return fallback


def resolve_db_path(db_path: str | os.PathLike[str] | None = None) -> str:
    """Return the canonical DB path for this environment.

    Args:
        db_path: optional override (tests pass a tmp_path DB).

    Returns:
        Absolute string path to the audiobooks SQLite DB.
    """
    if db_path is not None:
        return str(db_path)
    env_path = os.environ.get("AUDIOBOOKS_DATABASE")
    if env_path:
        return env_path
    return _canonical_default_db()


def connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """Open a sqlite3 connection with monitor-friendly PRAGMAs.

    The caller MUST close the connection. Prefer :func:`connection` below.

    .. warning::
       ``with connect(...) as conn:`` does **not** close anything.
       ``sqlite3.Connection.__exit__`` commits or rolls back the transaction
       and leaves the connection open — a long-standing trap, and this
       docstring used to recommend exactly that ("use ``with``"). Both
       monitor scripts followed the advice and leaked one connection per
       timer tick; the test suite reported them as
       ``ResourceWarning: unclosed database``.

    Returns rows as :class:`sqlite3.Row` for column-name access.
    """
    path = resolve_db_path(db_path)
    # detect_types=0 — we treat timestamps as strings; the queries cast
    # to julianday/strftime as needed. This avoids surprises with
    # non-ISO timestamps written by older codepaths.
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def connection(db_path: str | os.PathLike[str] | None = None) -> Iterator[sqlite3.Connection]:
    """Open a monitor connection and CLOSE it on exit.

    The safe counterpart to :func:`connect` — use this everywhere::

        with connection() as conn:
            ...

    Note this does not wrap the body in a transaction the way
    ``with sqlite3.connect(...)`` does; commit explicitly if you write.
    """
    with closing(connect(db_path)) as conn:
        yield conn


def schema_has_monitor_table(conn: sqlite3.Connection) -> bool:
    """Return True if the audit-trail table exists.

    Used by the monitor scripts to skip cleanly on pre-v8.3.9 databases
    where migration 025 hasn't run yet — better than crashing the timer.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='translation_monitor_events'"
    ).fetchone()
    return row is not None


def db_exists(db_path: str | os.PathLike[str] | None = None) -> bool:
    """Return True if the resolved DB file exists on disk.

    Used to short-circuit the monitor on hosts where the DB hasn't been
    initialised yet (pre-install, fresh container without volume mount).
    """
    return Path(resolve_db_path(db_path)).is_file()
