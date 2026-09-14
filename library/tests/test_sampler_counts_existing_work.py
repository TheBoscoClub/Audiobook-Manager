"""A sampler job must count translation that already exists, whoever made it.

Found 2026-09-13. ``segments_done`` is incremented in exactly one place —
``streaming_translate.py``, and only when the finished segment carries
``origin='sampler'``. ``streaming_segments`` has
``UNIQUE(audiobook_id, chapter_index, segment_index, locale)``, so when a
listener has already streamed a book the sampler's ``INSERT OR IGNORE`` yields
to those ``origin='live'`` rows and creates nothing.

The result is a job that can never finish: its slots are translated, no
sampler-origin completion will ever fire for them, so ``segments_done`` is
frozen below ``segments_target`` and the status sticks at ``running``
permanently. Two books sat that way from April to September 2026.

Both tables held a true fact. The only wire between them ran through
``origin``, so a correct answer arriving by any other route was unreadable.
Idempotency has to ask the destination whether the work is there, not ask
this subsystem whether it was the one who did it.
"""

from __future__ import annotations

import sqlite3

import pytest
from localization.sampler import enqueue_sampler

LOCALE = "zh-Hans"
# Long enough that compute_sampler_range returns a real multi-segment scope.
DURATIONS = [600.0, 600.0, 600.0]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE sampler_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "audiobook_id INTEGER NOT NULL, locale TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', segments_target INTEGER NOT NULL, "
        "segments_done INTEGER NOT NULL DEFAULT 0, error TEXT, "
        "created_at TIMESTAMP, updated_at TIMESTAMP, UNIQUE(audiobook_id, locale))"
    )
    c.execute(
        "CREATE TABLE streaming_segments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "audiobook_id INTEGER NOT NULL, chapter_index INTEGER NOT NULL, "
        "segment_index INTEGER NOT NULL, locale TEXT NOT NULL, state TEXT, "
        "priority INTEGER DEFAULT 0, origin TEXT, vtt_content TEXT, "
        "UNIQUE(audiobook_id, chapter_index, segment_index, locale))"
    )
    yield c
    c.close()


def _scope_slots(conn, book=1):
    """The (chapter, segment) slots the sampler asked for."""
    return conn.execute(
        "SELECT chapter_index, segment_index FROM streaming_segments "
        "WHERE audiobook_id = ? ORDER BY chapter_index, segment_index",
        (book,),
    ).fetchall()


def _prefill(conn, slots, *, state="completed", origin="live", book=1):
    for ch, seg in slots:
        conn.execute(
            "INSERT OR IGNORE INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority, "
            " origin, vtt_content) VALUES (?, ?, ?, ?, ?, 2, ?, '你好')",
            (book, ch, seg, LOCALE, state, origin),
        )
    conn.commit()


def _job(conn, book=1):
    return conn.execute(
        "SELECT status, segments_target, segments_done FROM sampler_jobs WHERE audiobook_id = ?",
        (book,),
    ).fetchone()


class TestExistingTranslationCounts:
    def test_fully_covered_book_completes_without_new_work(self, conn):
        """The live case: every slot already translated by playback. The job
        must finish, not sit `running` forever waiting on segments that will
        never be created because their rows already exist."""
        first = enqueue_sampler(conn, 1, LOCALE, DURATIONS)
        slots = _scope_slots(conn)
        conn.execute("DELETE FROM streaming_segments")
        conn.execute("DELETE FROM sampler_jobs")
        conn.commit()
        _prefill(conn, [(r[0], r[1]) for r in slots])

        result = enqueue_sampler(conn, 1, LOCALE, DURATIONS)

        assert result["status"] == "complete"
        job = _job(conn)
        assert job["status"] == "complete"
        assert job["segments_done"] >= first["segments_target"]

    def test_partial_coverage_is_credited_not_discarded(self, conn):
        """Half-listened book: the done half must count, so the job needs only
        the remainder. Starting from zero would re-translate paid-for work."""
        enqueue_sampler(conn, 1, LOCALE, DURATIONS)
        slots = [(r[0], r[1]) for r in _scope_slots(conn)]
        target = _job(conn)["segments_target"]
        half = slots[: len(slots) // 2]
        conn.execute("DELETE FROM streaming_segments")
        conn.execute("DELETE FROM sampler_jobs")
        conn.commit()
        _prefill(conn, half)

        enqueue_sampler(conn, 1, LOCALE, DURATIONS)

        job = _job(conn)
        assert job["status"] == "running"
        assert job["segments_done"] == len(half)
        assert job["segments_done"] < target

    def test_unfinished_existing_segments_are_not_credited(self, conn):
        """A pending or failed row is work outstanding, not work done. Crediting
        it would mark a book finished that has never been translated at all."""
        enqueue_sampler(conn, 1, LOCALE, DURATIONS)
        slots = [(r[0], r[1]) for r in _scope_slots(conn)]
        conn.execute("DELETE FROM streaming_segments")
        conn.execute("DELETE FROM sampler_jobs")
        conn.commit()
        _prefill(conn, slots, state="pending")

        result = enqueue_sampler(conn, 1, LOCALE, DURATIONS)

        assert result["status"] == "running"
        assert _job(conn)["segments_done"] == 0

    def test_another_locale_s_completion_is_not_credited(self, conn):
        """Spanish playback says nothing about the Chinese sampler."""
        enqueue_sampler(conn, 1, LOCALE, DURATIONS)
        slots = [(r[0], r[1]) for r in _scope_slots(conn)]
        conn.execute("DELETE FROM streaming_segments")
        conn.execute("DELETE FROM sampler_jobs")
        conn.commit()
        for ch, seg in slots:
            conn.execute(
                "INSERT INTO streaming_segments (audiobook_id, chapter_index, "
                "segment_index, locale, state, priority, origin) "
                "VALUES (1, ?, ?, 'es', 'completed', 2, 'live')",
                (ch, seg),
            )
        conn.commit()

        enqueue_sampler(conn, 1, LOCALE, DURATIONS)

        assert _job(conn)["segments_done"] == 0

    def test_a_fresh_book_is_unaffected(self, conn):
        """No existing translation anywhere — the ordinary path must still
        enqueue a full job and create its segments."""
        result = enqueue_sampler(conn, 1, LOCALE, DURATIONS)

        assert result["status"] == "running"
        assert _job(conn)["segments_done"] == 0
        assert len(_scope_slots(conn)) == result["segments_target"]
