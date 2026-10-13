"""Coverage-focused tests for ``library.localization.queue``.

Exercises the queue management paths — table/column bootstrap, enqueue
variants, priority bumping, status reads and stale-claim recovery. The module
executes no queue work since Audiobook-Manager-4j4 (the scheduled drain does,
out of process); see tests/test_translation_two_stage.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from localization import queue as lq


@pytest.fixture
def audiobooks_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with the audiobooks + chapter_subtitles tables.

    The queue only touches three tables (``audiobooks``, ``chapter_subtitles``,
    ``translation_queue``), so stubbing a minimal schema is sufficient here.
    """
    db_path = tmp_path / "queue_coverage.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL
        );

        CREATE TABLE chapter_subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            vtt_path TEXT NOT NULL,
            stt_provider TEXT,
            translation_provider TEXT,
            UNIQUE(audiobook_id, chapter_index, locale)
        );

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
            audio_path TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            UNIQUE(audiobook_id, chapter_index, segment_index, locale)
        );
        """)
    # Seed two books — one that already has an en-locale transcription, and
    # one that does not. The "enqueue_all_books_for_locale" helper filters
    # on whether the book has any en chapter_subtitles rows (those are the
    # books that have NOT been transcribed yet).
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'Book A', '/tmp/a.opus')"
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (2, 'Book B', '/tmp/b.opus')"
    )
    conn.execute(
        "INSERT INTO chapter_subtitles (audiobook_id, chapter_index, locale, vtt_path) "
        "VALUES (1, 0, 'en', '/tmp/a.ch0.vtt')"
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _reset_queue_module(audiobooks_db: Path, tmp_path: Path):
    """Ensure the queue module globals are bound to the test DB for every test.

    The queue module stores ``_db_path`` at module level. Clearing state
    between tests prevents cross-test pollution.
    """
    # Reset module globals to known-clean state.
    lq._db_path = None
    lq._library_path = None

    lq.init_queue(audiobooks_db, tmp_path)
    yield
    lq._db_path = None
    lq._library_path = None


# ── init_queue / _ensure_queue_table / _recover_stale_jobs ──


class TestInitQueue:
    def test_init_creates_translation_queue_table(self, audiobooks_db: Path):
        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='translation_queue'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_init_creates_indices(self, audiobooks_db: Path):
        conn = sqlite3.connect(str(audiobooks_db))
        try:
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_tq_state" in indexes
            assert "idx_tq_last_progress" in indexes
        finally:
            conn.close()

    def test_init_adds_missing_legacy_columns(self, tmp_path: Path):
        """A DB whose translation_queue predates last_progress_at/total_chapters
        columns should be migrated in place, not re-created."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT, file_path TEXT);
            CREATE TABLE chapter_subtitles (
                audiobook_id INTEGER, chapter_index INTEGER,
                locale TEXT, vtt_path TEXT
            );
            CREATE TABLE translation_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audiobook_id INTEGER NOT NULL,
                locale TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                state TEXT DEFAULT 'pending',
                step TEXT DEFAULT 'stt',
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                UNIQUE(audiobook_id, locale)
            );
            INSERT INTO translation_queue (audiobook_id, locale, started_at)
            VALUES (7, 'zh-Hans', '2020-01-01 00:00:00');
            """)
        conn.commit()
        conn.close()

        lq.init_queue(db_path, tmp_path)

        conn = sqlite3.connect(str(db_path))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(translation_queue)").fetchall()}
            assert "last_progress_at" in cols
            assert "total_chapters" in cols
            # Existing row must have last_progress_at populated from started_at.
            row = conn.execute(
                "SELECT last_progress_at FROM translation_queue "
                "WHERE audiobook_id = 7 AND locale = 'zh-Hans'"
            ).fetchone()
            assert row is not None and row[0] == "2020-01-01 00:00:00"
        finally:
            conn.close()

    def test_recover_stale_jobs_resets_processing_to_pending(self, audiobooks_db: Path):
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state, started_at) "
            "VALUES (1, 'zh-Hans', 'processing', '2024-01-01 00:00:00')"
        )
        conn.commit()
        conn.close()

        lq._recover_stale_jobs()

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT state, started_at FROM translation_queue "
                "WHERE audiobook_id = 1 AND locale = 'zh-Hans'"
            ).fetchone()
            assert row is not None
            assert row[0] == "pending"
            assert row[1] is None
        finally:
            conn.close()

    def test_get_db_raises_when_not_initialized(self):
        lq._db_path = None
        with pytest.raises(RuntimeError, match="not initialized"):
            lq._get_db()


# ── enqueue / enqueue_book_all_locales / enqueue_all_books_for_locale ──


class TestEnqueue:
    def test_enqueue_single_book_and_locale(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=5)
        status = lq.get_queue_status()
        assert status["pending"] == 1

    def test_enqueue_is_idempotent(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=5)
        lq.enqueue(1, "zh-Hans", priority=5)
        status = lq.get_queue_status()
        # UNIQUE constraint + INSERT OR IGNORE means second insert is a no-op.
        assert status["pending"] == 1

    def test_enqueue_book_all_locales_skips_empty(self, audiobooks_db: Path, monkeypatch):
        """If SUPPORTED_LOCALES contains only 'en', no rows are written."""

        class _Cfg:
            SUPPORTED_LOCALES = ["en"]

        monkeypatch.setitem(lq.__dict__, "config", _Cfg)
        # But the function imports at call-time: patch the real config module.
        import localization.config as cfg

        monkeypatch.setattr(cfg, "SUPPORTED_LOCALES", ["en"])
        lq.enqueue_book_all_locales(1, priority=3)
        status = lq.get_queue_status()
        assert status["pending"] == 0

    def test_enqueue_book_all_locales_writes_every_non_english(
        self, audiobooks_db: Path, monkeypatch
    ):
        import localization.config as cfg

        monkeypatch.setattr(cfg, "SUPPORTED_LOCALES", ["en", "zh-Hans", "ja"])
        lq.enqueue_book_all_locales(2, priority=7)
        status = lq.get_queue_status()
        assert status["pending"] == 2

    def test_enqueue_all_books_for_locale_only_covers_missing_en(self, audiobooks_db: Path):
        """Books 1 has en chapter_subtitles; book 2 does not. The helper
        targets the un-transcribed book only."""
        inserted = lq.enqueue_all_books_for_locale("zh-Hans", priority=4)
        assert inserted == 1
        status = lq.get_queue_status()
        assert status["pending"] == 1


# ── bump_priority / get_queue_status / get_book_translation_status ──


class TestPriorityAndStatus:
    def test_bump_priority_raises_pending_priority(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=5)
        lq.bump_priority(1, "zh-Hans", priority=100)

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT priority FROM translation_queue "
                "WHERE audiobook_id = 1 AND locale = 'zh-Hans'"
            ).fetchone()
            assert row is not None and row[0] == 100
        finally:
            conn.close()

    def test_bump_priority_keeps_existing_when_lower(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=200)
        lq.bump_priority(1, "zh-Hans", priority=50)

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT priority FROM translation_queue "
                "WHERE audiobook_id = 1 AND locale = 'zh-Hans'"
            ).fetchone()
            assert row is not None and row[0] == 200
        finally:
            conn.close()

    def test_bump_priority_ignores_non_pending(self, audiobooks_db: Path):
        """Bumping only affects pending rows — a processing row is left alone."""
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state, priority) "
            "VALUES (1, 'zh-Hans', 'processing', 0)"
        )
        conn.commit()
        conn.close()

        lq.bump_priority(1, "zh-Hans", priority=100)

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT priority FROM translation_queue "
                "WHERE audiobook_id = 1 AND locale = 'zh-Hans'"
            ).fetchone()
            assert row is not None and row[0] == 0
        finally:
            conn.close()

    def test_get_queue_status_counts_every_state(self, audiobooks_db: Path):
        conn = sqlite3.connect(str(audiobooks_db))
        conn.executemany(
            "INSERT INTO translation_queue (audiobook_id, locale, state) VALUES (?, ?, ?)",
            [
                (1, "zh-Hans", "pending"),
                (2, "zh-Hans", "pending"),
                (1, "ja", "processing"),
                (2, "ja", "completed"),
                (3, "fr", "failed"),
            ],
        )
        conn.commit()
        conn.close()

        status = lq.get_queue_status()
        assert status["pending"] == 2
        assert status["processing"] == 1
        assert status["completed"] == 1
        assert status["failed"] == 1
        assert status["current"] is None

    def test_get_book_translation_status_returns_none_for_missing(self, audiobooks_db: Path):
        assert lq.get_book_translation_status(999, "zh-Hans") is None

    def test_get_book_translation_status_returns_row(self, audiobooks_db: Path):
        """Row fields pass through unchanged. Uses 'en' locale because
        pending non-en rows now short-circuit to 'deferred' to mask stale
        pre-streaming batch queue entries (see *_defers_* tests)."""
        lq.enqueue(1, "en", priority=10)
        result = lq.get_book_translation_status(1, "en")
        assert result is not None
        assert result["audiobook_id"] == 1
        assert result["locale"] == "en"
        assert result["state"] == "pending"
        assert result["priority"] == 10

    def test_get_book_translation_status_defers_stale_failed_non_en(self, audiobooks_db: Path):
        """Legacy translation_queue failures for non-en locales must not surface
        as current status — they sit in the DB from the pre-streaming batch era
        (1,844 rows stamped 'No STT provider configured' on 2026-04-19 QA) and
        were being rendered as "字幕生成失败" toasts on every first book-open.
        get_book_translation_status now returns state='deferred' so the UI hides
        the legacy banner and defers to streaming-overlay.
        """
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state, error) "
            "VALUES (1, 'zh-Hans', 'failed', 'No STT provider configured.')"
        )
        conn.commit()
        conn.close()

        result = lq.get_book_translation_status(1, "zh-Hans")
        assert result is not None
        assert result["state"] == "deferred"
        assert result["reason"] == "streaming_pipeline"
        # No stale 'failed'/'error' field surfaces to the caller.
        assert "error" not in result

    def test_get_book_translation_status_defers_stale_pending_non_en(self, audiobooks_db: Path):
        """Same deferral applies to stale 'pending'/'processing' rows on non-en
        locales — no active worker drains the legacy queue, so leaving them as
        visible 'pending' would keep the UI polling forever."""
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state) VALUES (1, 'zh-Hans', 'pending')"
        )
        conn.commit()
        conn.close()

        result = lq.get_book_translation_status(1, "zh-Hans")
        assert result is not None
        assert result["state"] == "deferred"

    def test_get_book_translation_status_passes_failed_en_unchanged(self, audiobooks_db: Path):
        """English-locale failures are *not* deferred — there's no streaming
        replacement for the en pipeline (en subtitles come from Whisper STT
        direct, not translation), so surfacing the real failure is correct."""
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state, error) "
            "VALUES (1, 'en', 'failed', 'Whisper STT crashed')"
        )
        conn.commit()
        conn.close()

        result = lq.get_book_translation_status(1, "en")
        assert result is not None
        assert result["state"] == "failed"
        assert result["error"] == "Whisper STT crashed"

    def test_get_book_translation_status_completed_non_en_passes_through(self, audiobooks_db: Path):
        """A legitimately-completed legacy row (e.g. VTT files exist on disk
        from a successful batch run) must still pass through — the UI reads it
        to hide the banner, not to show an error."""
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state) VALUES (1, 'zh-Hans', 'completed')"
        )
        conn.commit()
        conn.close()

        result = lq.get_book_translation_status(1, "zh-Hans")
        assert result is not None
        assert result["state"] == "completed"


# ── _next_job / _finish_job / _set_current internals ──


# ── shutdown / worker machinery (safe subset) ──


# ── _run_stt_and_translate closure ─────────────────────────────────────


# ── _load_vtt_rows ────────────────────────────────────────────────────
