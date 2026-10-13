"""Two-stage queue accounting, claim ownership and the monthly ledger (4j4).

Covers:

* ``localization.queue_schema`` — the column additions and the one-time mapping
  of existing rows (and data migration 015, which calls it);
* the API — enqueues and reports only; a bump runs nothing in-process and an
  API restart never resets rows the drain holds;
* ``scripts/batch-translate.py`` — stage filters, stage completion,
  narration failures left visibly pending, drain-mode endpoint loss releases
  (never fails) the job, drain-mode provider lockdown, chapter-skip rule;
* ``localization.drain_ledger`` — budget, ceiling, rollover, crash safety.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import localization.queue as lq
import pytest
from localization import drain_ledger
from localization.queue_schema import ensure_queue_schema

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "batch_translate_2s", REPO / "scripts" / "batch-translate.py"
)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


# ── fixtures ─────────────────────────────────────────────────────────────────

LEGACY_QUEUE = """
CREATE TABLE translation_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT, audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL, priority INTEGER DEFAULT 0, state TEXT DEFAULT 'pending',
    step TEXT DEFAULT 'stt', error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP, finished_at TIMESTAMP, last_progress_at TIMESTAMP,
    total_chapters INTEGER, UNIQUE(audiobook_id, locale));
"""

BASE = """
CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT, file_path TEXT, asin TEXT,
                         translation_excluded INTEGER DEFAULT 0);
CREATE TABLE chapter_subtitles (id INTEGER PRIMARY KEY AUTOINCREMENT, audiobook_id INTEGER,
    chapter_index INTEGER, locale TEXT, vtt_path TEXT, stt_provider TEXT,
    translation_provider TEXT, UNIQUE(audiobook_id, chapter_index, locale));
CREATE TABLE chapter_translations_audio (id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER, chapter_index INTEGER, locale TEXT, audio_path TEXT,
    UNIQUE(audiobook_id, chapter_index, locale));
CREATE TABLE streaming_segments (id INTEGER PRIMARY KEY, audiobook_id INTEGER, locale TEXT,
    state TEXT);
"""


def _legacy_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(BASE + LEGACY_QUEUE)
    return conn


def _subs(conn, book, chapters, locales=("en", "zh-Hans")):
    for ch in chapters:
        for loc in locales:
            conn.execute(
                "INSERT INTO chapter_subtitles (audiobook_id, chapter_index, locale, vtt_path) "
                "VALUES (?, ?, ?, 'x.vtt')",
                (book, ch, loc),
            )


def _audio(conn, book, chapters):
    for ch in chapters:
        conn.execute(
            "INSERT INTO chapter_translations_audio (audiobook_id, chapter_index, locale, audio_path) "
            "VALUES (?, ?, 'zh-Hans', 'x.opus')",
            (book, ch),
        )


def _seed_mapping_cases(conn):
    rows = {
        # book: (state, subtitles chapters translated, narrated chapters)
        1: ("completed", [0, 1], [0, 1]),  # both evidenced -> stays completed
        2: ("completed", [0, 1], []),  # subtitles only -> pending, narration open
        3: ("completed", [], []),  # nothing on disk -> pending, both open
        4: ("pending", [0, 1], []),  # pending with full subtitles -> subtitles done
        5: ("failed", [0], []),  # other states keep their value (evidence still filled)
        6: ("completed", [0], []),  # partial: en ch1 has no translation -> subtitles open
    }
    for book, (state, tr, narrated) in rows.items():
        conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (?, 't', '/x')", (book,))
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale, state, finished_at) "
            "VALUES (?, 'zh-Hans', ?, '2026-01-02 03:04:05')",
            (book, state),
        )
        _subs(conn, book, tr)
        _audio(conn, book, narrated)
    _subs(conn, 6, [1], locales=("en",))
    conn.commit()


def _mapped(conn):
    return {
        r[0]: (r[1], r[2] is not None, r[3] is not None)
        for r in conn.execute(
            "SELECT audiobook_id, state, subtitles_done_at, narration_done_at FROM translation_queue"
        )
    }


EXPECTED_MAPPING = {
    1: ("completed", True, True),
    2: ("pending", True, False),
    3: ("pending", False, False),
    4: ("pending", True, False),
    5: ("failed", True, False),
    6: ("pending", False, False),
}


# ── schema / migration ───────────────────────────────────────────────────────


def test_schema_mapping_of_existing_rows(tmp_path: Path):
    conn = _legacy_db(tmp_path / "a.db")
    _seed_mapping_cases(conn)
    reopened = ensure_queue_schema(conn)
    assert _mapped(conn) == EXPECTED_MAPPING
    assert reopened == 3  # books 2, 3 and 6 left 'completed'


def test_schema_mapping_runs_once(tmp_path: Path):
    conn = _legacy_db(tmp_path / "a.db")
    _seed_mapping_cases(conn)
    ensure_queue_schema(conn)
    conn.execute("UPDATE translation_queue SET state='completed' WHERE audiobook_id=3")
    conn.commit()
    assert ensure_queue_schema(conn) == 0
    assert _mapped(conn)[3][0] == "completed", "a second open must not remap again"


def test_data_migration_015_applies_mapping(tmp_path: Path):
    db = tmp_path / "audiobooks.db"
    conn = _legacy_db(db)
    _seed_mapping_cases(conn)
    conn.close()
    script = REPO / "data-migrations" / "015_translation_queue_two_stage.sh"
    proc = subprocess.run(
        ["bash", "-c", f'source "{script}"'],
        env={
            **os.environ,
            "DB_PATH": str(db),
            "VENV_PYTHON": sys.executable,
            "APP_DIR": str(REPO),
            "USE_SUDO": "",
            "DRY_RUN": "false",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 completed row(s) reopened" in proc.stdout, proc.stdout + proc.stderr
    assert _mapped(sqlite3.connect(db)) == EXPECTED_MAPPING
    head = script.read_text()
    assert 'MIN_VERSION="' in head


# ── API: enqueue and status only, no in-process work ────────────────────────


@pytest.fixture
def api_db(tmp_path: Path):
    db = tmp_path / "api.db"
    conn = sqlite3.connect(db)
    conn.executescript(BASE)
    for book in (1, 2, 3):
        audio = tmp_path / f"b{book}.opus"
        audio.write_bytes(b"x")
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (?, 't', ?)", (book, str(audio))
        )
    conn.commit()
    conn.close()
    lq._db_path = None
    lq.init_queue(db, tmp_path)
    yield db
    lq._db_path = None


def _state(db, book):
    return (
        sqlite3.connect(db)
        .execute(
            "SELECT state, claimed_by, subtitles_done_at, narration_done_at, priority "
            "FROM translation_queue WHERE audiobook_id = ?",
            (book,),
        )
        .fetchone()
    )


def test_bump_on_narration_only_row_runs_nothing_in_process(api_db, monkeypatch):
    """MED-7: a zh-Hans play on a book whose narration is pending must not
    start TTS (or anything else) inside the API — it only raises priority."""
    import threading

    import localization.tts.factory as tts_factory

    lq.enqueue(1, "zh-Hans")
    conn = sqlite3.connect(api_db)
    conn.execute("UPDATE translation_queue SET subtitles_done_at = CURRENT_TIMESTAMP")
    conn.commit()
    monkeypatch.setattr(
        tts_factory, "get_tts_provider", lambda *a, **k: pytest.fail("TTS ran in the API process")
    )
    threads_before = threading.active_count()
    lq.enqueue(1, "zh-Hans", priority=100, start_worker=True)  # what /translation/bump does
    time.sleep(0.2)
    assert threading.active_count() == threads_before, "a worker thread was started"
    state, owner, subs, narr, prio = _state(api_db, 1)
    assert (state, owner, narr) == ("pending", None, None) and subs
    assert prio == 100, "the bump must still put the book first for the drain"


def test_bump_route_runs_nothing_in_process(api_db, monkeypatch):
    import threading

    lq.enqueue(2, "zh-Hans")
    before = threading.active_count()
    lq.enqueue(2, "zh-Hans", priority=100, start_worker=True)
    assert threading.active_count() == before
    assert _state(api_db, 2)[0] == "pending"


def test_queue_module_has_no_executor():
    for name in ("_ensure_worker", "_worker_loop", "_process_job", "_run_tts", "start_processing"):
        assert not hasattr(lq, name), f"{name} would let the API process run queue work"


def test_api_restart_recovers_only_legacy_api_claims(api_db, tmp_path):
    for book in (1, 2, 3):
        lq.enqueue(book, "zh-Hans")
    conn = sqlite3.connect(api_db)
    conn.execute(
        "UPDATE translation_queue SET state='processing', claimed_by='drain' WHERE audiobook_id=1"
    )
    conn.execute(
        "UPDATE translation_queue SET state='processing', claimed_by='api:999' WHERE audiobook_id=2"
    )
    conn.execute(
        "UPDATE translation_queue SET state='processing', claimed_by=NULL WHERE audiobook_id=3"
    )
    conn.commit()
    lq.init_queue(api_db, tmp_path)  # what an API restart does
    assert _state(api_db, 1)[:2] == ("processing", "drain")
    assert _state(api_db, 2)[:2] == ("pending", None)
    assert _state(api_db, 3)[:2] == ("pending", None)


# ── batch-translate stages ───────────────────────────────────────────────────


@pytest.fixture
def batch_db(tmp_path: Path):
    db = tmp_path / "b.db"
    conn = sqlite3.connect(db)
    conn.executescript(BASE)
    ensure_queue_schema(conn)
    for book in (1, 2):
        audio = tmp_path / f"b{book}.opus"
        audio.write_bytes(b"x")
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (?, 't', ?)", (book, str(audio))
        )
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale) VALUES (?, 'zh-Hans')", (book,)
        )
    conn.commit()
    conn.close()
    return str(db)


def _args(stage, drain=False, owner="drain"):
    return bt._parse_args(
        ["--db", "x", "--library", "y", "--stage", stage, "--owner", owner]
        + (["--drain-mode"] if drain else [])
    )


def _rows(db):
    return (
        sqlite3.connect(db)
        .execute(
            "SELECT audiobook_id, state, subtitles_done_at IS NOT NULL, narration_done_at IS NOT NULL, "
            "claimed_by, error FROM translation_queue ORDER BY audiobook_id"
        )
        .fetchall()
    )


def test_subtitle_stage_marks_only_subtitles(batch_db, monkeypatch):
    monkeypatch.setattr(bt, "process_book_stt", lambda *a: True)
    monkeypatch.setattr(bt, "process_book_tts", lambda *a: pytest.fail("no TTS in the GPU stage"))
    assert bt._run_batch(batch_db, _args("subtitles")) == 0
    assert [r[:4] for r in _rows(batch_db)] == [(1, "pending", 1, 0), (2, "pending", 1, 0)]
    # Narration stage then completes them, without touching STT.
    monkeypatch.setattr(bt, "process_book_stt", lambda *a: pytest.fail("no STT in narration"))
    monkeypatch.setattr(bt, "process_book_tts", lambda *a: True)
    assert bt._run_batch(batch_db, _args("narration")) == 0
    assert [r[:4] for r in _rows(batch_db)] == [(1, "completed", 1, 1), (2, "completed", 1, 1)]


def test_narration_failure_stays_pending_and_is_not_retried_in_run(batch_db, monkeypatch):
    conn = sqlite3.connect(batch_db)
    conn.execute("UPDATE translation_queue SET subtitles_done_at = CURRENT_TIMESTAMP")
    conn.commit()
    attempts = []

    def boom(db, book, locale, audio):
        attempts.append(book)
        raise RuntimeError("edge-tts 503")

    monkeypatch.setattr(bt, "process_book_tts", boom)
    assert bt._run_batch(batch_db, _args("narration")) == 0
    assert sorted(attempts) == [1, 2], "each row attempted once, no tight retry loop"
    for r in _rows(batch_db):
        assert r[1:5] == ("pending", 1, 0, None)
        assert "edge-tts 503" in r[5]


def test_drain_mode_endpoint_loss_releases_job(batch_db, monkeypatch):
    def lost(*a):
        raise ConnectionError("tunnel gone")

    monkeypatch.setattr(bt, "process_book_stt", lost)
    health = iter([True, False])
    monkeypatch.setattr(bt, "endpoints_healthy", lambda: next(health))
    assert bt._run_batch(batch_db, _args("subtitles", drain=True)) == bt.EXIT_ENDPOINTS_LOST
    rows = _rows(batch_db)
    assert all(r[1] == "pending" and r[4] is None for r in rows), rows
    assert all(r[1] != "failed" for r in rows)


def test_genuine_failure_outside_drain_mode_marks_failed(batch_db, monkeypatch):
    monkeypatch.setattr(
        bt, "process_book_stt", lambda *a: (_ for _ in ()).throw(ValueError("bad audio"))
    )
    assert bt._run_batch(batch_db, _args("subtitles", drain=False)) == 0
    assert {r[1] for r in _rows(batch_db)} == {"failed"}


def _attempts(db):
    return (
        sqlite3.connect(db)
        .execute(
            "SELECT audiobook_id, state, drain_attempts, error FROM translation_queue ORDER BY audiobook_id"
        )
        .fetchall()
    )


def _one_book(batch_db):
    conn = sqlite3.connect(batch_db)
    conn.execute("DELETE FROM translation_queue WHERE audiobook_id = 2")
    conn.commit()


def test_book_specific_failures_quarantine_after_three_runs(batch_db, monkeypatch):
    from localization.drain_queue import count_drainable, requeue_quarantined

    _one_book(batch_db)
    monkeypatch.setattr(
        bt, "process_book_stt", lambda *a: (_ for _ in ()).throw(ValueError("bad audio"))
    )
    monkeypatch.setattr(bt, "endpoints_healthy", lambda: True)
    seen = []
    for _run in range(3):
        assert bt._run_batch(batch_db, _args("subtitles", drain=True)) == 0
        seen.append(_attempts(batch_db)[0][1:3])
    assert seen == [("pending", 1), ("pending", 2), ("quarantined", 3)], seen
    assert "bad audio" in _attempts(batch_db)[0][3], "last error recorded"
    assert count_drainable(Path(batch_db), "subtitles") == 0, "no rental for it alone"
    # A fourth run does not even claim it.
    assert bt._run_batch(batch_db, _args("subtitles", drain=True)) == 0
    assert _attempts(batch_db)[0][2] == 3
    # Operator retry resets the counter.
    assert requeue_quarantined(Path(batch_db), book_id=1) == 1
    assert _attempts(batch_db)[0][1:4] == ("pending", 0, None)
    assert count_drainable(Path(batch_db), "subtitles") == 1


def _counters(db):
    return (
        sqlite3.connect(db)
        .execute("SELECT state, drain_attempts, suspect_runs, error FROM translation_queue")
        .fetchone()
    )


def test_loss_before_the_job_counts_against_nothing(batch_db, monkeypatch):
    """Endpoints already down when the batch reaches the book: a run-level
    failure — neither counter moves, however often it happens."""
    _one_book(batch_db)
    monkeypatch.setattr(bt, "process_book_stt", lambda *a: pytest.fail("must not start"))
    monkeypatch.setattr(bt, "endpoints_healthy", lambda: False)
    for _run in range(5):
        assert bt._run_batch(batch_db, _args("subtitles", drain=True)) == bt.EXIT_ENDPOINTS_LOST
    assert _counters(batch_db)[:3] == ("pending", 0, 0)


def test_in_flight_loss_is_reported_not_counted_by_the_batch(batch_db, monkeypatch, tmp_path):
    """The batch cannot tell a node that died under this book from one our
    own guard/reaper destroyed, so it only reports the job id; the drain
    decides (tests/test_translation_drain.py)."""
    _one_book(batch_db)
    lost = tmp_path / "lost"

    def crash(*a):
        raise ConnectionError("whisper died")

    monkeypatch.setattr(bt, "process_book_stt", crash)
    health = iter([True, False])
    monkeypatch.setattr(bt, "endpoints_healthy", lambda: next(health))
    args = _args("subtitles", drain=True)
    args.lost_job_file = str(lost)
    assert bt._run_batch(batch_db, args) == bt.EXIT_ENDPOINTS_LOST
    assert _counters(batch_db)[:3] == ("pending", 0, 0)
    job_id = sqlite3.connect(batch_db).execute("SELECT id FROM translation_queue").fetchone()[0]
    assert lost.read_text().strip() == str(job_id)


def test_mark_suspect_quarantines_after_three(batch_db):
    """bd 2c9: the drain's verdict, three times for the same book."""
    from localization.drain_queue import count_drainable, mark_suspect, requeue_quarantined

    _one_book(batch_db)
    job_id = sqlite3.connect(batch_db).execute("SELECT id FROM translation_queue").fetchone()[0]
    seen = [mark_suspect(Path(batch_db), job_id, 3, "endpoints lost") for _ in range(3)]
    assert seen == ["pending", "pending", "quarantined"], seen
    assert _counters(batch_db)[:3] == ("quarantined", 0, 3)
    assert _counters(batch_db)[3].startswith("suspect:")
    assert count_drainable(Path(batch_db), "subtitles") == 0
    assert requeue_quarantined(Path(batch_db)) == 1
    assert _counters(batch_db)[:3] == ("pending", 0, 0)


def test_success_resets_the_suspect_counter(batch_db, monkeypatch):
    _one_book(batch_db)
    conn = sqlite3.connect(batch_db)
    conn.execute("UPDATE translation_queue SET suspect_runs = 2")
    conn.commit()
    monkeypatch.setattr(bt, "endpoints_healthy", lambda: True)
    monkeypatch.setattr(bt, "process_book_stt", lambda *a: True)
    assert bt._run_batch(batch_db, _args("subtitles", drain=True)) == 0
    assert _counters(batch_db)[2] == 0


def test_release_stale_claims(batch_db):
    from localization.drain_queue import release_stale

    conn = sqlite3.connect(batch_db)
    for book, owner, age in (
        (1, "batch:999999", "-1 minutes"),  # dead pid -> released
        (2, f"batch:{os.getpid()}", "-30 hours"),  # live pid -> kept, however old
    ):
        conn.execute(
            "UPDATE translation_queue SET state='processing', claimed_by=?, "
            "last_progress_at=datetime('now', ?) WHERE audiobook_id=?",
            (owner, age, book),
        )
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (3, 't', '/x'), (4, 't', '/x')"
    )
    conn.execute(
        "INSERT INTO translation_queue (audiobook_id, locale, state, claimed_by, last_progress_at) "
        "VALUES (3, 'zh-Hans', 'processing', 'api:1', datetime('now', '-7 hours')), "
        "(4, 'zh-Hans', 'processing', 'drain', datetime('now', '-30 hours'))"
    )
    conn.commit()
    assert release_stale(Path(batch_db), 6) == 2
    states = dict(conn.execute("SELECT audiobook_id, state FROM translation_queue"))
    assert states == {1: "pending", 2: "processing", 3: "pending", 4: "processing"}


def test_verify_fix_redoes_only_broken_chapters(batch_db, tmp_path):
    spec = importlib.util.spec_from_file_location("vt", REPO / "scripts" / "verify-translations.py")
    vt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vt)
    good = tmp_path / "good.vtt"
    good.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHello there\n")
    conn = sqlite3.connect(batch_db)
    conn.row_factory = sqlite3.Row
    for ch in (0, 1):
        for loc in ("en", "zh-Hans"):
            path = good if not (ch == 1 and loc == "zh-Hans") else tmp_path / "missing.vtt"
            conn.execute(
                "INSERT INTO chapter_subtitles (audiobook_id, chapter_index, locale, vtt_path) "
                "VALUES (1, ?, ?, ?)",
                (ch, loc, str(path)),
            )
        conn.execute(
            "INSERT INTO chapter_translations_audio (audiobook_id, chapter_index, locale, audio_path) "
            "VALUES (1, ?, 'zh-Hans', 'x.opus')",
            (ch,),
        )
    conn.execute(
        "UPDATE translation_queue SET state='completed', subtitles_done_at=CURRENT_TIMESTAMP, "
        "narration_done_at=CURRENT_TIMESTAMP WHERE audiobook_id=1"
    )
    conn.commit()
    result = vt.verify_book(conn, 1, "zh-Hans")
    assert result["status"] == "FAIL" and result["bad_chapters"] == {1}
    vt.requeue_book(conn, 1, "zh-Hans", result["bad_chapters"])
    conn.commit()
    kept = sorted(
        tuple(r)
        for r in conn.execute(
            "SELECT chapter_index, locale FROM chapter_subtitles WHERE audiobook_id=1"
        )
    )
    assert kept == [(0, "en"), (0, "zh-Hans")], "the intact chapter must be kept"
    narrated = [
        r[0]
        for r in conn.execute(
            "SELECT chapter_index FROM chapter_translations_audio WHERE audiobook_id=1"
        )
    ]
    assert narrated == [0]
    row = conn.execute(
        "SELECT state, subtitles_done_at, narration_done_at FROM translation_queue WHERE audiobook_id=1"
    ).fetchone()
    assert tuple(row) == ("pending", None, None)


def test_verify_fix_on_a_passing_book_changes_nothing(batch_db, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "vt2", REPO / "scripts" / "verify-translations.py"
    )
    vt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vt)
    good = tmp_path / "good.vtt"
    good.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHello there\n")
    conn = sqlite3.connect(batch_db)
    conn.row_factory = sqlite3.Row
    for loc in ("en", "zh-Hans"):
        conn.execute(
            "INSERT INTO chapter_subtitles (audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (2, 0, ?, ?)",
            (loc, str(good)),
        )
    conn.commit()
    result = vt.verify_book(conn, 2, "zh-Hans")
    assert result["status"] == "PASS" and result["bad_chapters"] == set()


def test_success_resets_the_attempt_counter(batch_db, monkeypatch):
    _one_book(batch_db)
    monkeypatch.setattr(bt, "endpoints_healthy", lambda: True)
    monkeypatch.setattr(
        bt, "process_book_stt", lambda *a: (_ for _ in ()).throw(ValueError("flaky"))
    )
    bt._run_batch(batch_db, _args("subtitles", drain=True))
    bt._run_batch(batch_db, _args("subtitles", drain=True))
    monkeypatch.setattr(bt, "process_book_stt", lambda *a: True)
    bt._run_batch(batch_db, _args("subtitles", drain=True))
    assert _attempts(batch_db)[0][1:3] == ("pending", 0)


def test_requeue_cli(batch_db):
    conn = sqlite3.connect(batch_db)
    conn.execute("UPDATE translation_queue SET state='quarantined', drain_attempts=3")
    conn.commit()
    env = {**os.environ, "PYTHONPATH": str(REPO / "library")}
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "localization.drain_queue",
            "--db",
            batch_db,
            "requeue-quarantined",
            "--book-id",
            "2",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "1"
    assert [r[1:3] for r in _attempts(batch_db)] == [("quarantined", 3), ("pending", 0)]


def test_drain_mode_locks_down_providers(monkeypatch):
    monkeypatch.setattr(
        sys, "modules", {k: v for k, v in sys.modules.items() if k != "localization.config"}
    )
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT", "ep")
    monkeypatch.setenv("AUDIOBOOKS_MT_ENDPOINT", "http://127.0.0.1:8000")
    monkeypatch.setenv("AUDIOBOOKS_WHISPER_GPU_HOST", "127.0.0.1")
    monkeypatch.setattr(bt, "FORCED_STT_PROVIDER", "")
    bt._lock_down_providers("subtitles")
    assert "AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT" not in os.environ
    assert os.environ["AUDIOBOOKS_STT_PROVIDER"] == "local-gpu"
    assert bt.FORCED_STT_PROVIDER == "local-gpu"


def test_drain_mode_refuses_non_tunnel_endpoints(monkeypatch):
    monkeypatch.setattr(
        sys, "modules", {k: v for k, v in sys.modules.items() if k != "localization.config"}
    )
    monkeypatch.setenv("AUDIOBOOKS_MT_ENDPOINT", "https://paid.example.com")
    monkeypatch.setenv("AUDIOBOOKS_WHISPER_GPU_HOST", "127.0.0.1")
    with pytest.raises(RuntimeError, match="refusing"):
        bt._lock_down_providers("subtitles")


def test_stt_skips_only_chapters_done_in_both_languages(batch_db, monkeypatch, tmp_path):
    conn = sqlite3.connect(batch_db)
    _subs(conn, 1, [0])  # ch0 en+zh
    _subs(conn, 1, [1], locales=("en",))  # ch1 en only (a run without MT)
    conn.commit()
    captured = {}
    import localization.pipeline as pipeline

    class _Stt:
        name = "stub"

    monkeypatch.setattr(pipeline, "get_stt_provider", lambda *a, **k: _Stt())

    def gen(**kw):
        captured["skip"] = kw["skip_chapters"]
        kw["on_chapter_complete"](1, Path("en.vtt"), Path("zh.vtt"))

    monkeypatch.setattr(pipeline, "generate_book_subtitles", gen)
    import localization.translation.factory as fac

    monkeypatch.setattr(fac, "translation_provider_name", lambda: "vllm")
    bt.process_book_stt(batch_db, 1, "zh-Hans", tmp_path / "b1.opus")
    assert captured["skip"] == {0}, "ch1 has no translation and must be redone"


def test_stt_refuses_to_claim_done_without_translations(batch_db, monkeypatch, tmp_path):
    import localization.pipeline as pipeline

    class _Stt:
        name = "stub"

    monkeypatch.setattr(pipeline, "get_stt_provider", lambda *a, **k: _Stt())
    monkeypatch.setattr(
        pipeline,
        "generate_book_subtitles",
        lambda **kw: kw["on_chapter_complete"](0, Path("en.vtt"), None),
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        bt.process_book_stt(batch_db, 1, "zh-Hans", tmp_path / "b1.opus")


# ── ledger ───────────────────────────────────────────────────────────────────


def _line(epoch, run, kind, amount):
    month = time.strftime("%Y-%m", time.localtime(epoch))
    return f"{int(epoch)}\t{month}\t{run}\t{kind}\t{amount:.4f}\t\n"


def test_ledger_budget_and_ceiling(tmp_path: Path):
    led = tmp_path / "l.tsv"
    assert drain_ledger.budget(led, 10, 100, 1) == (10, 0, 100)
    led.write_text(_line(time.time(), "a", "reserve", 10) + _line(time.time(), "a", "final", 93.3))
    cap, mtd, rem = drain_ledger.budget(led, 10, 100, 1)
    assert (cap, mtd) == (6.7, 93.3)
    led.write_text(_line(time.time(), "a", "final", 100))
    with pytest.raises(RuntimeError, match="ceiling reached"):
        drain_ledger.budget(led, 10, 100, 1)
    led.write_text(_line(time.time(), "a", "final", 99.4))
    with pytest.raises(RuntimeError, match="minimum"):
        drain_ledger.budget(led, 10, 100, 1)


def test_ledger_reservation_counts_until_final_and_max_final_wins(tmp_path: Path):
    led = tmp_path / "l.tsv"
    now = time.time()
    led.write_text(_line(now, "crashed", "reserve", 10))
    assert drain_ledger.month_to_date(led) == 10, "a run with no final counts its full reservation"
    led.write_text(
        _line(now, "r", "reserve", 10) + _line(now, "r", "final", 4) + _line(now, "r", "final", 3)
    )
    assert drain_ledger.month_to_date(led) == 4
    assert drain_ledger.month_to_date(led, exclude_run="r") == 0


def test_ledger_month_rollover(tmp_path: Path):
    led = tmp_path / "l.tsv"
    led.write_text(
        _line(time.time() - 45 * 86400, "old", "final", 100) + _line(time.time(), "new", "final", 5)
    )
    assert drain_ledger.month_to_date(led) == 5, "only this calendar month counts"


def test_ledger_cli_survives_process_restart(tmp_path: Path):
    led = tmp_path / "sub" / "l.tsv"
    env = {**os.environ, "PYTHONPATH": str(REPO / "library")}
    cli = [sys.executable, "-m", "localization.drain_ledger", "--ledger", str(led)]
    subprocess.run(cli + ["reserve", "--run", "x", "--amount", "10"], env=env, check=True)
    subprocess.run(cli + ["final", "--run", "x", "--amount", "2.5"], env=env, check=True)
    out = subprocess.run(cli + ["mtd"], env=env, check=True, capture_output=True, text=True)
    assert out.stdout.strip() == "2.5000"
    refused = subprocess.run(
        cli + ["budget", "--per-run", "10", "--monthly", "2"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == drain_ledger.EXIT_REFUSED


def test_ledger_rejects_corruption(tmp_path: Path):
    led = tmp_path / "l.tsv"
    led.write_text("garbage line\n")
    with pytest.raises(ValueError):
        drain_ledger.month_to_date(led)
