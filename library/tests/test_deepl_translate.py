"""Coverage tests for ``library.localization.translation.deepl_translate``.

Exercises TM lookup/store error paths, glossary resolution (both the
happy path and the failure path), ``translate_one``, and the
``prune_translation_memory`` helper.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localization.translation.deepl_translate import (
    DEEPL_API_URL,
    DEEPL_FREE_API_URL,
    DeepLTranslator,
    _hash_source,
    prune_translation_memory,
)
from localization.translation.quota import QuotaExceededError


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tm_db(tmp_path: Path) -> Path:
    """Minimal string_translations table so TM paths can exercise the DB."""
    db = tmp_path / "tm.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE string_translations (
             source_hash TEXT NOT NULL,
             locale TEXT NOT NULL,
             source TEXT,
             translation TEXT,
             translator TEXT,
             updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             PRIMARY KEY (source_hash, locale)
           )"""
    )
    conn.commit()
    conn.close()
    return db


# ── Construction ─────────────────────────────────────────────────────


class TestConstruction:
    def test_empty_api_key_raises(self):
        with pytest.raises(ValueError, match="DeepL API key"):
            DeepLTranslator(api_key="")

    def test_pro_key_uses_pro_url(self):
        t = DeepLTranslator(api_key="pro-key")
        assert t._base_url == DEEPL_API_URL

    def test_free_key_uses_free_url(self):
        t = DeepLTranslator(api_key="abc123:fx")
        assert t._base_url == DEEPL_FREE_API_URL

    def test_tracker_auto_created_when_db_path_given(self, tm_db):
        # Covers line 80 (auto-init QuotaTracker branch).
        t = DeepLTranslator(api_key="k", db_path=tm_db)
        assert t._tracker is not None

    def test_injected_tracker_takes_precedence(self, tm_db):
        sentinel = MagicMock(name="injected-tracker")
        t = DeepLTranslator(api_key="k", db_path=tm_db, tracker=sentinel)
        assert t._tracker is sentinel

    def test_glossary_resolved_flag_set_from_initial_id(self):
        # glossary_id supplied → _glossary_resolved starts True, skipping
        # the resolve branch on first use.
        t = DeepLTranslator(api_key="k", glossary_id="gx-1")
        assert t._glossary_resolved is True


# ── TM lookup/store error paths ──────────────────────────────────────


class TestTMErrorPaths:
    def test_tm_lookup_sql_error_falls_back_to_all_misses(self, tm_db):
        # Covers lines 105-107 (sqlite3.Error handler in _tm_lookup).
        t = DeepLTranslator(api_key="k", db_path=tm_db)
        with patch("localization.translation.deepl_translate.sqlite3.connect") as conn_mock:
            fake_conn = MagicMock()
            fake_conn.execute.side_effect = sqlite3.Error("disk full")
            conn_mock.return_value = fake_conn
            hits, misses = t._tm_lookup(["hello", "world"], "zh-Hans")
        assert hits == {}
        assert len(misses) == 2

    def test_tm_store_sql_error_is_swallowed(self, tm_db):
        # Covers lines 140-141 (sqlite3.Error handler in _tm_store).
        t = DeepLTranslator(api_key="k", db_path=tm_db)
        with patch("localization.translation.deepl_translate.sqlite3.connect") as conn_mock:
            fake_conn = MagicMock()
            fake_conn.execute.side_effect = sqlite3.Error("locked")
            conn_mock.return_value = fake_conn
            # Must not raise — logger.exception swallows the error.
            t._tm_store([("src", "tgt")], "zh-Hans")


# ── Glossary resolution ──────────────────────────────────────────────


class TestGlossaryResolution:
    def test_disabled_glossary_returns_none(self, tm_db):
        t = DeepLTranslator(api_key="k", db_path=tm_db, enable_glossary=False)
        assert t._resolve_glossary() is None

    def test_already_resolved_returns_cached_id(self, tm_db):
        t = DeepLTranslator(api_key="k", db_path=tm_db, glossary_id="g-cached")
        # Second access — _glossary_resolved was set True in __init__.
        assert t._resolve_glossary() == "g-cached"

    def test_no_tracker_returns_none(self):
        # No DB path → no auto tracker → resolve returns None but marks resolved.
        t = DeepLTranslator(api_key="k", db_path=None)
        # Tracker is None AND glossary enabled AND unresolved → lines 153-154 hit.
        assert t._resolve_glossary() is None
        assert t._glossary_resolved is True  # Must flip to avoid re-entry

    def test_glossary_manager_success_sets_id(self, tm_db):
        t = DeepLTranslator(api_key="k", db_path=tm_db)
        # Build a fake glossary module with a stub GlossaryManager.
        fake_mgr = MagicMock()
        fake_mgr.ensure.return_value = "g-built"

        with patch("localization.translation.glossary.GlossaryManager", return_value=fake_mgr):
            resolved = t._resolve_glossary()
        assert resolved == "g-built"
        # Second call returns cached id without hitting the manager.
        fake_mgr.ensure.reset_mock()
        assert t._resolve_glossary() == "g-built"
        fake_mgr.ensure.assert_not_called()

    def test_glossary_manager_failure_returns_none(self, tm_db, caplog):
        t = DeepLTranslator(api_key="k", db_path=tm_db)
        # GlossaryError is intercepted by the broad except; use RuntimeError
        # since the except clause is `(GlossaryError, Exception)`.
        with patch(
            "localization.translation.glossary.GlossaryManager",
            side_effect=RuntimeError("bad yaml"),
        ):
            resolved = t._resolve_glossary()
        assert resolved is None
        assert "Glossary unavailable" in caplog.text


# ── translate_one ────────────────────────────────────────────────────


class TestTranslateOne:
    def test_translate_one_with_empty_list_returns_input(self):
        t = DeepLTranslator(api_key="k")
        # texts=[] short-circuits at top of translate — translate_one then
        # sees results=[] and returns the original text.
        assert t.translate_one("", "zh-Hans") == ""

    def test_translate_one_returns_first_translation(self):
        t = DeepLTranslator(api_key="k", enable_glossary=False)
        with patch.object(t, "translate", return_value=["你好"]):
            assert t.translate_one("hello", "zh-Hans") == "你好"


# ── QuotaExceededError propagation from _call_deepl_api ──────────────


class TestQuotaExceededPropagation:
    def test_quota_exceeded_reraises_past_pass_through(self, tm_db):
        # Covers line 202: the explicit re-raise of QuotaExceededError so
        # it isn't masked by the `except requests.RequestException` clause.
        t = DeepLTranslator(api_key="k", db_path=tm_db)
        with patch(
            "localization.translation.deepl_translate.requests.post",
            side_effect=QuotaExceededError("hard limit"),
        ):
            with pytest.raises(QuotaExceededError):
                t._call_deepl_api({"text": ["x"], "source_lang": "EN", "target_lang": "ZH-HANS"})


# ── prune_translation_memory ─────────────────────────────────────────


class TestPruneTM:
    def test_negative_days_rejected(self, tm_db):
        with pytest.raises(ValueError, match="older_than_days"):
            prune_translation_memory(tm_db, -1)

    def test_old_rows_pruned(self, tm_db):
        # Seed one row with a stamp in the distant past and one fresh row.
        conn = sqlite3.connect(tm_db)
        conn.execute(
            "INSERT INTO string_translations (source_hash, locale, source, translation, translator, updated_at) VALUES (?, ?, ?, ?, 'deepl', ?)",
            (_hash_source("old"), "zh-Hans", "old", "旧", "1999-01-01 00:00:00"),
        )
        conn.execute(
            "INSERT INTO string_translations (source_hash, locale, source, translation, translator) VALUES (?, ?, ?, ?, 'deepl')",
            (_hash_source("fresh"), "zh-Hans", "fresh", "新"),
        )
        conn.commit()
        conn.close()

        # 30-day cutoff: the 1999 row is way older, the fresh row survives.
        removed = prune_translation_memory(tm_db, 30)
        assert removed == 1
        conn = sqlite3.connect(tm_db)
        remaining = {
            r[0] for r in conn.execute("SELECT source FROM string_translations").fetchall()
        }
        conn.close()
        assert remaining == {"fresh"}

    def test_positive_days_keeps_fresh_rows(self, tm_db):
        # Just-inserted row is "now" — will NOT be pruned with 30-day cutoff.
        conn = sqlite3.connect(tm_db)
        conn.execute(
            "INSERT INTO string_translations (source_hash, locale, source, translation, translator) VALUES (?, ?, ?, ?, 'deepl')",
            (_hash_source("fresh"), "zh-Hans", "fresh", "新"),
        )
        conn.commit()
        conn.close()

        removed = prune_translation_memory(tm_db, 30)
        assert removed == 0


# ── Miss-fill branch (line 216) ──────────────────────────────────────


class TestFillMisses:
    def test_already_filled_slot_not_overwritten(self):
        # _fill_misses_with_source only writes when the slot is None.
        t = DeepLTranslator(api_key="k")
        output: list[str | None] = ["translated", None]
        misses = [(0, "src1"), (1, "src2")]
        t._fill_misses_with_source(output, misses)
        assert output == ["translated", "src2"]  # slot 0 preserved


# ── Hash helper ──────────────────────────────────────────────────────


class TestHashSource:
    def test_deterministic_16_char_hex(self):
        h1 = _hash_source("hello")
        h2 = _hash_source("hello")
        assert h1 == h2
        assert len(h1) == 16
        assert all(c in "0123456789abcdef" for c in h1)

    def test_different_inputs_yield_different_hashes(self):
        assert _hash_source("a") != _hash_source("b")
