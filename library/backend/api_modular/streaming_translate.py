"""
Streaming translation API blueprint.

Provides real-time, on-demand translation triggered by playback.
The player requests translation for a chapter; the coordinator
dispatches chapter-level work to GPU workers and streams segment
completion events back via WebSocket.

Endpoints:
    POST /api/translate/stream           — request streaming translation for a book
    GET  /api/translate/segments/<id>/<ch>/<locale> — segment bitmap for a chapter
    GET  /api/translate/session/<id>/<locale>       — current streaming session state
    POST /api/translate/warmup           — pre-warm GPU on app open
    POST /api/translate/seek             — handle seek to uncached position
    POST /api/translate/stop             — stop streaming (demote all pending to back-fill)
"""

import json
import logging
import os
import re
import sqlite3
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, request, send_file
from i18n import SUPPORTED_LOCALES

from .auth import admin_or_localhost, guest_allowed, localhost_only
from .websocket import connection_manager

streaming_bp = Blueprint("streaming_translate", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None
# Root directory where per-segment WebM-Opus files are stored — set by
# `init_streaming_routes`. Task 10 concatenates per-segment files from here
# into chapter-level WebM-Opus consolidation output.
_streaming_audio_root: Path | None = None
# Root directory where consolidated per-chapter VTT files are written. Lives
# under AUDIOBOOKS_VAR_DIR (writable runtime state) — NOT under the install
# tree at /opt/audiobooks/library, which systemd mounts read-only via
# ProtectSystem=strict. Set by `init_streaming_routes`.
_streaming_subtitles_root: Path | None = None

# Per-locale default edge-tts voice. MUST be kept in sync with the worker's
# `_LOCALE_DEFAULT_VOICE` mapping in scripts/stream-translate-worker.py — the
# worker selects the voice at synthesis time, but the server records it on the
# consolidated chapter row. Inlined rather than imported because the worker
# lives at a hyphenated script path that is not a valid Python module name.
_LOCALE_DEFAULT_VOICE = {
    "zh-Hans": "zh-CN-XiaoxiaoNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "zh-Hant": "zh-TW-HsiaoChenNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
}


def _default_voice_for_locale(locale: str) -> str:
    """Map locale → edge-tts voice. Unknown → en-US fallback.

    Must match the worker's `_default_voice_for_locale` semantics.
    """
    return _LOCALE_DEFAULT_VOICE.get(locale, "en-US-AriaNeural")


def _probe_audio_duration(audio_path: Path) -> float | None:
    """Return the duration of an audio file in seconds, or None on error.

    Only probes paths that live inside the streaming audio root
    (py/path-injection mitigation — callers may pass paths derived from
    DB values that CodeQL considers tainted).
    """
    # Containment check: reject any path outside the streaming audio root.
    if _streaming_audio_root is not None:
        try:
            audio_path.resolve(strict=False).relative_to(
                _streaming_audio_root.resolve(strict=False)
            )
        except (ValueError, OSError):  # fmt: skip
            return None
    try:
        result = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
            [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):  # fmt: skip
        return None
    return None


SEGMENT_DURATION_SEC = 30
# Cursor buffer window: the number of segments at and ahead of the playback
# cursor that get P0 (highest) priority. 6 × 30s = 3 minutes.
BUFFER_AHEAD_SEGMENTS = 6
# Alias preserved for callers that reference the session-level "buffer_threshold"
# knob (web JS, schema default, broadcast payloads). The two values must stay
# equal — the web UI thresholds match the cursor-buffer semantic.
BUFFER_THRESHOLD = BUFFER_AHEAD_SEGMENTS

# 6-minute pretranslation sampler (v8.3.8). Per book × locale, translate the
# opening of the book so non-EN listeners can preview it without waiting for
# GPU cold-start, and so live playback has runway to catch up once the user
# commits. Rule (confirmed 2026-04-23):
#   - Sample AT LEAST 6 min (SAMPLER_MIN_SECONDS)
#   - If the chapter containing the 6-min mark ends within SAMPLER_MAX_EXTEND_SECONDS
#     of that mark, extend to the chapter boundary (cohesive sample)
#   - Otherwise, stop exactly at 6 min (never translate mid-scene in a long chapter)
# See _compute_sampler_range() for the concrete algorithm and traces.
SAMPLER_MIN_SECONDS = 360  # 6 min — minimum sample length
SAMPLER_MAX_EXTEND_SECONDS = 180  # 3 min — extend past 6 min to reach chapter boundary
# Sampler always runs at priority 2. Live work (current book) uses 0/1.
# Backlog / other bulk work uses 3. The DB-level trigger ABORTs any insert
# or update that would land a sampler row at priority <2.
SAMPLER_PRIORITY = 2

# Adaptive buffer-fill threshold: when a user plays past this many segments
# of the sample, fire the live-pipeline buffer fill so the STT pipeline has
# runway to catch up before the sample ends. Adaptive on STT provider warmth
# (aggregate across every configured provider — RunPod, Vast.ai, etc.):
#   - cold (no provider has ready workers): fire at segment 3 (more runway needed)
#   - warm (at least one provider has ready workers): fire at segment 4 (more cost-aware)
# See docs/SAMPLER.md for the cold-start runway math.
BUFFER_FILL_THRESHOLD_COLD = 3
BUFFER_FILL_THRESHOLD_WARM = 4

# Allowed locale patterns for path/log safety
_SAFE_LOCALE_RE = re.compile(r"^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$")

# Control character stripper for log messages (CRLF injection / log forging defense)
_LOG_SCRUB_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _safe_log_value(value) -> str:
    """Sanitize a value for safe inclusion in log messages.

    Strips CR, LF, null bytes, and other control characters that could be
    used for log forging (CRLF injection). Truncates overly long values.
    """
    s = str(value) if value is not None else ""
    s = _LOG_SCRUB_RE.sub("_", s)
    if len(s) > 200:
        s = s[:200] + "...(truncated)"
    return s


def _sanitize_locale(locale: str) -> str:
    """Validate locale string — reject path traversal and log injection."""
    if not isinstance(locale, str) or not _SAFE_LOCALE_RE.match(locale):
        raise ValueError(f"invalid locale: {locale!r}")
    return locale


def _safe_join_under(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and verify the resolved result is contained.

    Raises ``ValueError`` if the resulting path escapes ``base`` (py/path-injection
    mitigation — defense in depth when any component could be tainted, even after
    upstream validation). Each ``parts`` element is coerced to ``str`` and must not
    contain traversal sequences or nulls.
    """
    base_resolved = base.resolve(strict=False)
    for p in parts:
        s = str(p)
        if "\x00" in s:
            raise ValueError("null byte in path component")
    target = (base_resolved.joinpath(*(str(p) for p in parts))).resolve(strict=False)
    if not target.is_relative_to(base_resolved):
        raise ValueError(f"path traversal rejected: {parts!r}")
    return target


def _safe_subtitles_path(
    subtitles_root: Path, audiobook_id: int, chapter_index: int, locale: str
) -> Path:
    """Build a VTT subtitle path and confirm it is inside `subtitles_root`.

    `subtitles_root` is the runtime root for streaming-generated VTT files
    (defaults to ``${AUDIOBOOKS_VAR_DIR}/streaming-subtitles``). The resolved
    path is ``<subtitles_root>/<audiobook_id>/ch<NNN>.<locale>.vtt``.

    `audiobook_id` and `chapter_index` must be ints; `locale` must already
    have been validated by `_sanitize_locale`. This function raises
    `ValueError` if the resolved path escapes the subtitles root (defense
    in depth against path injection — CodeQL py/path-injection).
    """
    if not isinstance(audiobook_id, int) or audiobook_id < 0:
        raise ValueError(f"invalid audiobook_id: {audiobook_id!r}")
    if not isinstance(chapter_index, int) or chapter_index < 0:
        raise ValueError(f"invalid chapter_index: {chapter_index!r}")
    # Re-validate locale (belt-and-suspenders) to ensure no traversal chars
    _sanitize_locale(locale)

    root = subtitles_root.resolve()
    book_dir = (root / str(audiobook_id)).resolve()
    # Python 3.9+: Path.is_relative_to
    if not book_dir.is_relative_to(root):
        raise ValueError("resolved subtitles dir escapes subtitles root")

    vtt_path = (book_dir / f"ch{chapter_index:03d}.{locale}.vtt").resolve()
    if not vtt_path.is_relative_to(root):
        raise ValueError("resolved VTT path escapes subtitles root")
    return vtt_path


def _validate_audio_path(audio_path) -> Path | None:
    """Validate that an audio_path from a worker callback is within the streaming audio root.

    Returns the resolved Path on success, or None if the path is invalid or
    escapes the allowed root. Accepts None (no audio) without error — the
    caller is responsible for checking whether None was the original value.

    Defense in depth: the worker is trusted, but input validation at the HTTP
    boundary prevents a compromised worker from writing arbitrary paths to the
    DB (py/path-injection mitigation).
    """
    if audio_path is None:
        return None
    if _streaming_audio_root is None:
        # Root not yet configured; reject all paths.
        return None
    try:
        candidate = Path(audio_path)
        if not candidate.is_absolute():
            candidate = _streaming_audio_root / candidate
        resolved = candidate.resolve(strict=False)
        audio_root = _streaming_audio_root.resolve(strict=False)
        resolved.relative_to(audio_root)  # raises ValueError if outside
        return resolved
    except (ValueError, OSError):  # fmt: skip
        return None


def _get_db():
    """Get database connection for this request."""
    db = getattr(g, "_streaming_db", None)
    if db is None:
        db = sqlite3.connect(str(_db_path))
        db.row_factory = sqlite3.Row
        g._streaming_db = db
    return db


def _close_db(
    exc=None,
):  # pylint: disable=unused-argument  # required by Flask teardown_appcontext signature
    db = getattr(g, "_streaming_db", None)
    if db is not None:
        db.close()


def _has_cached_subtitles(db, audiobook_id: int, chapter_index: int, locale: str) -> bool:
    """Check if full chapter subtitles already exist (from batch pipeline)."""
    row = db.execute(
        "SELECT id FROM chapter_subtitles "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    return row is not None


def _has_cached_audio(db, audiobook_id: int, chapter_index: int, locale: str) -> bool:
    """Check if translated audio already exists for a chapter."""
    row = db.execute(
        "SELECT id FROM chapter_translations_audio "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    return row is not None


# In-process memo to coalesce concurrent first-hit ffprobe calls for the same
# audiobook_id. Populated by _resolve_chapter_count; value is always a positive
# int. Not locked — ffprobe is idempotent and a few redundant calls during a
# first-hit race are cheaper than a contention point on every resolution.
_chapter_count_memo: dict[int, int] = {}

# Per-book cache of [(start_sec, duration_sec), ...] for each chapter, populated
# by _resolve_chapters via ffprobe. Empty list = no chapter metadata in file
# (callers fall back to uniform-average duration). Same locking rationale as
# _chapter_count_memo: idempotent ffprobe, race-free in practice.
_chapters_memo: dict[int, list[tuple[float, float]]] = {}


def _probe_chapter_count(audio_path: Path) -> int:
    """Run ffprobe on a file and return its chapter count, or 0 on failure.

    Uses ``-show_chapters`` which emits chapters at the top-level of the JSON
    output (NOT under ``format``). Returns 0 on any error — the caller decides
    whether to treat that as fatal. No network, no side effects.
    """
    try:
        result = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are hardcoded  # nosec B607,B603 — partial path — system tools must be on PATH
            [  # noqa: S603,S607
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_chapters",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0
        data = json.loads(result.stdout)
        chapters = data.get("chapters", [])
        return len(chapters) if isinstance(chapters, list) else 0
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError):  # fmt: skip
        return 0


def _resolve_chapter_count(db, audiobook_id: int) -> int:
    """Return the chapter count for a book, populating the column lazily.

    Resolution order:
      1. In-process memo (fast-path for repeated calls in the same process).
      2. ``audiobooks.chapter_count`` column if populated.
      3. ffprobe ``-show_chapters`` on ``audiobooks.file_path``; UPDATE the
         column on success so later calls skip the probe.

    Raises:
        ValueError: if the book row is missing, ``file_path`` is missing/empty,
            or ffprobe reports zero chapters. Callers at request boundaries
            catch this and return HTTP 500.
    """
    memoed = _chapter_count_memo.get(audiobook_id)
    if memoed is not None:
        return memoed

    row = db.execute(
        "SELECT chapter_count, file_path FROM audiobooks WHERE id = ?",
        (audiobook_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"audiobook {audiobook_id} not found")

    stored = row["chapter_count"]
    if stored and stored > 0:
        _chapter_count_memo[audiobook_id] = int(stored)
        return int(stored)

    file_path = row["file_path"]
    if not file_path:
        raise ValueError(f"audiobook {audiobook_id} has no file_path")

    count = _probe_chapter_count(Path(file_path))
    if count <= 0:
        raise ValueError(
            f"ffprobe reported no chapters for audiobook {audiobook_id} ({file_path!r})"
        )

    db.execute(
        "UPDATE audiobooks SET chapter_count = ? WHERE id = ?",
        (count, audiobook_id),
    )
    db.commit()
    _chapter_count_memo[audiobook_id] = count
    logger.info(
        "Backfilled chapter_count=%d for audiobook %d via ffprobe",
        int(count),
        int(audiobook_id),
    )
    return count


def _get_book_duration_sec(db, audiobook_id: int) -> float:
    """Get book duration in seconds."""
    row = db.execute(
        "SELECT duration_hours FROM audiobooks WHERE id = ?", (audiobook_id,)
    ).fetchone()
    if row and row["duration_hours"]:
        return row["duration_hours"] * 3600
    return 0


def _chapter_segment_count(duration_sec: float) -> int:
    """Calculate number of 30-second segments for a given duration."""
    if duration_sec <= 0:
        return 0
    import math

    return math.ceil(duration_sec / SEGMENT_DURATION_SEC)


def _resolve_chapters(db, audiobook_id: int) -> list[tuple[float, float]]:
    """Return [(start_sec, duration_sec), ...] for the book's chapters.

    Memoized in ``_chapters_memo``. Uses ffprobe via
    ``localization.chapters.extract_chapters``. Returns ``[]`` when the file
    has no chapter metadata — callers must fall back to a uniform average.

    Why this matters: real audiobooks frequently have wildly non-uniform
    chapters (a 24-second intro, a 3936-second main body, a 55-second outro).
    Allocating segments by averaging book_duration/chapter_count produces
    bogus segment slices for the short chapters — ffmpeg gets a negative
    `-t` duration and silently emits a zero-byte Opus file, which crashes
    Whisper's PyAV decoder with EOFError.
    """
    cached = _chapters_memo.get(audiobook_id)
    if cached is not None:
        return cached

    row = db.execute(
        "SELECT file_path FROM audiobooks WHERE id = ?",
        (audiobook_id,),
    ).fetchone()
    if row is None or not row["file_path"]:
        _chapters_memo[audiobook_id] = []
        return []

    from localization.chapters import extract_chapters

    chapters = extract_chapters(Path(row["file_path"]))
    bounds = [(c.start_sec, c.duration_ms / 1000.0) for c in chapters]
    _chapters_memo[audiobook_id] = bounds
    return bounds


def _get_chapter_duration_sec(db, audiobook_id: int, chapter_index: int) -> float:
    """Return the actual duration (seconds) of a specific chapter.

    Resolution order:
      1. ffprobe chapter metadata for the requested ``chapter_index`` (memoized).
      2. Uniform average ``book_duration / chapter_count`` if the file lacks
         chapter metadata or ``chapter_index`` is out of bounds.

    May raise ``ValueError`` via ``_resolve_chapter_count`` (fallback path)
    if the chapter count cannot be established from DB or ffprobe.
    """
    chapters = _resolve_chapters(db, audiobook_id)
    if chapters and 0 <= chapter_index < len(chapters):
        _, dur_sec = chapters[chapter_index]
        if dur_sec > 0:
            return dur_sec

    book_dur = _get_book_duration_sec(db, audiobook_id)
    chapter_count = _resolve_chapter_count(db, audiobook_id)
    return book_dur / chapter_count


def _ensure_chapter_segments(
    db, audiobook_id: int, chapter_index: int, locale: str, priority: int = 1
) -> int:
    """Ensure segment rows exist for the entire chapter at the requested priority.

    Three things must be true on return:
      1. A row exists for every segment_index in [0, expected_segment_count) —
         either by INSERTing a new pending row or by finding an existing one.
      2. Any existing PENDING rows (state='pending') whose current priority is
         LOWER (numerically higher — 2 or 3) than the requested priority are
         promoted UP to the requested priority. Live playback (priority=0) must
         NOT be blocked by sampler pre-enqueued rows (priority=2) sitting in
         front of the segment range the user is trying to play.
      3. Completed / in-flight / failed rows are left alone — promoting them
         would falsify their state and re-trigger work.

    Returns the resulting number of segment rows for the chapter.

    Sampler-origin rows (``origin='sampler'``) have their priority promoted too
    when a live session activates on the same chapter — that's the whole point
    of the sampler priority-invariant trigger (priority >= 2 for origin=sampler)
    which the UPDATE below deliberately sidesteps by not touching origin. The
    DB trigger blocks INSERT/UPDATE of sampler rows at priority < 2, so we
    cannot promote sampler rows. We create NEW rows with origin='live' at the
    target priority instead, and the claim query orders by priority ASC so the
    live rows win the race against the sampler rows for the same segment_index.
    """
    ch_duration = _get_chapter_duration_sec(db, audiobook_id, chapter_index)
    seg_count = _chapter_segment_count(ch_duration)
    if seg_count <= 0:
        # Fallback: with chapter_count now guaranteed >0 by
        # _resolve_chapter_count, seg_count can only reach 0 when book
        # duration is missing/zero. We still seed at least one pending
        # segment so the worker has something to claim; the worker derives
        # the real per-segment bounds from ffprobe chapter timings.
        seg_count = 1

    # UNIQUE(audiobook_id, chapter_index, segment_index, locale) means sampler
    # and live rows CAN'T coexist for the same segment — there is exactly one
    # row per tuple. When a user activates live playback on a chapter whose
    # sampler is still pending, we promote the existing sampler-origin rows
    # to origin='live' so they can drop to p=0 without tripping the
    # priority-invariant trigger (trigger only fires when origin='sampler'
    # AND priority<2; flipping origin first sidesteps the check).
    #
    # Completed sampler rows are LEFT UNTOUCHED — they already produced
    # audio/VTT and are valid cache regardless of live vs sampler origin.
    if priority in (0, 1):
        # Flip pending sampler rows → live (allows priority drop below 2).
        db.execute(
            "UPDATE streaming_segments SET origin = 'live' "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
            "  AND state = 'pending' AND origin = 'sampler'",
            (audiobook_id, chapter_index, locale),
        )
        # Lower priority for ALL pending rows whose current priority is less
        # urgent than what we want. This covers both the sampler-promoted
        # rows and any leftover live rows from a prior session at p=1.
        db.execute(
            "UPDATE streaming_segments SET priority = ? "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
            "  AND state = 'pending' AND priority > ?",
            (priority, audiobook_id, chapter_index, locale, priority),
        )

    # Re-snapshot existing segment_indices after any promotion.
    existing_indices = {
        r["segment_index"]
        for r in db.execute(
            "SELECT segment_index FROM streaming_segments "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
            (audiobook_id, chapter_index, locale),
        ).fetchall()
    }

    # Fill any gaps with fresh live rows at the requested priority.
    created = 0
    for seg_idx in range(seg_count):
        if seg_idx in existing_indices:
            continue
        cur = db.execute(
            "INSERT OR IGNORE INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority, origin) "
            "VALUES (?, ?, ?, ?, 'pending', ?, 'live')",
            (audiobook_id, chapter_index, seg_idx, locale, priority),
        )
        if cur.rowcount:
            created += 1

    db.commit()

    if created:
        logger.info(
            "Created %d segment rows: book=%d ch=%d locale=%s priority=%d",
            int(created),
            int(audiobook_id),
            int(chapter_index),
            _safe_log_value(locale),
            int(priority),
        )
    return seg_count


def _enqueue_sampler(db, audiobook_id: int, locale: str) -> dict:
    """API-layer wrapper around ``localization.sampler.enqueue_sampler``.

    Resolves chapter durations via the live ``_resolve_chapters`` memo (falls
    back to uniform average if ffprobe data is missing — same graceful
    degradation the live streaming path uses). All state mutation happens in
    the shared helper so the scanner path can exercise identical semantics
    without importing Flask.
    """
    from localization.sampler import enqueue_sampler as _shared_enqueue

    bounds = _resolve_chapters(db, audiobook_id)
    if bounds:
        chapter_durations = [dur for _start, dur in bounds]
    else:
        book_dur = _get_book_duration_sec(db, audiobook_id)
        chapter_count = _resolve_chapter_count(db, audiobook_id)
        if book_dur <= 0 or chapter_count <= 0:
            return {
                "status": "error",
                "reason": "cannot resolve book duration or chapter count",
                "audiobook_id": audiobook_id,
                "locale": locale,
            }
        per_ch = book_dur / chapter_count
        chapter_durations = [per_ch] * chapter_count

    return _shared_enqueue(db, audiobook_id, locale, chapter_durations)


def _get_segment_bitmap(db, audiobook_id: int, chapter_index: int, locale: str) -> dict:
    """Get segment completion bitmap for a chapter.

    The response decouples "what segments exist" from "is the chapter
    cached" so callers never see the contradictory shape that the older
    short-circuit produced (``all_cached: true`` alongside ``total: 0``):

    - ``completed`` — list of completed streaming segment indices (always a
      list; empty when no streaming has occurred for this chapter)
    - ``total`` — number of streaming segments rows for this chapter (0 if
      the chapter was cached entirely via the batch pipeline)
    - ``all_cached`` — true iff the chapter is fully playable: either every
      streaming segment is completed, or a batch-cached VTT is present
    - ``cache_source`` — diagnostic string: ``"streaming"`` (all segments
      done), ``"batch"`` (chapter_subtitles row exists), ``"none"``
      (in-progress), or ``"both"`` (rare: batch + streaming both present)
    """
    rows = db.execute(
        "SELECT segment_index, state FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "ORDER BY segment_index",
        (audiobook_id, chapter_index, locale),
    ).fetchall()

    completed = [r["segment_index"] for r in rows if r["state"] == "completed"]
    total = len(rows)

    # Expected total segments for the chapter = ceil(chapter_duration / 30).
    # Without this, a sampler that only enqueued + completed the opening 1-2
    # segments (sampler scope, not the whole chapter) trivially satisfies
    # ``len(completed) == total`` and the system falsely concludes the
    # chapter is "fully streamed" → writes a phantom chapter_translations_audio
    # row → /translated-audio serves 30s of Audible intro as "chapter 0" →
    # playback dead-ends at the end of the sample with no live-stream fallback.
    #
    # ``streaming_done`` now requires completed count to match the chapter's
    # expected segment count (with a 1-seg slack for rounding).
    try:
        expected = _chapter_segment_count(
            _get_chapter_duration_sec(db, audiobook_id, chapter_index)
        )
    except (ValueError, OSError):  # fmt: skip
        expected = 0  # unknown duration → fall back to legacy "match rows" behavior
    if expected > 0:
        streaming_done = len(completed) >= expected - 1
    else:
        streaming_done = total > 0 and len(completed) == total

    batch_cached = _has_cached_subtitles(db, audiobook_id, chapter_index, locale)

    if batch_cached and streaming_done:
        cache_source = "both"
    elif batch_cached:
        cache_source = "batch"
    elif streaming_done:
        cache_source = "streaming"
    else:
        cache_source = "none"

    return {
        "completed": completed,
        "total": total,
        "all_cached": batch_cached or streaming_done,
        "cache_source": cache_source,
    }


def _broadcast_segment_ready(
    audiobook_id: int, chapter_index: int, segment_index: int, locale: str
):
    """Push segment-ready event to all connected WebSocket clients."""
    connection_manager.broadcast(
        {
            "type": "segment_ready",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "segment_index": segment_index,
            "locale": locale,
        }
    )


def _broadcast_chapter_ready(audiobook_id: int, chapter_index: int, locale: str):
    """Push chapter-complete event to all connected WebSocket clients."""
    connection_manager.broadcast(
        {
            "type": "chapter_ready",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
        }
    )


def _derive_phase(conn, audiobook_id: int, locale: str) -> str:
    """Derive the current streaming-pipeline phase for (audiobook_id, locale).

    The phase is surfaced to the player via both the REST
    ``POST /api/translate/stream`` response and the WebSocket
    ``buffer_progress`` broadcast so the UI can render a distinct label
    for each pipeline stage (e.g. a monolingual zh-Hans player).

    Precedence (first match wins):
        1. failed > 0                                 → "error"
        2. completed >= BUFFER_AHEAD_SEGMENTS         → "streaming"
        3. processing > 0                             → "buffering"
        4. session warm + pending > 0                 → "gpu_provisioning"
        5. session warm + pending=0 + processing=0    → "warmup"
        6. no warm session + pending > 0              → "warmup"
        7. otherwise                                  → "idle"

    Schema-drift note vs the v8.3.2 plan text: the plan referenced a
    ``requested_at`` column (real column is ``created_at``) and a session
    state ``'warmup'`` (no row ever writes that; warmup is modelled via
    ``gpu_warm=1``). This helper follows the real schema.
    """
    counts_row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN state = 'processing' THEN 1 ELSE 0 END) AS processing, "
        "SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END) AS completed, "
        "SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed "
        "FROM streaming_segments "
        "WHERE audiobook_id = ? AND locale = ?",
        (audiobook_id, locale),
    ).fetchone()

    # SUM over an empty set returns NULL → None in Python.
    pending = (counts_row["pending"] or 0) if counts_row else 0
    processing = (counts_row["processing"] or 0) if counts_row else 0
    completed = (counts_row["completed"] or 0) if counts_row else 0
    failed = (counts_row["failed"] or 0) if counts_row else 0

    session = conn.execute(
        "SELECT state, gpu_warm FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (audiobook_id, locale),
    ).fetchone()
    # Compare to literal integer to avoid propagating DB taint through bool()
    # (py/reflective-xss mitigation — CodeQL sees session["gpu_warm"] as tainted).
    gpu_warm = (session["gpu_warm"] == 1) if session is not None else False

    if failed > 0:
        return "error"
    if completed >= BUFFER_AHEAD_SEGMENTS:
        return "streaming"
    if processing > 0:
        return "buffering"
    if gpu_warm and pending > 0:
        return "gpu_provisioning"
    if gpu_warm and pending == 0 and processing == 0:
        return "warmup"
    if pending > 0:
        return "warmup"
    return "idle"


def _get_current_segment(conn, audiobook_id: int, chapter_index: int, locale: str) -> int:
    """Return the next-to-play segment index for the active chapter.

    Defined as the lowest ``segment_index`` in state ``'processing'`` or
    ``'pending'`` for this (audiobook_id, chapter_index, locale). If no
    such row exists (all completed or none created yet), returns the count
    of completed segments — i.e. the next index to fill.
    """
    row = conn.execute(
        "SELECT MIN(segment_index) AS cur FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state IN ('processing', 'pending')",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    if row is not None and row["cur"] is not None:
        return int(row["cur"])

    completed_row = conn.execute(
        "SELECT COUNT(*) AS n FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state = 'completed'",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    return int(completed_row["n"]) if completed_row else 0


def _broadcast_buffer_progress(
    audiobook_id: int, chapter_index: int, locale: str, completed: int, total: int, phase: str
):
    """Push buffer progress update to connected clients.

    ``phase`` is computed by the caller (the caller already holds the DB
    connection needed by :func:`_derive_phase`) and is forwarded verbatim
    to the WebSocket payload so the player can render the stage label in
    the same tick as the progress update.
    """
    connection_manager.broadcast(
        {
            "type": "buffer_progress",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
            "completed": completed,
            "total": total,
            "threshold": BUFFER_THRESHOLD,
            "phase": phase,
        }
    )


# ── Pure reprioritization impls (cursor-centric 3-tier queue) ──
#
# The priority model:
#   P0 (0) — cursor buffer: the 6 segments at and just ahead of the seek target
#   P1 (1) — forward chase: remaining pending segments in the same chapter
#            with segment_index > t + BUFFER_AHEAD_SEGMENTS - 1
#   P2 (2) — back-fill: pending segments in the same chapter with
#            segment_index < t (plus everything on stop())
#
# Scope is chapter-local: seek only reshuffles within (audiobook_id, chapter,
# locale). Processing rows are never touched — the worker has claimed them.


def handle_seek_impl(conn, audiobook_id, locale, chapter_index, segment_index):
    """Reprioritize pending segments around a new cursor position.

    Writes three UPDATEs (all scoped to state='pending') and commits:
      1. Demote everything pending in this chapter to P2.
      2. Promote the cursor window [t .. t+BUFFER_AHEAD_SEGMENTS-1] to P0.
      3. Promote forward-chase (> t+BUFFER_AHEAD_SEGMENTS-1) to P1.

    Segments in state='processing' or 'completed' are never touched.
    """
    end = segment_index + BUFFER_AHEAD_SEGMENTS
    # 1. Demote all pending segments in this chapter → P2
    conn.execute(
        "UPDATE streaming_segments SET priority = 2 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state = 'pending'",
        (audiobook_id, chapter_index, locale),
    )
    # 2. Promote cursor window [t, t+BUFFER_AHEAD_SEGMENTS) → P0
    conn.execute(
        "UPDATE streaming_segments SET priority = 0 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND segment_index >= ? AND segment_index < ? AND state = 'pending'",
        (audiobook_id, chapter_index, locale, segment_index, end),
    )
    # 3. Promote forward-chase (beyond the cursor window) → P1
    conn.execute(
        "UPDATE streaming_segments SET priority = 1 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND segment_index >= ? AND state = 'pending'",
        (audiobook_id, chapter_index, locale, end),
    )
    conn.commit()


def stop_streaming_impl(conn, audiobook_id, locale):
    """Drain every pending segment for (book, locale).

    Used when the player stops streaming translation. DELETEs all
    ``state='pending'`` rows so the worker can't claim them after the
    user's Stop event. Processing segments (already claimed) are left
    alone — the worker finishes what it has and the segment-complete
    callback still lands normally. No demotion/promotion.

    Pre-v8.3.2 this demoted pending rows to priority=2 (back-fill) on the
    theory that the worker would deprioritize them; in practice the
    worker drained p0/p1 and then started chewing through the demoted
    p2 rows, meaning Stop never really stopped. v8.3.2 makes Stop stop.
    """
    conn.execute(
        "DELETE FROM streaming_segments "
        "WHERE audiobook_id = ? AND locale = ? AND state = 'pending'",
        (audiobook_id, locale),
    )
    conn.commit()


# ── Routes ──


def _parse_stream_request(data):
    """Extract+validate fields from /api/translate/stream payload.

    Returns (audiobook_id, locale, chapter_index, err_response_or_None).
    """
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    chapter_index = data.get("chapter_index", 0)

    if not audiobook_id:
        return None, None, None, (jsonify({"error": "audiobook_id required"}), 400)

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return None, None, None, (jsonify({"error": "invalid parameters"}), 400)

    return audiobook_id, locale, chapter_index, None


def _fully_cached_response(db, audiobook_id, chapter_index, locale):
    """Build the response when the active chapter is cached. Enumerates
    all chapters to report which others are cached.
    """
    chapter_count = _resolve_chapter_count(db, audiobook_id)
    all_cached = True
    cached_chapters = []
    for ch in range(chapter_count):
        if _has_cached_subtitles(db, audiobook_id, ch, locale) and _has_cached_audio(
            db, audiobook_id, ch, locale
        ):
            cached_chapters.append(ch)
        else:
            all_cached = False

    return jsonify(
        {
            "state": "cached",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
            "cached_chapters": cached_chapters,
            "total_chapters": chapter_count,
            "all_cached": all_cached,
            # Fully-cached chapters are effectively already streaming — the
            # player can immediately play from the permanent cache.
            "phase": "streaming",
            # Include the active chapter's segment_bitmap so the frontend's
            # chapter-advance flow (streaming-translate.js::advanceChapter
            # → enterBuffering) has data to populate segmentBitmap[ch] and
            # hit the all_cached fast-path to enterStreaming. v8.3.8.7
            # chapter-advance shipped without this and the frontend sat in
            # BUFFERING after a successful advance POST because
            # enterBuffering's populate-and-transition block no-ops when
            # bitmap is undefined. This closes the loop.
            "segment_bitmap": _get_segment_bitmap(db, audiobook_id, chapter_index, locale),
        }
    )


def _get_or_create_streaming_session(db, audiobook_id, locale, chapter_index):
    """Return session_id, creating a streaming_sessions row or updating
    an existing buffering/streaming session's active_chapter.
    """
    existing = db.execute(
        "SELECT id, state FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (audiobook_id, locale),
    ).fetchone()

    if existing:
        session_id = existing["id"]
        db.execute(
            "UPDATE streaming_sessions SET active_chapter = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (chapter_index, session_id),
        )
    else:
        cursor = db.execute(
            "INSERT INTO streaming_sessions "
            "(audiobook_id, locale, active_chapter, buffer_threshold) "
            "VALUES (?, ?, ?, ?)",
            (audiobook_id, locale, chapter_index, BUFFER_THRESHOLD),
        )
        session_id = cursor.lastrowid
    db.commit()
    return session_id


@streaming_bp.route("/api/translate/stream", methods=["POST"])
@guest_allowed
def request_streaming_translation():
    """Player requests on-demand translation for a book.

    Body:
        audiobook_id: int
        locale: str (e.g. "zh-Hans")
        chapter_index: int (default 0 — the chapter being played)

    Returns:
        - If all chapters are already cached: {state: "cached", chapters: [...]}
        - If streaming is needed: {state: "buffering", session_id: N, ...}
    """
    audiobook_id, locale, chapter_index, err = _parse_stream_request(
        request.get_json(silent=True) or {}
    )
    if err:
        return err

    db = _get_db()

    try:
        if _has_cached_subtitles(db, audiobook_id, chapter_index, locale) and _has_cached_audio(
            db, audiobook_id, chapter_index, locale
        ):
            return _fully_cached_response(db, audiobook_id, chapter_index, locale)

        session_id = _get_or_create_streaming_session(db, audiobook_id, locale, chapter_index)

        # Ensure segment rows exist for the active chapter (priority 0 = active playback)
        _ensure_chapter_segments(db, audiobook_id, chapter_index, locale, priority=0)

        # Also pre-create segments for the next chapter (priority 1 = prefetch)
        chapter_count = _resolve_chapter_count(db, audiobook_id)
        if chapter_count and chapter_index + 1 < chapter_count:
            _ensure_chapter_segments(db, audiobook_id, chapter_index + 1, locale, priority=1)

        return jsonify(
            {
                "state": "buffering",
                "session_id": session_id,
                "audiobook_id": audiobook_id,
                "chapter_index": chapter_index,
                "locale": locale,
                "buffer_threshold": BUFFER_THRESHOLD,
                "segment_bitmap": _get_segment_bitmap(db, audiobook_id, chapter_index, locale),
                "phase": _derive_phase(db, audiobook_id, locale),
                "current_segment": _get_current_segment(db, audiobook_id, chapter_index, locale),
                # total_chapters is what the frontend uses to know when to
                # stop walking chapters at end-of-stream. The cached-response
                # branch (see _fully_cached_response) already returns it;
                # the buffering branch previously didn't, which meant the
                # streaming chapter-advance-on-EOF path couldn't know when
                # it had reached the end of the book and would try to fetch
                # a nonexistent chapter N+1.
                "total_chapters": chapter_count or 0,
            }
        )
    except ValueError as exc:
        # Raised when chapter_count cannot be established from DB or ffprobe —
        # e.g. missing file_path, unreadable audio, or a book with zero chapters.
        # Surface as 500 so the player overlay can show a clear error instead
        # of spinning forever; log enough context for ops to diagnose.
        logger.error(
            "chapter_count unavailable for book=%d locale=%s chapter=%d: %s",
            int(audiobook_id),
            _safe_log_value(locale),
            int(chapter_index),
            _safe_log_value(exc),
        )
        return jsonify({"error": "chapter count unavailable"}), 500


@streaming_bp.route("/api/translate/segments/<int:audiobook_id>/<int:chapter_index>/<locale>")
@guest_allowed
def get_segment_bitmap(audiobook_id, chapter_index, locale):
    """Get segment completion bitmap for a chapter.

    Used by the player to determine which segments are cached
    (instant seek) vs uncached (need buffering state).
    """
    try:
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid locale"}), 400

    db = _get_db()
    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)
    return jsonify(bitmap)


@streaming_bp.route("/api/translate/session/<int:audiobook_id>/<locale>")
@guest_allowed
def get_session_state(audiobook_id, locale):
    """Get current streaming session state.

    Extended in Task 15 (v8.3.2) so the client's polling fallback can keep
    the overlay progress bar fresh when the WebSocket stalls or disconnects.
    When a session exists, the response mirrors the fields carried by the
    ``buffer_progress`` WS broadcast — ``phase``, ``completed``, ``total``,
    ``current_segment``, ``segment_bitmap`` — letting the client synthesize
    a ``buffer_progress`` event from a plain HTTP response and reuse its
    existing WS event handler (DRY). When no session exists, the response
    remains ``{"state": "none"}`` so the client knows to stop polling.
    """
    try:
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid locale"}), 400

    db = _get_db()
    session = db.execute(
        "SELECT * FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? "
        "ORDER BY id DESC LIMIT 1",
        (audiobook_id, locale),
    ).fetchone()

    if not session:
        return jsonify({"state": "none"})

    active_chapter = session["active_chapter"]

    # Count completed/total segments for the active chapter. Using a single
    # aggregate query rather than two round-trips keeps the polling endpoint
    # cheap (client hits it every 3 s per book during WS-down windows).
    counts_row = db.execute(
        "SELECT "
        "SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END) AS completed, "
        "COUNT(*) AS total "
        "FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
        (audiobook_id, active_chapter, locale),
    ).fetchone()
    completed = (counts_row["completed"] or 0) if counts_row else 0
    total = (counts_row["total"] or 0) if counts_row else 0

    return jsonify(
        {
            "session_id": session["id"],
            "state": session["state"],
            "active_chapter": active_chapter,
            "buffer_threshold": session["buffer_threshold"],
            "gpu_warm": bool(session["gpu_warm"]),
            "phase": _derive_phase(db, audiobook_id, locale),
            "completed": completed,
            "total": total,
            "current_segment": _get_current_segment(db, audiobook_id, active_chapter, locale),
            "segment_bitmap": _get_segment_bitmap(db, audiobook_id, active_chapter, locale),
        }
    )


# ─── STT warmth probe + buffer-fill threshold ───────────────────────────────

# Cache (TTL 60s) to avoid hitting provider /health endpoints on every session
# creation. Warm/cold state rarely flips faster than a minute — serverless
# idle-worker decay is measured in minutes, and warm-up on first request is
# also minutes.
# Structure:
#   {"ts": <epoch>,
#    "streaming_ready": <int — combined across providers>,
#    "cold": <bool — True iff every configured provider has 0 ready workers>,
#    "providers": [{"name": "runpod", "ready": N, "endpoint_id": "xxx"}, ...]}
_STT_WARMTH_CACHE: dict = {
    "ts": 0.0,
    "streaming_ready": 0,
    "cold": True,
    "providers": [],
}
_STT_WARMTH_TTL_SEC = 60


def _probe_stt_warmth() -> tuple[bool, int, list[dict]]:
    """Query every configured STT provider's streaming /health endpoint.

    Iterates known provider families (RunPod, Vast.ai serverless) — each
    reports the RunPod-compatible ``{"workers": {"ready": N, ...}}`` shape.
    Returns ``(is_cold, total_ready_workers, per_provider_list)``.

    - ``is_cold`` is True iff every configured provider has 0 ready workers
      (or none are configured).
    - ``total_ready_workers`` is the sum across providers.
    - ``per_provider_list`` is a list of ``{"name", "ready", "endpoint_id"}``
      dicts for each configured provider, in the order RunPod → Vast.ai.

    Cached for 60s to bound cost. If a provider can't be queried (network
    error, timeout), that provider contributes 0 ready workers but does not
    force ``is_cold=True`` if another provider has workers ready.

    Returning three values (instead of the old two) is a superset — existing
    callers that unpack only the first two still work.
    """
    import time
    import urllib.request
    import urllib.error

    now = time.time()
    if now - _STT_WARMTH_CACHE["ts"] < _STT_WARMTH_TTL_SEC:
        return (
            _STT_WARMTH_CACHE["cold"],
            _STT_WARMTH_CACHE["streaming_ready"],
            list(_STT_WARMTH_CACHE["providers"]),
        )

    providers: list[dict] = []
    total_ready = 0

    def _probe_one(name: str, api_key: str, endpoint: str, base_url: str) -> dict | None:
        """Probe a single {api_key, endpoint} pair; return summary dict or None."""
        if not api_key or not endpoint:
            return None
        url = f"{base_url}/v2/{endpoint}/health"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        entry = {"name": name, "ready": 0, "endpoint_id": endpoint}
        try:
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with urllib.request.urlopen(req, timeout=3) as resp:  # nosec B310 — trusted provider hosts
                import json as _json

                payload = _json.loads(resp.read().decode())
            workers = payload.get("workers", {}) or {}
            entry["ready"] = int(workers.get("ready", 0))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            logger.debug("%s warmth probe failed: %s", name, e)
        return entry

    # RunPod serverless — trusted host api.runpod.ai
    runpod_entry = _probe_one(
        name="runpod",
        api_key=os.environ.get("AUDIOBOOKS_RUNPOD_API_KEY", ""),
        endpoint=os.environ.get("AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT", ""),
        base_url="https://api.runpod.ai",
    )
    if runpod_entry is not None:
        providers.append(runpod_entry)
        total_ready += runpod_entry["ready"]

    # Vast.ai serverless — trusted host run.vast.ai
    vastai_entry = _probe_one(
        name="vastai",
        api_key=os.environ.get("AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY", ""),
        endpoint=os.environ.get("AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT", ""),
        base_url="https://run.vast.ai",
    )
    if vastai_entry is not None:
        providers.append(vastai_entry)
        total_ready += vastai_entry["ready"]

    # Cold = no provider configured, or every configured provider has 0 ready.
    is_cold = total_ready == 0
    _STT_WARMTH_CACHE.update(
        {"ts": now, "streaming_ready": total_ready, "cold": is_cold, "providers": providers}
    )
    return is_cold, total_ready, providers


# Backwards-compat shim: any in-process caller still importing
# _probe_runpod_warmth gets the new generalized probe and the first two
# elements of its tuple. Tests assert against the new name; this shim keeps
# the older name working for one release cycle.
def _probe_runpod_warmth() -> tuple[bool, int]:
    """Deprecated. Use ``_probe_stt_warmth`` — returns (cold, ready, providers).

    Retained as a two-tuple wrapper so any legacy import path continues to
    function. Will be removed once all callers have migrated.
    """
    cold, ready, _providers = _probe_stt_warmth()
    return cold, ready


def _buffer_fill_threshold() -> int:
    """Return the segment index at which the frontend should fire buffer-fill.

    Adaptive on whichever STT provider(s) are configured — warm if ANY
    configured provider has >=1 ready worker, cold otherwise:
    - cold (no provider has ready workers) → fire at segment 3 (4.5 min runway)
    - warm (at least one provider has workers ready) → fire at segment 4
      (4 min runway)

    The 6-min sample (12 segments) gives the live pipeline enough time to
    catch up before the user reaches end-of-sample IF buffer-fill starts
    within the threshold runway. See docs/SAMPLER.md.
    """
    cold, _ready, _providers = _probe_stt_warmth()
    return BUFFER_FILL_THRESHOLD_COLD if cold else BUFFER_FILL_THRESHOLD_WARM


@streaming_bp.route("/api/translate/warmth", methods=["GET"])
@guest_allowed
def gpu_warmth():
    """Expose current STT provider warmth to the frontend.

    Response:
      ``{"cold": bool,
         "streaming_ready": int,             # total ready workers across providers
         "buffer_fill_threshold": int,
         "providers": [{"name": str, "ready": int, "endpoint_id": str}, ...]}``

    Backwards-compatible: frontends that only consume ``cold``, ``streaming_ready``,
    and ``buffer_fill_threshold`` work unchanged. The ``providers`` array is
    additive, giving richer diagnostics when multiple STT backends are in use.

    Frontend uses ``buffer_fill_threshold`` to decide when, during sample
    playback, to fire ``/api/translate/sampler/activate`` to start the
    live pipeline.
    """
    cold, ready, providers = _probe_stt_warmth()
    return jsonify(
        {
            "cold": cold,
            "streaming_ready": ready,
            "buffer_fill_threshold": BUFFER_FILL_THRESHOLD_COLD
            if cold
            else BUFFER_FILL_THRESHOLD_WARM,
            "providers": providers,
        }
    )


@streaming_bp.route("/api/translate/sampler/activate", methods=["POST"])
@guest_allowed
def sampler_activate():
    """Frontend fires this once the user has played past the buffer-fill
    threshold during sample playback. The server kicks off the live
    translation pipeline from the cursor forward so the buffer is ready
    by the time the user reaches end-of-sample.

    Request body: ``{"audiobook_id": int, "locale": str, "chapter_index": int,
                     "segment_index": int}``
      ``segment_index`` is the last segment the user has confirmed listening
      to — live fill starts at ``segment_index + 1`` forward.

    Response: ``{"activated": bool, "cursor_segments_created": int}``

    This path is idempotent and cheap: calling it multiple times on the same
    (book, locale) only creates segments that don't already exist.
    """
    data = request.get_json(silent=True) or {}
    try:
        audiobook_id = int(data.get("audiobook_id"))
        locale = _sanitize_locale(data.get("locale"))
        chapter_index = int(data.get("chapter_index", 0))
        segment_index = int(data.get("segment_index", 0))
    except (ValueError, TypeError):  # fmt: skip
        return jsonify(
            {"error": "audiobook_id, locale, chapter_index, segment_index required"}
        ), 400

    if locale not in SUPPORTED_LOCALES:
        return jsonify({"error": "locale not supported"}), 400

    db = _get_db()
    # Ensure chapter segments exist at p0 (cursor), then p1 (forward chase).
    # _ensure_chapter_segments is idempotent. For sampler-origin segments at
    # segments < current cursor, we do NOT promote them to p0 — they stay at
    # sampler priority so live playback of future segments gets served first.
    # We create new p0/p1 rows for segments past the sampler scope.
    created = _ensure_chapter_segments(db, audiobook_id, chapter_index, locale, priority=0)
    # Chase priority for the next chapter so buffer stays ahead.
    _ensure_chapter_segments(db, audiobook_id, chapter_index + 1, locale, priority=1)

    logger.info(
        "sampler activated: book=%d locale=%s ch=%d seg=%d (cursor segments=%d)",
        int(audiobook_id),
        _safe_log_value(locale),
        int(chapter_index),
        int(segment_index),
        int(created),
    )

    return jsonify(
        {
            "activated": True,
            "cursor_segments_created": created,
        }
    )


@streaming_bp.route("/api/translate/warmup", methods=["POST"])
@guest_allowed
def warmup_gpu():
    """Pre-warm a GPU instance on app open.

    Called by the web UI on load to reduce cold-start latency
    when the user eventually presses play on an untranslated book.
    """
    # For now, this is a signal that a client connected.
    # The actual GPU warm-up will be handled by the translation daemon
    # when it sees this signal.
    logger.info("GPU warm-up requested by client")

    # Write a warm-up hint to DB so the daemon picks it up
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO streaming_sessions "
        "(audiobook_id, locale, state, gpu_warm) VALUES (0, 'warmup', 'warmup', 0)"
    )
    db.commit()

    return jsonify({"status": "warming"})


@streaming_bp.route("/api/translate/seek", methods=["POST"])
@guest_allowed
def handle_seek():
    """Handle a seek/skip into uncached territory.

    The player calls this when the user scrubs or skips beyond
    the cached segment range. The coordinator reprioritizes
    segment processing to start from the new position.

    Body:
        audiobook_id: int
        locale: str
        chapter_index: int
        segment_index: int (the segment at the seek target)
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    chapter_index = data.get("chapter_index", 0)
    segment_index = data.get("segment_index", 0)

    if not audiobook_id:
        return jsonify({"error": "audiobook_id required"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        segment_index = int(segment_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()

    try:
        # Ensure segments exist for this chapter (may raise ValueError if
        # chapter_count cannot be resolved from DB or ffprobe).
        _ensure_chapter_segments(db, audiobook_id, chapter_index, locale, priority=0)
    except ValueError as exc:
        logger.error(
            "chapter_count unavailable on seek for book=%d locale=%s chapter=%d: %s",
            int(audiobook_id),
            _safe_log_value(locale),
            int(chapter_index),
            _safe_log_value(exc),
        )
        return jsonify({"error": "chapter count unavailable"}), 500

    # Check if the target segment is already cached
    cached = db.execute(
        "SELECT state FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND segment_index = ? AND locale = ?",
        (audiobook_id, chapter_index, segment_index, locale),
    ).fetchone()

    if cached and cached["state"] == "completed":
        return jsonify({"state": "cached", "segment_index": segment_index})

    # 3-tier cursor-centric reprioritization (scoped to this chapter).
    handle_seek_impl(db, audiobook_id, locale, chapter_index, segment_index)

    # Update session active chapter
    db.execute(
        "UPDATE streaming_sessions SET active_chapter = ?, state = 'buffering', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (chapter_index, audiobook_id, locale),
    )
    db.commit()

    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)

    return jsonify(
        {
            "state": "buffering",
            "chapter_index": chapter_index,
            "segment_index": segment_index,
            "segment_bitmap": bitmap,
            "buffer_threshold": BUFFER_THRESHOLD,
        }
    )


@streaming_bp.route("/api/translate/stop", methods=["POST"])
@guest_allowed
def stop_streaming():
    """Stop streaming translation for a book+locale.

    DELETEs every pending segment for (audiobook_id, locale) so the GPU
    workers have no further work for this book. Processing segments are
    left alone — the worker finishes what it claimed. Any active
    streaming_sessions row for this pair is marked 'stopped'; the worker
    reads that row via LEFT JOIN in ``claim_next_segment`` and will
    skip any pending rows that survive a race with this endpoint.

    Body:
        audiobook_id: int
        locale: str
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")

    if not audiobook_id:
        return jsonify({"error": "audiobook_id required"}), 400

    try:
        audiobook_id = int(audiobook_id)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()

    stop_streaming_impl(db, audiobook_id, locale)

    db.execute(
        "UPDATE streaming_sessions SET state = 'stopped', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (audiobook_id, locale),
    )
    db.commit()

    return jsonify({"state": "stopped", "audiobook_id": audiobook_id, "locale": locale})


# ── Worker callback endpoints (called by GPU workers) ──


@streaming_bp.route("/api/translate/segment-complete", methods=["POST"])
@localhost_only
def segment_complete():
    """GPU worker reports a segment is done.

    Authentication: ``@localhost_only`` (not ``@admin_or_localhost``). The
    streaming worker is a co-located systemd service that POSTs to
    ``http://127.0.0.1:5001`` with no session — in AUTH_ENABLED=true
    deployments (QA, prod), ``admin_or_localhost`` would reject the worker
    with 401. Pure localhost gating is correct here because the endpoint
    is only ever called by the local worker, never by users.


    Body:
        audiobook_id: int
        chapter_index: int
        segment_index: int
        locale: str
        vtt_content: str (optional — translated VTT cues)
        source_vtt_content: str (optional — English source VTT cues; v8.3.2+)
        audio_path: str (optional — path to TTS audio segment)
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    chapter_index = data.get("chapter_index")
    segment_index = data.get("segment_index")
    locale = data.get("locale")

    if audiobook_id is None or locale is None or chapter_index is None or segment_index is None:
        return jsonify({"error": "missing fields"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        segment_index = int(segment_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid parameters"}), 400

    # Validate audio_path is within the allowed streaming audio root
    raw_audio_path = data.get("audio_path")
    safe_audio_path = _validate_audio_path(raw_audio_path)
    if raw_audio_path is not None and safe_audio_path is None:
        return jsonify({"error": "audio_path outside allowed root"}), 400

    db = _get_db()
    # Capture origin before the state update so we can attribute this completion
    # to the sampler_jobs row if it was a sampler-origin segment.
    origin_row = db.execute(
        "SELECT origin FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND segment_index = ? AND locale = ?",
        (audiobook_id, chapter_index, segment_index, locale),
    ).fetchone()
    segment_origin = origin_row["origin"] if origin_row else "live"

    db.execute(
        "UPDATE streaming_segments SET state = 'completed', "
        "vtt_content = ?, source_vtt_content = ?, audio_path = ?, "
        "completed_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND chapter_index = ? AND segment_index = ? AND locale = ?",
        (
            data.get("vtt_content"),
            data.get("source_vtt_content"),
            str(safe_audio_path) if safe_audio_path is not None else None,
            audiobook_id,
            chapter_index,
            segment_index,
            locale,
        ),
    )

    # Sampler accounting: if this completed segment came from the sampler,
    # bump the matching sampler_jobs.segments_done counter and flip status
    # to 'complete' once all target segments are in.
    if segment_origin == "sampler":
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE sampler_jobs "
            "SET segments_done = segments_done + 1, updated_at = ? "
            "WHERE audiobook_id = ? AND locale = ?",
            (now, audiobook_id, locale),
        )
        # Flip to 'complete' when segments_done >= segments_target.
        db.execute(
            "UPDATE sampler_jobs SET status = 'complete', updated_at = ? "
            "WHERE audiobook_id = ? AND locale = ? "
            "AND status = 'running' AND segments_done >= segments_target",
            (now, audiobook_id, locale),
        )
    db.commit()

    # Broadcast to WebSocket clients
    _broadcast_segment_ready(audiobook_id, chapter_index, segment_index, locale)

    # Check buffer progress for the active chapter
    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)
    completed_count = len(bitmap["completed"]) if isinstance(bitmap["completed"], list) else 0
    phase = _derive_phase(db, audiobook_id, locale)
    _broadcast_buffer_progress(
        audiobook_id, chapter_index, locale, completed_count, bitmap["total"], phase
    )

    # If this chapter is fully done, broadcast chapter_ready
    if bitmap["all_cached"]:
        _broadcast_chapter_ready(audiobook_id, chapter_index, locale)

        # Also write consolidated VTT to chapter_subtitles for permanent cache
        _consolidate_chapter(db, audiobook_id, chapter_index, locale)

    return jsonify({"status": "ok"})


@streaming_bp.route("/api/translate/chapter-complete", methods=["POST"])
@localhost_only
def chapter_complete():
    """GPU worker reports an entire chapter is done (prefetch chapters).

    Authentication: ``@localhost_only`` (not ``@admin_or_localhost``). Same
    rationale as ``segment_complete`` — the streaming worker is a co-located
    systemd service POSTing to ``http://127.0.0.1:5001`` with no session.

    For prefetch chapters, the worker sends the complete VTT directly
    rather than segment-by-segment.

    Body:
        audiobook_id: int
        chapter_index: int
        locale: str
        source_vtt_path: str
        translated_vtt_path: str (optional)
        audio_path: str (optional)
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    chapter_index = data.get("chapter_index")
    locale = data.get("locale")

    if audiobook_id is None or chapter_index is None or locale is None:
        return jsonify({"error": "missing fields"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid parameters"}), 400

    # Validate audio_path is within the allowed streaming audio root
    raw_audio_path = data.get("audio_path")
    safe_audio_path = _validate_audio_path(raw_audio_path)
    if raw_audio_path is not None and safe_audio_path is None:
        return jsonify({"error": "audio_path outside allowed root"}), 400

    # Validate VTT paths live under the streaming-subtitles root before
    # we record them in the DB — py/path-injection mitigation even though
    # the caller is the trusted worker (defense in depth).
    def _validate_vtt_path(raw) -> str | None:
        if raw is None:
            return None
        if _streaming_subtitles_root is None:
            return None
        try:
            candidate = Path(raw).resolve(strict=False)
            root = _streaming_subtitles_root.resolve(strict=False)
            if not candidate.is_relative_to(root):
                return None
            return str(candidate)
        except (ValueError, OSError):  # fmt: skip
            return None

    raw_translated_vtt = data.get("translated_vtt_path")
    safe_translated_vtt = _validate_vtt_path(raw_translated_vtt)
    if raw_translated_vtt is not None and safe_translated_vtt is None:
        return jsonify({"error": "translated_vtt_path outside allowed root"}), 400

    raw_source_vtt = data.get("source_vtt_path")
    safe_source_vtt = _validate_vtt_path(raw_source_vtt)
    if raw_source_vtt is not None and safe_source_vtt is None:
        return jsonify({"error": "source_vtt_path outside allowed root"}), 400

    db = _get_db()

    # Insert into chapter_subtitles (permanent cache)
    if safe_translated_vtt:
        db.execute(
            "INSERT OR REPLACE INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider, translation_provider) "
            "VALUES (?, ?, ?, ?, 'streaming', 'deepl')",
            (audiobook_id, chapter_index, locale, safe_translated_vtt),
        )
    if safe_source_vtt:
        db.execute(
            "INSERT OR REPLACE INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (?, ?, 'en', ?, 'streaming')",
            (audiobook_id, chapter_index, safe_source_vtt),
        )

    # Insert into chapter_translations_audio if audio was generated
    if safe_audio_path is not None:
        db.execute(
            "INSERT OR REPLACE INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (?, ?, ?, ?, 'streaming')",
            (audiobook_id, chapter_index, locale, str(safe_audio_path)),
        )

    db.commit()

    # Broadcast
    _broadcast_chapter_ready(audiobook_id, chapter_index, locale)

    return jsonify({"status": "ok"})


def _consolidate_chapter_audio(db, audiobook_id: int, chapter_index: int, locale: str) -> None:
    """Concatenate per-segment WebM-Opus files into chapter.webm and persist a row.

    All completed segments must have `audio_path` set (Task 9's TTS may
    degrade to text-only on failure — those chapters produce no consolidated
    audio). On any error, logs and returns without raising; VTT
    consolidation continues unaffected in the caller.

    Container is WebM-Opus to match the per-segment files served via MSE
    (Chromium MSE rejects Ogg-Opus). ffmpeg ``-c copy`` does not transcode;
    it just concatenates the same opus codec inside a single WebM container.
    """
    if _streaming_audio_root is None:
        logger.warning("Cannot consolidate chapter audio — _streaming_audio_root not configured")
        return

    # Pull (segment_index, audio_path) for all completed segments to confirm
    # every one has audio before we attempt to concat.
    audio_rows = db.execute(
        "SELECT segment_index, audio_path FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state = 'completed' "
        "ORDER BY segment_index",
        (audiobook_id, chapter_index, locale),
    ).fetchall()

    if not audio_rows:
        return

    if any(r["audio_path"] is None for r in audio_rows):
        logger.info(
            "Skipping chapter audio consolidation — at least one segment "
            "has no audio_path (TTS degraded to text-only): "
            "book=%d ch=%d locale=%s",
            int(audiobook_id),
            int(chapter_index),
            _safe_log_value(locale),
        )
        return

    # Resolve each per-segment relative path to an absolute path under the
    # streaming audio root, and verify the file exists on disk.
    # SECURITY: validate containment — reject any path that resolves outside
    # the streaming audio root (py/path-injection guard; mirrors _validate_audio_path).
    audio_root_resolved = _streaming_audio_root.resolve(strict=False)
    segment_paths: list[Path] = []
    for r in audio_rows:
        rel = r["audio_path"]
        p = _streaming_audio_root / rel if not os.path.isabs(rel) else Path(rel)
        try:
            p_resolved = p.resolve(strict=False)
            p_resolved.relative_to(audio_root_resolved)  # raises ValueError if outside
        except (ValueError, OSError):  # fmt: skip
            # Parenthesised tuple — prior ``except (ValueError, OSError):``
            # was silently parsed as "catch ValueError as OSError" (Py2-style
            # binding), which would let real OSErrors escape.
            logger.warning(
                "Rejecting audio_path that escapes streaming root — "
                "skipping chapter audio consolidation: book=%d ch=%d seg=%d path=%s",
                audiobook_id,
                chapter_index,
                r["segment_index"],
                _safe_log_value(rel),
            )
            return
        if not p_resolved.exists():
            logger.warning(
                "Missing per-segment WebM on disk — skipping chapter audio "
                "consolidation: book=%d ch=%d seg=%d path=%s",
                audiobook_id,
                chapter_index,
                r["segment_index"],
                _safe_log_value(p_resolved),
            )
            return
        segment_paths.append(p_resolved)

    # Output: <root>/<book_id>/ch<NNN>/<locale>/chapter.webm.
    # `_safe_join_under` defends against path injection even though the three
    # components are already validated (audiobook_id int, chapter_index int,
    # locale regex-validated upstream) — required by CodeQL py/path-injection.
    try:
        chapter_dir = _safe_join_under(
            _streaming_audio_root,
            str(int(audiobook_id)),
            f"ch{int(chapter_index):03d}",
            _sanitize_locale(locale),
        )
    except ValueError as exc:
        logger.error("Rejected unsafe chapter_dir path: %s", _safe_log_value(exc))
        return
    chapter_dir.mkdir(parents=True, exist_ok=True)
    out_path = chapter_dir / "chapter.webm"

    # ffmpeg concat demuxer with -c copy. All per-segment WebM-Opus files
    # are uniform 48k/48kHz libopus (enforced by Task 9), so no re-encode
    # is needed — sub-second latency. ``-f webm`` makes the output container
    # explicit even though the .webm extension also implies it.
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            concat_list = Path(tmp_dir) / "concat.txt"
            concat_list.write_text("\n".join(f"file '{p}'" for p in segment_paths) + "\n")
            subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
                [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    "-f",
                    "webm",
                    str(out_path),
                ],
                check=True,
                capture_output=True,
            )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning(
            "ffmpeg concat failed for chapter audio: book=%d ch=%d locale=%s err=%s",
            int(audiobook_id),
            int(chapter_index),
            _safe_log_value(locale),
            _safe_log_value(exc),
        )
        return

    duration = _probe_audio_duration(out_path)
    voice = _default_voice_for_locale(locale)

    try:
        db.execute(
            "INSERT OR REPLACE INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, "
            " tts_provider, tts_voice, duration_seconds) "
            "VALUES (?, ?, ?, ?, 'streaming', ?, ?)",
            (audiobook_id, chapter_index, locale, str(out_path), voice, duration),
        )
        db.commit()
    except sqlite3.DatabaseError as exc:
        logger.warning(
            "Failed to persist chapter audio row: book=%d ch=%d locale=%s err=%s",
            int(audiobook_id),
            int(chapter_index),
            _safe_log_value(locale),
            _safe_log_value(exc),
        )
        return

    logger.info(
        "Consolidated streaming segments into chapter.webm: "
        "book=%d ch=%d locale=%s segments=%d duration=%s",
        int(audiobook_id),
        int(chapter_index),
        _safe_log_value(locale),
        len(segment_paths),
        _safe_log_value(duration),
    )


def _consolidate_chapter(db, audiobook_id: int, chapter_index: int, locale: str):
    """Merge streaming segments into a permanent chapter_subtitles entry.

    After all segments for a chapter are done, consolidate the VTT
    content into a single file and write to the permanent cache so
    future plays don't need the streaming pipeline. If every segment
    also has a per-segment WebM-Opus audio file (Task 9), concatenate them
    into a single chapter.webm and register a chapter_translations_audio
    row so `_has_cached_audio` returns True on next play.
    """
    rows = db.execute(
        "SELECT segment_index, vtt_content, source_vtt_content FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? AND state = 'completed' "
        "ORDER BY segment_index",
        (audiobook_id, chapter_index, locale),
    ).fetchall()

    if not rows:
        return

    def _merge_segment_vtts(column: str) -> str:
        merged = "WEBVTT\n\n"
        for row in rows:
            content = row[column] if column in row.keys() else None
            if not content:
                continue
            if content.startswith("WEBVTT"):
                content = content.split("\n\n", 1)[-1] if "\n\n" in content else ""
            if content.strip():
                merged += content.strip() + "\n\n"
        return merged

    all_translated_vtt = _merge_segment_vtts("vtt_content")
    all_source_vtt = _merge_segment_vtts("source_vtt_content")

    if len(all_translated_vtt.strip()) <= len("WEBVTT"):
        return

    # Write consolidated VTT file — validated to live inside the writable
    # streaming-subtitles runtime root. Must NOT use _library_path because the
    # install tree at /opt/audiobooks/library is read-only at runtime
    # (systemd ProtectSystem=strict).
    if _streaming_subtitles_root is None:
        logger.error(
            "Cannot consolidate streaming chapter — streaming subtitles root not configured"
        )
        return
    try:
        translated_vtt_path = _safe_subtitles_path(
            _streaming_subtitles_root, audiobook_id, chapter_index, locale
        )
    except ValueError as exc:
        logger.error("Rejected unsafe consolidated VTT path: %s", _safe_log_value(exc))
        return
    translated_vtt_path.parent.mkdir(parents=True, exist_ok=True)
    translated_vtt_path.write_text(all_translated_vtt)

    source_vtt_path = None
    if len(all_source_vtt.strip()) > len("WEBVTT"):
        try:
            source_vtt_path = _safe_subtitles_path(
                _streaming_subtitles_root, audiobook_id, chapter_index, "en"
            )
            source_vtt_path.parent.mkdir(parents=True, exist_ok=True)
            source_vtt_path.write_text(all_source_vtt)
        except ValueError as exc:
            # Source-side write is best-effort — translated row still goes in.
            logger.warning(
                "Rejected unsafe consolidated source VTT path: %s",
                _safe_log_value(exc),
            )
            source_vtt_path = None

    # Insert translated locale row into permanent cache
    db.execute(
        "INSERT OR REPLACE INTO chapter_subtitles "
        "(audiobook_id, chapter_index, locale, vtt_path, stt_provider, translation_provider) "
        "VALUES (?, ?, ?, ?, 'streaming', 'deepl')",
        (audiobook_id, chapter_index, locale, str(translated_vtt_path)),
    )
    # Insert English source row so the bilingual transcript panel
    # (双语文字记录) can render after consolidation. Mirrors the
    # chapter-complete (prefetch) path at lines 1217-1223.
    if source_vtt_path is not None:
        db.execute(
            "INSERT OR REPLACE INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (?, ?, 'en', ?, 'streaming')",
            (audiobook_id, chapter_index, str(source_vtt_path)),
        )
    db.commit()

    logger.info(
        "Consolidated streaming segments into permanent VTT: book=%d ch=%d locale=%s bilingual=%s",
        int(audiobook_id),
        int(chapter_index),
        _safe_log_value(locale),
        "yes" if source_vtt_path is not None else "no",
    )

    # Audio consolidation is a best-effort addition — any failure logs and
    # leaves the VTT-side cache intact.
    try:
        _consolidate_chapter_audio(db, audiobook_id, chapter_index, locale)
    except Exception as exc:  # pylint: disable=broad-except  # defense in depth — audio side must never break VTT path
        logger.warning(
            "Chapter audio consolidation raised unexpected exception: "
            "book=%d ch=%d locale=%s err=%s",
            int(audiobook_id),
            int(chapter_index),
            _safe_log_value(locale),
            _safe_log_value(exc),
        )


@streaming_bp.route("/api/translate/sampler/prefetch", methods=["POST"])
@admin_or_localhost
def sampler_prefetch():
    """Enqueue a 6-minute sampler for (audiobook_id, locale).

    Auth: ``@admin_or_localhost`` — admins hit this from the UI, scanner and
    locale-add triggers hit it from the API host itself. Casual users don't
    trigger sampler work (cost control).

    Request body: ``{"audiobook_id": int, "locale": str}``
    Response: the ``sampler_jobs`` row (id, status, segments_target,
    segments_done, scope). Idempotent: re-hitting with an already-complete
    (book, locale) returns status='complete' without side effects.
    """
    data = request.get_json(silent=True) or {}
    try:
        audiobook_id = int(data.get("audiobook_id"))
        locale = _sanitize_locale(data.get("locale"))
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "audiobook_id (int) and locale (str) required"}), 400

    # Reject unknown locales to prevent enqueue-for-nonsense-locales.
    if locale not in SUPPORTED_LOCALES:
        return jsonify(
            {
                "error": "locale not supported",
                "locale": locale,
                "supported": sorted(SUPPORTED_LOCALES),
            }
        ), 400

    db = _get_db()
    result = _enqueue_sampler(db, audiobook_id, locale)
    # Distinguish hard errors from soft skip/complete short-circuits.
    status = result.get("status")
    if status == "error":
        return jsonify(result), 400
    return jsonify(result), 200


@streaming_bp.route("/api/translate/sampler/batch-status", methods=["GET"])
@guest_allowed
def sampler_batch_status():
    """Bulk-query sampler job status for many books at once.

    Called by library.js during ``applyBookTranslations`` to decide which
    book cards show the "Play sample" affordance. Single-row queries would
    mean ~2000 HTTP calls per library load; this batches them.

    Query params:
      - ``ids``: comma-separated audiobook ids (max 100 per call)
      - ``locale``: non-EN locale to check

    Response:
      ``{"<id>": "complete" | "running" | "pending" | "failed" | "none", ...}``
    """
    ids_raw = request.args.get("ids", "")
    locale_raw = request.args.get("locale", "")
    try:
        locale = _sanitize_locale(locale_raw)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid locale"}), 400

    ids: list[int] = []
    for token in ids_raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError:
            return jsonify({"error": f"invalid id: {token}"}), 400

    if not ids:
        return jsonify({}), 200
    if len(ids) > 100:
        return jsonify({"error": "too many ids (max 100 per call)"}), 400

    db = _get_db()
    # Build placeholders safely — ids are ints validated above; locale is
    # _sanitize_locale-validated. Only '?,?,?' chars are interpolated.
    placeholders = ",".join("?" * len(ids))
    _sql_sampler_status = f"SELECT audiobook_id, status FROM sampler_jobs WHERE locale = ? AND audiobook_id IN ({placeholders})"  # nosec B608  # noqa: S608, E501  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    rows = db.execute(_sql_sampler_status, (locale, *ids)).fetchall()

    result = {str(book_id): "none" for book_id in ids}
    for r in rows:
        result[str(r["audiobook_id"])] = r["status"]
    return jsonify(result), 200


@streaming_bp.route("/api/translate/sampler/status/<int:audiobook_id>/<locale>")
@guest_allowed
def sampler_status(audiobook_id, locale):
    """Return the sampler job state for (audiobook_id, locale).

    Auth: ``@guest_allowed`` — readable by library-browse UI so each book
    card knows whether to show the "Play sample" affordance.

    Response:
      - ``{"status": "none"}`` if no job exists
      - full sampler_jobs row otherwise:
        ``{"status": "pending|running|complete|failed",
           "segments_target": N, "segments_done": M,
           "progress": 0.0..1.0, "error": str|None,
           "chapter_audio_urls": [...] when complete}``
    """
    try:
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):  # fmt: skip
        return jsonify({"error": "invalid locale"}), 400

    db = _get_db()
    row = db.execute(
        "SELECT id, status, segments_target, segments_done, error, created_at, updated_at "
        "FROM sampler_jobs WHERE audiobook_id = ? AND locale = ?",
        (audiobook_id, locale),
    ).fetchone()

    if row is None:
        return jsonify({"status": "none", "audiobook_id": audiobook_id, "locale": locale}), 200

    resp = {
        "audiobook_id": audiobook_id,
        "locale": locale,
        "status": row["status"],
        "segments_target": row["segments_target"],
        "segments_done": row["segments_done"],
        "progress": (
            float(row["segments_done"]) / float(row["segments_target"])
            if row["segments_target"] > 0
            else 0.0
        ),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

    # When complete, surface the chapter audio URLs the frontend can use for
    # the instant-play affordance. The live path writes chapter_translations_audio
    # rows as part of _consolidate_chapter; reuse that cache here.
    if row["status"] == "complete":
        chapters = db.execute(
            "SELECT chapter_index, audio_path "
            "FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND locale = ? "
            "ORDER BY chapter_index",
            (audiobook_id, locale),
        ).fetchall()
        resp["chapter_audio_urls"] = [
            {
                "chapter_index": c["chapter_index"],
                # Relative URL — frontend prepends /streaming-audio/ or uses
                # the chapter_translations_audio-specific serve route.
                "audio_path": c["audio_path"],
            }
            for c in chapters
        ]
    return jsonify(resp), 200


@streaming_bp.route(
    "/streaming-audio/<int:audiobook_id>/<int:chapter_index>/<int:segment_index>/<locale>"
)
@guest_allowed
def serve_streaming_segment(audiobook_id, chapter_index, segment_index, locale):
    """Serve a per-segment WebM-Opus file to the client MSE chain.

    Path layout (owned by the streaming worker):
        ``<_streaming_audio_root>/<book>/ch<NNN>/<locale>/seg<NNNN>.webm``

    Container is WebM-Opus (not Ogg-Opus): Chromium-based browsers reject
    ``audio/ogg; codecs=opus`` in MediaSource.addSourceBuffer. The frontend
    MseAudioChain uses ``addSourceBuffer('audio/webm; codecs="opus"')`` so
    we serve a matching MIME and matching container.

    Defense in depth:
    - Reject locales not in ``SUPPORTED_LOCALES`` (whitelist).
    - Resolve the requested path and the root, then verify containment.
      This catches ``..`` traversal, symlink escape, and any future
      race window where the segment directory is replaced.
    - Return 503 if the streaming root was never configured
      (``init_streaming_routes`` not yet called) — distinct from 404 so
      ops can tell "missing file" from "misconfigured deployment".
    """
    # Whitelist check. Routes that reached here with a bogus locale slug
    # (``xx``, ``..``) are rejected before any filesystem work happens.
    if locale not in SUPPORTED_LOCALES:
        abort(404)

    if _streaming_audio_root is None:
        # Route was hit before init_streaming_routes configured the root.
        abort(503)

    # Resolve BOTH sides of the containment check. The module global is
    # stored unresolved by init_streaming_routes, so we resolve here; if
    # the target is a symlink pointing outside, .resolve() on the
    # candidate exposes that and the containment check rejects it.
    try:
        root = _streaming_audio_root.resolve(strict=False)
        candidate = (
            _streaming_audio_root
            / str(audiobook_id)
            / f"ch{chapter_index:03d}"
            / locale
            / f"seg{segment_index:04d}.webm"
        )
        # strict=False so a missing file still resolves (we check exists()
        # below and return 404); strict=True would raise FileNotFoundError.
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):  # fmt: skip
        # Resolve can raise on broken symlink loops; treat as not-found.
        # Parenthesised tuple: prior ``except (OSError, RuntimeError):`` was
        # silently parsed as "catch OSError as RuntimeError" (Py2-style
        # binding), which would let real RuntimeErrors escape.
        abort(404)

    # Containment: the resolved candidate must live under the resolved
    # root. Using .is_relative_to (3.9+); equivalent to "root in parents"
    # but correctly handles the edge case where resolved == root.
    if not resolved.is_relative_to(root):
        abort(403)

    if not resolved.is_file():
        abort(404)

    # conditional=True enables HTTP Range/If-Modified-Since handling,
    # which MSE SourceBuffer.appendBuffer relies on for resumable fetches.
    return send_file(resolved, mimetype="audio/webm", conditional=True)


def init_streaming_routes(
    database_path,
    library_path=None,
    streaming_audio_dir=None,
    streaming_subtitles_dir=None,
):
    """Initialize the streaming translation blueprint.

    Args:
        database_path: Path to the main audiobooks SQLite database.
        library_path: Project library root (used only for resolving
            cached static assets, not for writing). Defaults to the DB
            file's parent directory.
        streaming_audio_dir: Root directory holding per-segment WebM-Opus
            files (used by chapter audio consolidation). Defaults to
            $AUDIOBOOKS_STREAMING_AUDIO_DIR, falling back to
            $AUDIOBOOKS_VAR_DIR/streaming-audio.
        streaming_subtitles_dir: Root directory holding consolidated
            per-chapter VTT files. Defaults to
            $AUDIOBOOKS_STREAMING_SUBTITLES_DIR, falling back to
            $AUDIOBOOKS_VAR_DIR/streaming-subtitles. MUST be writable —
            this is the canonical writable location for streaming-
            generated VTT output (the install tree is read-only at
            runtime under systemd ProtectSystem=strict).
    """
    global _db_path, _library_path, _streaming_audio_root, _streaming_subtitles_root
    _db_path = Path(database_path) if database_path else None
    if library_path:
        _library_path = Path(library_path)
    else:
        # Default to the parent of the DB path
        _library_path = Path(database_path).parent if database_path else None
    # Direct env reads (rather than importing library.config) keep this module
    # safe to import before the API factory completes config loading.
    _var_dir = os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
    if streaming_audio_dir:
        _streaming_audio_root = Path(streaming_audio_dir)
    else:
        _streaming_audio_root = Path(
            os.environ.get("AUDIOBOOKS_STREAMING_AUDIO_DIR", f"{_var_dir}/streaming-audio")
        )
    if streaming_subtitles_dir:
        _streaming_subtitles_root = Path(streaming_subtitles_dir)
    else:
        _streaming_subtitles_root = Path(
            os.environ.get("AUDIOBOOKS_STREAMING_SUBTITLES_DIR", f"{_var_dir}/streaming-subtitles")
        )


@streaming_bp.teardown_app_request
def _teardown_streaming_db(exc=None):
    _close_db(exc)
