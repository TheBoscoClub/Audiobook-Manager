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
