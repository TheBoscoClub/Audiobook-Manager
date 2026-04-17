"""Coverage-focused tests for translations.py pure helpers.

Targets helper functions in ``backend.api_modular.translations`` that aren't
reachable through the public endpoints without patching DeepL. These tests
exercise pure logic (``_load_books_for_missing``, ``_insert_fresh_translation``,
``_update_series_only``, ``_apply_translations``, ``_load_cached_on_demand``,
``_persist_on_demand_translations``, ``_translate_batch_field_with_map``,
``_translate_batch_descriptions``, ``_persist_batch_translations``,
``_load_batch_books``, ``_find_existing_translations``,
``_batch_nothing_to_do_response``) directly.

These paths execute when DeepL is configured; the existing endpoint tests
take the DEEPL_API_KEY=None short-circuit. Covering them here lifts the
blueprint's coverage without requiring a real DeepL client.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.api_modular import translations as tr


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """Minimal SQLite DB with audiobooks and audiobook_translations tables.

    Seeds four books — two with series strings, one with an empty series,
    and one existing translation row — so the helpers have realistic data
    to walk through.
    """
    db_path = tmp_path / "trans_helpers.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            series TEXT,
            description TEXT,
            publisher_summary TEXT,
            file_path TEXT,
            format TEXT
        );
        CREATE TABLE audiobook_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            title TEXT,
            author_display TEXT,
            series_display TEXT,
            description TEXT,
            translator TEXT DEFAULT 'deepl',
            pinyin_sort TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(audiobook_id, locale)
        );
        """
    )
    conn.executemany(
        "INSERT INTO audiobooks "
        "(id, title, author, series, description, publisher_summary, file_path, format) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                1,
                "The Lord of the Rings",
                "Tolkien",
                "Middle-earth",
                "Epic fantasy",
                None,
                "/tmp/1.opus",
                "opus",
            ),  # nosec B108 -- DB string fixture, no filesystem write
            (2, "The Hobbit", "Tolkien", "Middle-earth", "Prequel", None, "/tmp/2.opus", "opus"),  # nosec B108 -- DB string fixture, no filesystem write
            (3, "Dune", "Herbert", "", None, "Arrakis sci-fi", "/tmp/3.opus", "opus"),  # nosec B108 -- DB string fixture, no filesystem write
            (4, "Standalone", "Single Author", None, "A lone book", None, "/tmp/4.opus", "opus"),  # nosec B108 -- DB string fixture, no filesystem write
        ],
    )
    conn.commit()
    conn.close()
    return db_path


# ── _load_books_for_missing ──


class TestLoadBooksForMissing:
    def test_returns_only_requested_ids(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = tr._load_books_for_missing(conn, [1, 3])
            ids = {b["id"] for b in books}
            assert ids == {1, 3}
        finally:
            conn.close()

    def test_empty_missing_list(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = tr._load_books_for_missing(conn, [])
            assert books == []
        finally:
            conn.close()


# ── _translate_title_author_batch ──


class TestTranslateTitleAuthorBatch:
    def test_empty_needs_title_returns_empty(self):
        translator = MagicMock()
        titles, authors = tr._translate_title_author_batch(translator, [], "zh-Hans")
        assert titles == []
        assert authors == []
        translator.translate.assert_not_called()

    def test_translates_titles_and_authors(self):
        translator = MagicMock()
        # Return distinct translations for titles and authors. The helper
        # consumes translated_authors via an iterator once per non-empty
        # author — so 2 authors need 2 translation entries.
        translator.translate.side_effect = [
            ["魔戒", "霍比特人"],  # titles
            ["托尔金", "托尔金"],  # authors — aligned 1:1 with non-empty entries
        ]
        needs = [
            {"id": 1, "title": "LotR", "author": "Tolkien"},
            {"id": 2, "title": "Hobbit", "author": "Tolkien"},
        ]
        titles, authors = tr._translate_title_author_batch(translator, needs, "zh-Hans")
        assert titles == ["魔戒", "霍比特人"]
        assert authors == ["托尔金", "托尔金"]

    def test_skips_empty_authors(self):
        translator = MagicMock()
        translator.translate.side_effect = [["T1"], []]
        needs = [{"id": 1, "title": "Book", "author": None}]
        titles, authors = tr._translate_title_author_batch(translator, needs, "es")
        assert titles == ["T1"]
        assert authors == [""]


# ── _translate_unique_series ──


class TestTranslateUniqueSeries:
    def test_no_series_books_returns_empty(self):
        translator = MagicMock()
        mapping, unique = tr._translate_unique_series(translator, [{"id": 1, "series": None}], "es")
        assert mapping == {}
        translator.translate.assert_not_called()

    def test_dedupes_series(self):
        translator = MagicMock()
        translator.translate.return_value = ["中土"]
        books = [{"id": 1, "series": "Middle-earth"}, {"id": 2, "series": "Middle-earth"}]
        mapping, unique = tr._translate_unique_series(translator, books, "zh-Hans")
        # Called once with the deduped list.
        translator.translate.assert_called_once_with(["Middle-earth"], "zh-Hans")
        assert mapping == {"Middle-earth": "中土"}


# ── _insert_fresh_translation / _update_series_only / _apply_translations ──


class TestApplyTranslations:
    def test_insert_fresh_and_update_series(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            # Pre-seed book 1 with a partial translation (no series_display).
            conn.execute(
                "INSERT INTO audiobook_translations "
                "(audiobook_id, locale, title, author_display) "
                "VALUES (1, 'zh-Hans', '预存', '作者')"
            )
            conn.commit()

            result = {"1": {"title": "预存", "author_display": "作者"}}
            # Book 1 already has a row → _update_series_only path.
            # Book 2 has no row → _insert_fresh_translation path. Title/author
            # iterators are aligned to the fresh-only list via caller-supplied
            # order, so only Book 2's translation entry is provided.
            books = [
                {"id": 1, "title": "LotR", "author": "Tolkien", "series": "Middle-earth"},
                {"id": 2, "title": "Hobbit", "author": "Tolkien", "series": "Middle-earth"},
            ]
            tr._apply_translations(
                conn,
                books,
                "zh-Hans",
                ["霍比特人"],  # only the fresh book needs a title
                ["托尔金"],  # only the fresh book needs an author
                {"Middle-earth": "中土"},
                result,
            )
            conn.commit()

            row1 = conn.execute(
                "SELECT title, series_display FROM audiobook_translations "
                "WHERE audiobook_id = 1 AND locale = 'zh-Hans'"
            ).fetchone()
            row2 = conn.execute(
                "SELECT title, series_display, author_display "
                "FROM audiobook_translations "
                "WHERE audiobook_id = 2 AND locale = 'zh-Hans'"
            ).fetchone()
            assert row1["series_display"] == "中土"
            # Title NOT overwritten for already-present row.
            assert row1["title"] == "预存"
            # Fresh row — title/author/series all populated.
            assert row2["title"] == "霍比特人"
            assert row2["author_display"] == "托尔金"
            assert row2["series_display"] == "中土"
            # result_dict picks up both.
            assert "2" in result
            assert result["2"]["series_display"] == "中土"
            assert result["1"]["series_display"] == "中土"
        finally:
            conn.close()

    def test_empty_series_maps_to_empty_string(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            result: dict = {}
            books = [{"id": 4, "title": "Standalone", "author": "Single Author", "series": None}]
            tr._apply_translations(conn, books, "zh-Hans", ["孤本"], ["作者"], {}, result)
            conn.commit()
            row = conn.execute(
                "SELECT series_display FROM audiobook_translations WHERE audiobook_id = 4"
            ).fetchone()
            # Empty source → empty string (NOT NULL) so it doesn't re-qualify.
            assert row["series_display"] == ""
            assert result["4"]["series_display"] == ""
        finally:
            conn.close()


# ── _load_cached_on_demand ──


class TestLoadCachedOnDemand:
    def test_returns_only_cached_in_requested(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                "INSERT INTO audiobook_translations "
                "(audiobook_id, locale, title, author_display, description) "
                "VALUES (1, 'zh-Hans', '魔戒', '托尔金', '史诗')"
            )
            conn.execute(
                "INSERT INTO audiobook_translations "
                "(audiobook_id, locale, title, author_display, description) "
                "VALUES (2, 'zh-Hans', '霍比特', '托尔金', '前传')"
            )
            conn.commit()

            cached = tr._load_cached_on_demand(conn, "zh-Hans", [1, 4])
            assert "1" in cached
            assert "4" not in cached
            assert "2" not in cached  # not in requested list
            assert cached["1"]["title"] == "魔戒"
        finally:
            conn.close()


# ── _persist_on_demand_translations ──


class TestPersistOnDemand:
    def test_inserts_rows_and_sets_pinyin_for_zh(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = [
                {"id": 1, "title": "LotR", "author": "Tolkien"},
                {"id": 2, "title": "Hobbit", "author": "Tolkien"},
            ]
            result = tr._persist_on_demand_translations(
                conn, books, ["魔戒", "霍比特"], ["托尔金", "托尔金"], "zh-Hans"
            )
            conn.commit()

            row = conn.execute(
                "SELECT title, author_display, pinyin_sort "
                "FROM audiobook_translations WHERE audiobook_id = 1"
            ).fetchone()
            assert row["title"] == "魔戒"
            # zh-Hans locale → pinyin_sort populated.
            assert row["pinyin_sort"] is not None
            assert "1" in result and "2" in result
        finally:
            conn.close()

    def test_no_pinyin_for_non_zh(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = [{"id": 4, "title": "Standalone", "author": "Single Author"}]
            tr._persist_on_demand_translations(conn, books, ["Solo"], ["Autor"], "es")
            conn.commit()
            row = conn.execute(
                "SELECT pinyin_sort FROM audiobook_translations WHERE audiobook_id = 4"
            ).fetchone()
            assert row["pinyin_sort"] is None
        finally:
            conn.close()

    def test_fallback_when_translations_short(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            # Fewer translated titles/authors than books → code falls back to
            # the source title/author for the trailing entries.
            books = [
                {"id": 1, "title": "Book 1", "author": "Author 1"},
                {"id": 2, "title": "Book 2", "author": "Author 2"},
            ]
            tr._persist_on_demand_translations(conn, books, ["译1"], [], "zh-Hans")
            conn.commit()
            rows = conn.execute(
                "SELECT audiobook_id, title, author_display "
                "FROM audiobook_translations "
                "WHERE locale = 'zh-Hans' AND audiobook_id IN (1, 2) "
                "ORDER BY audiobook_id"
            ).fetchall()
            assert rows[0]["title"] == "译1"
            assert rows[1]["title"] == "Book 2"  # fallback to source
            assert rows[0]["author_display"] == "Author 1"  # fallback (iter empty)
        finally:
            conn.close()


# ── _translate_batch_field_with_map / _translate_batch_descriptions ──


class TestTranslateBatchFieldWithMap:
    def test_empty_values_all_empty(self):
        translator = MagicMock()
        result = tr._translate_batch_field_with_map(translator, ["", "  "], "es")
        assert result == ["", ""]
        translator.translate.assert_not_called()

    def test_maps_translations_back_to_original_positions(self):
        translator = MagicMock()
        translator.translate.return_value = ["un", "deux"]
        result = tr._translate_batch_field_with_map(translator, ["one", "", "two"], "fr")
        assert result == ["un", "", "deux"]


class TestTranslateBatchDescriptions:
    def test_chunks_of_ten(self):
        translator = MagicMock()
        # 15 non-empty descriptions → 2 sub-batches (10 + 5).
        translator.translate.side_effect = [
            [f"T{i}" for i in range(10)],
            [f"T{i}" for i in range(10, 15)],
        ]
        descriptions = [f"desc {i}" for i in range(15)]
        result = tr._translate_batch_descriptions(translator, descriptions, "de")
        assert result == [f"T{i}" for i in range(15)]
        assert translator.translate.call_count == 2

    def test_empty_descriptions_untranslated(self):
        translator = MagicMock()
        translator.translate.return_value = ["keep"]
        descriptions = ["", "to translate", "   "]
        result = tr._translate_batch_descriptions(translator, descriptions, "de")
        assert result == ["", "keep", ""]

    def test_missing_translation_stays_empty(self):
        translator = MagicMock()
        # Translator returns fewer items than requested — trailing stays "".
        translator.translate.return_value = ["only-one"]
        result = tr._translate_batch_descriptions(translator, ["a", "b"], "de")
        assert result[0] == "only-one"
        assert result[1] == ""


# ── _load_batch_books / _find_existing_translations / _persist_batch_translations ──


class TestLoadBatchBooks:
    def test_all_rows_when_requested_ids_none(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = tr._load_batch_books(conn, None)
            assert len(books) == 4
        finally:
            conn.close()

    def test_filter_to_requested(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = tr._load_batch_books(conn, {1, 4})
            ids = {b["id"] for b in books}
            assert ids == {1, 4}
        finally:
            conn.close()


class TestFindExistingTranslations:
    def test_empty_books_returns_empty_set(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        try:
            assert tr._find_existing_translations(conn, "zh-Hans", []) == set()
        finally:
            conn.close()

    def test_intersects_translated_with_book_ids(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                "INSERT INTO audiobook_translations (audiobook_id, locale, title) "
                "VALUES (1, 'zh-Hans', '魔戒')"
            )
            conn.execute(
                "INSERT INTO audiobook_translations (audiobook_id, locale, title) "
                "VALUES (99, 'zh-Hans', 'phantom')"  # not in books list
            )
            conn.commit()
            existing = tr._find_existing_translations(conn, "zh-Hans", [{"id": 1}, {"id": 2}])
            assert existing == {1}
        finally:
            conn.close()


class TestPersistBatchTranslations:
    def test_inserts_all_fields(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = [
                {
                    "id": 1,
                    "title": "LotR",
                    "author": "Tolkien",
                    "series": "ME",
                    "description": "epic",
                    "publisher_summary": None,
                }
            ]
            result = tr._persist_batch_translations(
                conn, books, ["魔戒"], ["托尔金"], ["中土"], ["史诗"], "zh-Hans"
            )
            conn.commit()
            row = conn.execute(
                "SELECT title, author_display, series_display, description, "
                "pinyin_sort FROM audiobook_translations WHERE audiobook_id = 1"
            ).fetchone()
            assert row["title"] == "魔戒"
            assert row["author_display"] == "托尔金"
            assert row["series_display"] == "中土"
            assert row["description"] == "史诗"
            assert row["pinyin_sort"] is not None
            assert result["1"]["description"] == "史诗"
        finally:
            conn.close()

    def test_fallback_when_translations_short(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            books = [
                {
                    "id": 1,
                    "title": "Book1",
                    "author": "Auth1",
                    "series": "",
                    "description": None,
                    "publisher_summary": "pub",
                },
                {
                    "id": 2,
                    "title": "Book2",
                    "author": None,
                    "series": None,
                    "description": None,
                    "publisher_summary": None,
                },
            ]
            # Empty translation arrays — helper uses source as fallback.
            tr._persist_batch_translations(conn, books, [], [], [], ["", ""], "es")
            conn.commit()
            rows = conn.execute(
                "SELECT audiobook_id, title, author_display "
                "FROM audiobook_translations ORDER BY audiobook_id"
            ).fetchall()
            assert rows[0]["title"] == "Book1"
            assert rows[0]["author_display"] == "Auth1"
            assert rows[1]["author_display"] == ""
        finally:
            conn.close()


# ── _batch_nothing_to_do_response ──


class TestBatchNothingToDo:
    def test_shape(self, flask_app):
        with flask_app.app_context():
            resp = tr._batch_nothing_to_do_response(books=[{"id": 1}, {"id": 2}], existing={1})
            data = resp.get_json()
            assert data["total_books"] == 2
            assert data["translated"] == 1
            assert data["needs_translation"] == 0
            assert data["translations"] == {}


# ── _parse_on_demand_ids ──


class TestParseOnDemandIds:
    def test_valid_ids(self):
        ids, err = tr._parse_on_demand_ids({"audiobook_ids": [1, "2", 3]})
        assert err is None
        assert ids == [1, 2, 3]

    def test_invalid_ids_returns_error_tuple(self, flask_app):
        with flask_app.app_context():
            ids, err = tr._parse_on_demand_ids({"audiobook_ids": ["abc", "xyz"]})
            assert ids is None
            assert err is not None
            resp, status = err
            assert status == 400


# ── _translate_on_demand_titles_authors ──


class TestTranslateOnDemandTitlesAuthors:
    def test_empty_books(self):
        translator = MagicMock()
        translator.translate.return_value = []
        titles, authors = tr._translate_on_demand_titles_authors(translator, [], "fr")
        assert titles == []
        assert authors == []

    def test_mixed_author_presence(self):
        translator = MagicMock()
        translator.translate.side_effect = [
            ["T1", "T2"],  # titles
            ["A1"],  # authors (only one non-empty)
        ]
        books = [
            {"id": 1, "title": "Book1", "author": "Alice"},
            {"id": 2, "title": "Book2", "author": None},
        ]
        titles, authors = tr._translate_on_demand_titles_authors(translator, books, "de")
        assert titles == ["T1", "T2"]
        assert authors == ["A1", ""]


# ── _validate_translate_strings_request ──


class TestValidateTranslateStringsRequest:
    def test_missing_data_returns_400(self, flask_app):
        with flask_app.app_context():
            locale, raw, err = tr._validate_translate_strings_request(None)
            assert locale is None
            assert err is not None
            resp, status = err
            assert status == 400

    def test_missing_locale_returns_400(self, flask_app):
        with flask_app.app_context():
            _, _, err = tr._validate_translate_strings_request({})
            resp, status = err
            assert status == 400

    def test_english_short_circuits(self, flask_app):
        with flask_app.app_context():
            locale, raw, err = tr._validate_translate_strings_request(
                {"locale": "en", "strings": ["x"]}
            )
            assert locale is None
            assert raw is None
            # err is a jsonify response with {}.
            assert err is not None

    def test_valid_returns_locale_and_strings(self, flask_app):
        with flask_app.app_context():
            locale, raw, err = tr._validate_translate_strings_request(
                {"locale": "fr", "strings": ["hello"]}
            )
            assert locale == "fr"
            assert raw == ["hello"]
            assert err is None


# ── _normalize_strings_payload ──


class TestNormalizeStringsPayload:
    def test_strips_and_dedupes(self):
        seen = tr._normalize_strings_payload(["hello", " hello ", "world"])
        # hello and " hello " collapse to the same hash after strip.
        values = list(seen.values())
        assert values.count("hello") == 1
        assert "world" in values

    def test_skips_empty_and_non_str(self):
        seen = tr._normalize_strings_payload(["", "   ", 42, None, "ok"])
        assert list(seen.values()) == ["ok"]

    def test_skips_overlong(self):
        seen = tr._normalize_strings_payload(["x" * 1001, "short"])
        assert list(seen.values()) == ["short"]

    def test_caps_at_200(self):
        seen = tr._normalize_strings_payload([f"s-{i}" for i in range(250)])
        assert len(seen) == 200


# ── _do_translate_missing / _translate_and_cache_strings short-circuits ──


class TestDeepLKeyShortCircuits:
    def test_do_translate_missing_no_key(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        with patch("localization.config.DEEPL_API_KEY", ""):
            tr._do_translate_missing(conn, [1], "zh-Hans", {})
        # No rows should be inserted.
        n = conn.execute("SELECT COUNT(*) AS c FROM audiobook_translations").fetchone()[0]
        assert n == 0
        conn.close()

    def test_translate_and_cache_strings_no_key(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        # Create string_translations table for this isolated DB.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS string_translations (
                source_hash TEXT NOT NULL,
                locale TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                translator TEXT DEFAULT 'deepl',
                PRIMARY KEY (source_hash, locale)
            )"""
        )
        with patch("localization.config.DEEPL_API_KEY", ""):
            result: dict = {}
            tr._translate_and_cache_strings(conn, {"abc123": "hello"}, "zh-Hans", result)
            assert result == {}
        conn.close()


# ── _do_translate_missing / _translate_missing ──


class TestDoTranslateMissing:
    """Verify the DeepL-backed translation orchestrator runs end to end."""

    def test_no_api_key_short_circuits(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            with patch("localization.config.DEEPL_API_KEY", ""):
                # Should return silently without touching the translator
                result: dict = {}
                tr._do_translate_missing(conn, [1, 2], "zh-Hans", result)
                assert result == {}
        finally:
            conn.close()

    def test_no_books_for_ids_short_circuits(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator"
                ) as TranslatorClass,
            ):
                result: dict = {}
                # No matching books for these ids
                tr._do_translate_missing(conn, [999, 1000], "zh-Hans", result)
                TranslatorClass.assert_not_called()
                assert result == {}
        finally:
            conn.close()

    def test_full_translation_flow(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            # Create the translator stub that returns predictable translations
            translator = MagicMock()
            translator.translate.side_effect = [
                ["魔戒", "霍比特人"],  # titles
                ["托尔金", "托尔金"],  # authors
                ["中土"],  # unique_series
            ]
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                result: dict = {}
                tr._do_translate_missing(conn, [1, 2], "zh-Hans", result)
                assert "1" in result or 1 in result
        finally:
            conn.close()

    def test_translate_missing_swallows_exceptions(self, seeded_db: Path):
        """The public wrapper must not propagate translator errors."""
        conn = sqlite3.connect(str(seeded_db))
        try:
            with patch(
                "backend.api_modular.translations._do_translate_missing",
                side_effect=RuntimeError("fail"),
            ):
                # Should NOT raise
                tr._translate_missing(conn, [1], "zh-Hans", {})
        finally:
            conn.close()


# ── _translate_missing_collections ──


class TestTranslateMissingCollections:
    def _setup_collection_db(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collection_translations (
                collection_id TEXT NOT NULL,
                locale TEXT NOT NULL,
                name TEXT NOT NULL,
                translator TEXT DEFAULT 'deepl',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collection_id, locale)
            );
            """
        )
        conn.commit()
        return conn

    def test_no_api_key_short_circuits(self, seeded_db: Path):
        conn = self._setup_collection_db(seeded_db)
        try:
            with patch("localization.config.DEEPL_API_KEY", ""):
                result: dict = {}
                tr._translate_missing_collections(conn, ["c1"], {"c1": "A"}, "zh-Hans", result)
                assert result == {}
        finally:
            conn.close()

    def test_no_unique_names_short_circuits(self, seeded_db: Path):
        conn = self._setup_collection_db(seeded_db)
        try:
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator"
                ) as TranslatorClass,
            ):
                result: dict = {}
                # id_to_name has no matches for the missing_ids → empty unique_names
                tr._translate_missing_collections(conn, ["c1"], {"c2": "A"}, "zh-Hans", result)
                TranslatorClass.assert_not_called()
        finally:
            conn.close()

    def test_happy_path_translates_and_caches(self, seeded_db: Path):
        conn = self._setup_collection_db(seeded_db)
        try:
            translator = MagicMock()
            translator.translate.return_value = ["翻译A", "翻译B"]
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                result: dict = {}
                tr._translate_missing_collections(
                    conn, ["c1", "c2"], {"c1": "Alpha", "c2": "Beta"}, "zh-Hans", result
                )
                assert result == {"c1": "翻译A", "c2": "翻译B"}
                # Verify cache row was written
                row = conn.execute(
                    "SELECT name FROM collection_translations WHERE collection_id='c1' AND locale='zh-Hans'"
                ).fetchone()
                assert row[0] == "翻译A"
        finally:
            conn.close()

    def test_exception_is_swallowed(self, seeded_db: Path):
        conn = self._setup_collection_db(seeded_db)
        try:
            translator = MagicMock()
            translator.translate.side_effect = RuntimeError("api down")
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                # Should NOT raise — function logs and returns
                tr._translate_missing_collections(conn, ["c1"], {"c1": "A"}, "zh-Hans", {})
        finally:
            conn.close()


# ── _translate_and_cache_strings (happy path with translator) ──


class TestTranslateAndCacheStringsHappy:
    def _setup_strings_db(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS string_translations (
                source_hash TEXT NOT NULL,
                locale TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                translator TEXT DEFAULT 'deepl',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_hash, locale)
            )"""
        )
        conn.commit()
        return conn

    def test_translates_and_caches(self, seeded_db: Path):
        conn = self._setup_strings_db(seeded_db)
        try:
            translator = MagicMock()
            translator.translate.return_value = ["你好", "再见"]
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                result: dict = {}
                tr._translate_and_cache_strings(
                    conn,
                    {"hash1": "Hello", "hash2": "Goodbye"},
                    "zh-Hans",
                    result,
                )
                assert result == {"hash1": "你好", "hash2": "再见"}
        finally:
            conn.close()

    def test_exception_is_swallowed(self, seeded_db: Path):
        conn = self._setup_strings_db(seeded_db)
        try:
            translator = MagicMock()
            translator.translate.side_effect = RuntimeError("boom")
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                tr._translate_and_cache_strings(conn, {"h": "X"}, "zh-Hans", {})
        finally:
            conn.close()


# ── _do_on_demand_translation ──


class TestDoOnDemandTranslation:
    def test_no_api_key_short_circuits(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            with patch("localization.config.DEEPL_API_KEY", ""):
                cached: dict = {}
                tr._do_on_demand_translation(conn, "zh-Hans", [1, 2], cached)
                assert cached == {}
        finally:
            conn.close()

    def test_no_matching_books_short_circuits(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator"
                ) as TranslatorClass,
            ):
                tr._do_on_demand_translation(conn, "zh-Hans", [9999], {})
                TranslatorClass.assert_not_called()
        finally:
            conn.close()

    def test_full_flow(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            translator = MagicMock()
            # _translate_on_demand_titles_authors needs titles + author batch
            translator.translate.side_effect = [
                ["魔戒", "霍比特人"],  # titles
                ["托尔金", "托尔金"],  # authors
            ]
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                cached: dict = {}
                tr._do_on_demand_translation(conn, "zh-Hans", [1, 2], cached)
                # Cached should be populated with the new translations
                assert len(cached) >= 1
        finally:
            conn.close()


# ── _translate_batch_all_fields ──


class TestTranslateBatchAllFields:
    def test_translates_all_four_fields(self):
        translator = MagicMock()
        translator.translate.side_effect = [
            ["魔戒", "霍比特人"],  # titles (2 inputs, one call)
            ["托尔金", "托尔金"],  # authors (both non-empty → 2 translations)
            ["中土", "中土"],  # series (both non-empty → 2 translations)
            ["简介A", "简介B"],  # descriptions
        ]
        needs = [
            {
                "id": 1,
                "title": "LotR",
                "author": "Tolkien",
                "series": "Middle-earth",
                "description": "Epic",
                "publisher_summary": None,
            },
            {
                "id": 2,
                "title": "Hobbit",
                "author": "Tolkien",
                "series": "Middle-earth",
                "description": None,
                "publisher_summary": "Prequel",
            },
        ]
        titles, author_map, series_map, desc_map = tr._translate_batch_all_fields(
            translator, needs, "zh-Hans"
        )
        assert titles == ["魔戒", "霍比特人"]
        # author_map / series_map return lists aligned to input (not dicts)
        assert author_map == ["托尔金", "托尔金"]
        assert series_map == ["中土", "中土"]
        assert len(desc_map) == 2
        assert desc_map[0] == "简介A"
        assert desc_map[1] == "简介B"


# ── _run_batch_translation ──


class TestRunBatchTranslation:
    def test_no_api_key_returns_503(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            tr._db_path = seeded_db
            with patch("localization.config.DEEPL_API_KEY", ""):
                from flask import Flask

                app = Flask(__name__)
                with app.app_context():
                    translations, err = tr._run_batch_translation(conn, "zh-Hans", [])
                    assert translations is None
                    assert err is not None
                    _, status = err
                    assert status == 503
        finally:
            conn.close()

    def test_full_flow(self, seeded_db: Path):
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            tr._db_path = seeded_db
            translator = MagicMock()
            translator.translate.side_effect = [
                ["魔戒"],  # titles
                ["托尔金"],  # unique authors
                ["中土"],  # unique series
                ["史诗"],  # descriptions
            ]
            needs = [
                {
                    "id": 1,
                    "title": "LotR",
                    "author": "Tolkien",
                    "series": "Middle-earth",
                    "description": "Epic",
                    "publisher_summary": None,
                }
            ]
            with (
                patch("localization.config.DEEPL_API_KEY", "key"),
                patch(
                    "localization.translation.deepl_translate.DeepLTranslator",
                    return_value=translator,
                ),
            ):
                translations, err = tr._run_batch_translation(conn, "zh-Hans", needs)
                assert err is None
                assert translations is not None
                assert 1 in translations or "1" in translations
        finally:
            conn.close()


# ── _batch_execute / _batch_nothing_to_do_response ──


class TestBatchExecute:
    def test_nothing_to_do_short_circuits(self, seeded_db: Path):
        """When all books already have translations, return early."""
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO audiobook_translations "
            "(audiobook_id, locale, title, author_display, series_display, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "zh-Hans", "cached", "author", "series", "desc"),
        )
        conn.commit()
        try:
            tr._db_path = seeded_db
            from flask import Flask

            app = Flask(__name__)
            with app.app_context():
                resp = tr._batch_execute(conn, "zh-Hans", [1])
                body = resp.get_json()
                assert body["needs_translation"] == 0
        finally:
            conn.close()

    def test_translation_error_propagates(self, seeded_db: Path):
        """If _run_batch_translation returns an error tuple, _batch_execute returns it."""
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        try:
            tr._db_path = seeded_db
            from flask import Flask

            app = Flask(__name__)
            with app.app_context():
                with patch("localization.config.DEEPL_API_KEY", ""):
                    # No API key → _run_batch_translation returns (None, (jsonify, 503))
                    resp = tr._batch_execute(conn, "zh-Hans", [1, 2])
                    # Unpack tuple: (response, status)
                    response, status = resp
                    assert status == 503
        finally:
            conn.close()
