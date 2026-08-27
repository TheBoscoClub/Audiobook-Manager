"""Tests for enrichment_source column in audiobooks schema."""

import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


class TestEnrichmentSourceColumn:
    """Verify enrichment_source column exists and works correctly."""

    @pytest.fixture
    def db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        yield conn
        conn.close()

    def test_column_exists(self, db):
        """enrichment_source column must exist in audiobooks table."""
        cursor = db.execute("PRAGMA table_info(audiobooks)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "enrichment_source" in columns

    def test_default_is_null(self, db):
        """enrichment_source defaults to NULL for new rows."""
        db.execute(
            "INSERT INTO audiobooks (title, file_path) VALUES (?, ?)",
            ("Test Book", "/test/book.opus"),
        )
        cursor = db.execute("SELECT enrichment_source FROM audiobooks WHERE title = 'Test Book'")
        assert cursor.fetchone()[0] is None

    def test_accepts_provider_names(self, db):
        """enrichment_source accepts known provider name strings."""
        for source in ("local", "audible", "google_books", "openlibrary"):
            db.execute(
                "INSERT INTO audiobooks (title, file_path, enrichment_source) VALUES (?, ?, ?)",
                (f"Book {source}", f"/test/{source}.opus", source),
            )
        cursor = db.execute("SELECT enrichment_source FROM audiobooks ORDER BY id")
        values = [row[0] for row in cursor.fetchall()]
        assert values == ["local", "audible", "google_books", "openlibrary"]
