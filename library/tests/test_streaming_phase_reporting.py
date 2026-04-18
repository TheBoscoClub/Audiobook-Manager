"""Tests for `_derive_phase` — the streaming-pipeline phase reporter.

The phase reflects the pipeline's current stage for the (audiobook_id, locale)
pair and is surfaced to the player via both the REST `POST /api/translate/stream`
response and the WebSocket `buffer_progress` broadcast. Phases cover a single
active chapter context and are derived from `streaming_segments` +
`streaming_sessions` DB state using the precedence rules documented on
`_derive_phase`.

Precedence (first match wins):
    1. failed > 0                                 → "error"
    2. completed >= BUFFER_AHEAD_SEGMENTS         → "streaming"
    3. processing > 0                             → "buffering"
    4. session warm + pending > 0                 → "gpu_provisioning"
    5. session warm + pending=0 + processing=0    → "warmup"
    6. no warm session + pending > 0              → "warmup"
    7. otherwise                                  → "idle"

Schema-drift note (vs the v8.3.2 plan text):
  - The plan referenced `requested_at`; the real column is `created_at`.
  - The plan referenced a session state `'warmup'`; no row ever writes that
    state. Warmup is modelled via `gpu_warm=1` on a 'buffering' session.
  `_derive_phase` resolves to the real schema — these tests match the real
  schema, not the plan text.
"""

import sqlite3
from pathlib import Path

import pytest

from backend.api_modular.streaming_translate import (
    BUFFER_AHEAD_SEGMENTS,
    _derive_phase,
)

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"

AUDIOBOOK_ID = 42
CHAPTER_INDEX = 0
LOCALE = "zh-Hans"


@pytest.fixture
def db(tmp_path):
    """A fresh sqlite DB loaded with the project schema."""
    p = tmp_path / "phase.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    yield conn
    conn.close()


def _insert_segment(conn, segment_index, state, chapter_index=CHAPTER_INDEX):
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (AUDIOBOOK_ID, chapter_index, segment_index, LOCALE, state),
    )
    conn.commit()


def _insert_session(conn, state="buffering", gpu_warm=0):
    conn.execute(
        "INSERT INTO streaming_sessions "
        "(audiobook_id, locale, active_chapter, buffer_threshold, state, gpu_warm) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (AUDIOBOOK_ID, LOCALE, CHAPTER_INDEX, BUFFER_AHEAD_SEGMENTS, state, gpu_warm),
    )
    conn.commit()


def test_phase_idle_when_no_rows(db):
    """No segments, no session → phase is 'idle'."""
    phase = _derive_phase(db, AUDIOBOOK_ID, LOCALE)
    assert phase == "idle"


def test_phase_warmup_when_session_warm_no_segments(db):
    """Warm session but no segments yet → phase is 'warmup'."""
    _insert_session(db, state="buffering", gpu_warm=1)
    phase = _derive_phase(db, AUDIOBOOK_ID, LOCALE)
    assert phase == "warmup"


def test_phase_gpu_provisioning_when_daemon_starting(db):
    """Warm session + pending segments (no processing yet) → 'gpu_provisioning'.

    The GPU is being provisioned and the work queue has filled with pending
    segments but nothing has started processing yet.
    """
    _insert_session(db, state="buffering", gpu_warm=1)
    for i in range(3):
        _insert_segment(db, i, "pending")
    phase = _derive_phase(db, AUDIOBOOK_ID, LOCALE)
    assert phase == "gpu_provisioning"


def test_phase_buffering_when_completed_below_threshold(db):
    """completed < BUFFER_AHEAD_SEGMENTS AND ≥1 processing row → 'buffering'."""
    _insert_session(db, state="buffering", gpu_warm=1)
    # 2 completed (below threshold of 6), 1 processing, 3 pending
    for i in range(2):
        _insert_segment(db, i, "completed")
    _insert_segment(db, 2, "processing")
    for i in range(3, 6):
        _insert_segment(db, i, "pending")
    phase = _derive_phase(db, AUDIOBOOK_ID, LOCALE)
    assert phase == "buffering"


def test_phase_streaming_when_completed_at_or_above_threshold(db):
    """completed >= BUFFER_AHEAD_SEGMENTS → 'streaming'."""
    _insert_session(db, state="streaming", gpu_warm=1)
    # 6 completed (meets threshold), 2 pending, 1 processing
    for i in range(BUFFER_AHEAD_SEGMENTS):
        _insert_segment(db, i, "completed")
    _insert_segment(db, BUFFER_AHEAD_SEGMENTS, "processing")
    for i in range(BUFFER_AHEAD_SEGMENTS + 1, BUFFER_AHEAD_SEGMENTS + 3):
        _insert_segment(db, i, "pending")
    phase = _derive_phase(db, AUDIOBOOK_ID, LOCALE)
    assert phase == "streaming"


def test_phase_error_when_any_failed(db):
    """A single failed segment takes precedence over everything else."""
    _insert_session(db, state="streaming", gpu_warm=1)
    # Lots of completed + one failed — failed wins.
    for i in range(BUFFER_AHEAD_SEGMENTS):
        _insert_segment(db, i, "completed")
    _insert_segment(db, BUFFER_AHEAD_SEGMENTS, "failed")
    phase = _derive_phase(db, AUDIOBOOK_ID, LOCALE)
    assert phase == "error"
