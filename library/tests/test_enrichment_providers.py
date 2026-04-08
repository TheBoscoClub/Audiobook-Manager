"""Tests for enrichment provider chain.

All external API calls are mocked. Database operations use real
in-memory SQLite initialized from schema.sql.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment.base import EnrichmentProvider

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


def init_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def insert_test_book(db_path: Path, **overrides) -> int:
    defaults = {
        "title": "Test Book",
        "author": "Test Author",
        "file_path": "/test/book.opus",
    }
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


import json
from unittest.mock import patch

from scripts.enrichment.provider_local import LocalProvider


class TestLocalProvider:
    """Test local file-based enrichment (no API calls)."""

    def test_can_enrich_always_true(self):
        p = LocalProvider()
        assert p.can_enrich({"title": "Any Book"}) is True

    def test_extracts_asin_from_voucher(self, tmp_path):
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B0D7JLGFST"}}
        (sources_dir / "B0D7JLGFST_Revenge_Prey-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

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
        (sources_dir / "B0NEWONE00_Book-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

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
