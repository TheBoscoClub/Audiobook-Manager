"""Regression test for chapter-advance starvation in the stream-translate worker.

Pre-fix bug: ``claim_next_segment`` ordered by
``(priority ASC, chapter_index ASC, segment_index ASC)``. When the player
advanced from chapter N to N+1 mid-book, both chapters had pending rows at
priority 0; the worker kept claiming chapter N because chapter_index ASC
won. The user buffered indefinitely on chapter N+1 while the worker drained
the previous chapter.

Fix: claim ordering now prefers rows where ``chapter_index`` matches the
most-recent ``streaming_sessions.active_chapter`` for the same
``(audiobook_id, locale)`` tuple, *within* the priority tier. The 3-tier
priority semantics (P0/P1/P2) are unchanged — the active-chapter preference
only breaks ties.

Reproduces book 115056 / ch6 incident on prod 2026-05-04 ~21:00 CDT.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER_PATH = REPO / "scripts" / "stream-translate-worker.py"
SCHEMA_PATH = REPO / "library" / "backend" / "schema.sql"


def _load_worker():
    spec = importlib.util.spec_from_file_location("stream_translate_worker", WORKER_PATH)
    assert spec is not None and spec.loader is not None, "Failed to load stream_translate_worker"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker_db(tmp_path):
    """Build an SQLite DB with the production schema for streaming_* tables."""
    import sqlite3

    db_path = tmp_path / "test_streaming.db"
    schema_sql = SCHEMA_PATH.read_text()

    conn = sqlite3.connect(db_path)
    # The schema.sql has many tables; the worker's claim query references only
    # streaming_segments and streaming_sessions (plus their indexes/triggers).
    # Loading the entire schema is the safest way to keep parity with prod.
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
    return str(db_path)


def _insert_audiobook(db_path: str, book_id: int) -> None:
    """streaming_segments has a FK to audiobooks; insert a placeholder."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (?, ?, ?)",
        (book_id, "Test Book", "/tmp/test.opus"),  # nosec B108 — fake DB row, no FS access
    )
    conn.commit()
    conn.close()


def _insert_session(db_path: str, book_id: int, locale: str, active_chapter: int) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO streaming_sessions (audiobook_id, locale, active_chapter, state) "
        "VALUES (?, ?, ?, 'streaming')",
        (book_id, locale, active_chapter),
    )
    conn.commit()
    conn.close()


def _insert_pending(db_path: str, book_id: int, locale: str, ch: int, seg: int, prio: int) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority, origin) "
        "VALUES (?, ?, ?, ?, 'pending', ?, 'live')",
        (book_id, ch, seg, locale, prio),
    )
    conn.commit()
    conn.close()


def test_active_chapter_wins_within_priority_tier(worker_db):
    """When player is on chapter 6 and chapters 5 & 6 both have P0 pending rows,
    the worker must claim chapter 6 first (active chapter wins)."""
    book = 9999
    locale = "zh-Hans"
    _insert_audiobook(worker_db, book)
    _insert_session(worker_db, book, locale, active_chapter=6)
    # Chapter 5 — older, lower index, would win pre-fix
    _insert_pending(worker_db, book, locale, ch=5, seg=10, prio=0)
    _insert_pending(worker_db, book, locale, ch=5, seg=11, prio=0)
    # Chapter 6 — player's active chapter, must win post-fix
    _insert_pending(worker_db, book, locale, ch=6, seg=0, prio=0)
    _insert_pending(worker_db, book, locale, ch=6, seg=1, prio=0)

    worker = _load_worker()
    claimed = worker.claim_next_segment(worker_db)

    assert claimed is not None
    assert claimed["chapter_index"] == 6, (
        f"expected active-chapter 6 to win, got chapter {claimed['chapter_index']}"
    )
    assert claimed["segment_index"] == 0


def test_active_chapter_preference_does_not_violate_priority(worker_db):
    """Active-chapter preference is a tie-breaker WITHIN priority tier — it must
    NOT promote a P1 active-chapter row over a P0 non-active-chapter row."""
    book = 9999
    locale = "zh-Hans"
    _insert_audiobook(worker_db, book)
    _insert_session(worker_db, book, locale, active_chapter=6)
    # Chapter 5 — P0 (cursor buffer), highest priority
    _insert_pending(worker_db, book, locale, ch=5, seg=10, prio=0)
    # Chapter 6 — P1 (forward chase), lower priority
    _insert_pending(worker_db, book, locale, ch=6, seg=0, prio=1)

    worker = _load_worker()
    claimed = worker.claim_next_segment(worker_db)

    assert claimed["chapter_index"] == 5, "P0 must beat P1 even with active-chapter preference"
    assert claimed["priority"] == 0


def test_falls_back_to_chapter_asc_when_no_session(worker_db):
    """No streaming_sessions row → no active_chapter signal. Worker must fall
    back to chapter_index ASC ordering (back-compat behavior)."""
    book = 9999
    locale = "zh-Hans"
    _insert_audiobook(worker_db, book)
    # No session row
    _insert_pending(worker_db, book, locale, ch=8, seg=0, prio=0)
    _insert_pending(worker_db, book, locale, ch=3, seg=0, prio=0)

    worker = _load_worker()
    claimed = worker.claim_next_segment(worker_db)

    assert claimed["chapter_index"] == 3, "with no session, lowest chapter_index wins"


def test_active_chapter_with_multiple_pending_in_active(worker_db):
    """When active chapter has multiple pending rows at P0, segment_index ASC
    still applies *after* the active-chapter tier."""
    book = 9999
    locale = "zh-Hans"
    _insert_audiobook(worker_db, book)
    _insert_session(worker_db, book, locale, active_chapter=4)
    # Active chapter — segments out of order to verify segment ordering
    _insert_pending(worker_db, book, locale, ch=4, seg=12, prio=0)
    _insert_pending(worker_db, book, locale, ch=4, seg=3, prio=0)
    _insert_pending(worker_db, book, locale, ch=4, seg=8, prio=0)

    worker = _load_worker()
    claimed = worker.claim_next_segment(worker_db)

    assert claimed["chapter_index"] == 4
    assert claimed["segment_index"] == 3, "lowest segment_index in active chapter wins"
