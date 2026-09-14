"""Guards on the streaming worker, both from the 2026-09-13 sampler test.

Each failure was SILENT in its own way, which is what earns them a test:

* A reset that clears ``state`` but not ``retry_count`` leaves rows the
  claim query can never take. A worker then polls forever beside 29 rows
  marked pending, reporting nothing — "idle" and "blocked" look identical.
* The TTS provider shells out to ``sys.executable -m edge_tts``. Started
  under an interpreter lacking it, the worker does STT and translation on
  rented GPU time and only then fails, so the cost is paid before the
  error appears.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_WORKER = Path(__file__).resolve().parents[2] / "scripts" / "stream-translate-worker.py"
_SPEC = importlib.util.spec_from_file_location("stream_translate_worker", _WORKER)
assert _SPEC is not None and _SPEC.loader is not None
worker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(worker)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "a.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE streaming_segments (id INTEGER PRIMARY KEY, audiobook_id INT, "
        "chapter_index INT, segment_index INT, locale TEXT, state TEXT, "
        "priority INT DEFAULT 0, worker_id TEXT, vtt_content TEXT, audio_path TEXT, "
        "error TEXT, created_at TIMESTAMP, started_at TIMESTAMP, "
        "completed_at TIMESTAMP, retry_count INT DEFAULT 0, "
        "source_vtt_content TEXT, origin TEXT)"
    )
    conn.commit()
    conn.close()
    return str(path)


def _add(db_path, *, state="pending", retry=0, n=1):
    conn = sqlite3.connect(db_path)
    for i in range(n):
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, retry_count, origin) "
            "VALUES (1, 0, ?, 'zh-Hans', ?, ?, 'sampler')",
            (i, state, retry),
        )
    conn.commit()
    conn.close()


class TestUnclaimablePendingAreVisible:
    def test_counts_rows_the_claim_query_can_never_take(self, db):
        _add(db, state="pending", retry=worker.MAX_SEGMENT_RETRIES, n=29)
        assert worker._unclaimable_pending_count(db) == 29

    def test_claimable_pending_are_not_counted(self, db):
        _add(db, state="pending", retry=0, n=5)
        assert worker._unclaimable_pending_count(db) == 0

    def test_non_pending_rows_are_not_counted(self, db):
        _add(db, state="failed", retry=worker.MAX_SEGMENT_RETRIES, n=4)
        _add(db, state="completed", retry=worker.MAX_SEGMENT_RETRIES, n=3)
        assert worker._unclaimable_pending_count(db) == 0

    def test_the_boundary_is_the_claim_query_s_own_cap(self, db):
        """One below the cap is claimable; at the cap it is not. If the two
        ever disagree the warning becomes a lie, so they share a constant."""
        _add(db, state="pending", retry=worker.MAX_SEGMENT_RETRIES - 1, n=1)
        assert worker._unclaimable_pending_count(db) == 0
        _add(db, state="pending", retry=worker.MAX_SEGMENT_RETRIES, n=1)
        assert worker._unclaimable_pending_count(db) == 1

    def test_unreadable_db_reports_zero_rather_than_crashing(self, tmp_path):
        """Diagnostics must never take the worker down."""
        assert worker._unclaimable_pending_count(str(tmp_path / "missing" / "x.db")) == 0

    def test_claim_query_and_the_warning_share_one_cap(self):
        source = _WORKER.read_text(encoding="utf-8")
        assert "COALESCE(s.retry_count, 0) < {MAX_SEGMENT_RETRIES}" in source
        assert "AND COALESCE(s.retry_count, 0) < 3 " not in source


class TestFailFastOnMissingTts:
    def test_worker_checks_edge_tts_before_doing_work(self):
        """The import guard must sit in main() ahead of the claim loop —
        after it, the check is worthless because the GPU time is spent."""
        source = _WORKER.read_text(encoding="utf-8")
        guard = source.index("import edge_tts")
        loop = source.index("while not _shutdown:")
        assert guard < loop, "edge_tts check must precede the work loop"

    def test_guard_names_the_interpreter_in_its_error(self):
        """The failure is always 'wrong python', so the message has to say
        WHICH python — that is the whole diagnostic."""
        source = _WORKER.read_text(encoding="utf-8")
        block = source[source.index("import edge_tts") : source.index("while not _shutdown:")]
        assert "sys.executable" in block
        assert "venv" in block
