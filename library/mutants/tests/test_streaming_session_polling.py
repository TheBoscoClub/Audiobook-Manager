"""Tests for the streaming session-state polling fallback (Task 15, v8.3.2).

When the WebSocket disconnects or stalls mid-chapter, the player falls back to
polling ``GET /api/translate/session/<audiobook_id>/<locale>`` every 3 s to keep
the overlay progress bar up to date. That polling contract requires the
endpoint to return enough information to synthesize a ``buffer_progress``-style
update on the client — i.e. the same ``phase`` + progress fields the WebSocket
broadcast carries.

This module exercises the extended response added in Task 15. The happy-path
cases cover both the ``buffering`` and ``streaming`` phases; the no-session
and invalid-locale cases preserve the pre-Task-15 behaviour.

Fixture pattern mirrors ``test_streaming_translate.py`` — rebind
``st._db_path`` to the session DB because other test modules may overwrite
the module-level global.
"""

from __future__ import annotations

import sqlite3

import pytest
from backend.api_modular import streaming_translate as st

AUDIOBOOK_ID = 701
CHAPTER_INDEX = 0
LOCALE = "zh-Hans"


@pytest.fixture
def streaming_db(flask_app, session_temp_dir):
    """Provide the session DB path and re-bind the streaming module global.

    Cleans up streaming rows after the test to avoid cross-test pollution —
    the session-scoped DB is shared across the whole test session.
    """
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    st._db_path = db_path
    yield db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM streaming_segments")
    conn.execute("DELETE FROM streaming_sessions")
    conn.commit()
    conn.close()


def _insert_session(db_path, state="buffering", gpu_warm=0, active_chapter=CHAPTER_INDEX):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO streaming_sessions "
        "(audiobook_id, locale, active_chapter, buffer_threshold, state, gpu_warm) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (AUDIOBOOK_ID, LOCALE, active_chapter, 6, state, gpu_warm),
    )
    conn.commit()
    conn.close()


def _insert_segments(db_path, completed=0, processing=0, pending=0, failed=0):
    """Insert streaming_segments rows for AUDIOBOOK_ID/CHAPTER_INDEX/LOCALE.

    Segment indices are assigned sequentially so ``_get_current_segment``'s
    "lowest processing-or-pending" semantics are deterministic:
    completed rows take indices [0..completed), processing rows take
    [completed..completed+processing), pending rows follow, failed last.
    """
    conn = sqlite3.connect(str(db_path))
    idx = 0
    for _ in range(completed):
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (?, ?, ?, ?, 'completed', 0)",
            (AUDIOBOOK_ID, CHAPTER_INDEX, idx, LOCALE),
        )
        idx += 1
    for _ in range(processing):
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (?, ?, ?, ?, 'processing', 0)",
            (AUDIOBOOK_ID, CHAPTER_INDEX, idx, LOCALE),
        )
        idx += 1
    for _ in range(pending):
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (?, ?, ?, ?, 'pending', 0)",
            (AUDIOBOOK_ID, CHAPTER_INDEX, idx, LOCALE),
        )
        idx += 1
    for _ in range(failed):
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (?, ?, ?, ?, 'failed', 0)",
            (AUDIOBOOK_ID, CHAPTER_INDEX, idx, LOCALE),
        )
        idx += 1
    conn.commit()
    conn.close()


def test_session_state_includes_phase_and_progress(app_client, streaming_db):
    """An active buffering session returns phase + completed + total + current_segment + segment_bitmap.

    Setup: 3 completed / 3 processing / 4 pending = 10 segments total; session
    is ``buffering``, GPU warm. Expected phase per ``_derive_phase`` precedence
    with completed=3 (below BUFFER_AHEAD_SEGMENTS=6) + processing=3 → "buffering".
    """
    _insert_session(streaming_db, state="buffering", gpu_warm=1)
    _insert_segments(streaming_db, completed=3, processing=3, pending=4)

    resp = app_client.get(f"/api/translate/session/{AUDIOBOOK_ID}/{LOCALE}")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["state"] == "buffering"
    assert body["active_chapter"] == CHAPTER_INDEX
    assert body["phase"] == "buffering"
    assert body["completed"] == 3
    assert body["total"] == 10
    # Lowest processing-or-pending index = 3 (first processing row).
    assert body["current_segment"] == 3

    bitmap = body["segment_bitmap"]
    assert isinstance(bitmap, dict)
    # segment_bitmap is the same payload used by the /segments route:
    # completed is a list of indices, total is row-count.
    assert set(bitmap["completed"]) == {0, 1, 2}
    assert bitmap["total"] == 10


def test_session_state_streaming_phase(app_client, streaming_db):
    """≥ BUFFER_AHEAD_SEGMENTS completed rows → phase is 'streaming'."""
    _insert_session(streaming_db, state="streaming", gpu_warm=1)
    # 6 completed (meets threshold), 2 pending.
    _insert_segments(streaming_db, completed=6, pending=2)

    resp = app_client.get(f"/api/translate/session/{AUDIOBOOK_ID}/{LOCALE}")
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["state"] == "streaming"
    assert body["phase"] == "streaming"
    assert body["completed"] == 6
    assert body["total"] == 8
    # No processing rows → current_segment is first pending (index 6).
    assert body["current_segment"] == 6


def test_session_state_no_session(app_client, streaming_db):
    """No session row → response is unchanged ``{"state": "none"}``.

    The client interprets this as "nothing to poll" and stops polling.
    """
    resp = app_client.get(f"/api/translate/session/{AUDIOBOOK_ID}/{LOCALE}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"state": "none"}


def test_session_state_invalid_locale(app_client, streaming_db):
    """Invalid locale returns 400 (preserve pre-Task-15 behaviour)."""
    resp = app_client.get(f"/api/translate/session/{AUDIOBOOK_ID}/bad\nlocale")
    assert resp.status_code == 400
