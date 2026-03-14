"""Tests for author/narrator data migration from flat columns to normalized tables."""

import sqlite3
import tempfile

from pathlib import Path


def create_test_db(db_path):
    """Create a minimal DB with schema and test data."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    # Create minimal audiobooks table
    conn.execute("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            narrator TEXT,
            author_last_name TEXT,
            author_first_name TEXT,
            narrator_last_name TEXT,
            narrator_first_name TEXT,
            file_path TEXT UNIQUE NOT NULL,
            content_type TEXT DEFAULT 'Product'
        )
    """)
    # Create new normalized tables from migration SQL
    migration_sql = (
        Path(__file__).parent.parent
        / "backend"
        / "migrations"
        / "011_multi_author_narrator.sql"
    ).read_text()
    conn.executescript(migration_sql)
    return conn


class TestMigration:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.conn = create_test_db(self.db_path)

    def teardown_method(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _insert_book(self, title, author, narrator="Test Narrator"):
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            (title, author, narrator, f"/fake/{title}.opus"),
        )
        self.conn.commit()

    def test_single_author_migrated(self):
        self._insert_book("It", "Stephen King")
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)

        authors = self.conn.execute("SELECT name, sort_name FROM authors").fetchall()
        assert len(authors) == 1
        assert authors[0][0] == "Stephen King"
        assert authors[0][1] == "King, Stephen"

        links = self.conn.execute("SELECT * FROM book_authors").fetchall()
        assert len(links) == 1
        assert links[0][2] == 0  # position

    def test_multi_author_creates_both(self):
        self._insert_book("The Talisman", "Stephen King, Peter Straub")
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)

        authors = self.conn.execute("SELECT name FROM authors ORDER BY name").fetchall()
        assert len(authors) == 2
        names = {a[0] for a in authors}
        assert "Stephen King" in names
        assert "Peter Straub" in names

        links = self.conn.execute(
            "SELECT * FROM book_authors ORDER BY position"
        ).fetchall()
        assert len(links) == 2

    def test_deduplication(self):
        self._insert_book("It", "Stephen King")
        self._insert_book("The Shining", "Stephen King")
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)

        authors = self.conn.execute("SELECT name FROM authors").fetchall()
        assert len(authors) == 1  # Deduplicated

        links = self.conn.execute("SELECT * FROM book_authors").fetchall()
        assert len(links) == 2  # Two books linked

    def test_narrator_migrated(self):
        self._insert_book("It", "Stephen King", "Steven Weber")
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)

        narrators = self.conn.execute(
            "SELECT name, sort_name FROM narrators"
        ).fetchall()
        assert len(narrators) == 1
        assert narrators[0][0] == "Steven Weber"
        assert narrators[0][1] == "Weber, Steven"

    def test_null_author_no_junction_row(self):
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, NULL, ?, ?)",
            ("Orphan Book", "Some Narrator", "/fake/orphan.opus"),
        )
        self.conn.commit()
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)

        links = self.conn.execute(
            "SELECT * FROM book_authors WHERE book_id ="
            " (SELECT id FROM audiobooks WHERE title='Orphan Book')"
        ).fetchall()
        assert len(links) == 0

    def test_group_name_redirected_to_narrator(self):
        self._insert_book("Drama", "Full Cast", "Someone Else")
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)

        # "Full Cast" should NOT be in authors
        authors = self.conn.execute("SELECT name FROM authors").fetchall()
        author_names = {a[0] for a in authors}
        assert "Full Cast" not in author_names

        # "Full Cast" SHOULD be in narrators
        narrators = self.conn.execute("SELECT name FROM narrators").fetchall()
        narrator_names = {n[0] for n in narrators}
        assert "Full Cast" in narrator_names

    def test_idempotent(self):
        """Running migration twice should not duplicate data."""
        self._insert_book("It", "Stephen King")
        from backend.migrations.migrate_to_normalized_authors import migrate

        migrate(self.db_path)
        migrate(self.db_path)  # Second run

        authors = self.conn.execute("SELECT name FROM authors").fetchall()
        assert len(authors) == 1
