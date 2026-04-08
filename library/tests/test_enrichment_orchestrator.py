"""Tests for the enrichment orchestrator (enrichment/__init__.py).

Tests the chain logic, merge-only-empty semantics, and DB writes.
All external providers are mocked.
"""

import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment import enrich_book
from scripts.enrichment.base import EnrichmentProvider


class StubProvider(EnrichmentProvider):
    """Test provider that returns canned data."""

    name = "stub"

    def __init__(self, data: dict, can: bool = True):
        super().__init__()
        self._data = data
        self._can = can

    def can_enrich(self, book: dict) -> bool:
        return self._can

    def enrich(self, book: dict) -> dict:
        return dict(self._data)


def _create_test_db(tmp_path: Path) -> Path:
    """Create a minimal audiobooks DB with schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    schema_path = Path(__file__).parent.parent / "backend" / "schema.sql"
    conn.executescript(schema_path.read_text())
    conn.execute(
        """INSERT INTO audiobooks (id, title, author, file_path, file_size_mb, duration_hours)
           VALUES (1, 'Test Book', 'Test Author', '/fake/path.opus', 100.0, 10.5)"""
    )
    conn.commit()
    conn.close()
    return db_path


class TestEnrichBookChain:
    def test_single_provider_fills_fields(self, tmp_path):
        db = _create_test_db(tmp_path)
        provider = StubProvider({"series": "Dark Tower", "series_sequence": 1.0})
        result = enrich_book(1, db_path=db, quiet=True, providers=[provider])
        assert result["fields_updated"] >= 2
        assert "stub" in result["providers_used"]

        # Verify DB was updated
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM audiobooks WHERE id = 1").fetchone())
        conn.close()
        assert row["series"] == "Dark Tower"
        assert row["series_sequence"] == 1.0
        assert row["enrichment_source"] == "stub"
        assert row["audible_enriched_at"] is not None

    def test_merge_only_empty_fields(self, tmp_path):
        db = _create_test_db(tmp_path)
        # Pre-populate series
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audiobooks SET series = 'Existing' WHERE id = 1")
        conn.commit()
        conn.close()

        provider = StubProvider({"series": "Overwrite Attempt", "isbn": "978TEST"})
        enrich_book(1, db_path=db, quiet=True, providers=[provider])

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM audiobooks WHERE id = 1").fetchone())
        conn.close()
        assert row["series"] == "Existing"  # NOT overwritten
        assert row["isbn"] == "978TEST"  # New field filled

    def test_chain_order_first_writer_wins(self, tmp_path):
        db = _create_test_db(tmp_path)
        p1 = StubProvider({"series": "First"})
        p1.name = "first"
        p2 = StubProvider({"series": "Second", "isbn": "978ISBN"})
        p2.name = "second"

        enrich_book(1, db_path=db, quiet=True, providers=[p1, p2])

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM audiobooks WHERE id = 1").fetchone())
        conn.close()
        assert row["series"] == "First"  # First provider wins
        assert row["isbn"] == "978ISBN"  # Second fills remaining

    def test_skips_provider_that_cannot_enrich(self, tmp_path):
        db = _create_test_db(tmp_path)
        p_skip = StubProvider({"series": "Nope"}, can=False)
        p_fill = StubProvider({"series": "Yes"})
        enrich_book(1, db_path=db, quiet=True, providers=[p_skip, p_fill])

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM audiobooks WHERE id = 1").fetchone())
        conn.close()
        assert row["series"] == "Yes"

    def test_no_db_path_returns_error(self):
        result = enrich_book(1, db_path=None, quiet=True)
        assert "No database path" in result["errors"]

    def test_missing_book_returns_error(self, tmp_path):
        db = _create_test_db(tmp_path)
        result = enrich_book(999, db_path=db, quiet=True, providers=[])
        assert "not found" in result["errors"][0]

    def test_categories_written_to_side_table(self, tmp_path):
        db = _create_test_db(tmp_path)
        cats = [
            {
                "category_path": "Fiction > Thriller",
                "category_name": "Thriller",
                "root_category": "Fiction",
                "depth": 2,
                "audible_category_id": "123",
            }
        ]
        provider = StubProvider({"categories": cats, "asin": "B08TEST"})
        enrich_book(1, db_path=db, quiet=True, providers=[provider])

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT * FROM audible_categories WHERE audiobook_id = 1"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][2] == "Fiction > Thriller"  # category_path

    def test_editorial_reviews_written(self, tmp_path):
        db = _create_test_db(tmp_path)
        reviews = [{"review_text": "Brilliant!", "source": "NYT"}]
        provider = StubProvider({"editorial_reviews": reviews, "asin": "B08TEST"})
        enrich_book(1, db_path=db, quiet=True, providers=[provider])

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT * FROM editorial_reviews WHERE audiobook_id = 1"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][2] == "Brilliant!"

    def test_backward_compat_result_format(self, tmp_path):
        db = _create_test_db(tmp_path)
        result = enrich_book(1, db_path=db, quiet=True, providers=[])
        assert "audible_enriched" in result
        assert "isbn_enriched" in result
        assert "fields_updated" in result
        assert "errors" in result
