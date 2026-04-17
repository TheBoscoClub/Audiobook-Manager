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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

SOFT_LIMIT_PCT = 0.90
HARD_LIMIT_PCT = 0.99
USAGE_ENDPOINT = "/usage"


class QuotaExceededError(RuntimeError):
    """Raised when a translation request would exceed the hard quota."""


class QuotaTracker:
    """DB-backed DeepL quota tracker.

    One instance per process is sufficient — state lives in SQLite, not
    in memory, so multiple instances across workers converge through
    the DB row. The in-process lock only serializes concurrent access
    from the same Python process.
    """

    _lock = threading.Lock()

    def __init__(self, db_path: Path, api_key: str = "", base_url: str = "") -> None:
        self._db_path = Path(db_path)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ensure_schema()

    # -- schema bootstrap ------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create the quota table if the migration has not been applied.

        The production path runs the SQL migration at startup; this is a
        belt-and-suspenders safety net so tests against a fresh SQLite
        file work without explicitly invoking the migration runner.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS deepl_quota (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    chars_used INTEGER NOT NULL DEFAULT 0,
                    char_limit INTEGER NOT NULL DEFAULT 1000000000000,
                    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_api_check TIMESTAMP,
                    glossary_id TEXT,
                    glossary_source_hash TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
        finally:
            conn.close()

    # -- DB helpers ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _load_row(self, conn: sqlite3.Connection) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        if row is None:
            conn.execute("INSERT INTO deepl_quota (id) VALUES ('default')")
            conn.commit()
            row = conn.execute("SELECT * FROM deepl_quota WHERE id = 'default'").fetchone()
        return row

    # -- public API ------------------------------------------------------

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

    def remaining_chars(self) -> int:
        snap = self.snapshot()
        return int(snap["remaining"])

    def check_before_translate(self, char_count: int) -> None:
        """Block the caller if this request would blow the hard limit."""
        if char_count <= 0:
            return
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

    def get_glossary(self) -> tuple[str | None, str | None]:
        snap_row = self._raw_row()
        return snap_row["glossary_id"], snap_row["glossary_source_hash"]

    def _raw_row(self) -> sqlite3.Row:
        with self._lock:
            conn = self._connect()
            try:
                return self._load_row(conn)
            finally:
                conn.close()

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

    # -- optional live sync with DeepL /usage ---------------------------

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
