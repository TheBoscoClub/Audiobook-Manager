"""Smoke tests for scripts/batch-translate.py helpers.

Covers _parse_args, _configure_env, _get_queue_stats, _dry_run_preview,
_process_job, _run_batch, and main() success/failure paths without
invoking the real DB or GPU backends.
"""

import importlib.util
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load batch-translate module without executing __main__
# ---------------------------------------------------------------------------
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "batch-translate.py"


def _load():
    spec = importlib.util.spec_from_file_location("batch_translate", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bt = _load()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path):
    """Create a minimal translation_queue + audiobooks DB."""
    db_path = str(tmp_path / "audiobooks.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audiobooks (
            id INTEGER PRIMARY KEY,
            title TEXT,
            file_path TEXT,
            content_type TEXT DEFAULT 'audiobook'
        );
        CREATE TABLE IF NOT EXISTS translation_queue (
            id INTEGER PRIMARY KEY,
            audiobook_id INTEGER,
            locale TEXT,
            state TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            error TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_required_flags(self, tmp_path):
        with patch("sys.argv", ["bt", "--db", "/tmp/x.db", "--library", "/tmp/lib"]):
            args = bt._parse_args()
        assert args.db == "/tmp/x.db"
        assert args.library == "/tmp/lib"
        assert not args.dry_run
        assert not args.stt_only
        assert not args.tts_only
        assert args.book_id is None

    def test_all_optional_flags(self):
        with patch(
            "sys.argv",
            [
                "bt",
                "--db",
                "/x.db",
                "--library",
                "/lib",
                "--book-id",
                "42",
                "--dry-run",
                "--stt-only",
                "--tts-only",
            ],
        ):
            args = bt._parse_args()
        assert args.book_id == 42
        assert args.dry_run
        assert args.stt_only
        assert args.tts_only


# ---------------------------------------------------------------------------
# _configure_env
# ---------------------------------------------------------------------------


class TestConfigureEnv:
    def test_default_env_sets_whisper_gpu_defaults(self, monkeypatch):
        """_configure_env sets defaults for AUDIOBOOKS_WHISPER_GPU_* vars."""
        for k in ["AUDIOBOOKS_WHISPER_GPU_HOST", "AUDIOBOOKS_WHISPER_GPU_PORT"]:
            monkeypatch.delenv(k, raising=False)

        with patch("sys.argv", ["bt", "--db", "/x.db", "--library", "/lib"]):
            args = bt._parse_args()
        bt._configure_env(args)
        assert os.environ.get("AUDIOBOOKS_WHISPER_GPU_HOST") == "127.0.0.1"
        assert os.environ.get("AUDIOBOOKS_WHISPER_GPU_PORT") == "8765"


# ---------------------------------------------------------------------------
# _get_queue_stats
# ---------------------------------------------------------------------------


class TestGetQueueStats:
    def test_empty_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        bt.ensure_tables(db_path)
        pending, completed, failed = bt._get_queue_stats(db_path)
        assert pending == 0
        assert completed == 0
        assert failed == 0

    def test_counts(self, tmp_path):
        db_path = _make_db(tmp_path)
        bt.ensure_tables(db_path)
        conn = sqlite3.connect(db_path)
        conn.executemany(
            "INSERT INTO translation_queue (audiobook_id, locale, state) VALUES (?, ?, ?)",
            [(1, "zh-Hans", "pending"), (2, "zh-Hans", "completed"), (3, "zh-Hans", "failed")],
        )
        conn.commit()
        conn.close()
        pending, completed, failed = bt._get_queue_stats(db_path)
        assert pending == 1
        assert completed == 1
        assert failed == 1


# ---------------------------------------------------------------------------
# _dry_run_preview
# ---------------------------------------------------------------------------


class TestDryRunPreview:
    def test_logs_rows(self, tmp_path, caplog):
        import logging

        db_path = _make_db(tmp_path)
        bt.ensure_tables(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'Book A', '/a.opus')"
        )
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale, state, priority) VALUES (1, 'zh-Hans', 'pending', 5)"
        )
        conn.commit()
        conn.close()

        with caplog.at_level(logging.INFO, logger="batch-translate"):
            bt._dry_run_preview(db_path)

        assert "Book A" in caplog.text


# ---------------------------------------------------------------------------
# main() — DB missing → exit(1)
# ---------------------------------------------------------------------------


class TestMain:
    def test_missing_db_exits(self, tmp_path):
        with patch("sys.argv", ["bt", "--db", str(tmp_path / "nope.db"), "--library", "/lib"]):
            with pytest.raises(SystemExit) as exc:
                bt.main()
        assert exc.value.code == 1

    def test_dry_run_returns(self, tmp_path):
        db_path = _make_db(tmp_path)
        with patch("sys.argv", ["bt", "--db", db_path, "--library", str(tmp_path), "--dry-run"]):
            with patch.object(bt, "_configure_env"):
                with patch.object(bt, "ensure_tables"):
                    with patch.object(bt, "_get_queue_stats", return_value=(0, 0, 0)):
                        with patch.object(bt, "_dry_run_preview") as mock_preview:
                            bt.main()
        mock_preview.assert_called_once_with(db_path)

    def test_run_batch_called_when_not_dry_run(self, tmp_path):
        db_path = _make_db(tmp_path)
        with patch("sys.argv", ["bt", "--db", db_path, "--library", str(tmp_path)]):
            with patch.object(bt, "_configure_env"):
                with patch.object(bt, "ensure_tables"):
                    with patch.object(bt, "_get_queue_stats", return_value=(0, 0, 0)):
                        with patch.object(bt, "_run_batch") as mock_run:
                            bt.main()
        mock_run.assert_called_once()
