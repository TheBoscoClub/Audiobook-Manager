"""6-minute pretranslation sampler — shared core logic.

The sampler pre-translates the opening of each book per enabled non-EN locale
so that:

1. Non-EN listeners can browse the library and preview any book in their
   language without waiting for GPU cold-start.
2. Live playback has runway (the sample continues playing while the live
   streaming worker catches up after the user commits).
3. Only books users actually listen past the sample incur full-book
   translation cost.

This module contains the pure helpers callable from anywhere:
- ``compute_sampler_range(chapter_durations_sec)`` — deterministic scope.
- ``enqueue_sampler(conn, audiobook_id, locale, chapter_durations_sec)`` —
  creates the sampler_jobs row + pending streaming_segments rows.

API-module wrapper lives in ``library/backend/api_modular/streaming_translate.py``
(``_enqueue_sampler``), which resolves chapter durations via the live
``_resolve_chapters`` memo. Scanner wrapper lives in
``library/scanner/utils/sampler_hook.py`` which resolves durations from
``extract_chapters`` directly.

Rule (confirmed with the user 2026-04-23):

- Sample AT LEAST 6 min of audio (SAMPLER_MIN_SECONDS).
- If we reach the 6-min mark inside a chapter that ends within 3 min of that
  mark (SAMPLER_MAX_EXTEND_SECONDS), take the full chapter for cohesion.
- If we reach 6 min in a long chapter (remainder > 3 min), hard-stop at
  exactly 6 min.
- If the whole book is shorter than 6 min, sample the whole book.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from datetime import UTC, datetime
from typing import Sequence

logger = logging.getLogger(__name__)

# Log-injection defense: strip CR/LF/null bytes and other control chars from
# values that may have originated in untrusted input (e.g. locale from an
# admin API body). Mirrors _safe_log_value in the streaming_translate API
# module. Kept private to the sampler module to avoid a cross-package import.
_LOG_SCRUB_RE = re.compile(r"[\r\n\t\x00-\x1f\x7f]")


def _safe_log(value) -> str:
    """Sanitize a value for safe inclusion in log messages."""
    s = str(value) if value is not None else ""
    s = _LOG_SCRUB_RE.sub("_", s)
    if len(s) > 200:
        s = s[:200] + "...(truncated)"
    return s


# Constants — kept in sync with streaming_translate.py. A test pins that
# both modules agree on these values.
SEGMENT_DURATION_SEC = 30
SAMPLER_MIN_SECONDS = 360
SAMPLER_MAX_EXTEND_SECONDS = 180
SAMPLER_PRIORITY = 2


def compute_sampler_range(chapter_durations_sec: Sequence[float]) -> list[tuple[int, int]]:
    """Return the sampler scope as ``[(chapter_index, segment_count), ...]``.

    Empty list if no chapters have positive duration. See module docstring
    for the rule.
    """
    if not chapter_durations_sec:
        return []

    min_sec = SAMPLER_MIN_SECONDS
    max_extend = SAMPLER_MAX_EXTEND_SECONDS
    seg = SEGMENT_DURATION_SEC

    accumulated = 0.0
    chosen: list[tuple[int, float]] = []
    for idx, dur in enumerate(chapter_durations_sec):
        if dur is None or dur <= 0:
            continue
        chosen.append((idx, float(dur)))
        accumulated += float(dur)
        if accumulated >= min_sec:
            break

    if not chosen:
        return []

    if accumulated < min_sec:
        # Short book — sample all of it.
        return [(idx, math.ceil(dur / seg)) for idx, dur in chosen]

    # We hit 6 min in the last chosen chapter. Decide: extend or hard-stop?
    *earlier, (last_idx, last_dur) = chosen
    earlier_total = sum(d for _, d in earlier)
    needed_from_last = min_sec - earlier_total
    remainder_in_last = last_dur - needed_from_last

    if remainder_in_last <= max_extend:
        last_take = last_dur
    else:
        last_take = needed_from_last

    result = [(idx, math.ceil(dur / seg)) for idx, dur in earlier]
    result.append((last_idx, math.ceil(last_take / seg)))
    return result


def enqueue_sampler(
    conn: sqlite3.Connection, audiobook_id: int, locale: str, chapter_durations_sec: Sequence[float]
) -> dict:
    """Create sampler_jobs + pending streaming_segments for (book, locale).

    Idempotent:
    - Skips immediately if locale is en* (source locale — nothing to translate).
    - Returns the existing row without side effects if a prior complete job exists.
    - Resets pending/failed jobs and re-enqueues (admin retry path).

    Relies on the DB trigger ``streaming_segments_sampler_priority_ins`` to
    enforce the priority floor (inserts with ``origin='sampler' priority<2`` are
    ABORTed by the engine regardless of what this function does).
    """
    if locale.lower().startswith("en"):
        return {
            "status": "skipped",
            "reason": "source locale (no translation needed)",
            "audiobook_id": audiobook_id,
            "locale": locale,
        }

    existing = conn.execute(
        "SELECT id, status, segments_target, segments_done FROM sampler_jobs "
        "WHERE audiobook_id = ? AND locale = ?",
        (audiobook_id, locale),
    ).fetchone()

    # sqlite3.Row and dict both support subscript — normalize access.
    def _g(row, key):
        return row[key] if hasattr(row, "keys") else row[list(row.keys()).index(key)]

    if existing is not None:
        status = existing["status"]
        if status == "complete":
            return {
                "id": existing["id"],
                "audiobook_id": audiobook_id,
                "locale": locale,
                "status": "complete",
                "segments_target": existing["segments_target"],
                "segments_done": existing["segments_done"],
                "reason": "already complete",
            }

    scope = compute_sampler_range(chapter_durations_sec)
    if not scope:
        return {
            "status": "error",
            "reason": "empty sampler scope (no chapters with positive duration)",
            "audiobook_id": audiobook_id,
            "locale": locale,
        }

    segments_target = sum(seg_count for _, seg_count in scope)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    if existing is not None:
        conn.execute(
            "UPDATE sampler_jobs SET status = 'pending', segments_target = ?, "
            "segments_done = 0, error = NULL, updated_at = ? WHERE id = ?",
            (segments_target, now, existing["id"]),
        )
        job_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO sampler_jobs (audiobook_id, locale, status, segments_target, "
            "segments_done, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, 0, ?, ?)",
            (audiobook_id, locale, segments_target, now, now),
        )
        job_id = cur.lastrowid

    for ch_idx, seg_count in scope:
        for seg_idx in range(seg_count):
            conn.execute(
                "INSERT OR IGNORE INTO streaming_segments "
                "(audiobook_id, chapter_index, segment_index, locale, state, "
                "priority, origin) "
                "VALUES (?, ?, ?, ?, 'pending', ?, 'sampler')",
                (audiobook_id, ch_idx, seg_idx, locale, SAMPLER_PRIORITY),
            )

    conn.execute(
        "UPDATE sampler_jobs SET status = 'running', updated_at = ? WHERE id = ?", (now, job_id)
    )
    conn.commit()

    logger.info(
        "sampler enqueued: book=%d locale=%s scope=%s segments_target=%d",
        int(audiobook_id),
        _safe_log(locale),
        _safe_log(scope),
        segments_target,
    )

    return {
        "id": job_id,
        "audiobook_id": audiobook_id,
        "locale": locale,
        "status": "running",
        "segments_target": segments_target,
        "segments_done": 0,
        "scope": [{"chapter": ch, "segments": segs} for ch, segs in scope],
    }
