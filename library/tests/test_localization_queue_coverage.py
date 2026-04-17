"""Coverage-focused tests for ``library.localization.queue``.

Exercises the synchronous queue management paths — table/column bootstrap,
enqueue variants, priority bumping, status reads, stale-job recovery, job
lifecycle via ``_next_job`` and ``_finish_job``, and the ``_set_current``
in-memory/DB bridge. The long-running worker paths
(``_process_job``/``_run_stt_and_translate``/``_run_tts``) are intentionally
out of scope here — those are network-bound (Vast.ai GPU) and exercised
via integration fixtures on the test VM, not the unit suite.
"""

from __future__ import annotations

import sqlite3
import time
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
    conn.executescript(
        """
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
        """
    )
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
    between tests prevents cross-test pollution — especially the
    ``_current_status`` dict which would otherwise leak between cases.
    """
    # Reset module globals to known-clean state.
    lq._db_path = None
    lq._library_path = None
    lq._current_status = {}
    lq._shutdown_event.clear()
    # Don't touch _worker_thread — its _ensure_worker guard covers both
    # "None" and "not alive" cases, and we never let the worker start
    # during coverage tests.

    lq.init_queue(audiobooks_db, tmp_path)
    yield
    lq._db_path = None
    lq._library_path = None
    lq._current_status = {}


# ── init_queue / _ensure_queue_table / _recover_stale_jobs ──


class TestInitQueue:
    def test_init_creates_translation_queue_table(self, audiobooks_db: Path):
        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='translation_queue'"
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
        conn.executescript(
            """
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
            """
        )
        conn.commit()
        conn.close()

        lq.init_queue(db_path, tmp_path)

        conn = sqlite3.connect(str(db_path))
        try:
            cols = {
                r[1] for r in conn.execute("PRAGMA table_info(translation_queue)").fetchall()
            }
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

    def test_recover_stale_jobs_resets_processing_to_pending(
        self, audiobooks_db: Path
    ):
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

    def test_enqueue_start_worker_true_does_not_raise(self, monkeypatch):
        """``start_worker=True`` schedules ``_ensure_worker``; the real
        worker would call out to GPU services, so we stub it to a no-op
        and just confirm the call site doesn't explode. A clean call is
        enough signal — the worker thread machinery is covered by its own
        lock-guarded branches below."""
        called: list[bool] = []

        def _stub() -> None:
            called.append(True)

        monkeypatch.setattr(lq, "_ensure_worker", _stub)
        lq.enqueue(1, "zh-Hans", start_worker=True)
        assert called == [True]

    def test_enqueue_book_all_locales_skips_empty(
        self, audiobooks_db: Path, monkeypatch
    ):
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

    def test_enqueue_all_books_for_locale_only_covers_missing_en(
        self, audiobooks_db: Path
    ):
        """Books 1 has en chapter_subtitles; book 2 does not. The helper
        targets the un-transcribed book only."""
        inserted = lq.enqueue_all_books_for_locale("zh-Hans", priority=4)
        assert inserted == 1
        status = lq.get_queue_status()
        assert status["pending"] == 1

    def test_start_processing_calls_ensure_worker(self, monkeypatch):
        called: list[bool] = []

        def _stub() -> None:
            called.append(True)

        monkeypatch.setattr(lq, "_ensure_worker", _stub)
        lq.start_processing()
        assert called == [True]


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

    def test_get_queue_status_returns_current_when_set(self, audiobooks_db: Path):
        lq._current_status = {"audiobook_id": 1, "locale": "zh-Hans", "phase": "stt"}
        status = lq.get_queue_status()
        assert status["current"] is not None
        assert status["current"]["phase"] == "stt"

    def test_get_book_translation_status_returns_none_for_missing(
        self, audiobooks_db: Path
    ):
        assert lq.get_book_translation_status(999, "zh-Hans") is None

    def test_get_book_translation_status_returns_row(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=10)
        result = lq.get_book_translation_status(1, "zh-Hans")
        assert result is not None
        assert result["audiobook_id"] == 1
        assert result["locale"] == "zh-Hans"
        assert result["state"] == "pending"
        assert result["priority"] == 10

    def test_get_book_translation_status_merges_current(self, audiobooks_db: Path):
        """When the row is in 'processing' state and matches _current_status,
        the live status dict is overlaid onto the DB row."""
        conn = sqlite3.connect(str(audiobooks_db))
        conn.execute(
            "INSERT INTO translation_queue "
            "(audiobook_id, locale, state) "
            "VALUES (1, 'zh-Hans', 'processing')"
        )
        conn.commit()
        conn.close()

        lq._current_status = {
            "audiobook_id": 1,
            "locale": "zh-Hans",
            "phase": "transcribing",
            "message": "Chapter 5/42",
            "chapter_index": 4,
            "chapter_total": 42,
        }
        result = lq.get_book_translation_status(1, "zh-Hans")
        assert result is not None
        assert result["phase"] == "transcribing"
        assert result["message"] == "Chapter 5/42"
        assert result["chapter_total"] == 42


# ── _next_job / _finish_job / _set_current internals ──


class TestJobLifecycle:
    def test_next_job_returns_none_when_empty(self, audiobooks_db: Path):
        assert lq._next_job() is None

    def test_next_job_claims_highest_priority_first(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=1)
        # Note: audiobook 2 needs chapter_subtitles en-less record first so
        # enqueue_all_books_for_locale is a separate pathway — here we use
        # plain enqueue which ignores the dependency.
        lq.enqueue(2, "zh-Hans", priority=99)

        job = lq._next_job()
        assert job is not None
        assert job["audiobook_id"] == 2
        # And it was flipped to 'processing' by the claim.
        status = lq.get_queue_status()
        assert status["processing"] == 1

    def test_next_job_respects_fifo_within_same_priority(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans", priority=5)
        # Sleep a beat so created_at timestamps differ in SQLite's 1s resolution.
        time.sleep(1.1)
        lq.enqueue(2, "zh-Hans", priority=5)

        job = lq._next_job()
        assert job is not None
        assert job["audiobook_id"] == 1

    def test_finish_job_marks_completed(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans")
        job = lq._next_job()
        assert job is not None
        lq._finish_job(job["id"], "completed")

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT state, error, finished_at "
                "FROM translation_queue WHERE id = ?",
                (job["id"],),
            ).fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] is None
            assert row[2] is not None  # finished_at written
        finally:
            conn.close()
        # _current_status must be cleared after finish.
        assert lq._current_status == {}

    def test_finish_job_records_error(self, audiobooks_db: Path):
        lq.enqueue(1, "zh-Hans")
        job = lq._next_job()
        assert job is not None
        lq._finish_job(job["id"], "failed", error="GPU unavailable")

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT state, error FROM translation_queue WHERE id = ?",
                (job["id"],),
            ).fetchone()
            assert row is not None
            assert row[0] == "failed"
            assert row[1] == "GPU unavailable"
        finally:
            conn.close()

    def test_set_current_populates_in_memory_dict(self, audiobooks_db: Path):
        lq._set_current(42, "zh-Hans", phase="starting", message="Go!")
        assert lq._current_status["audiobook_id"] == 42
        assert lq._current_status["locale"] == "zh-Hans"
        assert lq._current_status["phase"] == "starting"
        assert lq._current_status["message"] == "Go!"
        assert "updated_at" in lq._current_status

    def test_set_current_persists_step_to_db(self, audiobooks_db: Path):
        """When ``step`` is included, _set_current writes it into the
        processing row so a polling client sees progress between
        heartbeats."""
        lq.enqueue(1, "zh-Hans")
        job = lq._next_job()
        assert job is not None
        lq._set_current(1, "zh-Hans", step="tts", phase="synthesizing")

        conn = sqlite3.connect(str(audiobooks_db))
        try:
            row = conn.execute(
                "SELECT step FROM translation_queue WHERE id = ?",
                (job["id"],),
            ).fetchone()
            assert row is not None
            assert row[0] == "tts"
        finally:
            conn.close()

    def test_set_current_swallows_db_errors(self, monkeypatch):
        """Heartbeat writes must never raise into the worker."""
        def _broken() -> None:
            raise sqlite3.Error("intentional")

        monkeypatch.setattr(lq, "_get_db", _broken)
        # No assertion beyond "doesn't raise" — swallowing is the contract.
        lq._set_current(1, "zh-Hans", step="stt")


# ── shutdown / worker machinery (safe subset) ──


class TestShutdown:
    def test_shutdown_signals_event_when_no_worker(self):
        """shutdown() is safe to call when no worker ever started."""
        lq.shutdown()
        assert lq._shutdown_event.is_set()

    def test_ensure_worker_skips_when_thread_alive(self, monkeypatch):
        """If a worker is already alive, _ensure_worker must return without
        starting another thread. We fake an alive thread and assert that
        Thread() is never invoked."""
        class _FakeThread:
            def is_alive(self) -> bool:
                return True

        lq._worker_thread = _FakeThread()
        started: list[bool] = []

        class _Block:
            def __init__(self, *a, **kw) -> None:
                started.append(True)

            def start(self) -> None:
                started.append(True)

        monkeypatch.setattr("threading.Thread", _Block)
        lq._ensure_worker()
        assert started == []  # no thread creation
        lq._worker_thread = None
