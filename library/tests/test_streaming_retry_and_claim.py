"""v8.3.2 regression tests: retry policy + session-aware claim filter.

Covers:

- Bug A: worker retries transient failures up to 3 attempts before dead-letter
- Bug B: streaming_segments.error is populated on failure
- Bug E: claim_next_segment skips rows whose session.state is stopped/cancelled/error
- Bug E: claim_next_segment skips rows with retry_count >= 3

These tests exercise the worker's claim_next_segment and the SQL contract
behind the retry path. The worker's actual exception handler is exercised
at /test audit time on test-audiobook-cachyos with real RunPod/DeepL calls;
here we verify the shape the worker depends on.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "library" / "backend" / "schema.sql"


def _load_worker():
    spec = importlib.util.spec_from_file_location(
        "stream_translate_worker",
        PROJECT_ROOT / "scripts" / "stream-translate-worker.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stream_translate_worker"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "t.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.close()
    return str(p)


def _insert_seg(
    db_path,
    *,
    audiobook_id=1,
    chapter=0,
    segment=0,
    state="pending",
    priority=1,
    retry_count=0,
    locale="zh-Hans",
    origin="live",
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority, "
        " retry_count, origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (audiobook_id, chapter, segment, locale, state, priority, retry_count, origin),
    )
    conn.commit()
    conn.close()


def _insert_session(db_path, *, audiobook_id=1, locale="zh-Hans", state="buffering"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO streaming_sessions "
        "(audiobook_id, locale, active_chapter, state) "
        "VALUES (?, ?, 0, ?)",
        (audiobook_id, locale, state),
    )
    conn.commit()
    conn.close()


def test_claim_skips_retry_count_at_cap(db_path):
    """retry_count >= 3 means the row is dead-lettered to state='failed' path.
    Even if it's somehow still state='pending', claim must skip it."""
    worker = _load_worker()
    _insert_seg(db_path, segment=0, retry_count=3)
    _insert_seg(db_path, segment=1, retry_count=2)
    _insert_seg(db_path, segment=2, retry_count=5)
    _insert_seg(db_path, segment=3, retry_count=0)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is not None
    # Must claim segment 1 (retry_count=2, still < cap) before 3 (priority order)
    # Actually priority is equal so it uses segment_index ASC — segment 1 (r=2) first
    assert claimed["retry_count"] < 3

    # Second claim — should skip the r=3 and r=5 rows
    worker.claim_next_segment(db_path)  # claims another
    # After those two claims, only segments with retry_count >= 3 are left as pending
    conn = sqlite3.connect(db_path)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM streaming_segments WHERE state='pending'"
    ).fetchone()[0]
    conn.close()
    assert remaining == 2  # segments 0 and 2 with retry_count >= 3


def test_claim_respects_retry_count_ordering(db_path):
    """With equal priority, claim picks by (priority, chapter, segment) order.
    retry_count below cap shouldn't reorder."""
    worker = _load_worker()
    _insert_seg(db_path, segment=0, retry_count=2)
    _insert_seg(db_path, segment=1, retry_count=0)
    claimed = worker.claim_next_segment(db_path)
    assert claimed["segment_index"] == 0


def test_claim_skips_stopped_session(db_path):
    """Bug E defense-in-depth: pending rows under a stopped session are skipped
    even if stop_streaming_impl's DELETE somehow missed them."""
    worker = _load_worker()
    _insert_session(db_path, state="stopped")
    _insert_seg(db_path, segment=0)
    _insert_seg(db_path, segment=1)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is None, "no row should be claimable under stopped session"


def test_claim_skips_cancelled_session(db_path):
    worker = _load_worker()
    _insert_session(db_path, state="cancelled")
    _insert_seg(db_path, segment=0)
    assert worker.claim_next_segment(db_path) is None


def test_claim_skips_error_session(db_path):
    worker = _load_worker()
    _insert_session(db_path, state="error")
    _insert_seg(db_path, segment=0)
    assert worker.claim_next_segment(db_path) is None


def test_claim_allows_active_session(db_path):
    worker = _load_worker()
    _insert_session(db_path, state="streaming")
    _insert_seg(db_path, segment=0)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is not None
    assert claimed["segment_index"] == 0


def test_claim_allows_no_session(db_path):
    """A segment without any matching session (legacy/back-fill) is still
    claimable — only stopped/cancelled/error actively blocks."""
    worker = _load_worker()
    _insert_seg(db_path, segment=0)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is not None


def test_claim_sampler_row_ignores_stopped_session(db_path):
    """v8.3.8.6 orphan-repair fix: session-state block applies ONLY to
    origin='live' rows. A user pressing Stop on their playback session
    must not freeze background pretranslation (origin='sampler') or
    operator-initiated orphan repair (origin='backlog').

    Reproduction of the v8.3.8.6 incident snag: after the user Stopped
    playback on book 115401 to clear a stuck player, the orphan-repair
    flow reset 132 ch=1 rows to state='pending' with origin='live'
    (those were legacy-.opus orphans). The stopped session blocked all
    132 rows from being claimed; operator had to manually transition
    the session state back to 'buffering'. Long-term fix (schema-level
    repair sessions) is deferred to v8.3.8.7; this carve-out is the
    minimum bug-stopping change.

    For live-origin rows the block still applies — see
    test_claim_skips_stopped_session.
    """
    worker = _load_worker()
    _insert_session(db_path, state="stopped")
    _insert_seg(db_path, segment=0, origin="sampler", priority=2)
    _insert_seg(db_path, segment=1, origin="backlog", priority=2)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is not None, (
        "sampler/backlog rows must be claimable regardless of session state"
    )
    assert claimed["origin"] in ("sampler", "backlog")


def test_claim_live_row_still_blocked_by_stopped_session(db_path):
    """Regression guard on the live-origin invariant preserved across
    the v8.3.8.6 session-block carve-out. Live rows in a stopped
    session MUST NOT be claimed — that is the original Bug E defense
    (user pressed Stop, do not silently resume their live playback)."""
    worker = _load_worker()
    _insert_session(db_path, state="stopped")
    _insert_seg(db_path, segment=0, origin="live", priority=0)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is None, "live row MUST still be blocked by stopped session"


def test_claim_priority_ordering_unaffected_by_origin(db_path):
    """When both live and sampler rows are pending under a stopped
    session, the filter excludes live but still honors ORDER BY
    priority ASC — so the sampler row (p=2) is claimable even though a
    p=0 live row nominally has higher priority but is blocked."""
    worker = _load_worker()
    _insert_session(db_path, state="stopped")
    _insert_seg(db_path, segment=0, origin="live", priority=0)  # blocked
    _insert_seg(db_path, segment=1, origin="sampler", priority=2)  # eligible
    claimed = worker.claim_next_segment(db_path)
    assert claimed is not None
    assert claimed["origin"] == "sampler"
    assert claimed["segment_index"] == 1


def test_claim_uses_latest_session_by_id(db_path):
    """If a user starts, stops, then re-starts streaming for the same book,
    the LATEST session wins (ORDER BY id DESC). Old stopped session must
    not block work for a new active session."""
    worker = _load_worker()
    _insert_session(db_path, state="stopped")  # session id=1
    _insert_session(db_path, state="streaming")  # session id=2 (newer)
    _insert_seg(db_path, segment=0)
    claimed = worker.claim_next_segment(db_path)
    assert claimed is not None, "newest session state 'streaming' should unblock"


def test_streaming_translate_js_populates_bitmap_before_all_cached_shortcut():
    """Static-source guard for v8.3.8.6 MSE buffer-threshold fix.

    In ``library/web-v2/js/streaming-translate.js::enterBuffering``, the
    ``bitmap.all_cached`` fast-path early-returns via ``enterStreaming()``.
    If the local ``segmentBitmap[chapterIndex]`` has not been populated
    BEFORE this return, ``enterStreaming``'s replay loop iterates an
    empty Set, never enqueues a segment into the MSE chain, and the
    audio element sits at currentTime=0 readyState=0 forever. Books
    affected: any whose ch=0 has fewer than BUFFER_THRESHOLD (6)
    segments — observed on 115401 (1 seg), 115852 (3 segs), 116062
    (1 seg) during v8.3.8.6 orphan-repair browser proof.

    This test reads the source and asserts that ``if (bitmap.all_cached)``
    appears AFTER the ``segmentBitmap[chapterIndex]`` population loop.
    """
    js_path = PROJECT_ROOT / "library" / "web-v2" / "js" / "streaming-translate.js"
    src = js_path.read_text(encoding="utf-8")
    bitmap_pop_idx = src.find("segmentBitmap[chapterIndex].add(idx)")
    all_cached_branch_idx = src.find("if (bitmap.all_cached)")
    assert bitmap_pop_idx > 0, "segmentBitmap population loop not found"
    assert all_cached_branch_idx > 0, "bitmap.all_cached branch not found"
    assert bitmap_pop_idx < all_cached_branch_idx, (
        "segmentBitmap must be populated BEFORE the all_cached short-circuit "
        "— otherwise enterStreaming replays an empty Set and MSE is never fed "
        "for short-chapter books"
    )


def test_streaming_translate_js_has_chapter_advance_on_ended():
    """Static-source guard for v8.3.8.7 chapter auto-advance fix (RCA §4.8c).

    streaming-translate.js must attach an ``ended`` listener to the
    audio element while in STREAMING state and call advanceChapter
    when it fires. Without this, books play their active chapter
    through and then sit silent at ``currentTime == duration`` —
    worst-observed on short-ch=0 books (115401, 115852, 116062)
    during v8.3.8.6 proof, where the Audible intro is 1-3 segments
    and the 131+ actual-content segments in ch=1 never play.

    Expected: (1) an ``audio.addEventListener('ended', ...)`` call
    inside enterStreaming, (2) an ``advanceChapter`` function that
    tears down mseChain and POSTs ``/translate/stream`` with
    ``chapter_index: nextChapter``, (3) a matching
    ``removeEventListener('ended', ...)`` in enterIdle so
    book-switch doesn't leave a dead listener firing.
    """
    js_path = PROJECT_ROOT / "library" / "web-v2" / "js" / "streaming-translate.js"
    src = js_path.read_text(encoding="utf-8")
    assert "function advanceChapter" in src, "advanceChapter function missing"
    assert 'addEventListener("ended"' in src, (
        "enterStreaming must install an audio.ended listener"
    )
    assert 'removeEventListener("ended"' in src, (
        "enterIdle (and advanceChapter mid-transition) must clean up the ended listener"
    )
    # advanceChapter must actually attempt to POST /translate/stream with
    # the next chapter, not just log-and-quit
    assert "chapter_index: nextChapter" in src, (
        "advanceChapter must request streaming for nextChapter"
    )
    assert "totalChapters" in src, (
        "advanceChapter needs totalChapters to know when to stop"
    )


def test_stop_impl_deletes_pending(db_path):
    """Bug E fix: /api/translate/stop DELETEs pending rows (v8.3.2 semantics)."""
    sys.path.insert(0, str(PROJECT_ROOT / "library"))
    from backend.api_modular.streaming_translate import stop_streaming_impl

    for s in range(5):
        _insert_seg(db_path, segment=s, state="pending")
    _insert_seg(db_path, segment=99, state="processing")
    _insert_seg(db_path, segment=98, state="completed")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    stop_streaming_impl(conn, audiobook_id=1, locale="zh-Hans")

    pending = conn.execute(
        "SELECT COUNT(*) FROM streaming_segments WHERE state='pending'"
    ).fetchone()[0]
    processing = conn.execute(
        "SELECT COUNT(*) FROM streaming_segments WHERE state='processing'"
    ).fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM streaming_segments WHERE state='completed'"
    ).fetchone()[0]
    conn.close()
    assert pending == 0
    assert processing == 1
    assert completed == 1
