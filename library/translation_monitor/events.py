"""Audit-trail writer for the translation monitor.

Every action either monitor takes — a claim reset, a retry-exhausted
mark-as-failed, a spend-cap pause — is logged to the
``translation_monitor_events`` table via :func:`log_event`. The audit trail
is the only way to distinguish "the worker handled it" from "the monitor
forced a reset" when post-mortem-ing a stalled book.

The writer is best-effort: if the DB is locked or the table is missing
(pre-migration), the call returns without raising. A monitor pass should
never fail because of an audit-log write.

Type taxonomy mirrors the docstring in
``library/backend/migrations/025_translation_monitor_events.sql``. Keep
event names short and predicate-style (claim_reset, retry_exceeded, …).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# Allowed event_type values. Extend as new monitor behaviours are added.
# Tests assert against this set so a typo in the script side fails loudly
# rather than silently writing a never-queried event_type.
ALLOWED_EVENT_TYPES = frozenset(
    {
        "claim_reset",
        "retry_exceeded",
        "sampler_job_failed",
        "sampler_job_reset",
        "spend_pause_book",
        "spend_pause_global",
        "live_age_alert",
        "capacity_warning",
        "gpu_probe_failed",
    }
)

ALLOWED_MONITORS = frozenset({"live", "sampler"})


# pylint: disable=too-many-arguments
# Audit-trail signature is intentional — every column is named for clarity.
def log_event(
    conn: sqlite3.Connection,
    *,
    monitor: str,
    event_type: str,
    audiobook_id: int | None = None,
    segment_id: int | None = None,
    sampler_job_id: int | None = None,
    worker_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int | None:
    """Append one row to ``translation_monitor_events``.

    Returns the new row ID, or ``None`` if the write was skipped (table
    missing) or failed (DB locked, table dropped mid-flight, etc.).

    The caller is responsible for committing — :func:`log_event` issues a
    commit at the end so the event is durable even if the script crashes
    before its own commit.
    """
    if monitor not in ALLOWED_MONITORS:
        raise ValueError(f"unknown monitor: {monitor!r}")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"unknown event_type: {event_type!r}")

    payload = json.dumps(details, sort_keys=True) if details else None
    try:
        cur = conn.execute(
            "INSERT INTO translation_monitor_events "
            "(monitor, event_type, audiobook_id, segment_id, "
            " sampler_job_id, worker_id, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (monitor, event_type, audiobook_id, segment_id, sampler_job_id, worker_id, payload),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.OperationalError as exc:
        # Table may not exist on a pre-migration host. Don't crash the
        # monitor — the next migration pass will create it, and meanwhile
        # the reset itself still happened (it's the source-of-truth row,
        # not the audit row).
        logger.warning("translation_monitor_events write skipped: %s", exc)
        return None


def recent_events(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    monitor: str | None = None,
    event_type: str | None = None,
) -> list[sqlite3.Row]:
    """Read the most recent N events, optionally filtered by tier and type.

    Used by the operator CLI (``audiobook-translations monitor-events``) and
    by tests to verify monitor passes wrote what they were supposed to.
    """
    sql = "SELECT * FROM translation_monitor_events"
    where: list[str] = []
    params: list[Any] = []
    if monitor is not None:
        where.append("monitor = ?")
        params.append(monitor)
    if event_type is not None:
        where.append("event_type = ?")
        params.append(event_type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    return list(conn.execute(sql, params).fetchall())
