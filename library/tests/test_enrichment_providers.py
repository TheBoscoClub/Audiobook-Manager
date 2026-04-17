"""Tests for enrichment provider chain.

All external API calls are mocked. Database operations use real
in-memory SQLite initialized from schema.sql.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment import _merge_updates, enrich_book
from scripts.enrichment.base import EnrichmentProvider
from scripts.enrichment.provider_local import LocalProvider

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


def init_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def insert_test_book(db_path: Path, **overrides) -> int:
    defaults = {"title": "Test Book", "author": "Test Author", "file_path": "/test/book.opus"}
    defaults.update(overrides)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO audiobooks (title, author, file_path) VALUES (?, ?, ?)",
        (defaults["title"], defaults["author"], defaults["file_path"]),
    )
    book_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return book_id


class TestEnrichmentProviderBase:
    """Test the provider base class interface."""

    def test_base_class_has_name(self):
        class TestProvider(EnrichmentProvider):
            name = "test"

            def can_enrich(self, book: dict) -> bool:
                return True

            def enrich(self, book: dict) -> dict:
                return {"series": "Test Series"}

        p = TestProvider()
        assert p.name == "test"

    def test_base_class_requires_name(self):
        with pytest.raises(TypeError):
            EnrichmentProvider()

    def test_can_enrich_returns_bool(self):
        class AlwaysProvider(EnrichmentProvider):
            name = "always"

            def can_enrich(self, book: dict) -> bool:
                return True

            def enrich(self, book: dict) -> dict:
                return {}

        p = AlwaysProvider()
        assert p.can_enrich({"title": "X"}) is True

    def test_enrich_returns_dict(self):
        class FieldProvider(EnrichmentProvider):
            name = "field"

            def can_enrich(self, book: dict) -> bool:
                return True

            def enrich(self, book: dict) -> dict:
                return {"series": "Alpha", "series_sequence": 1.0}

        p = FieldProvider()
        result = p.enrich({"title": "X"})
        assert result == {"series": "Alpha", "series_sequence": 1.0}


class TestLocalProvider:
    """Test local file-based enrichment (no API calls)."""

    def test_can_enrich_always_true(self):
        p = LocalProvider()
        assert p.can_enrich({"title": "Any Book"}) is True

    def test_extracts_asin_from_voucher(self, tmp_path):
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B0D7JLGFST"}}
        (sources_dir / "B0D7JLGFST_Revenge_Prey-AAX_44_128.voucher").write_text(json.dumps(voucher))

        p = LocalProvider(sources_dir=sources_dir)
        book = {
            "title": "Revenge Prey",
            "author": "Author Name",
            "file_path": "/lib/Author Name/Revenge Prey/Revenge Prey.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("asin") == "B0D7JLGFST"

    def test_extracts_series_from_tags(self):
        p = LocalProvider()
        book = {
            "title": "Book Title",
            "author": "Author",
            "file_path": "/lib/Author/Book Title/Book Title.opus",
            "asin": "B123456789",
            "series": "",
            "series_part": "3",
        }
        result = p.enrich(book)
        assert result.get("series_sequence") == 3.0

    def test_parses_series_from_title_colon_format(self):
        p = LocalProvider()
        book = {
            "title": "Dark Tower: The Gunslinger, Book 1",
            "author": "Stephen King",
            "file_path": "/lib/King/DT/dt.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("series") == "The Gunslinger"
        assert result.get("series_sequence") == 1.0

    def test_parses_series_from_title_paren_format(self):
        p = LocalProvider()
        book = {
            "title": "Gone Girl (Amazing Amy Book 3)",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("series") == "Amazing Amy"
        assert result.get("series_sequence") == 3.0

    def test_parses_series_novel_format(self):
        p = LocalProvider()
        book = {
            "title": "Reckless: A Jack Reacher Novel",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("series") == "Jack Reacher"

    def test_novel_format_ignores_plain_a_novel(self):
        """'Title: A Novel' should NOT extract 'A' as series."""
        p = LocalProvider()
        book = {
            "title": "The Night Tiger: A Novel",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert "series" not in result

    def test_skips_series_if_already_populated(self):
        p = LocalProvider()
        book = {
            "title": "Book: Some Series, Book 5",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "Existing Series",
        }
        result = p.enrich(book)
        assert "series" not in result

    def test_skips_asin_if_already_populated(self, tmp_path):
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B0NEWONE00"}}
        (sources_dir / "B0NEWONE00_Book-AAX_44_128.voucher").write_text(json.dumps(voucher))

        p = LocalProvider(sources_dir=sources_dir)
        book = {
            "title": "Book",
            "author": "Author",
            "file_path": "/lib/a/Book/Book.opus",
            "asin": "B0EXISTING",
            "series": "",
        }
        result = p.enrich(book)
        assert "asin" not in result


class TestMergeUpdates:
    """Test the _merge_updates function, especially default-value handling."""

    def test_fills_none_field(self):
        current = {"content_type": None, "series": None}
        result = _merge_updates(current, {"content_type": "Podcast", "series": "X"})
        assert result["content_type"] == "Podcast"
        assert result["series"] == "X"

    def test_fills_empty_string(self):
        current = {"series": ""}
        result = _merge_updates(current, {"series": "Jack Reacher"})
        assert result["series"] == "Jack Reacher"

    def test_does_not_overwrite_real_value(self):
        current = {"series": "Existing Series"}
        result = _merge_updates(current, {"series": "New Series"})
        assert "series" not in result

    def test_overwrites_product_default_content_type(self):
        """content_type='Product' (schema default) should be treated as unfilled."""
        current = {"content_type": "Product"}
        result = _merge_updates(current, {"content_type": "Podcast"})
        assert result["content_type"] == "Podcast"

    def test_does_not_overwrite_non_default_content_type(self):
        """content_type='Podcast' is a real value, not the default."""
        current = {"content_type": "Podcast"}
        result = _merge_updates(current, {"content_type": "Show"})
        assert "content_type" not in result

    def test_ignores_unknown_columns(self):
        current = {"series": ""}
        result = _merge_updates(current, {"unknown_field": "value"})
        assert "unknown_field" not in result

    def test_side_table_keys_always_pass(self):
        current = {"series": "Existing"}
        result = _merge_updates(current, {"categories": [{"name": "Fiction"}]})
        assert result["categories"] == [{"name": "Fiction"}]


class TestEnrichBookIntegration:
    """Integration tests for enrich_book with genre/topic population."""

    def test_genres_populated_from_categories(self, tmp_path):
        """Enrichment should populate audiobook_genres from Audible categories."""
        db_path = tmp_path / "test.db"
        init_test_db(db_path)
        book_id = insert_test_book(db_path)

        # Create a mock provider that returns categories
        class MockAudibleProvider(EnrichmentProvider):
            name = "audible"

            def can_enrich(self, book):
                return True

            def enrich(self, book):
                return {
                    "content_type": "Product",
                    "categories": [
                        {
                            "category_path": "Literature & Fiction",
                            "category_name": "Literature & Fiction",
                            "root_category": "Literature & Fiction",
                            "depth": 1,
                            "audible_category_id": "123",
                        },
                        {
                            "category_path": "Literature & Fiction > Mystery",
                            "category_name": "Mystery",
                            "root_category": "Literature & Fiction",
                            "depth": 2,
                            "audible_category_id": "456",
                        },
                    ],
                }

        result = enrich_book(
            book_id, db_path=db_path, quiet=True, providers=[MockAudibleProvider()]
        )

        assert result["audible_enriched"] is True

        # Verify genres were populated
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT g.name FROM genres g
               JOIN audiobook_genres ag ON g.id = ag.genre_id
               WHERE ag.audiobook_id = ?
               ORDER BY g.name""",
            (book_id,),
        )
        genres = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "Literature & Fiction" in genres
        assert "Mystery" in genres

    def test_content_type_default_overwritten(self, tmp_path):
        """content_type='Product' (default) should be overwritten by enrichment."""
        db_path = tmp_path / "test.db"
        init_test_db(db_path)
        book_id = insert_test_book(db_path)

        class MockProvider(EnrichmentProvider):
            name = "audible"

            def can_enrich(self, book):
                return True

            def enrich(self, book):
                return {"content_type": "Podcast"}

        enrich_book(book_id, db_path=db_path, quiet=True, providers=[MockProvider()])

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT content_type FROM audiobooks WHERE id = ?", (book_id,))
        ct = cursor.fetchone()[0]
        conn.close()

        assert ct == "Podcast"

    def test_topics_extracted_from_summary(self, tmp_path):
        """Topics should be extracted from publisher_summary during enrichment."""
        db_path = tmp_path / "test.db"
        init_test_db(db_path)
        book_id = insert_test_book(db_path)

        class MockProvider(EnrichmentProvider):
            name = "audible"

            def can_enrich(self, book):
                return True

            def enrich(self, book):
                return {
                    "publisher_summary": (
                        "A gripping war story about military conflict "
                        "and political intrigue in a society at the brink."
                    )
                }

        enrich_book(book_id, db_path=db_path, quiet=True, providers=[MockProvider()])

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT t.name FROM topics t
               JOIN audiobook_topics at ON t.id = at.topic_id
               WHERE at.audiobook_id = ?""",
            (book_id,),
        )
        topics = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "war" in topics or "politics" in topics or "society" in topics
