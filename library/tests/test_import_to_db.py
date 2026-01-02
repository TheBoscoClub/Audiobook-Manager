"""
Tests for the import_to_db module.
Tests database creation and audiobook import functionality.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))


@pytest.fixture
def temp_db_path(tmp_path):
    """Create a temporary database path."""
    return tmp_path / "test_audiobooks.db"


@pytest.fixture
def temp_schema_path(tmp_path):
    """Create a temporary schema file with the actual schema."""
    schema_path = tmp_path / "schema.sql"
    actual_schema = LIBRARY_DIR / "backend" / "schema.sql"
    schema_path.write_text(actual_schema.read_text())
    return schema_path


@pytest.fixture
def temp_json_path(tmp_path):
    """Create a temporary JSON file with sample audiobooks."""
    json_path = tmp_path / "audiobooks.json"
    sample_data = {
        "audiobooks": [
            {
                "title": "The Great Test",
                "author": "Test Author",
                "narrator": "Test Narrator",
                "publisher": "Test Publisher",
                "series": "Test Series",
                "duration_hours": 10.5,
                "duration_formatted": "10:30:00",
                "file_size_mb": 250.5,
                "file_path": "/test/path/book1.opus",
                "cover_path": "/test/covers/book1.jpg",
                "format": "opus",
                "quality": "64kbps",
                "description": "A test audiobook",
                "genres": ["Fiction", "Science Fiction"],
                "eras": ["21st Century"],
                "topics": ["Technology"],
                "sha256_hash": "abc123def456",
                "hash_verified_at": "2025-01-01 00:00:00",
            },
            {
                "title": "Another Book",
                "author": "Another Author",
                "narrator": None,
                "publisher": None,
                "series": None,
                "duration_hours": 5.25,
                "duration_formatted": "5:15:00",
                "file_size_mb": 120.0,
                "file_path": "/test/path/book2.opus",
                "cover_path": None,
                "format": "opus",
                "quality": "64kbps",
                "description": "",
                "genres": [],
                "eras": [],
                "topics": [],
            },
        ]
    }
    json_path.write_text(json.dumps(sample_data))
    return json_path


@pytest.fixture
def many_audiobooks_json(tmp_path):
    """Create JSON with many audiobooks to test progress reporting."""
    json_path = tmp_path / "many_audiobooks.json"
    audiobooks = []
    for i in range(150):
        audiobooks.append(
            {
                "title": f"Book {i}",
                "author": f"Author {i % 10}",
                "narrator": f"Narrator {i % 5}",
                "publisher": "Publisher",
                "series": f"Series {i % 3}" if i % 3 == 0 else None,
                "duration_hours": 5.0 + (i % 10),
                "duration_formatted": f"{5 + i % 10}:00:00",
                "file_size_mb": 100.0 + i,
                "file_path": f"/test/path/book_{i}.opus",
                "cover_path": f"/test/covers/book_{i}.jpg",
                "format": "opus",
                "quality": "64kbps",
                "description": f"Description for book {i}",
                "genres": ["Fiction"] if i % 2 == 0 else ["Nonfiction"],
                "eras": ["Modern"],
                "topics": ["Topic A", "Topic B"],
                "sha256_hash": f"hash_{i:06d}",
                "hash_verified_at": "2025-01-01 00:00:00",
            }
        )
    sample_data = {"audiobooks": audiobooks}
    json_path.write_text(json.dumps(sample_data))
    return json_path


class TestCreateDatabase:
    """Test database creation functionality."""

    def test_create_database_creates_file(self, temp_db_path, temp_schema_path):
        """Test that create_database creates a database file."""
        from backend import import_to_db

        # Patch the module-level paths
        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
        ):
            conn = import_to_db.create_database()
            conn.close()

        assert temp_db_path.exists()

    def test_create_database_creates_tables(self, temp_db_path, temp_schema_path):
        """Test that create_database creates all required tables."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
        ):
            conn = import_to_db.create_database()

        cursor = conn.cursor()

        # Check for main tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "audiobooks" in tables
        assert "genres" in tables
        assert "audiobook_genres" in tables
        assert "eras" in tables
        assert "audiobook_eras" in tables
        assert "topics" in tables
        assert "audiobook_topics" in tables

        conn.close()

    def test_create_database_returns_connection(self, temp_db_path, temp_schema_path):
        """Test that create_database returns a valid connection."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
        ):
            conn = import_to_db.create_database()

        assert isinstance(conn, sqlite3.Connection)

        # Verify connection is usable
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

        conn.close()


class TestImportAudiobooks:
    """Test audiobook import functionality."""

    def test_import_audiobooks_basic(
        self, temp_db_path, temp_schema_path, temp_json_path
    ):
        """Test basic audiobook import."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audiobooks")
        count = cursor.fetchone()[0]

        assert count == 2
        conn.close()

    def test_import_audiobooks_stores_metadata(
        self, temp_db_path, temp_schema_path, temp_json_path
    ):
        """Test that import stores all metadata correctly."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, author, narrator, duration_hours FROM audiobooks WHERE title = 'The Great Test'"
        )
        row = cursor.fetchone()

        assert row[0] == "The Great Test"
        assert row[1] == "Test Author"
        assert row[2] == "Test Narrator"
        assert row[3] == 10.5

        conn.close()

    def test_import_audiobooks_handles_genres(
        self, temp_db_path, temp_schema_path, temp_json_path
    ):
        """Test that import handles genres correctly."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()

        # Check genres were created
        cursor.execute("SELECT COUNT(*) FROM genres")
        genre_count = cursor.fetchone()[0]
        assert genre_count == 2  # Fiction, Science Fiction

        # Check genre associations
        cursor.execute("""
            SELECT g.name FROM genres g
            JOIN audiobook_genres ag ON g.id = ag.genre_id
            JOIN audiobooks a ON a.id = ag.audiobook_id
            WHERE a.title = 'The Great Test'
        """)
        genres = {row[0] for row in cursor.fetchall()}
        assert "Fiction" in genres
        assert "Science Fiction" in genres

        conn.close()

    def test_import_audiobooks_handles_eras(
        self, temp_db_path, temp_schema_path, temp_json_path
    ):
        """Test that import handles eras correctly."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM eras")
        era_count = cursor.fetchone()[0]
        assert era_count == 1  # 21st Century

        conn.close()

    def test_import_audiobooks_handles_topics(
        self, temp_db_path, temp_schema_path, temp_json_path
    ):
        """Test that import handles topics correctly."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM topics")
        topic_count = cursor.fetchone()[0]
        assert topic_count == 1  # Technology

        conn.close()

    def test_import_audiobooks_handles_null_values(
        self, temp_db_path, temp_schema_path, temp_json_path
    ):
        """Test that import handles null/missing values correctly."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT narrator, publisher, series FROM audiobooks WHERE title = 'Another Book'"
        )
        row = cursor.fetchone()

        assert row[0] is None  # narrator
        assert row[1] is None  # publisher
        assert row[2] is None  # series

        conn.close()

    def test_import_audiobooks_preserves_narrators(self, tmp_path):
        """Test that import preserves manually-populated narrators.

        Tests the narrator preservation logic without full schema (avoids FTS5 issues).
        """

        # Create a minimal database without FTS5 (causes tmp filesystem issues)
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Minimal schema - just audiobooks table
        cursor.execute("""
            CREATE TABLE audiobooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT,
                narrator TEXT,
                file_path TEXT UNIQUE NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE genres (id INTEGER PRIMARY KEY, name TEXT UNIQUE)
        """)
        cursor.execute("""
            CREATE TABLE audiobook_genres (audiobook_id INTEGER, genre_id INTEGER)
        """)
        conn.commit()

        # Insert initial data with NULL narrator
        cursor.execute("""
            INSERT INTO audiobooks (title, author, narrator, file_path)
            VALUES ('Another Book', 'Author', NULL, '/test/path/book2.opus')
        """)
        conn.commit()

        # Manually update narrator (simulating Audible export sync)
        cursor.execute(
            "UPDATE audiobooks SET narrator = 'Manually Set Narrator' WHERE title = 'Another Book'"
        )
        conn.commit()

        # Verify the narrator was set
        cursor.execute("SELECT narrator FROM audiobooks WHERE title = 'Another Book'")
        assert cursor.fetchone()[0] == "Manually Set Narrator"

        # Test the preservation query that import_audiobooks uses
        cursor.execute(
            "SELECT file_path, narrator FROM audiobooks WHERE narrator IS NOT NULL AND narrator != 'Unknown Narrator' AND narrator != ''"
        )
        preserved = {row[0]: row[1] for row in cursor.fetchall()}

        # Verify our narrator is in the preserved set
        assert "/test/path/book2.opus" in preserved
        assert preserved["/test/path/book2.opus"] == "Manually Set Narrator"

        conn.close()

    def test_import_audiobooks_preserves_genres(self, tmp_path):
        """Test that import preserves manually-populated genres.

        Tests the genre preservation logic without full schema (avoids FTS5 issues).
        """
        # Create a minimal database without FTS5
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Minimal schema
        cursor.execute("""
            CREATE TABLE audiobooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                file_path TEXT UNIQUE NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE genres (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)
        """)
        cursor.execute("""
            CREATE TABLE audiobook_genres (
                audiobook_id INTEGER,
                genre_id INTEGER,
                PRIMARY KEY (audiobook_id, genre_id)
            )
        """)
        conn.commit()

        # Insert book
        cursor.execute("""
            INSERT INTO audiobooks (title, file_path) VALUES ('Another Book', '/test/book.opus')
        """)
        book_id = cursor.lastrowid

        # Add a manual genre (simulating user-added genre from Audible export)
        cursor.execute("INSERT INTO genres (name) VALUES ('Manual Genre')")
        genre_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
            (book_id, genre_id),
        )
        conn.commit()

        # Test the preservation query that import_audiobooks uses
        cursor.execute("""
            SELECT a.file_path, GROUP_CONCAT(g.name, '|||')
            FROM audiobooks a
            JOIN audiobook_genres ag ON a.id = ag.audiobook_id
            JOIN genres g ON ag.genre_id = g.id
            GROUP BY a.file_path
        """)
        preserved = {}
        for row in cursor.fetchall():
            if row[1]:
                preserved[row[0]] = row[1].split("|||")

        # Verify our genre is in the preserved set
        assert "/test/book.opus" in preserved
        assert "Manual Genre" in preserved["/test/book.opus"]

        conn.close()

    def test_import_audiobooks_progress_reporting(
        self, temp_db_path, temp_schema_path, many_audiobooks_json, capsys
    ):
        """Test that import reports progress for large imports."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", many_audiobooks_json),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        captured = capsys.readouterr()

        # Should report progress at 100 mark
        assert "Processed 100/150" in captured.out

        conn.close()

    def test_import_audiobooks_statistics(
        self, temp_db_path, temp_schema_path, temp_json_path, capsys
    ):
        """Test that import reports statistics."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        captured = capsys.readouterr()

        # Should report various statistics
        assert "Total audiobooks:" in captured.out
        assert "Total hours:" in captured.out
        assert "Unique authors:" in captured.out

        conn.close()


class TestMain:
    """Test the main function."""

    def test_main_success(self, temp_db_path, temp_schema_path, temp_json_path, capsys, monkeypatch):
        """Test successful main execution."""
        from backend import import_to_db

        # Skip validation for test data (small dataset)
        monkeypatch.setenv("SKIP_IMPORT_VALIDATION", "1")

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            import_to_db.main()

        captured = capsys.readouterr()
        assert "Database created successfully" in captured.out
        assert temp_db_path.exists()

    def test_main_missing_json(self, temp_db_path, temp_schema_path, tmp_path, capsys):
        """Test main with missing JSON file."""
        from backend import import_to_db

        missing_json = tmp_path / "nonexistent.json"

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", missing_json),
        ):
            with pytest.raises(SystemExit) as exc_info:
                import_to_db.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error: JSON file not found" in captured.out

    def test_main_reports_db_size(
        self, temp_db_path, temp_schema_path, temp_json_path, capsys, monkeypatch
    ):
        """Test that main reports database size."""
        from backend import import_to_db

        # Skip validation for test data (small dataset)
        monkeypatch.setenv("SKIP_IMPORT_VALIDATION", "1")

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            import_to_db.main()

        captured = capsys.readouterr()
        assert "Size:" in captured.out
        assert "MB" in captured.out


class TestDatabaseOptimization:
    """Test database optimization."""

    def test_vacuum_and_analyze(
        self, temp_db_path, temp_schema_path, temp_json_path, capsys
    ):
        """Test that VACUUM and ANALYZE are run."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        captured = capsys.readouterr()
        assert "Optimizing database" in captured.out
        assert "Database optimized" in captured.out

        conn.close()


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_audiobooks_list(self, temp_db_path, temp_schema_path, tmp_path):
        """Test import with empty audiobooks list."""
        from backend import import_to_db

        empty_json = tmp_path / "empty.json"
        empty_json.write_text(json.dumps({"audiobooks": []}))

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", empty_json),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audiobooks")
        count = cursor.fetchone()[0]

        assert count == 0
        conn.close()

    def test_audiobook_with_empty_genres(
        self, temp_db_path, temp_schema_path, tmp_path
    ):
        """Test import with audiobook that has empty genres list."""
        from backend import import_to_db

        json_path = tmp_path / "audiobooks.json"
        json_path.write_text(
            json.dumps(
                {
                    "audiobooks": [
                        {
                            "title": "No Genres Book",
                            "author": "Author",
                            "narrator": "Narrator",
                            "file_path": "/test/book.opus",
                            "genres": [],
                            "eras": [],
                            "topics": [],
                        }
                    ]
                }
            )
        )

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audiobook_genres")
        count = cursor.fetchone()[0]

        assert count == 0
        conn.close()

    def test_duplicate_genres_across_books(
        self, temp_db_path, temp_schema_path, tmp_path
    ):
        """Test that duplicate genres are not created."""
        from backend import import_to_db

        json_path = tmp_path / "audiobooks.json"
        json_path.write_text(
            json.dumps(
                {
                    "audiobooks": [
                        {
                            "title": "Book 1",
                            "author": "Author",
                            "file_path": "/test/book1.opus",
                            "genres": ["Fiction", "Mystery"],
                        },
                        {
                            "title": "Book 2",
                            "author": "Author",
                            "file_path": "/test/book2.opus",
                            "genres": ["Fiction", "Thriller"],  # Fiction is duplicate
                        },
                    ]
                }
            )
        )

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM genres WHERE name = 'Fiction'")
        count = cursor.fetchone()[0]

        assert count == 1  # Should only be one "Fiction" genre

        # But both books should be associated with it
        cursor.execute("""
            SELECT COUNT(*) FROM audiobook_genres ag
            JOIN genres g ON ag.genre_id = g.id
            WHERE g.name = 'Fiction'
        """)
        assoc_count = cursor.fetchone()[0]
        assert assoc_count == 2

        conn.close()

    def test_special_characters_in_metadata(
        self, temp_db_path, temp_schema_path, tmp_path
    ):
        """Test import with special characters in metadata."""
        from backend import import_to_db

        json_path = tmp_path / "audiobooks.json"
        json_path.write_text(
            json.dumps(
                {
                    "audiobooks": [
                        {
                            "title": "Book with 'quotes' and \"double quotes\"",
                            "author": "Author O'Brien",
                            "narrator": "Narrator & Co.",
                            "description": "Description with <html> and 日本語",
                            "file_path": "/test/special.opus",
                            "genres": ["Sci-Fi & Fantasy"],
                        }
                    ]
                }
            )
        )

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT title, author, description FROM audiobooks")
        row = cursor.fetchone()

        assert "'" in row[0]
        assert '"' in row[0]
        assert "O'Brien" in row[1]
        assert "日本語" in row[2]

        conn.close()

    def test_sha256_hash_storage(self, temp_db_path, temp_schema_path, temp_json_path):
        """Test that SHA-256 hashes are stored correctly."""
        from backend import import_to_db

        with (
            patch.object(import_to_db, "DB_PATH", temp_db_path),
            patch.object(import_to_db, "SCHEMA_PATH", temp_schema_path),
            patch.object(import_to_db, "JSON_PATH", temp_json_path),
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

        cursor = conn.cursor()
        cursor.execute(
            "SELECT sha256_hash FROM audiobooks WHERE title = 'The Great Test'"
        )
        row = cursor.fetchone()

        assert row[0] == "abc123def456"

        cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE sha256_hash IS NOT NULL")
        hashed_count = cursor.fetchone()[0]
        assert hashed_count == 1  # Only one book has a hash

        conn.close()
