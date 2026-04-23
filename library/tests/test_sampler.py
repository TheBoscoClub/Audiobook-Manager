"""Regression tests for the 6-minute pretranslation sampler (v8.3.8).

Covers:
- Pure algorithm ``compute_sampler_range`` — each trace case from the design
  discussion with the user.
- Priority-invariant trigger: DB-level ABORT when origin='sampler' tries to
  land at priority < 2. Tested via sqlite3 directly so trigger behavior is
  pinned regardless of Python-side logic.
- ``enqueue_sampler`` idempotency: re-enqueue on complete is a no-op;
  re-enqueue on pending/failed resets the job.
- ``enqueue_sampler`` creates the expected segment rows with origin='sampler'
  and priority=2 for a realistic chapter layout.
- Trigger correctly allows priority>=2 sampler rows (no false positives).
- Constants stay in sync between the shared module and the API module.
"""

from __future__ import annotations

import sqlite3

import pytest

from localization.sampler import (
    SAMPLER_MAX_EXTEND_SECONDS,
    SAMPLER_MIN_SECONDS,
    SAMPLER_PRIORITY,
    SEGMENT_DURATION_SEC,
    compute_sampler_range,
    enqueue_sampler,
)


# ─── Pure algorithm — compute_sampler_range ─────────────────────────────────


def test_range_empty_input():
    assert compute_sampler_range([]) == []


def test_range_all_zero_durations():
    assert compute_sampler_range([0, 0, -1]) == []


def test_range_short_book_samples_everything():
    # 4-min book total — below the 6-min min. Sample the whole thing.
    # 240s / 30s = 8 segments.
    assert compute_sampler_range([240.0]) == [(0, 8)]


def test_range_exactly_6_min_single_chapter():
    # 6-min chapter exactly. No extend needed (remainder_in_last = 0).
    # 12 segments.
    assert compute_sampler_range([360.0]) == [(0, 12)]


def test_range_slightly_over_6_min_extends():
    # 7-min chapter. Remainder after 6 min = 60s, within MAX_EXTEND (180s).
    # Extend to full chapter → 14 segments.
    assert compute_sampler_range([420.0]) == [(0, 14)]


def test_range_boundary_9_min_extends():
    # 9-min chapter — remainder is exactly 3 min == MAX_EXTEND. Extend.
    # 540/30 = 18 segments.
    assert compute_sampler_range([540.0]) == [(0, 18)]


def test_range_long_chapter_hard_stops():
    # 10-min chapter — remainder 4 min > MAX_EXTEND. Hard-stop at 6 min.
    # 12 segments.
    assert compute_sampler_range([600.0]) == [(0, 12)]


def test_range_spills_into_next_chapter_extends():
    # ch0 = 5 min, ch1 = 2 min. After 5+1 = 6 min we're in ch1 with
    # 1 min remainder — within extend slack. Take full ch1.
    # ch0: 300/30 = 10, ch1: 120/30 = 4. Total 14 segs = 7 min.
    assert compute_sampler_range([300.0, 120.0]) == [(0, 10), (1, 4)]


def test_range_spills_into_long_next_chapter_stops():
    # ch0 = 4 min, ch1 = 15 min. After 4+2 = 6 min we're in ch1 with
    # 13 min remainder — way beyond extend. Hard-stop at 6 min.
    # ch0: 240/30 = 8, ch1: 120/30 = 4. Total 12 segs = 6 min.
    assert compute_sampler_range([240.0, 900.0]) == [(0, 8), (1, 4)]


def test_range_short_intro_long_body():
    # ch0 = 3 min, ch1 = 20 min. 3+3 = 6 min, ch1 remainder 17 min → stop.
    assert compute_sampler_range([180.0, 1200.0]) == [(0, 6), (1, 6)]


def test_range_skips_zero_duration_chapters():
    # A zero-duration chapter at index 1 shouldn't consume budget.
    # ch0=4 (240s), ch1=0, ch2=5 (300s). Accumulated after ch0=240, after
    # ch2=540. Reached at ch2. ch0 included, ch1 SKIPPED.
    # Earlier=[(0,240)], last=(2, 300). Needed from ch2=120, remainder=180.
    # 180 <= 180 → extend to full ch2.
    # ch0: 240/30=8, ch2: 300/30=10. Total 18 segs.
    assert compute_sampler_range([240.0, 0, 300.0]) == [(0, 8), (2, 10)]


# ─── DB-level priority invariant ────────────────────────────────────────────


@pytest.fixture
def sampler_db(tmp_path):
    """Fresh sqlite DB with schema + triggers installed."""
    db_path = tmp_path / "test_sampler.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        CREATE TABLE audiobooks (id INTEGER PRIMARY KEY);

        CREATE TABLE streaming_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            segment_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 2,
            worker_id TEXT,
            vtt_content TEXT,
            source_vtt_content TEXT,
            audio_path TEXT,
            error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            origin TEXT NOT NULL DEFAULT 'live'
                CHECK (origin IN ('live','sampler','backlog')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(audiobook_id, chapter_index, segment_index, locale),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        );

        CREATE TRIGGER streaming_segments_sampler_priority_ins
        BEFORE INSERT ON streaming_segments
        WHEN NEW.origin = 'sampler' AND NEW.priority < 2
        BEGIN
            SELECT RAISE(ABORT, 'sampler rows must have priority >= 2');
        END;

        CREATE TRIGGER streaming_segments_sampler_priority_upd
        BEFORE UPDATE ON streaming_segments
        WHEN NEW.origin = 'sampler' AND NEW.priority < 2
        BEGIN
            SELECT RAISE(ABORT, 'sampler rows must have priority >= 2');
        END;

        CREATE TABLE sampler_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            segments_target INTEGER NOT NULL,
            segments_done INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(audiobook_id, locale),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        );
    """)
    conn.execute("INSERT INTO audiobooks (id) VALUES (42)")
    conn.commit()
    yield conn
    conn.close()


def test_trigger_rejects_sampler_priority_0(sampler_db):
    with pytest.raises(sqlite3.IntegrityError, match="priority >= 2"):
        sampler_db.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, priority, origin) "
            "VALUES (42, 0, 0, 'zh-Hans', 0, 'sampler')"
        )


def test_trigger_rejects_sampler_priority_1(sampler_db):
    with pytest.raises(sqlite3.IntegrityError, match="priority >= 2"):
        sampler_db.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, priority, origin) "
            "VALUES (42, 0, 0, 'zh-Hans', 1, 'sampler')"
        )


def test_trigger_allows_sampler_priority_2(sampler_db):
    sampler_db.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, priority, origin) "
        "VALUES (42, 0, 0, 'zh-Hans', 2, 'sampler')"
    )
    sampler_db.commit()
    rows = sampler_db.execute("SELECT origin, priority FROM streaming_segments").fetchall()
    assert rows[0]["origin"] == "sampler" and rows[0]["priority"] == 2


def test_trigger_allows_live_priority_0(sampler_db):
    # The invariant constrains origin='sampler' only — live at p0 is fine.
    sampler_db.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, priority, origin) "
        "VALUES (42, 0, 0, 'zh-Hans', 0, 'live')"
    )
    sampler_db.commit()
    row = sampler_db.execute("SELECT origin, priority FROM streaming_segments").fetchone()
    assert row["origin"] == "live" and row["priority"] == 0


def test_trigger_rejects_update_that_would_violate(sampler_db):
    sampler_db.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, priority, origin) "
        "VALUES (42, 0, 0, 'zh-Hans', 2, 'sampler')"
    )
    sampler_db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="priority >= 2"):
        sampler_db.execute("UPDATE streaming_segments SET priority = 1 WHERE origin = 'sampler'")


# ─── enqueue_sampler behavior ────────────────────────────────────────────────


def test_enqueue_skips_en_source_locale(sampler_db):
    result = enqueue_sampler(sampler_db, 42, "en", [360.0])
    assert result["status"] == "skipped"
    # No side effects.
    assert sampler_db.execute("SELECT COUNT(*) FROM sampler_jobs").fetchone()[0] == 0


def test_enqueue_creates_job_and_segments(sampler_db):
    # 7-min chapter → 14 segments with extend.
    result = enqueue_sampler(sampler_db, 42, "zh-Hans", [420.0])
    assert result["status"] == "running"
    assert result["segments_target"] == 14

    # sampler_jobs row present with correct state.
    job = sampler_db.execute("SELECT status, segments_target FROM sampler_jobs").fetchone()
    assert job["status"] == "running"
    assert job["segments_target"] == 14

    # streaming_segments rows present, all origin='sampler', all priority=2.
    segs = sampler_db.execute(
        "SELECT COUNT(*), origin, priority FROM streaming_segments GROUP BY origin, priority"
    ).fetchall()
    assert len(segs) == 1
    count, origin, priority = segs[0][0], segs[0]["origin"], segs[0]["priority"]
    assert count == 14
    assert origin == "sampler"
    assert priority == SAMPLER_PRIORITY


def test_enqueue_idempotent_on_complete(sampler_db):
    # Set up a complete job.
    enqueue_sampler(sampler_db, 42, "zh-Hans", [360.0])
    sampler_db.execute("UPDATE sampler_jobs SET status = 'complete', segments_done = 12")
    sampler_db.commit()
    baseline_segs = sampler_db.execute("SELECT COUNT(*) FROM streaming_segments").fetchone()[0]

    # Re-enqueue — should return 'complete' short-circuit, NOT create rows.
    result = enqueue_sampler(sampler_db, 42, "zh-Hans", [360.0])
    assert result["status"] == "complete"
    assert result["reason"] == "already complete"

    after_segs = sampler_db.execute("SELECT COUNT(*) FROM streaming_segments").fetchone()[0]
    assert after_segs == baseline_segs  # no new rows


def test_enqueue_resets_failed_job(sampler_db):
    enqueue_sampler(sampler_db, 42, "zh-Hans", [360.0])
    sampler_db.execute("UPDATE sampler_jobs SET status = 'failed', error = 'x'")
    sampler_db.commit()

    result = enqueue_sampler(sampler_db, 42, "zh-Hans", [360.0])
    assert result["status"] == "running"
    job = sampler_db.execute("SELECT status, error FROM sampler_jobs").fetchone()
    assert job["status"] == "running"
    assert job["error"] is None


def test_enqueue_empty_chapters_returns_error(sampler_db):
    result = enqueue_sampler(sampler_db, 42, "zh-Hans", [])
    assert result["status"] == "error"
    assert "empty sampler scope" in result["reason"].lower()


# ─── Constants stay in sync between modules ──────────────────────────────────


def test_constants_match_api_module():
    """The streaming_translate.py API module has its own copies of these
    constants for backward import-locality. A drift between them and the
    shared module would silently change behavior. Pin both."""
    try:
        from backend.api_modular import streaming_translate as st_mod
    except ImportError:
        pytest.skip("backend module not importable in this test env")
    assert st_mod.SEGMENT_DURATION_SEC == SEGMENT_DURATION_SEC
    assert st_mod.SAMPLER_MIN_SECONDS == SAMPLER_MIN_SECONDS
    assert st_mod.SAMPLER_MAX_EXTEND_SECONDS == SAMPLER_MAX_EXTEND_SECONDS
    assert st_mod.SAMPLER_PRIORITY == SAMPLER_PRIORITY
    # Adaptive buffer thresholds — these live in streaming_translate only but
    # are part of the sampler contract.
    assert st_mod.BUFFER_FILL_THRESHOLD_COLD == 3
    assert st_mod.BUFFER_FILL_THRESHOLD_WARM == 4
