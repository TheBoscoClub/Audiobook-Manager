"""Tests for the provider-neutral translation layer (Audiobook-Manager-4uj).

Covers the abstraction that replaced the removed hosted-MT integration:

- ``localization/translation/base.py`` — the TranslationProvider ABC and
  TranslationUnavailableError.
- ``localization/translation/factory.py`` — no backend ships, so the factory
  returns None and callers degrade (skip + log, or 503).
- ``localization/translation/memory.py`` — the provider-neutral translation
  memory over the ``string_translations`` table.
- ``localization/pipeline.py`` — subtitle generation degrades to
  source-language-only output when no provider is configured.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from localization.stt.base import STTProvider
from localization.translation.base import TranslationProvider, TranslationUnavailableError
from localization.translation.factory import get_translation_provider, translation_provider_name
from localization.translation.memory import (
    hash_source,
    prune_translation_memory,
    tm_lookup,
    tm_store,
)

# ── factory ──


class TestFactory:
    def test_get_translation_provider_returns_none(self):
        assert get_translation_provider() is None

    def test_translation_provider_name_returns_none(self):
        assert translation_provider_name() is None


# ── hashing convention ──


class TestHashSource:
    def test_matches_backend_hash_convention(self):
        """TM rows written by either code path must be mutually readable, so
        memory.hash_source and translations._hash_source must agree."""
        from backend.api_modular.translations import _hash_source

        for sample in ("hello", "世界", "", "A sentence with spaces.", "café"):
            assert hash_source(sample) == _hash_source(sample)

    def test_is_16_hex_chars(self):
        h = hash_source("anything")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ── translation memory ──


@pytest.fixture
def tm_db(tmp_path: Path) -> Path:
    """A temp DB with the string_translations schema from
    ``translations._migrate_string_translations``."""
    db_path = tmp_path / "tm.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS string_translations (
            source_hash TEXT NOT NULL,
            locale TEXT NOT NULL,
            source TEXT NOT NULL,
            translation TEXT NOT NULL,
            translator TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_hash, locale)
        )""")
    conn.commit()
    conn.close()
    return db_path


class TestTranslationMemory:
    def test_store_lookup_round_trip(self, tm_db: Path):
        tm_store(tm_db, [("Hello", "你好"), ("Goodbye", "再见")], "zh-Hans", "stub-mt")
        hits, misses = tm_lookup(tm_db, ["Hello", "Goodbye"], "zh-Hans")
        assert hits == {0: "你好", 1: "再见"}
        assert misses == []
        # Provenance recorded.
        conn = sqlite3.connect(str(tm_db))
        try:
            translators = {r[0] for r in conn.execute("SELECT translator FROM string_translations")}
        finally:
            conn.close()
        assert translators == {"stub-mt"}

    def test_identity_pairs_are_not_stored(self, tm_db: Path):
        """A 'translation' equal to its source is indistinguishable from a
        degraded pass-through and must never be cached."""
        tm_store(tm_db, [("42", "42"), ("Hello", "你好")], "zh-Hans", "stub-mt")
        conn = sqlite3.connect(str(tm_db))
        try:
            rows = conn.execute("SELECT source, translation FROM string_translations").fetchall()
        finally:
            conn.close()
        assert rows == [("Hello", "你好")]

    def test_lookup_returns_misses_for_unknown_texts(self, tm_db: Path):
        tm_store(tm_db, [("Hello", "你好")], "zh-Hans", "stub-mt")
        hits, misses = tm_lookup(tm_db, ["Hello", "Unknown text"], "zh-Hans")
        assert hits == {0: "你好"}
        assert misses == [(1, "Unknown text")]

    def test_lookup_is_locale_scoped(self, tm_db: Path):
        tm_store(tm_db, [("Hello", "你好")], "zh-Hans", "stub-mt")
        hits, misses = tm_lookup(tm_db, ["Hello"], "ja")
        assert hits == {}
        assert misses == [(0, "Hello")]

    def test_lookup_empty_input(self, tm_db: Path):
        assert tm_lookup(tm_db, [], "zh-Hans") == ({}, [])

    def test_prune_deletes_old_rows_only(self, tm_db: Path):
        tm_store(tm_db, [("Old", "旧"), ("New", "新")], "zh-Hans", "stub-mt")
        conn = sqlite3.connect(str(tm_db))
        try:
            conn.execute(
                "UPDATE string_translations SET updated_at = datetime('now', '-40 days') "
                "WHERE source = 'Old'"
            )
            conn.commit()
        finally:
            conn.close()

        removed = prune_translation_memory(tm_db, 30)
        assert removed == 1
        hits, misses = tm_lookup(tm_db, ["Old", "New"], "zh-Hans")
        assert hits == {1: "新"}
        assert [text for _i, text in misses] == ["Old"]

    def test_prune_refuses_negative_days(self, tm_db: Path):
        with pytest.raises(ValueError, match=r"^older_than_days must be >= 0$"):
            prune_translation_memory(tm_db, -1)

    def test_prune_zero_days_is_valid_and_sweeps_everything(self, tm_db: Path):
        """0 is a legal argument (the guard is strictly `< 0`) and means
        "older than now" — with a backdated row, that row goes."""
        tm_store(tm_db, [("Old", "旧")], "zh-Hans", "stub-mt")
        conn = sqlite3.connect(str(tm_db))
        try:
            conn.execute("UPDATE string_translations SET updated_at = datetime('now', '-1 hour')")
            conn.commit()
        finally:
            conn.close()
        assert prune_translation_memory(tm_db, 0) == 1

    def test_prune_empty_table_returns_zero(self, tm_db: Path):
        assert prune_translation_memory(tm_db, 30) == 0

    def test_lookup_on_unreadable_db_degrades_to_all_misses(self, tmp_path: Path, caplog):
        """A broken TM is a cache miss, never an exception — every text
        comes back as a miss and the failure is logged for the operator."""
        bogus = tmp_path / "not-a-dir" / "tm.db"  # parent missing → sqlite3.OperationalError
        with caplog.at_level("ERROR", logger="localization.translation.memory"):
            hits, misses = tm_lookup(bogus, ["One", "Two"], "zh-Hans")
        assert hits == {}
        assert misses == [(0, "One"), (1, "Two")]
        assert any(r.getMessage() == "TM lookup failed" for r in caplog.records)

    def test_store_on_unreadable_db_logs_and_does_not_raise(self, tmp_path: Path, caplog):
        bogus = tmp_path / "not-a-dir" / "tm.db"
        with caplog.at_level("ERROR", logger="localization.translation.memory"):
            tm_store(bogus, [("One", "一")], "zh-Hans", "stub-mt")
        assert any(r.getMessage() == "TM store failed" for r in caplog.records)

    def test_identity_skip_is_logged_with_the_exact_count(self, tm_db: Path, caplog):
        """The skip log is the only operator-visible signal that identity
        results were withheld — its count must be right (2 of 3 here)."""
        with caplog.at_level("INFO", logger="localization.translation.memory"):
            tm_store(
                tm_db,
                [("42", "42"), ("Hello", "你好"), ("N/A", "N/A")],
                "zh-Hans",
                "stub-mt",
            )
        messages = [r.getMessage() for r in caplog.records]
        assert (
            "Not caching 2 identity result(s) — a translation equal to its "
            "source is indistinguishable from a degraded pass-through" in messages
        )
        hits, _misses = tm_lookup(tm_db, ["42", "Hello", "N/A"], "zh-Hans")
        assert hits == {1: "你好"}


# ── pipeline degradation without a provider ──


class _StubSTT(STTProvider):
    """Canned-transcript STT provider."""

    is_local = True

    def __init__(self, transcript):
        self._transcript = transcript

    @property
    def name(self) -> str:
        return "stub-stt"

    def transcribe(self, audio_path, language="en"):
        return self._transcript

    def supports_language(self, language):
        return True

    def usage_remaining(self):
        return None


class TestPipelineDegradation:
    def test_generate_subtitles_source_only_without_provider(self, tmp_path: Path):
        from localization.pipeline import generate_subtitles
        from localization.stt.base import Transcript, WordTimestamp

        words = [
            WordTimestamp("Hello", 0, 500),
            WordTimestamp("world.", 600, 1200),
        ]
        transcript = Transcript(words=words, language="en", provider="stub", duration_ms=1200)

        with patch("localization.pipeline.get_translation_provider", return_value=None):
            source_vtt, translated_vtt = generate_subtitles(
                audio_path=tmp_path / "ch01.opus",
                output_dir=tmp_path,
                target_locale="zh-Hans",
                source_lang="en",
                chapter_name="ch01",
                stt_provider=_StubSTT(transcript),
            )

        assert translated_vtt is None
        assert source_vtt == tmp_path / "ch01.en.vtt"
        assert source_vtt.exists()
        assert source_vtt.read_text(encoding="utf-8").startswith("WEBVTT")
        # Only the source VTT was produced — no target-locale artifact at all.
        assert list(tmp_path.glob("*.zh-Hans.vtt")) == []


# ── TranslationProvider ABC ──


class _MinimalProvider(TranslationProvider):
    """Smallest possible concrete implementation."""

    def __init__(self):
        self.calls: list[tuple[list[str], str, str, bool]] = []

    @property
    def name(self) -> str:
        return "minimal"

    def translate(self, texts, target_locale, source_lang="EN", strict=False):
        self.calls.append((list(texts), target_locale, source_lang, strict))
        return [f"[{target_locale}]{t}" for t in texts]


class TestTranslationProviderABC:
    def test_cannot_instantiate_abstract_base(self):
        with pytest.raises(TypeError):
            TranslationProvider()  # type: ignore[abstract]

    def test_minimal_concrete_subclass_works(self):
        provider = _MinimalProvider()
        assert provider.name == "minimal"
        assert provider.degraded is False
        assert provider.degraded_texts == 0
        assert provider.translate(["Hi"], "zh-Hans") == ["[zh-Hans]Hi"]

    def test_translate_one_delegates_to_translate(self):
        provider = _MinimalProvider()
        result = provider.translate_one("Hello", "zh-Hans", source_lang="EN", strict=True)
        assert result == "[zh-Hans]Hello"
        assert provider.calls == [(["Hello"], "zh-Hans", "EN", True)]

    def test_unavailable_error_is_runtime_error(self):
        assert issubclass(TranslationUnavailableError, RuntimeError)
