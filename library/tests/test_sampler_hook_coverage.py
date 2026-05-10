"""
Coverage backfill for library/scanner/utils/sampler_hook.py.

Uncovered lines before this file (56% / 14 lines):
  38-40   — ImportError branch (imports fail)
  49-53   — no non-EN locales → early return
  59-66   — extract_chapters raises an exception
  74-84   — the per-locale enqueue loop (success + exception paths)

All localization dependencies are mocked; no real ffprobe or DB is needed.
"""

import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure scanner package is importable
LIBRARY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from scanner.utils.sampler_hook import enqueue_sampler_for_new_book  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_conn():
    """Return a real in-memory sqlite3.Connection (closed by the test)."""
    return sqlite3.connect(":memory:")


def _mock_chapter(duration_ms: float) -> MagicMock:
    chapter = MagicMock()
    chapter.duration_ms = duration_ms
    return chapter


# ---------------------------------------------------------------------------
# ImportError branch — lines 38-40
# ---------------------------------------------------------------------------


class TestImportErrorBranch:
    """Covers lines 38-40: ImportError during localization imports."""

    def test_import_error_logs_warning_and_returns(self, caplog):
        """If localization modules can't be imported, log a warning and return."""
        with caplog.at_level(logging.WARNING, logger="scanner.utils.sampler_hook"):
            with patch.dict(sys.modules, {
                "localization": None,
                "localization.chapters": None,
                "localization.config": None,
                "localization.sampler": None,
            }):
                # Remove cached modules so the import inside the function retries
                for mod in list(sys.modules.keys()):
                    if mod.startswith("localization"):
                        sys.modules.pop(mod, None)
                # Patch builtins.__import__ to raise ImportError for localization
                original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

                def _fake_import(name, *args, **kwargs):
                    if name.startswith("localization"):
                        raise ImportError(f"No module named '{name}'")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=_fake_import):
                    conn = _mock_conn()
                    try:
                        enqueue_sampler_for_new_book(conn, 1, "/fake/book.opus")
                    finally:
                        conn.close()

        # Warning should mention the import failure
        assert any("imports failed" in r.message or "sampler hook" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# No non-EN locales branch — lines 48-53
# ---------------------------------------------------------------------------


class TestNoNonENLocales:
    """Covers lines 49-53: SUPPORTED_LOCALES contains only EN locales."""

    def _run_with_locales(self, locales, caplog):
        mock_extract = MagicMock(return_value=[_mock_chapter(30_000)])
        mock_enqueue = MagicMock(return_value={"status": "ok"})

        mock_config = MagicMock()
        mock_config.SUPPORTED_LOCALES = locales

        with caplog.at_level(logging.DEBUG, logger="scanner.utils.sampler_hook"):
            with patch.dict(sys.modules, {
                "localization.chapters": MagicMock(extract_chapters=mock_extract),
                "localization.config": mock_config,
                "localization.sampler": MagicMock(enqueue_sampler=mock_enqueue),
            }):
                conn = _mock_conn()
                try:
                    enqueue_sampler_for_new_book(conn, 42, "/book/path.opus")
                finally:
                    conn.close()

        return mock_enqueue

    def test_en_only_locales_skips_enqueue(self, caplog):
        """Only EN locales → enqueue_sampler never called."""
        mock_enqueue = self._run_with_locales(["en", "en-US"], caplog)
        mock_enqueue.assert_not_called()

    def test_empty_locales_list_skips_enqueue(self, caplog):
        """Empty locale list → enqueue_sampler never called."""
        mock_enqueue = self._run_with_locales([], caplog)
        mock_enqueue.assert_not_called()

    def test_whitespace_only_locale_skips_enqueue(self, caplog):
        """Whitespace-only locale entries are filtered out."""
        mock_enqueue = self._run_with_locales(["   ", "en"], caplog)
        mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# extract_chapters exception branch — lines 59-66
# ---------------------------------------------------------------------------


class TestExtractChaptersFailure:
    """Covers lines 59-66: extract_chapters raises an exception."""

    def test_extract_chapters_exception_logs_warning_and_returns(self, caplog):
        mock_extract = MagicMock(side_effect=RuntimeError("ffprobe not found"))
        mock_enqueue = MagicMock()

        mock_config = MagicMock()
        mock_config.SUPPORTED_LOCALES = ["zh-Hans"]

        with caplog.at_level(logging.WARNING, logger="scanner.utils.sampler_hook"):
            with patch.dict(sys.modules, {
                "localization.chapters": MagicMock(extract_chapters=mock_extract),
                "localization.config": mock_config,
                "localization.sampler": MagicMock(enqueue_sampler=mock_enqueue),
            }):
                conn = _mock_conn()
                try:
                    enqueue_sampler_for_new_book(conn, 7, "/bad/path.opus")
                finally:
                    conn.close()

        mock_enqueue.assert_not_called()
        assert any("extract_chapters failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Empty chapter_durations branch — line 68-72
# ---------------------------------------------------------------------------


class TestEmptyChapterDurations:
    """Covers lines 68-72: chapters list is empty → skip sampler."""

    def test_empty_chapters_logs_info_and_returns(self, caplog):
        mock_extract = MagicMock(return_value=[])  # empty chapter list
        mock_enqueue = MagicMock()

        mock_config = MagicMock()
        mock_config.SUPPORTED_LOCALES = ["zh-Hans"]

        with caplog.at_level(logging.INFO, logger="scanner.utils.sampler_hook"):
            with patch.dict(sys.modules, {
                "localization.chapters": MagicMock(extract_chapters=mock_extract),
                "localization.config": mock_config,
                "localization.sampler": MagicMock(enqueue_sampler=mock_enqueue),
            }):
                conn = _mock_conn()
                try:
                    enqueue_sampler_for_new_book(conn, 5, "/empty/book.opus")
                finally:
                    conn.close()

        mock_enqueue.assert_not_called()
        assert any("no chapter metadata" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Per-locale enqueue loop — lines 74-86 (success + exception)
# ---------------------------------------------------------------------------


class TestEnqueueLoop:
    """Covers lines 74-84: the per-locale enqueue loop."""

    def _run(self, locales, enqueue_side_effect, caplog):
        chapters = [_mock_chapter(60_000), _mock_chapter(90_000)]
        mock_extract = MagicMock(return_value=chapters)

        mock_enqueue = MagicMock(side_effect=enqueue_side_effect)
        mock_config = MagicMock()
        mock_config.SUPPORTED_LOCALES = locales

        with caplog.at_level(logging.WARNING, logger="scanner.utils.sampler_hook"):
            with patch.dict(sys.modules, {
                "localization.chapters": MagicMock(extract_chapters=mock_extract),
                "localization.config": mock_config,
                "localization.sampler": MagicMock(enqueue_sampler=mock_enqueue),
            }):
                conn = _mock_conn()
                try:
                    enqueue_sampler_for_new_book(conn, 99, "/good/book.opus")
                finally:
                    conn.close()

        return mock_enqueue

    def test_success_calls_enqueue_for_each_locale(self, caplog):
        """enqueue_sampler called once per non-EN locale."""
        mock_enqueue = self._run(
            ["zh-Hans", "fr"],
            [{"status": "enqueued"}, {"status": "exists"}],
            caplog,
        )
        assert mock_enqueue.call_count == 2

    def test_enqueue_exception_logs_warning_continues(self, caplog):
        """Exception in one locale's enqueue is logged but the loop continues."""
        mock_enqueue = self._run(
            ["zh-Hans", "fr"],
            [RuntimeError("DB locked"), {"status": "enqueued"}],
            caplog,
        )
        # Both locales attempted despite first failing
        assert mock_enqueue.call_count == 2
        assert any("enqueue failed" in r.message for r in caplog.records)

    def test_chapter_durations_passed_correctly(self, caplog):
        """Chapter durations (ms→s conversion) are forwarded to enqueue_sampler."""
        chapters = [_mock_chapter(30_000)]  # 30s
        mock_extract = MagicMock(return_value=chapters)
        mock_enqueue = MagicMock(return_value={"status": "ok"})

        mock_config = MagicMock()
        mock_config.SUPPORTED_LOCALES = ["zh-Hans"]

        with patch.dict(sys.modules, {
            "localization.chapters": MagicMock(extract_chapters=mock_extract),
            "localization.config": mock_config,
            "localization.sampler": MagicMock(enqueue_sampler=mock_enqueue),
        }):
            conn = _mock_conn()
            try:
                enqueue_sampler_for_new_book(conn, 1, "/book.opus")
            finally:
                conn.close()

        call_args = mock_enqueue.call_args
        # 4th positional arg is chapter_durations list
        durations = call_args[0][3]
        assert durations == [30.0]

    def test_all_en_locales_with_mixed_case(self, caplog):
        """EN locale detection is case-insensitive."""
        mock_enqueue = self._run(
            ["EN", "EN-US", "English"],
            [],
            caplog,
        )
        # "English".lower() starts with "en" → filtered out too
        mock_enqueue.assert_not_called()
