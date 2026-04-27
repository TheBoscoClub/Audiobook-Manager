"""Detector + reset primitives for the two-tier translation monitor.

Each function in this module is a single, idempotent SQL operation that:
    1. Queries for rows matching a "stuck" predicate
    2. Resets the offending state (clears claim, marks failed, etc.)
    3. Writes one audit-trail row per affected entity via
       :func:`library.translation_monitor.events.log_event`
    4. Returns the list of affected IDs so the caller can log a summary

The functions are pure with respect to wall-clock time — they take a
``now_ts`` argument (default: ``CURRENT_TIMESTAMP`` via SQLite). Tests
inject deterministic timestamps via the parameter; production callers
let SQLite resolve it.

Idempotency contract: running these functions twice in a row, with no
intervening worker activity, MUST produce zero events on the second
run. This is what makes the 30s/5min cadence safe.

Thresholds live as module constants so the tests can monkey-patch them
without editing config. They are deliberately conservative — the
GPU-burn cost of a false negative (stuck claim survives one tick) is
ten seconds of inference; the cost of a false positive (we reset a
row a healthy worker was about to commit) is at most one wasted
inference. The asymmetry favours aggressive resets.
"""

from __future__ import annotations

import logging
import sqlite3

from translation_monitor.events import log_event

logger = logging.getLogger(__name__)

# ─── Thresholds ────────────────────────────────────────────────────────────

# A live segment claim that has been held >60s with no progress is stuck.
# The longest legitimate live segment processing time depends on the configured
# STT/translation/TTS backends; in typical configurations end-to-end is well
# under 60s. The 60s default leaves headroom to avoid resetting a healthy
# slow worker while keeping the stuck-claim window small enough that a tick
# budget isn't wasted. Operators with much slower or much faster pipelines
# can override via /etc/audiobooks/audiobooks.conf (key not yet plumbed —
# edit this constant directly until then).
LIVE_CLAIM_TIMEOUT_SEC = 60

# Sampler/backlog claims have a much longer ceiling. Sampler segments are
# 30s of audio each but a cold-start GPU may take 90s to warm up before the
# first segment completes. 2 hours is the wall-clock budget after which we
# unconditionally consider the claim dead and recyclable.
SAMPLER_CLAIM_TIMEOUT_SEC = 2 * 3600

# A sampler_job in status='running' that hasn't been touched in 2h is
# almost certainly orphaned (worker crashed, instance terminated, etc.).
# Reset to pending so the next sampler-daemon pass picks it up.
SAMPLER_JOB_RUNNING_TIMEOUT_SEC = 2 * 3600

# Hard cap on retries. Matches the worker's own retry policy
# (scripts/stream-translate-worker.py retry budget) — we don't want to
# fight the worker; we want to catch rows the worker has already given up
# on but never marked as failed because it crashed mid-handler.
RETRY_CAP = 3

# A live segment that has been pending/processing/claimed for more than this
# many seconds is past the latency point a human listener would tolerate.
# Distinct from LIVE_CLAIM_TIMEOUT_SEC (which catches *worker-stuck* claims
# at 60s): LIVE_AGE_ALERT_SEC catches *queue-depth* problems where a healthy
# worker is just busy with prior work. The alert is informational only —
# it logs an event for operator awareness, doesn't reset anything.
LIVE_AGE_ALERT_SEC = 120

# Live queue is "under pressure" when pending live segments exceed this
# count. Combined with the worker-saturation check, a pending count above
# this floor signals that current capacity isn't keeping up with intake.
LIVE_PENDING_PRESSURE_THRESHOLD = 50

# Capacity-warning idempotency window. Prevents log-spam: at most one
# capacity_warning event per this many seconds, even if the condition
# persists across many monitor ticks.
CAPACITY_WARNING_COOLDOWN_SEC = 300


# ─── Live-tier detectors ──────────────────────────────────────────────────


def reset_stuck_live_claims(
    conn: sqlite3.Connection,
    *,
    timeout_sec: int = LIVE_CLAIM_TIMEOUT_SEC,
) -> list[int]:
    """Reset live segments whose worker claim is older than ``timeout_sec``.

    A "claim" is recognised by ``state IN ('processing','claimed')`` AND
    a non-NULL ``started_at`` AND ``state != 'completed'``. The reset
    clears ``worker_id``, ``started_at``, and sets state back to 'pending'
    so the next worker poll picks the row up. ``retry_count`` is left
    untouched — the segment will count its retry on next failure as
    normal.

    Returns the list of segment IDs that were reset, in DB order.
    """
    rows = conn.execute(
        "SELECT id, audiobook_id, worker_id, retry_count, started_at "
        "FROM streaming_segments "
        "WHERE origin = 'live' "
        "  AND state IN ('processing','claimed') "
        "  AND started_at IS NOT NULL "
        "  AND (strftime('%s','now') - strftime('%s', started_at)) >= ?",
        (timeout_sec,),
    ).fetchall()

    affected: list[int] = []
    for row in rows:
        seg_id = row["id"]
        conn.execute(
            "UPDATE streaming_segments "
            "SET state='pending', worker_id=NULL, started_at=NULL "
            "WHERE id = ? AND state IN ('processing','claimed')",
            (seg_id,),
        )
        log_event(
            conn,
            monitor="live",
            event_type="claim_reset",
            audiobook_id=row["audiobook_id"],
            segment_id=seg_id,
            worker_id=row["worker_id"],
            details={
                "timeout_sec": timeout_sec,
                "retry_count": row["retry_count"],
                "started_at": row["started_at"],
            },
        )
        affected.append(seg_id)
    conn.commit()
    return affected


# ─── Sampler-tier detectors ───────────────────────────────────────────────


def reset_stuck_sampler_claims(
    conn: sqlite3.Connection,
    *,
    timeout_sec: int = SAMPLER_CLAIM_TIMEOUT_SEC,
) -> list[int]:
    """Reset sampler/backlog segments whose claim has aged past ``timeout_sec``.

    Same shape as :func:`reset_stuck_live_claims` but targets
    ``origin IN ('sampler','backlog')`` and uses a much larger timeout.
    The trigger that forbids ``origin='sampler' AND priority<2`` is
    respected because the reset preserves the existing priority.
    """
    rows = conn.execute(
        "SELECT id, audiobook_id, worker_id, retry_count, started_at, origin "
        "FROM streaming_segments "
        "WHERE origin IN ('sampler','backlog') "
        "  AND state IN ('processing','claimed') "
        "  AND started_at IS NOT NULL "
        "  AND (strftime('%s','now') - strftime('%s', started_at)) >= ?",
        (timeout_sec,),
    ).fetchall()

    affected: list[int] = []
    for row in rows:
        seg_id = row["id"]
        conn.execute(
            "UPDATE streaming_segments "
            "SET state='pending', worker_id=NULL, started_at=NULL "
            "WHERE id = ? AND state IN ('processing','claimed')",
            (seg_id,),
        )
        log_event(
            conn,
            monitor="sampler",
            event_type="claim_reset",
            audiobook_id=row["audiobook_id"],
            segment_id=seg_id,
            worker_id=row["worker_id"],
            details={
                "timeout_sec": timeout_sec,
                "retry_count": row["retry_count"],
                "started_at": row["started_at"],
                "origin": row["origin"],
            },
        )
        affected.append(seg_id)
    conn.commit()
    return affected


def reset_stuck_sampler_jobs(
    conn: sqlite3.Connection,
    *,
    timeout_sec: int = SAMPLER_JOB_RUNNING_TIMEOUT_SEC,
) -> list[int]:
    """Reset sampler_jobs stuck in status='running' past ``timeout_sec``.

    A sampler_job tracks the per-(book, locale) progress through the
    6-min pretranslation. If it sits in ``running`` longer than the
    ceiling, the worker has died without committing the final
    transition — flip back to ``pending`` so the next sampler-daemon
    sweep picks it up. ``segments_done`` is preserved.
    """
    rows = conn.execute(
        "SELECT id, audiobook_id, locale, segments_done, segments_target, updated_at "
        "FROM sampler_jobs "
        "WHERE status = 'running' "
        "  AND (strftime('%s','now') - strftime('%s', updated_at)) >= ?",
        (timeout_sec,),
    ).fetchall()

    affected: list[int] = []
    for row in rows:
        job_id = row["id"]
        conn.execute(
            "UPDATE sampler_jobs "
            "SET status='pending', updated_at=CURRENT_TIMESTAMP "
            "WHERE id = ? AND status='running'",
            (job_id,),
        )
        log_event(
            conn,
            monitor="sampler",
            event_type="sampler_job_reset",
            audiobook_id=row["audiobook_id"],
            sampler_job_id=job_id,
            details={
                "timeout_sec": timeout_sec,
                "locale": row["locale"],
                "segments_done": row["segments_done"],
                "segments_target": row["segments_target"],
                "previous_updated_at": row["updated_at"],
            },
        )
        affected.append(job_id)
    conn.commit()
    return affected


# ─── Retry-budget detector (both tiers) ───────────────────────────────────


def sweep_retry_exhausted_segments(
    conn: sqlite3.Connection,
    *,
    monitor: str,
    origins: tuple[str, ...],
    retry_cap: int = RETRY_CAP,
) -> list[int]:
    """Mark segments past the retry cap as state='failed'.

    Targets segments where ``retry_count >= retry_cap`` AND
    ``state != 'failed'`` AND ``state != 'completed'`` AND
    ``origin IN origins``. Writes a ``retry_exceeded`` event per row.

    This is the second-line defence: the worker itself is supposed to
    mark a segment failed on its 4th attempt, but if the worker crashed
    during the handler the row may sit in ``state='processing'`` with
    ``retry_count >= 3`` and never advance. Sweeping here unblocks the
    queue.

    Args:
        monitor: 'live' or 'sampler' — which tier is performing the sweep.
        origins: tuple of origin values to consider (e.g. ('live',) or
                 ('sampler','backlog')).
        retry_cap: threshold for retry_count.
    """
    placeholders = ",".join("?" for _ in origins)
    sql = (
        "SELECT id, audiobook_id, worker_id, retry_count, origin, error "
        "FROM streaming_segments "
        f"WHERE origin IN ({placeholders}) "
        "  AND retry_count >= ? "
        "  AND state NOT IN ('failed','completed')"
    )
    params: list[object] = list(origins) + [retry_cap]
    rows = conn.execute(sql, params).fetchall()

    affected: list[int] = []
    for row in rows:
        seg_id = row["id"]
        conn.execute(
            "UPDATE streaming_segments "
            "SET state='failed', "
            "    error = COALESCE(error, 'retry budget exhausted (monitor)') "
            "WHERE id = ? AND state NOT IN ('failed','completed')",
            (seg_id,),
        )
        log_event(
            conn,
            monitor=monitor,
            event_type="retry_exceeded",
            audiobook_id=row["audiobook_id"],
            segment_id=seg_id,
            worker_id=row["worker_id"],
            details={
                "retry_count": row["retry_count"],
                "retry_cap": retry_cap,
                "origin": row["origin"],
                "previous_error": row["error"],
            },
        )
        affected.append(seg_id)
    conn.commit()
    return affected


# ─── Live-tier observers (informational, no state mutation) ──────────────


def alert_old_live_segments(
    conn: sqlite3.Connection,
    *,
    age_threshold_sec: int = LIVE_AGE_ALERT_SEC,
    cooldown_sec: int = 3600,
) -> list[int]:
    """Log ``live_age_alert`` events for live segments past the latency
    threshold that a human listener would tolerate.

    Distinct from :func:`reset_stuck_live_claims`: that one is for
    worker-stuck claims (>60s with no progress, reset and recycle). This
    one catches *queue-depth* problems — a segment is sitting in pending
    or processing for >2min because the queue is backed up, not because
    the worker died. We don't reset; we log so operators can see the
    pattern and decide whether to scale up worker capacity.

    Idempotent over ``cooldown_sec``: each segment gets at most one alert
    per cooldown window, regardless of how many monitor ticks observe it
    in the over-age state.

    Returns the list of segment IDs that triggered new alerts.
    """
    rows = conn.execute(
        """
        SELECT s.id, s.audiobook_id, s.state, s.worker_id, s.created_at,
               s.started_at,
               (julianday('now') - julianday(s.created_at)) * 86400 AS age_sec
        FROM streaming_segments s
        WHERE s.origin = 'live'
          AND s.state IN ('pending','processing','claimed')
          AND (julianday('now') - julianday(s.created_at)) * 86400 > ?
          AND NOT EXISTS (
              SELECT 1 FROM translation_monitor_events e
              WHERE e.segment_id = s.id
                AND e.event_type = 'live_age_alert'
                AND (julianday('now') - julianday(e.created_at)) * 86400 < ?
          )
        ORDER BY s.created_at ASC
        """,
        (age_threshold_sec, cooldown_sec),
    ).fetchall()

    alerted: list[int] = []
    for row in rows:
        seg_id = row["id"]
        log_event(
            conn,
            monitor="live",
            event_type="live_age_alert",
            audiobook_id=row["audiobook_id"],
            segment_id=seg_id,
            worker_id=row["worker_id"],
            details={
                "state": row["state"],
                "age_sec": round(row["age_sec"], 1),
                "threshold_sec": age_threshold_sec,
            },
        )
        alerted.append(seg_id)
    return alerted


def alert_capacity_pressure(
    conn: sqlite3.Connection,
    *,
    pending_threshold: int = LIVE_PENDING_PRESSURE_THRESHOLD,
    cooldown_sec: int = CAPACITY_WARNING_COOLDOWN_SEC,
) -> int | None:
    """Log a ``capacity_warning`` event when the live queue is backing up.

    Heuristic: pending live segments exceed ``pending_threshold`` AND at
    least one worker is currently active. Two-condition check ensures we
    don't false-fire when the system is fully idle (a large pending count
    with no active workers is "the worker just hasn't started yet" — a
    different problem).

    Idempotent over ``cooldown_sec``: at most one capacity_warning per
    cooldown window, regardless of monitor tick frequency. This prevents
    log-spam during persistent capacity pressure.

    Returns the new event row ID (or ``None`` if no event was logged
    because conditions weren't met or the cooldown was active).
    """
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM streaming_segments "
        "WHERE origin = 'live' AND state = 'pending'"
    ).fetchone()[0]

    if pending_count <= pending_threshold:
        return None

    active_workers = conn.execute(
        "SELECT COUNT(DISTINCT worker_id) FROM streaming_segments "
        "WHERE origin = 'live' "
        "  AND state IN ('processing','claimed') "
        "  AND worker_id IS NOT NULL"
    ).fetchone()[0]

    if active_workers == 0:
        # Queue is full but no workers are processing — that's a "worker
        # absent" failure, not a capacity-pressure failure. Different
        # alert path (which a future release will add). Don't fire here.
        return None

    # Cooldown gate
    recent = conn.execute(
        "SELECT 1 FROM translation_monitor_events "
        "WHERE event_type = 'capacity_warning' "
        "  AND (julianday('now') - julianday(created_at)) * 86400 < ? "
        "LIMIT 1",
        (cooldown_sec,),
    ).fetchone()
    if recent is not None:
        return None

    return log_event(
        conn,
        monitor="live",
        event_type="capacity_warning",
        details={
            "pending_count": pending_count,
            "active_workers": active_workers,
            "pending_threshold": pending_threshold,
        },
    )


# ─── GPU instance health probe (stub) ─────────────────────────────────────


def probe_gpu_instance_health(
    conn: sqlite3.Connection,
    *,
    monitor: str = "live",
) -> dict[str, object]:
    """Probe the configured inference backend(s) for instance health.

    A future release will integrate with whichever inference backend(s) the
    operator has configured for STT/translation/TTS work, surfacing instance
    status in a provider-agnostic combined dict:

        {
          "providers": {
            "<provider-key>": {"healthy": True, "running": 1, "endpoint": "..."},
            ...
          },
          "any_healthy": True,
        }

    Multiple providers are supported because some installations run more than
    one inference backend in parallel for redundancy.

    For now this is a no-op stub returning a structurally-correct empty
    payload so the monitor scripts can call it without branching. When the
    real probe lands, the existing call sites in
    ``scripts/translation-monitor-{live,sampler}.py`` continue working
    unchanged — they only inspect ``any_healthy``.

    The ``monitor`` and ``conn`` arguments are accepted so the eventual
    implementation can write ``backend_probe_failed`` events without
    changing any call site.
    """
    _ = conn  # reserved for future event logging
    _ = monitor
    return {
        "providers": {},
        "any_healthy": True,  # optimistic until real probe lands
        "stub": True,
    }
