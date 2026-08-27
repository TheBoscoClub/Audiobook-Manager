"""DeepL quota tracking + enforcement.

Backed by the ``deepl_quota`` table in the audiobooks SQLite DB. The
tracker is the single gatekeeper in front of the DeepL client: every
paid translation call must pass through :meth:`QuotaTracker.check_before_translate`
before the request is fired, and :meth:`QuotaTracker.record_usage` after
the response is parsed.

Two limits are enforced:

* **Soft limit (90%)** — logs a warning and records a flag on the row.
  Translation still proceeds.
* **Hard limit (99%)** — raises :class:`QuotaExceededError`. Callers are
  expected to fall back to pass-through English (source text returned
  verbatim) and surface a one-time admin notification.

Thread safety is provided by a module-level :class:`threading.Lock` —
multiple chapter translation workers can share a tracker without
corrupting the billed-chars counter.

The tracker does NOT re-derive the DB path from globals. It accepts an
explicit path so tests can point at a temp SQLite file.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# How stale the locally-tracked tally may get before it is reconciled with
# DeepL. Without reconciliation the row is a purely local count that drifts
# from the vendor's own counter (which resets each billing period), and
# check_before_translate() gates on a number nobody has verified
# (Audiobook-Manager-2s6).
REFRESH_INTERVAL_SECONDS = 3600
# Floor between *attempts*, so a failing usage endpoint is not retried on
# every translate call.
REFRESH_RETRY_SECONDS = 300

SOFT_LIMIT_PCT = 0.90
HARD_LIMIT_PCT = 0.99
USAGE_ENDPOINT = "/usage"


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict


class QuotaExceededError(RuntimeError):
    """Raised when a translation request would exceed the hard quota."""
mutants_xǁQuotaTrackerǁ__init____mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁ_connect__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁ_load_row__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁsnapshot__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁremaining_chars__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁrecord_usage__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁset_limit__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁset_glossary__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁget_glossary__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁ_raw_row__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁreset_period__mutmut: MutantDict = {}  # type: ignore
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut: MutantDict = {}  # type: ignore


class QuotaTracker:
    """DB-backed DeepL quota tracker.

    One instance per process is sufficient — state lives in SQLite, not
    in memory, so multiple instances across workers converge through
    the DB row. The in-process lock only serializes concurrent access
    from the same Python process.
    """

    _lock = threading.Lock()

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁ__init____mutmut)
    def __init__(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_orig(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_1(self, db_path: Path, api_key: str = "XXXX", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_2(self, db_path: Path, api_key: str = "", base_url: str = "XXXX") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_3(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = None
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_4(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(None)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_5(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = None
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_6(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 1.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_7(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = None
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_8(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = None
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_9(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip(None)
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_10(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.lstrip("/")
        self._ensure_schema()

    def xǁQuotaTrackerǁ__init____mutmut_11(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._last_refresh_attempt = 0.0
        self._api_key = api_key
        self._base_url = base_url.rstrip("XX/XX")
        self._ensure_schema()

    # -- schema bootstrap ------------------------------------------------

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut)
    def _ensure_schema(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_orig(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_1(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = None
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_2(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(None)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_3(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(None))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_4(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(None)
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_5(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute(None)
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_6(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("XXINSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')XX")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_7(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("insert or ignore into deepl_quota (id) values ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- schema bootstrap ------------------------------------------------

    def xǁQuotaTrackerǁ_ensure_schema__mutmut_8(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.execute("INSERT OR IGNORE INTO DEEPL_QUOTA (ID) VALUES ('DEFAULT')")
            conn.commit()
        finally:
            conn.close()

    # -- DB helpers ------------------------------------------------------

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁ_connect__mutmut)
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # -- DB helpers ------------------------------------------------------

    def xǁQuotaTrackerǁ_connect__mutmut_orig(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # -- DB helpers ------------------------------------------------------

    def xǁQuotaTrackerǁ_connect__mutmut_1(self) -> sqlite3.Connection:
        conn = None
        conn.row_factory = sqlite3.Row
        return conn

    # -- DB helpers ------------------------------------------------------

    def xǁQuotaTrackerǁ_connect__mutmut_2(self) -> sqlite3.Connection:
        conn = sqlite3.connect(None)
        conn.row_factory = sqlite3.Row
        return conn

    # -- DB helpers ------------------------------------------------------

    def xǁQuotaTrackerǁ_connect__mutmut_3(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(None))
        conn.row_factory = sqlite3.Row
        return conn

    # -- DB helpers ------------------------------------------------------

    def xǁQuotaTrackerǁ_connect__mutmut_4(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = None
        return conn

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁ_load_row__mutmut)
    def _load_row(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_orig(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_1(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = None
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_2(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute(None).fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_3(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("XXSELECT * FROM deepl_quota WHERE id = 'default'XX").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_4(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("select * from deepl_quota where id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_5(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM DEEPL_QUOTA WHERE ID = 'DEFAULT'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_6(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is not None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_7(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute(None)
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_8(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("XXINSERT INTO deepl_quota (id) VALUES ('default')XX")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_9(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("insert into deepl_quota (id) values ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_10(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO DEEPL_QUOTA (ID) VALUES ('DEFAULT')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_11(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = None
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_12(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute(None).fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_13(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("XXSELECT * FROM deepl_quota WHERE id = 'default'XX").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_14(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("select * from deepl_quota where id = 'default'").fetchone()
        return row

    def xǁQuotaTrackerǁ_load_row__mutmut_15(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM DEEPL_QUOTA WHERE ID = 'DEFAULT'").fetchone()
        return row

    # -- public API ------------------------------------------------------

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁsnapshot__mutmut)
    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_orig(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_1(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = None
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_2(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = None
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_3(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(None)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_4(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = None
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_5(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(None)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_6(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] and 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_7(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["XXchars_usedXX"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_8(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["CHARS_USED"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_9(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 1)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_10(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = None
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_11(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) and 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_12(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(None) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_13(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] and 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_14(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["XXchar_limitXX"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_15(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["CHAR_LIMIT"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_16(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 1) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_17(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 2
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_18(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = None
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_19(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["XXperiod_startXX"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_20(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["PERIOD_START"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_21(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = None
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_22(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(None)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_23(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "XXusedXX": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_24(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "USED": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_25(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "XXlimitXX": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_26(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "LIMIT": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_27(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "XXpercentXX": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_28(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "PERCENT": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_29(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(None, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_30(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, None),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_31(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_32(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, ),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_33(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit / 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_34(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used * limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_35(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 101.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_36(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 3),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_37(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "XXremainingXX": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_38(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "REMAINING": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_39(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(None, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_40(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, None),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_41(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_42(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, ),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_43(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit + used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_44(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 1),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_45(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "XXperiod_startXX": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_46(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "PERIOD_START": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_47(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "XXreset_dateXX": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_48(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "RESET_DATE": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_49(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "XXlast_api_checkXX": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_50(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "LAST_API_CHECK": row["last_api_check"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_51(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["XXlast_api_checkXX"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_52(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["LAST_API_CHECK"],
            "glossary_id": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_53(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "XXglossary_idXX": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_54(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "GLOSSARY_ID": row["glossary_id"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_55(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["XXglossary_idXX"],
        }

    # -- public API ------------------------------------------------------

    def xǁQuotaTrackerǁsnapshot__mutmut_56(self) -> dict[str, Any]:
        """Return a JSON-ready view of the current quota row."""
        with self._lock:
            conn = self._connect()
            try:
                row = self._load_row(conn)
            finally:
                conn.close()

        used = int(row["chars_used"] or 0)
        limit = int(row["char_limit"] or 0) or 1
        period_start = row["period_start"]
        reset_date = _compute_reset_date(period_start)
        return {
            "used": used,
            "limit": limit,
            "percent": round(used / limit * 100.0, 2),
            "remaining": max(limit - used, 0),
            "period_start": period_start,
            "reset_date": reset_date,
            "last_api_check": row["last_api_check"],
            "glossary_id": row["GLOSSARY_ID"],
        }

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁremaining_chars__mutmut)
    def remaining_chars(self) -> int:
        snap = self.snapshot()
        return int(snap["remaining"])

    def xǁQuotaTrackerǁremaining_chars__mutmut_orig(self) -> int:
        snap = self.snapshot()
        return int(snap["remaining"])

    def xǁQuotaTrackerǁremaining_chars__mutmut_1(self) -> int:
        snap = None
        return int(snap["remaining"])

    def xǁQuotaTrackerǁremaining_chars__mutmut_2(self) -> int:
        snap = self.snapshot()
        return int(None)

    def xǁQuotaTrackerǁremaining_chars__mutmut_3(self) -> int:
        snap = self.snapshot()
        return int(snap["XXremainingXX"])

    def xǁQuotaTrackerǁremaining_chars__mutmut_4(self) -> int:
        snap = self.snapshot()
        return int(snap["REMAINING"])

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut)
    def _reconcile_if_stale(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_orig(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_1(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key and not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_2(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_3(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_4(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = None
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_5(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt or now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_6(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now + self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_7(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt <= REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_8(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = None
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_9(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = None
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_10(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["XXlast_api_checkXX"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_11(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["LAST_API_CHECK"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_12(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last or _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_13(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(None) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_14(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) <= REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_15(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = None
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_16(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                None,
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_17(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                None,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_18(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                None,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_19(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_20(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_21(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_22(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "XXDeepL usage reconcile failed (%s: %s) — continuing on the local XX"
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_23(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "deepl usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_24(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DEEPL USAGE RECONCILE FAILED (%S: %S) — CONTINUING ON THE LOCAL "
                "tally, which may have drifted from the vendor's counter",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_25(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "XXtally, which may have drifted from the vendor's counterXX",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_26(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "TALLY, WHICH MAY HAVE DRIFTED FROM THE VENDOR'S COUNTER",
                type(exc).__name__,
                exc,
            )

    def xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_27(self) -> None:
        """Reconcile the local tally with DeepL when it has gone stale.

        Called from :meth:`check_before_translate` — the gate whose decision
        depends on the numbers being real. Silent by design on failure: a
        usage-endpoint outage must not block translation, so the tracker
        carries on with the local tally and says so at WARNING.
        """
        if not self._api_key or not self._base_url:
            return
        now = time.monotonic()
        if self._last_refresh_attempt and now - self._last_refresh_attempt < REFRESH_RETRY_SECONDS:
            return

        row = self._raw_row()
        last = row["last_api_check"]
        if last and _age_seconds(last) < REFRESH_INTERVAL_SECONDS:
            return

        self._last_refresh_attempt = now
        try:
            self.refresh_from_api()
        except Exception as exc:  # noqa: BLE001 — never block a translate on this
            logger.warning(
                "DeepL usage reconcile failed (%s: %s) — continuing on the local "
                "tally, which may have drifted from the vendor's counter",
                type(None).__name__,
                exc,
            )

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut)
    def check_before_translate(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_orig(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_1(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count < 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_2(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 1:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_3(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = None
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_4(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = None
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_5(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] - char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_6(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["XXusedXX"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_7(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["USED"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_8(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = None
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_9(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["XXlimitXX"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_10(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["LIMIT"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_11(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected > limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_12(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit / HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_13(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                None
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_14(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(None)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_15(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit / HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_16(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected > limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_17(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit / SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_18(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                None,
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_19(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                None,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_20(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                None,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_21(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                None,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_22(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_23(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_24(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_25(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_26(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "XXDeepL quota soft-limit breach: %d/%d chars (%.1f%%)XX",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_27(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "deepl quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_28(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DEEPL QUOTA SOFT-LIMIT BREACH: %D/%D CHARS (%.1F%%)",
                projected,
                limit,
                projected / limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_29(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit / 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_30(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected * limit * 100.0,
            )

    def xǁQuotaTrackerǁcheck_before_translate__mutmut_31(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
        self._reconcile_if_stale()
        snap = self.snapshot()
        projected = snap["used"] + char_count
        limit = snap["limit"]
        if projected >= limit * HARD_LIMIT_PCT:
            raise QuotaExceededError(
                f"DeepL quota would be exceeded: {projected}/{limit} chars "
                f"(hard limit {int(limit * HARD_LIMIT_PCT)})"
            )
        if projected >= limit * SOFT_LIMIT_PCT:
            logger.warning(
                "DeepL quota soft-limit breach: %d/%d chars (%.1f%%)",
                projected,
                limit,
                projected / limit * 101.0,
            )

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁrecord_usage__mutmut)
    def record_usage(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_orig(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_1(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count < 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_2(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 1:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_3(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = None
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_4(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    None,
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_5(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    None,
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_6(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_7(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_8(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "XXUPDATE deepl_quota XX"
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_9(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "update deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_10(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE DEEPL_QUOTA "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_11(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "XXSET chars_used = chars_used + ?, XX"
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_12(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "set chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_13(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET CHARS_USED = CHARS_USED + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_14(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "XX    updated_at = CURRENT_TIMESTAMP XX"
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_15(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = current_timestamp "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_16(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    UPDATED_AT = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_17(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "XXWHERE id = 'default'XX",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_18(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "where id = 'default'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_19(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE ID = 'DEFAULT'",
                    (int(char_count),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁrecord_usage__mutmut_20(self, char_count: int) -> None:
        """Add ``char_count`` characters to the billed tally."""
        if char_count <= 0:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota "
                    "SET chars_used = chars_used + ?, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 'default'",
                    (int(None),),
                )
                conn.commit()
            finally:
                conn.close()

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁset_limit__mutmut)
    def set_limit(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_orig(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_1(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit < 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_2(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 1:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_3(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError(None)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_4(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("XXlimit must be positiveXX")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_5(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("LIMIT MUST BE POSITIVE")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_6(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = None
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_7(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    None,
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_8(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    None,
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_9(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_10(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_11(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "XXUPDATE deepl_quota SET char_limit = ?, XX"
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_12(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "update deepl_quota set char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_13(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE DEEPL_QUOTA SET CHAR_LIMIT = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_14(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "XXupdated_at = CURRENT_TIMESTAMP WHERE id = 'default'XX",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_15(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = current_timestamp where id = 'default'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_16(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "UPDATED_AT = CURRENT_TIMESTAMP WHERE ID = 'DEFAULT'",
                    (int(new_limit),),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_limit__mutmut_17(self, new_limit: int) -> None:
        """Adjust the character limit (e.g., paid tier upgrade)."""
        if new_limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET char_limit = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (int(None),),
                )
                conn.commit()
            finally:
                conn.close()

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁset_glossary__mutmut)
    def set_glossary(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_orig(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_1(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = None
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_2(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    None,
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_3(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    None,
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_4(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_5(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_6(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "XXUPDATE deepl_quota SET glossary_id = ?, XX"
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_7(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "update deepl_quota set glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_8(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE DEEPL_QUOTA SET GLOSSARY_ID = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_9(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "XXglossary_source_hash = ?, XX"
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_10(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "GLOSSARY_SOURCE_HASH = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_11(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "XXupdated_at = CURRENT_TIMESTAMP WHERE id = 'default'XX",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_12(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "updated_at = current_timestamp where id = 'default'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁset_glossary__mutmut_13(self, glossary_id: str, source_hash: str) -> None:
        """Persist the glossary ID + source hash after a successful build."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET glossary_id = ?, "
                    "glossary_source_hash = ?, "
                    "UPDATED_AT = CURRENT_TIMESTAMP WHERE ID = 'DEFAULT'",
                    (glossary_id, source_hash),
                )
                conn.commit()
            finally:
                conn.close()

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁget_glossary__mutmut)
    def get_glossary(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["glossary_id"], snap_row["glossary_source_hash"]

    def xǁQuotaTrackerǁget_glossary__mutmut_orig(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["glossary_id"], snap_row["glossary_source_hash"]

    def xǁQuotaTrackerǁget_glossary__mutmut_1(self) -> tuple[str | None, str | None]:
        snap_row = None
        return snap_row["glossary_id"], snap_row["glossary_source_hash"]

    def xǁQuotaTrackerǁget_glossary__mutmut_2(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["XXglossary_idXX"], snap_row["glossary_source_hash"]

    def xǁQuotaTrackerǁget_glossary__mutmut_3(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["GLOSSARY_ID"], snap_row["glossary_source_hash"]

    def xǁQuotaTrackerǁget_glossary__mutmut_4(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["glossary_id"], snap_row["XXglossary_source_hashXX"]

    def xǁQuotaTrackerǁget_glossary__mutmut_5(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["glossary_id"], snap_row["GLOSSARY_SOURCE_HASH"]

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁ_raw_row__mutmut)
    def _raw_row(self) -> sqlite3.Row:
        with self._lock:
            conn = self._connect()
            try:
                return self._load_row(conn)
            finally:
                conn.close()

    def xǁQuotaTrackerǁ_raw_row__mutmut_orig(self) -> sqlite3.Row:
        with self._lock:
            conn = self._connect()
            try:
                return self._load_row(conn)
            finally:
                conn.close()

    def xǁQuotaTrackerǁ_raw_row__mutmut_1(self) -> sqlite3.Row:
        with self._lock:
            conn = None
            try:
                return self._load_row(conn)
            finally:
                conn.close()

    def xǁQuotaTrackerǁ_raw_row__mutmut_2(self) -> sqlite3.Row:
        with self._lock:
            conn = self._connect()
            try:
                return self._load_row(None)
            finally:
                conn.close()

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁreset_period__mutmut)
    def reset_period(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_orig(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_1(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = None
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_2(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    None
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_3(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "XXUPDATE deepl_quota SET chars_used = 0, XX"
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_4(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "update deepl_quota set chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_5(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE DEEPL_QUOTA SET CHARS_USED = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_6(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "XXperiod_start = CURRENT_TIMESTAMP, XX"
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_7(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = current_timestamp, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_8(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "PERIOD_START = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_9(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "XXupdated_at = CURRENT_TIMESTAMP WHERE id = 'default'XX"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_10(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "updated_at = current_timestamp where id = 'default'"
                )
                conn.commit()
            finally:
                conn.close()

    def xǁQuotaTrackerǁreset_period__mutmut_11(self) -> None:
        """Reset the counter at the start of a new billing month."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = 0, "
                    "period_start = CURRENT_TIMESTAMP, "
                    "UPDATED_AT = CURRENT_TIMESTAMP WHERE ID = 'DEFAULT'"
                )
                conn.commit()
            finally:
                conn.close()

    # -- optional live sync with DeepL /usage ---------------------------

    @_mutmut_mutated(mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut)
    def refresh_from_api(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_orig(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_1(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key and not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_2(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_3(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_4(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError(None)
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_5(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("XXQuotaTracker has no API credentials configuredXX")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_6(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("quotatracker has no api credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_7(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QUOTATRACKER HAS NO API CREDENTIALS CONFIGURED")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_8(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = None
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_9(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            None,
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_10(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers=None,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_11(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=None,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_12(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_13(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_14(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_15(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"XXAuthorizationXX": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_16(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_17(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"AUTHORIZATION": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_18(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=16,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_19(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = None
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_20(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() and {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_21(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = None
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_22(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(None)
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_23(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get(None, 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_24(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", None))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_25(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get(0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_26(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", ))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_27(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("XXcharacter_countXX", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_28(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("CHARACTER_COUNT", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_29(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 1))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_30(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = None
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_31(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(None)
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_32(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get(None, 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_33(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", None))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_34(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get(0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_35(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", ))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_36(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("XXcharacter_limitXX", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_37(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("CHARACTER_LIMIT", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_38(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 1))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_39(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = None
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_40(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit >= 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_41(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 1 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_42(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1000000000001
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_43(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = None
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_44(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    None,
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_45(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    None,
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_46(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_47(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_48(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "XXUPDATE deepl_quota SET chars_used = ?, XX"
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_49(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "update deepl_quota set chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_50(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE DEEPL_QUOTA SET CHARS_USED = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_51(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "XXchar_limit = ?, last_api_check = CURRENT_TIMESTAMP, XX"
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_52(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = current_timestamp, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_53(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "CHAR_LIMIT = ?, LAST_API_CHECK = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_54(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "XXupdated_at = CURRENT_TIMESTAMP WHERE id = 'default'XX",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_55(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "updated_at = current_timestamp where id = 'default'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

    # -- optional live sync with DeepL /usage ---------------------------

    def xǁQuotaTrackerǁrefresh_from_api__mutmut_56(self) -> dict[str, Any]:
        """Pull the authoritative usage figure from DeepL.

        Requires the tracker to have been constructed with a non-empty
        ``api_key`` and ``base_url``. Returns the raw DeepL response so
        the caller can surface it (e.g., for admin endpoints).
        """
        if not self._api_key or not self._base_url:
            raise RuntimeError("QuotaTracker has no API credentials configured")
        resp = requests.get(
            f"{self._base_url}{USAGE_ENDPOINT}",
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        used = int(payload.get("character_count", 0))
        raw_limit = int(payload.get("character_limit", 0))
        # DeepL Pro returns character_limit=0 meaning unlimited.
        # Use a very high sentinel so quota checks never block.
        limit = raw_limit if raw_limit > 0 else 1_000_000_000_000
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE deepl_quota SET chars_used = ?, "
                    "char_limit = ?, last_api_check = CURRENT_TIMESTAMP, "
                    "UPDATED_AT = CURRENT_TIMESTAMP WHERE ID = 'DEFAULT'",
                    (used, limit),
                )
                conn.commit()
            finally:
                conn.close()
        return payload

mutants_xǁQuotaTrackerǁ__init____mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ__init____mutmut['xǁQuotaTrackerǁ__init____mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁ__init____mutmut_11 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_ensure_schema__mutmut['xǁQuotaTrackerǁ_ensure_schema__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁ_ensure_schema__mutmut_8 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁ_connect__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁ_connect__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_connect__mutmut['xǁQuotaTrackerǁ_connect__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁ_connect__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_connect__mutmut['xǁQuotaTrackerǁ_connect__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁ_connect__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_connect__mutmut['xǁQuotaTrackerǁ_connect__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁ_connect__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_connect__mutmut['xǁQuotaTrackerǁ_connect__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁ_connect__mutmut_4 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁ_load_row__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_load_row__mutmut['xǁQuotaTrackerǁ_load_row__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁ_load_row__mutmut_15 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁsnapshot__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_15 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_16'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_16 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_17'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_17 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_18'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_18 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_19'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_19 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_20'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_20 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_21'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_21 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_22'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_22 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_23'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_23 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_24'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_24 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_25'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_25 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_26'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_26 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_27'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_27 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_28'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_28 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_29'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_29 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_30'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_30 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_31'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_31 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_32'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_32 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_33'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_33 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_34'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_34 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_35'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_35 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_36'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_36 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_37'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_37 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_38'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_38 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_39'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_39 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_40'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_40 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_41'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_41 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_42'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_42 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_43'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_43 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_44'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_44 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_45'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_45 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_46'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_46 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_47'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_47 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_48'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_48 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_49'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_49 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_50'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_50 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_51'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_51 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_52'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_52 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_53'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_53 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_54'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_54 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_55'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_55 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁsnapshot__mutmut['xǁQuotaTrackerǁsnapshot__mutmut_56'] = QuotaTracker.xǁQuotaTrackerǁsnapshot__mutmut_56 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁremaining_chars__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁremaining_chars__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁremaining_chars__mutmut['xǁQuotaTrackerǁremaining_chars__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁremaining_chars__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁremaining_chars__mutmut['xǁQuotaTrackerǁremaining_chars__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁremaining_chars__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁremaining_chars__mutmut['xǁQuotaTrackerǁremaining_chars__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁremaining_chars__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁremaining_chars__mutmut['xǁQuotaTrackerǁremaining_chars__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁremaining_chars__mutmut_4 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_15 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_16'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_16 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_17'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_17 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_18'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_18 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_19'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_19 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_20'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_20 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_21'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_21 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_22'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_22 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_23'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_23 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_24'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_24 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_25'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_25 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_26'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_26 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_reconcile_if_stale__mutmut['xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_27'] = QuotaTracker.xǁQuotaTrackerǁ_reconcile_if_stale__mutmut_27 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_15 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_16'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_16 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_17'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_17 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_18'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_18 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_19'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_19 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_20'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_20 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_21'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_21 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_22'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_22 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_23'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_23 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_24'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_24 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_25'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_25 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_26'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_26 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_27'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_27 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_28'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_28 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_29'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_29 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_30'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_30 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁcheck_before_translate__mutmut['xǁQuotaTrackerǁcheck_before_translate__mutmut_31'] = QuotaTracker.xǁQuotaTrackerǁcheck_before_translate__mutmut_31 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁrecord_usage__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_15 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_16'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_16 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_17'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_17 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_18'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_18 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_19'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_19 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrecord_usage__mutmut['xǁQuotaTrackerǁrecord_usage__mutmut_20'] = QuotaTracker.xǁQuotaTrackerǁrecord_usage__mutmut_20 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁset_limit__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_15 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_16'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_16 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_limit__mutmut['xǁQuotaTrackerǁset_limit__mutmut_17'] = QuotaTracker.xǁQuotaTrackerǁset_limit__mutmut_17 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁset_glossary__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁset_glossary__mutmut['xǁQuotaTrackerǁset_glossary__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁset_glossary__mutmut_13 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁget_glossary__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁget_glossary__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁget_glossary__mutmut['xǁQuotaTrackerǁget_glossary__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁget_glossary__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁget_glossary__mutmut['xǁQuotaTrackerǁget_glossary__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁget_glossary__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁget_glossary__mutmut['xǁQuotaTrackerǁget_glossary__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁget_glossary__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁget_glossary__mutmut['xǁQuotaTrackerǁget_glossary__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁget_glossary__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁget_glossary__mutmut['xǁQuotaTrackerǁget_glossary__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁget_glossary__mutmut_5 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁ_raw_row__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁ_raw_row__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_raw_row__mutmut['xǁQuotaTrackerǁ_raw_row__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁ_raw_row__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁ_raw_row__mutmut['xǁQuotaTrackerǁ_raw_row__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁ_raw_row__mutmut_2 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁreset_period__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁreset_period__mutmut['xǁQuotaTrackerǁreset_period__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁreset_period__mutmut_11 # type: ignore # mutmut generated

mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['_mutmut_orig'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_orig # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_1'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_1 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_2'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_2 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_3'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_3 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_4'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_4 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_5'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_5 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_6'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_6 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_7'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_7 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_8'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_8 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_9'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_9 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_10'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_10 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_11'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_11 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_12'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_12 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_13'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_13 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_14'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_14 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_15'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_15 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_16'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_16 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_17'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_17 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_18'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_18 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_19'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_19 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_20'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_20 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_21'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_21 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_22'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_22 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_23'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_23 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_24'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_24 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_25'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_25 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_26'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_26 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_27'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_27 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_28'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_28 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_29'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_29 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_30'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_30 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_31'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_31 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_32'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_32 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_33'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_33 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_34'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_34 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_35'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_35 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_36'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_36 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_37'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_37 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_38'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_38 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_39'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_39 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_40'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_40 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_41'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_41 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_42'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_42 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_43'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_43 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_44'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_44 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_45'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_45 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_46'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_46 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_47'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_47 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_48'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_48 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_49'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_49 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_50'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_50 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_51'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_51 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_52'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_52 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_53'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_53 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_54'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_54 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_55'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_55 # type: ignore # mutmut generated
mutants_xǁQuotaTrackerǁrefresh_from_api__mutmut['xǁQuotaTrackerǁrefresh_from_api__mutmut_56'] = QuotaTracker.xǁQuotaTrackerǁrefresh_from_api__mutmut_56 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__age_seconds__mutmut)
def _age_seconds(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_orig(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_1(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = None
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_2(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=None)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_3(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(None, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_4(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), None).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_5(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime("%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_6(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), ).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_7(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(None).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_8(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "XX%Y-%m-%d %H:%M:%SXX").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_9(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%y-%m-%d %h:%m:%s").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_10(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%M-%D %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_11(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float(None)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_12(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("XXinfXX")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_13(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("INF")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def x__age_seconds__mutmut_14(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(timezone.utc) + dt).total_seconds()


def x__age_seconds__mutmut_15(stamp: str) -> float:
    """Seconds since a SQLite CURRENT_TIMESTAMP value (UTC, no tz suffix).

    Returns infinity for anything unparseable, so a malformed timestamp is
    treated as stale rather than as fresh — the safe direction.
    """
    try:
        dt = datetime.strptime(str(stamp).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.now(None) - dt).total_seconds()

mutants_x__age_seconds__mutmut['_mutmut_orig'] = x__age_seconds__mutmut_orig # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_1'] = x__age_seconds__mutmut_1 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_2'] = x__age_seconds__mutmut_2 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_3'] = x__age_seconds__mutmut_3 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_4'] = x__age_seconds__mutmut_4 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_5'] = x__age_seconds__mutmut_5 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_6'] = x__age_seconds__mutmut_6 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_7'] = x__age_seconds__mutmut_7 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_8'] = x__age_seconds__mutmut_8 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_9'] = x__age_seconds__mutmut_9 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_10'] = x__age_seconds__mutmut_10 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_11'] = x__age_seconds__mutmut_11 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_12'] = x__age_seconds__mutmut_12 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_13'] = x__age_seconds__mutmut_13 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_14'] = x__age_seconds__mutmut_14 # type: ignore # mutmut generated
mutants_x__age_seconds__mutmut['x__age_seconds__mutmut_15'] = x__age_seconds__mutmut_15 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__compute_reset_date__mutmut)
def _compute_reset_date(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_orig(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_1(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_2(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return "XXXX"
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_3(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = None
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_4(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(None)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_5(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace(None, "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_6(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", None))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_7(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_8(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", ))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_9(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("XXZXX", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_10(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_11(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "XX+00:00XX"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_12(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return "XXXX"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_13(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_14(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = None
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_15(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=None)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_16(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = None
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_17(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year - (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_18(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (2 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_19(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month != 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_20(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 13 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_21(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 1)
    month = 1 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_22(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = None
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_23(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 2 if dt.month == 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_24(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month != 12 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_25(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 13 else dt.month + 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_26(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month - 1
    return f"{year:04d}-{month:02d}-01"


def x__compute_reset_date__mutmut_27(period_start: str | None) -> str:
    """Return ISO date of the next monthly reset for a DeepL period.

    DeepL's billing month is anchored on the day the subscription
    started, but for free accounts it is the first of each calendar
    month. We approximate by adding one month to ``period_start``.
    """
    if not period_start:
        return ""
    try:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Next calendar month, same day (clamped to month-end by dateutil-free math).
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 2
    return f"{year:04d}-{month:02d}-01"

mutants_x__compute_reset_date__mutmut['_mutmut_orig'] = x__compute_reset_date__mutmut_orig # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_1'] = x__compute_reset_date__mutmut_1 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_2'] = x__compute_reset_date__mutmut_2 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_3'] = x__compute_reset_date__mutmut_3 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_4'] = x__compute_reset_date__mutmut_4 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_5'] = x__compute_reset_date__mutmut_5 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_6'] = x__compute_reset_date__mutmut_6 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_7'] = x__compute_reset_date__mutmut_7 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_8'] = x__compute_reset_date__mutmut_8 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_9'] = x__compute_reset_date__mutmut_9 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_10'] = x__compute_reset_date__mutmut_10 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_11'] = x__compute_reset_date__mutmut_11 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_12'] = x__compute_reset_date__mutmut_12 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_13'] = x__compute_reset_date__mutmut_13 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_14'] = x__compute_reset_date__mutmut_14 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_15'] = x__compute_reset_date__mutmut_15 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_16'] = x__compute_reset_date__mutmut_16 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_17'] = x__compute_reset_date__mutmut_17 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_18'] = x__compute_reset_date__mutmut_18 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_19'] = x__compute_reset_date__mutmut_19 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_20'] = x__compute_reset_date__mutmut_20 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_21'] = x__compute_reset_date__mutmut_21 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_22'] = x__compute_reset_date__mutmut_22 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_23'] = x__compute_reset_date__mutmut_23 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_24'] = x__compute_reset_date__mutmut_24 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_25'] = x__compute_reset_date__mutmut_25 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_26'] = x__compute_reset_date__mutmut_26 # type: ignore # mutmut generated
mutants_x__compute_reset_date__mutmut['x__compute_reset_date__mutmut_27'] = x__compute_reset_date__mutmut_27 # type: ignore # mutmut generated
