"""Tests for the 3-tier cursor-centric priority model.

Covers the pure-function implementations that sit behind the
`/api/translate/seek` and `/api/translate/stop` routes:

    P0 (priority=0) — cursor buffer fill: segments [t .. t+5]
    P1 (priority=1) — forward chase: segments > t+5 in same chapter
    P2 (priority=2) — back-fill: segments < t in same chapter (and stop())

Already-processing segments are never touched.
"""

import sqlite3

import pytest

from library.backend.api_modular.streaming_translate import (
    handle_seek_impl,
    stop_streaming_impl,
)

BUFFER_AHEAD = 6
SEG_SEC = 30


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "t.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(open("library/backend/schema.sql").read())
    yield conn
    conn.close()


def _insert(conn, ch, seg, state="pending", priority=1):
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
        "VALUES (1, ?, ?, 'zh-Hans', ?, ?)",
        (ch, seg, state, priority),
    )
    conn.commit()


def test_seek_promotes_p0_and_demotes_others(db):
    for s in range(20):
        _insert(db, 0, s, priority=1)
    handle_seek_impl(db, audiobook_id=1, locale="zh-Hans", chapter_index=0, segment_index=10)
    p0 = db.execute(
        "SELECT segment_index FROM streaming_segments "
        "WHERE priority=0 AND state='pending' ORDER BY segment_index"
    ).fetchall()
    assert [r[0] for r in p0] == [10, 11, 12, 13, 14, 15]
    p1 = db.execute(
        "SELECT segment_index FROM streaming_segments "
        "WHERE priority=1 AND state='pending' ORDER BY segment_index"
    ).fetchall()
    assert [r[0] for r in p1] == [16, 17, 18, 19]
    p2 = db.execute(
        "SELECT segment_index FROM streaming_segments "
        "WHERE priority=2 AND state='pending' ORDER BY segment_index"
    ).fetchall()
    assert [r[0] for r in p2] == list(range(10))


def test_stop_demotes_all_to_p2(db):
    for s in range(10):
        _insert(db, 0, s, priority=0)
    for s in range(10, 20):
        _insert(db, 0, s, priority=1)
    stop_streaming_impl(db, audiobook_id=1, locale="zh-Hans")
    p2 = db.execute(
        "SELECT COUNT(*) FROM streaming_segments WHERE priority=2 AND state='pending'"
    ).fetchone()[0]
    p_other = db.execute(
        "SELECT COUNT(*) FROM streaming_segments WHERE priority!=2 AND state='pending'"
    ).fetchone()[0]
    assert p2 == 20
    assert p_other == 0


def test_seek_does_not_touch_processing_segments(db):
    # processing segments must NOT be demoted
    _insert(db, 0, 5, state="processing", priority=0)
    for s in [0, 1, 2, 6, 7]:
        _insert(db, 0, s, priority=1)
    handle_seek_impl(db, audiobook_id=1, locale="zh-Hans", chapter_index=0, segment_index=10)
    # the processing row still at priority 0 (untouched)
    row = db.execute("SELECT priority FROM streaming_segments WHERE segment_index=5").fetchone()
    assert row[0] == 0
