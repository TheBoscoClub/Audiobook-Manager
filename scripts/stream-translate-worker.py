#!/usr/bin/env python3
"""Streaming translation worker — chapter-level GPU processing.

Polls the streaming_segments table for pending work, processes each
segment through STT → Translation → VTT, and reports completion back
to the coordinator API via HTTP callbacks. Designed to run under the
audiobook-stream-translate.service systemd unit; inference is dispatched
to serverless STT providers (RunPod + Vast.ai serverless) — no dedicated
GPU host lifecycle to manage.

Active chapters stream segment-by-segment (30s each) for low-latency
playback. Prefetch chapters process as a single unit via the batch
pipeline for efficiency.

Usage:
    python stream-translate-worker.py \
        --db $AUDIOBOOKS_VAR_DIR/db/audiobooks.db \
        --library $AUDIOBOOKS_LIBRARY \
        --api-base http://localhost:5001
"""

import argparse
import logging
import os
import signal
import sqlite3
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import sys
import tempfile
import time
from pathlib import Path

# Add the library directory to the path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
LIB_DIR = PROJECT_DIR / "library"
sys.path.insert(0, str(LIB_DIR))

# TTS provider factory — imported at module top so tests can patch it
# (lazy import would change the patch target and break test coverage).
from localization.tts.factory import get_tts_provider  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stream-worker")

_shutdown = False


def _signal_handler(_sig, _frame):
    global _shutdown
    logger.info("Shutdown signal received — finishing current segment then exiting")
    _shutdown = True


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

SEGMENT_DURATION_SEC = 30

# Streaming TTS audio root is required for v8.3.2+.
# Resolved from AUDIOBOOKS_STREAMING_AUDIO_DIR (set by stream-translate-daemon.sh
# which sources lib/audiobook-config.sh, the canonical source of
# ${AUDIOBOOKS_VAR_DIR}/streaming-audio). The literal fallback below is the
# hard-coded canonical default used only when unit tests import this module
# without the env var set.
_STREAMING_AUDIO_DEFAULT = (
    "/var/lib/audiobooks/streaming-audio"  # default for AUDIOBOOKS_VAR_DIR fallback
)
_STREAMING_AUDIO_ROOT = Path(
    os.environ.get("AUDIOBOOKS_STREAMING_AUDIO_DIR", _STREAMING_AUDIO_DEFAULT)
)

# Per-locale default edge-tts voice. Conservative picks — can be broadened as
# more locales come online. Unknown locales fall back to en-US.
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
    """Map locale → edge-tts voice. Unknown → en-US fallback."""
    return _LOCALE_DEFAULT_VOICE.get(locale, "en-US-AriaNeural")


def _vtt_to_plain(vtt_text: str) -> str:
    """Extract spoken text from VTT cues — drop headers, timings, cue indices.

    edge-tts takes plain UTF-8 text. WebVTT cue structure (WEBVTT header,
    numeric cue IDs, ``HH:MM:SS.mmm --> HH:MM:SS.mmm`` timing lines, and
    blank separators) would be spoken verbatim and ruin the audio.

    Note: only numeric cue IDs are filtered. WebVTT allows alphanumeric cue
    identifiers, but this pipeline's VTT is exclusively Whisper-generated
    and only emits numeric IDs. If a non-Whisper VTT source is introduced
    upstream, extend this filter to detect cue IDs as non-timing lines that
    precede a timing line.
    """
    lines: list[str] = []
    for raw in vtt_text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("WEBVTT") or "-->" in ln or ln.isdigit():
            continue
        lines.append(ln)
    return " ".join(lines)


def _synthesize_segment_audio(
    vtt_content: str, audiobook_id: int, chapter_index: int, segment_index: int, locale: str
) -> Path | None:
    """Generate per-segment WebM-Opus audio from a translated VTT cue block.

    edge-tts emits MP3 regardless of filename suffix, so we synthesize to a
    temp MP3 then re-encode to Opus inside a WebM container via ffmpeg.
    WebM (not Ogg) because Chromium-based browsers (Brave, Chrome, Edge)
    do NOT support Ogg-Opus in MediaSource Extensions — only WebM-Opus
    and MP4-Opus play through MSE. The frontend's MseAudioChain calls
    ``addSourceBuffer('audio/webm; codecs="opus"')`` and the backend serves
    these segments with ``mimetype="audio/webm"``. Task 10's chapter
    consolidation uses ``ffmpeg -c copy`` which demands a uniform codec
    AND container across inputs — per-segment files MUST be WebM-Opus.

    Returns the final .webm path, or ``None`` when the VTT has no spoken
    text (empty cue block, synthesis would produce 0-byte silence).
    """
    text = _vtt_to_plain(vtt_content)
    if not text.strip():
        return None

    # Short clips → edge-tts (no cold-start, no per-minute billing).
    tts = get_tts_provider("edge-tts")
    voice = _default_voice_for_locale(locale)

    out_webm = (
        _STREAMING_AUDIO_ROOT
        / str(audiobook_id)
        / f"ch{chapter_index:03d}"
        / locale
        / f"seg{segment_index:04d}.webm"
    )
    out_webm.parent.mkdir(parents=True, exist_ok=True)

    # edge-tts → temp MP3
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
        mp3_tmp = Path(tf.name)
    try:
        tts.synthesize(text=text, language=locale, voice=voice, output_path=mp3_tmp)
        # ffmpeg MP3 → WebM-Opus (48k, 48kHz). Explicit ``-f webm`` so the
        # container is unambiguous regardless of the output extension.
        subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
            [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp3_tmp),
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-ar",
                "48000",
                "-f",
                "webm",
                str(out_webm),
            ],
            check=True,
        )
    finally:
        mp3_tmp.unlink(missing_ok=True)
    return out_webm


def get_db(db_path: str) -> sqlite3.Connection:
    # 30s busy_timeout: absorbs transient lock contention at startup when the
    # API server and this worker race to initialize WAL mode on the shared DB.
    # Without this, the worker crashes on "database is locked" during a normal
    # cold start, relying on systemd's Restart=on-failure for recovery.
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def claim_next_segment(db_path: str) -> dict | None:
    """Atomically claim the next pending streaming segment.

    Prioritizes lower priority numbers using the 3-tier cursor-centric model:

    - ``0`` = P0 cursor buffer fill (6 segments forward of cursor — drained first)
    - ``1`` = P1 forward chase (segments past buffer toward end-of-chapter)
    - ``2`` = P2 back-fill (segments behind cursor — side panel / resume completeness)

    The authoritative source of priority semantics (promotion/demotion on
    play/seek/stop) lives in
    ``library/backend/api_modular/streaming_translate.py::handle_seek_impl``.
    """
    conn = get_db(db_path)
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        # Skip segments whose session has been stopped/cancelled/errored —
        # otherwise /api/translate/stop would demote rows the worker then
        # still claims and processes (Bug E). Back-fill work (no session,
        # or session.state='completed') is still eligible because
        # stop_streaming_impl DELETEs rows, not demotes, so anything left
        # is genuinely wanted work. The LEFT JOIN picks the most recent
        # session for (book, locale) by id DESC so a user's latest
        # Stop/abort wins over any stale row.
        #
        # Session-state filter applies ONLY to origin='live' rows — user
        # Stop is about the user's live playback session. Sampler and
        # backlog pretranslation work must not be blocked by a stale
        # session state, because:
        #   (1) orphan-repair flows reset rows to state='pending' and
        #       rely on the worker draining them regardless of the
        #       (possibly ancient) last session's state.
        #   (2) sampler pre-translation is a background job that has
        #       its own `sampler_jobs.status` lifecycle — it is not
        #       controlled by streaming_sessions.state.
        # Without this carve-out, a single user Stop on a book where
        # pretranslation is underway silently freezes all 7 of the
        # book's sampler rows, and the operator has to manually
        # transition the session state back to 'buffering' to resume
        # work. That was the exact snag that delayed orphan repair on
        # books 115401, 115852, and 116062 during the v8.3.8.6 repair.
        row = conn.execute(
            "UPDATE streaming_segments "
            "SET state = 'processing', worker_id = ?, started_at = ? "
            "WHERE id = (SELECT s.id FROM streaming_segments s "
            "            LEFT JOIN streaming_sessions sess "
            "              ON sess.id = (SELECT id FROM streaming_sessions "
            "                            WHERE audiobook_id = s.audiobook_id "
            "                              AND locale = s.locale "
            "                            ORDER BY id DESC LIMIT 1) "
            "            WHERE s.state = 'pending' "
            "              AND COALESCE(s.retry_count, 0) < 3 "
            "              AND (s.origin != 'live' "
            "                   OR sess.state IS NULL "
            "                   OR sess.state NOT IN "
            "                      ('stopped','cancelled','error')) "
            "            ORDER BY s.priority ASC, s.chapter_index ASC, "
            "                     s.segment_index ASC "
            "            LIMIT 1) "
            "RETURNING *",
            (f"worker-{os.getpid()}", now),
        ).fetchone()
        if row:
            counts = conn.execute(
                "SELECT priority, COUNT(*) FROM streaming_segments "
                "WHERE state='pending' GROUP BY priority"
            ).fetchall()
            depths = {p: c for p, c in counts}
            logger.info(
                "claimed segment p=%s ch=%s seg=%s (pending: p0=%s p1=%s p2=%s)",
                row["priority"],
                row["chapter_index"],
                row["segment_index"],
                depths.get(0, 0),
                depths.get(1, 0),
                depths.get(2, 0),
            )
        conn.commit()
        return dict(row) if row else None
    finally:
        conn.close()


def split_audio_segment(
    audio_path: Path, chapter_start_sec: float, segment_index: int, chapter_duration_sec: float
) -> Path:
    """Extract a 30-second segment from the audiobook using ffmpeg stream copy."""
    seg_start = chapter_start_sec + (segment_index * SEGMENT_DURATION_SEC)
    seg_duration = min(SEGMENT_DURATION_SEC, chapter_start_sec + chapter_duration_sec - seg_start)

    suffix = audio_path.suffix or ".opus"
    tmp = tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=f"seg{segment_index:04d}_", delete=False
    )
    tmp.close()
    out_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{seg_start:.3f}",
        "-t",
        f"{seg_duration:.3f}",
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-map_metadata",
        "-1",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
    if result.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg segment split failed: {result.stderr[-200:]}")

    return out_path


def process_segment(
    db_path: str,
    segment: dict,
    audio_path: Path,
    chapter_start_sec: float,
    chapter_duration_sec: float,
    api_base: str,
) -> bool:
    """Process a single 30-second segment: STT → translate → VTT → report."""
    from localization.pipeline import generate_subtitles, get_stt_provider
    from localization.selection import WorkloadHint

    audiobook_id = segment["audiobook_id"]
    chapter_index = segment["chapter_index"]
    segment_index = segment["segment_index"]
    locale = segment["locale"]

    logger.info(
        "Processing segment: book=%d ch=%d seg=%d locale=%s",
        audiobook_id,
        chapter_index,
        segment_index,
        locale,
    )

    seg_audio: Path | None = None  # extracted temp 30-sec slice (cleanup target)
    tts_opus: Path | None = None  # permanent synthesized opus (DO NOT delete)
    output_dir: Path | None = None  # tempdir for generate_subtitles outputs (STT-path only)
    try:
        # Idempotent TTS-regen path: when a pending row already has
        # vtt_content (a previous run completed STT+translate but the audio
        # is missing — e.g. a broken burst worker, a legacy .opus orphan,
        # or a manual reset to repair orphaned audio_path), skip STT and
        # translation entirely and regenerate only the per-segment TTS.
        # This is a pure economic optimization (no STT GPU cost, no DeepL
        # per-char cost) AND the correctness primitive that lets an
        # operator reset orphan rows to pending to recover missing audio.
        vtt_raw = segment.get("vtt_content") or ""
        source_vtt_raw = segment.get("source_vtt_content") or ""
        if vtt_raw.strip():
            logger.info(
                "TTS-only regen (vtt_content present): book=%d ch=%d seg=%d",
                audiobook_id,
                chapter_index,
                segment_index,
            )
            # Preserve the DB-stored VTT verbatim — timestamp offsets were
            # already applied when originally written, and the callback's
            # update path is idempotent w.r.t. unchanged content.
            vtt_content = vtt_raw
            source_vtt_content = source_vtt_raw
        else:
            # Extract the 30-second audio segment
            seg_audio = split_audio_segment(
                audio_path, chapter_start_sec, segment_index, chapter_duration_sec
            )

            # Run STT + translation on the segment. Workload hint routes to the
            # right endpoint tier:
            #   - origin='live' (real user playback) → STREAMING → warm pool (RunPod
            #     min_workers=1, Vast.ai streaming if configured) for sub-second
            #     first-segment latency.
            #   - origin='sampler' / 'backlog' (pretranslation backfill) → LONG_FORM
            #     → backlog pool (min_workers=0) so Vast.ai scale-to-zero cold
            #     endpoints participate alongside RunPod backlog, giving dual-farm
            #     throughput for bulk work without burning warm-instance cost.
            origin = segment.get("origin", "live")
            workload = WorkloadHint.STREAMING if origin == "live" else WorkloadHint.LONG_FORM
            stt = get_stt_provider("", workload=workload)
            output_dir = Path(tempfile.mkdtemp(prefix="stream-seg-"))

            source_vtt, translated_vtt = generate_subtitles(
                audio_path=seg_audio,
                output_dir=output_dir,
                target_locale=locale,
                chapter_name=f"book{audiobook_id}_ch{chapter_index:03d}_seg{segment_index:04d}",
                stt_provider=stt,
            )

            # Read VTT content for inline storage. Persist BOTH the translated
            # cues (vtt_content) AND the English source cues (source_vtt_content)
            # so the bilingual transcript panel has data to render once the
            # chapter consolidates. v8.3.2 and earlier discarded source_vtt here,
            # which left chapter_subtitles with only the translated locale row.
            vtt_content = ""
            if translated_vtt and translated_vtt.exists():
                vtt_content = translated_vtt.read_text(encoding="utf-8")

            source_vtt_content = ""
            if source_vtt and source_vtt.exists():
                source_vtt_content = source_vtt.read_text(encoding="utf-8")

            # Offset cue timestamps to account for segment position in chapter.
            # Both VTTs share the same time base (the source audio slice), so
            # they get the same offset.
            offset_ms = segment_index * SEGMENT_DURATION_SEC * 1000
            if offset_ms > 0:
                if vtt_content:
                    vtt_content = _offset_vtt_timestamps(vtt_content, offset_ms)
                if source_vtt_content:
                    source_vtt_content = _offset_vtt_timestamps(source_vtt_content, offset_ms)

        # Synthesize per-segment TTS audio (opus). VTT alone is still useful,
        # so a TTS failure downgrades to "text-only" rather than failing the
        # whole segment.
        audio_rel: str | None = None
        try:
            tts_opus = _synthesize_segment_audio(
                vtt_content, audiobook_id, chapter_index, segment_index, locale
            )
            if tts_opus is not None:
                try:
                    audio_rel = str(tts_opus.relative_to(_STREAMING_AUDIO_ROOT))
                except ValueError:
                    audio_rel = None
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "TTS synthesis failed for seg %d/%d/%d (locale=%s): %s",
                audiobook_id,
                chapter_index,
                segment_index,
                locale,
                exc,
            )
            audio_rel = None

        # Report completion to coordinator API
        import json
        import urllib.request

        payload = json.dumps(
            {
                "audiobook_id": audiobook_id,
                "chapter_index": chapter_index,
                "segment_index": segment_index,
                "locale": locale,
                "vtt_content": vtt_content,
                "source_vtt_content": source_vtt_content,
                "audio_path": audio_rel,
            }
        ).encode()

        req = urllib.request.Request(  # noqa: S310 — Request for fixed HTTPS TTS API endpoint; no user-controlled scheme
            f"{api_base}/api/translate/segment-complete",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if not req.full_url.startswith(("http://", "https://")):
            raise ValueError(f"Refusing non-http(s) callback URL: {req.full_url!r}")
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        urllib.request.urlopen(req, timeout=30)  # noqa: S310  # nosec B310

        logger.info(
            "Segment complete: book=%d ch=%d seg=%d", audiobook_id, chapter_index, segment_index
        )

        # Cleanup temp files — only when STT branch created the tempdir.
        # The TTS-only regen path (vtt_content already present) skips
        # generate_subtitles entirely, so output_dir stays None.
        if output_dir is not None:
            for f in output_dir.iterdir():
                f.unlink(missing_ok=True)
            output_dir.rmdir()

        return True

    except Exception as e:
        logger.exception(
            "Segment failed: book=%d ch=%d seg=%d — %s",
            audiobook_id,
            chapter_index,
            segment_index,
            e,
        )
        # Bounded retry: increment retry_count and requeue (state='pending')
        # until the budget (cap = 3) is exhausted, then flip to 'failed'.
        # The exception string is persisted in `error` so operators don't
        # have to correlate worker logs to DB rows — `error` was previously
        # never populated (Bug B). claim_next_segment skips rows where
        # retry_count >= 3, so there is no infinite-loop risk.
        conn = get_db(db_path)
        try:
            err_msg = f"{type(e).__name__}: {e}"[:500]
            conn.execute(
                "UPDATE streaming_segments SET "
                "  retry_count = COALESCE(retry_count, 0) + 1, "
                "  error = ?, "
                "  state = CASE "
                "    WHEN COALESCE(retry_count, 0) + 1 >= 3 THEN 'failed' "
                "    ELSE 'pending' "
                "  END, "
                "  worker_id = NULL, "
                "  started_at = NULL "
                "WHERE audiobook_id = ? AND chapter_index = ? "
                "AND segment_index = ? AND locale = ?",
                (err_msg, audiobook_id, chapter_index, segment_index, locale),
            )
            conn.commit()
        finally:
            conn.close()
        return False

    finally:
        # Only the extracted temp slice is disposable. Never unlink tts_opus —
        # it is referenced by streaming_segments.audio_path and is the input to
        # Task 10's ffmpeg -c copy chapter consolidation.
        if seg_audio and seg_audio.exists():
            seg_audio.unlink(missing_ok=True)


def _offset_vtt_timestamps(vtt_content: str, offset_ms: int) -> str:
    """Shift all VTT timestamps by offset_ms milliseconds."""
    import re

    def _shift_ts(match):
        h, m, s, ms = int(match[1]), int(match[2]), int(match[3]), int(match[4])
        total_ms = h * 3600000 + m * 60000 + s * 1000 + ms + offset_ms
        nh = total_ms // 3600000
        nm = (total_ms % 3600000) // 60000
        ns = (total_ms % 60000) // 1000
        nms = total_ms % 1000
        return f"{nh:02d}:{nm:02d}:{ns:02d}.{nms:03d}"

    return re.sub(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})", _shift_ts, vtt_content)


def get_chapter_info(db_path: str, audiobook_id: int, chapter_index: int) -> tuple:
    """Get chapter start time and duration from the audiobook."""
    conn = get_db(db_path)
    try:
        book = conn.execute(
            "SELECT file_path FROM audiobooks WHERE id = ?", (audiobook_id,)
        ).fetchone()
        if not book:
            return None, 0.0, 0.0

        audio_path = Path(book["file_path"])

        # Extract chapter info using ffprobe
        from localization.chapters import extract_chapters

        chapters = extract_chapters(audio_path)
        if chapter_index < len(chapters):
            ch = chapters[chapter_index]
            return audio_path, ch.start_sec, ch.duration_ms / 1000.0

        # Fallback: single-chapter book
        result = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
            ["ffprobe", "-v", "quiet", "-show_format", "-print_format", "json", str(audio_path)],  # noqa: S603,S607 — ffmpeg/ffprobe are system-installed media tools; inputs are internal paths and config values, not user-controlled
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            duration = float(data.get("format", {}).get("duration", 0))
            return audio_path, 0.0, duration

        return audio_path, 0.0, 0.0
    finally:
        conn.close()


def main():
    # Must run as audiobooks service account — DB + config are 0640
    # audiobooks:audiobooks. Defensive check; systemd / daemon wrappers
    # already run as audiobooks, but manual invocations from a shell
    # would silently fail without this gate.
    from config import require_audiobooks_user  # noqa: E402

    require_audiobooks_user()

    parser = argparse.ArgumentParser(description="Streaming translation worker")
    parser.add_argument("--db", required=True, help="Path to audiobooks.db")
    parser.add_argument("--library", required=True, help="Path to audiobook library")
    parser.add_argument(
        "--api-base", default="http://localhost:5001", help="Coordinator API base URL"
    )
    parser.add_argument(
        "--poll-interval", type=int, default=2, help="Seconds between polling for new segments"
    )
    args = parser.parse_args()

    db_path = args.db
    api_base = args.api_base.rstrip("/")

    logger.info("Streaming worker starting (PID=%d)", os.getpid())
    logger.info("DB: %s", db_path)
    logger.info("API: %s", api_base)

    # Cache chapter info to avoid repeated ffprobe calls
    chapter_cache: dict[tuple[int, int], tuple[Path, float, float]] = {}

    idle_count = 0
    while not _shutdown:
        segment = claim_next_segment(db_path)
        if not segment:
            idle_count += 1
            if idle_count % 30 == 1:
                logger.debug("No pending segments — polling")
            time.sleep(args.poll_interval)
            continue

        idle_count = 0
        book_id = segment["audiobook_id"]
        ch_idx = segment["chapter_index"]

        # Get chapter audio info (cached)
        cache_key = (book_id, ch_idx)
        if cache_key not in chapter_cache:
            audio_path, ch_start, ch_dur = get_chapter_info(db_path, book_id, ch_idx)
            if audio_path is None:
                logger.error("Book %d not found — skipping segment", book_id)
                continue
            chapter_cache[cache_key] = (audio_path, ch_start, ch_dur)
        else:
            audio_path, ch_start, ch_dur = chapter_cache[cache_key]

        process_segment(db_path, segment, audio_path, ch_start, ch_dur, api_base)

    logger.info("Streaming worker shutting down")


if __name__ == "__main__":
    main()
