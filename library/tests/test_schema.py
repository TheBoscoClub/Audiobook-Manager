"""Tests for database schema: foreign key enforcement and new tables."""

import sqlite3
from pathlib import Path

import pytest

from library.backend.api_modular.core import get_db

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "backend" / "schema.sql"


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh database from schema.sql and return its path."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.close()
    return db_path


class TestForeignKeyEnforcement:
    """Verify that get_db() enables foreign key enforcement."""

    def test_foreign_keys_enabled(self, tmp_path):
        """get_db() connections must have PRAGMA foreign_keys=ON."""
        db_path = tmp_path / "fk_test.db"
        conn = get_db(db_path)
        try:
            result = conn.execute("PRAGMA foreign_keys").fetchone()
            assert result[0] == 1, "foreign_keys PRAGMA should be ON (1)"
        finally:
            conn.close()

    def test_foreign_key_violation_raises(self, fresh_db):
        """Inserting a book_authors row with invalid book_id should fail."""
        conn = get_db(fresh_db)
        try:
            # Create an author first
            conn.execute(
                "INSERT INTO authors (name, sort_name) VALUES (?, ?)",
                ("Test Author", "Author, Test"),
            )
            # Attempt to link to a non-existent audiobook — should raise
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO book_authors (book_id, author_id, position) "
                    "VALUES (?, ?, ?)",
                    (99999, 1, 0),
                )
        finally:
            conn.close()


class TestNewTablesExist:
    """Verify that schema.sql creates the new author/narrator tables."""

    EXPECTED_TABLES = ["authors", "narrators", "book_authors", "book_narrators"]

    def test_tables_created(self, fresh_db):
        """All four new tables must exist after running schema.sql."""
        conn = sqlite3.connect(fresh_db)
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            for table in self.EXPECTED_TABLES:
                assert table in tables, f"Table '{table}' missing from schema"
        finally:
            conn.close()

    EXPECTED_INDICES = [
        "idx_authors_sort",
        "idx_narrators_sort",
        "idx_book_authors_author",
        "idx_book_narrators_narrator",
    ]

    def test_indices_created(self, fresh_db):
        """All new indices must exist after running schema.sql."""
        conn = sqlite3.connect(fresh_db)
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indices = {row[0] for row in cursor.fetchall()}
            for index in self.EXPECTED_INDICES:
                assert index in indices, f"Index '{index}' missing from schema"
        finally:
            conn.close()

    def test_authors_table_columns(self, fresh_db):
        """authors table must have id, name, sort_name columns."""
        conn = sqlite3.connect(fresh_db)
        try:
            cursor = conn.execute("PRAGMA table_info(authors)")
            columns = {row[1] for row in cursor.fetchall()}
            assert columns == {"id", "name", "sort_name"}
        finally:
            conn.close()

    def test_book_authors_table_columns(self, fresh_db):
        """book_authors table must have book_id, author_id, position columns."""
        conn = sqlite3.connect(fresh_db)
        try:
            cursor = conn.execute("PRAGMA table_info(book_authors)")
            columns = {row[1] for row in cursor.fetchall()}
            assert columns == {"book_id", "author_id", "position"}
        finally:
            conn.close()

    def test_narrators_table_columns(self, fresh_db):
        """narrators table must have id, name, sort_name columns."""
        conn = sqlite3.connect(fresh_db)
        try:
            cursor = conn.execute("PRAGMA table_info(narrators)")
            columns = {row[1] for row in cursor.fetchall()}
            assert columns == {"id", "name", "sort_name"}
        finally:
            conn.close()

    def test_book_narrators_table_columns(self, fresh_db):
        """book_narrators table must have book_id, narrator_id, position columns."""
        conn = sqlite3.connect(fresh_db)
        try:
            cursor = conn.execute("PRAGMA table_info(book_narrators)")
            columns = {row[1] for row in cursor.fetchall()}
            assert columns == {"book_id", "narrator_id", "position"}
        finally:
            conn.close()
