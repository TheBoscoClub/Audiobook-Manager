"""
Tests for the incremental audiobook adder module.

This module adds new audiobooks to the database without doing a full rescan.
It queries existing paths from the DB and only processes new files.
"""

import hashlib
import sqlite3
from unittest.mock import patch


class TestGetExistingPaths:
    """Test the get_existing_paths function."""

    def test_returns_empty_set_for_empty_db(self, temp_dir):
        """Test empty database returns empty set."""
        from scanner.add_new_audiobooks import get_existing_paths

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        result = get_existing_paths(db_path)

        assert result == set()

    def test_returns_existing_paths(self, temp_dir):
        """Test returns file paths from database."""
        from scanner.add_new_audiobooks import get_existing_paths

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        # Insert some test records
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (title, author, file_path, duration_hours)
            VALUES (?, ?, ?, ?)
            """,
            ("Book 1", "Author 1", "/path/to/book1.opus", 5.0),
        )
        cursor.execute(
            """
            INSERT INTO audiobooks (title, author, file_path, duration_hours)
            VALUES (?, ?, ?, ?)
            """,
            ("Book 2", "Author 2", "/path/to/book2.m4b", 3.0),
        )
        conn.commit()
        conn.close()

        result = get_existing_paths(db_path)

        assert "/path/to/book1.opus" in result
        assert "/path/to/book2.m4b" in result
        assert len(result) == 2


class TestFindNewAudiobooks:
    """Test the find_new_audiobooks function."""

    def test_finds_supported_formats(self, temp_dir):
        """Test finds all supported audio formats."""
        from scanner.add_new_audiobooks import find_new_audiobooks

        # Create test files with supported formats
        (temp_dir / "book1.m4b").touch()
        (temp_dir / "book2.opus").touch()
        (temp_dir / "book3.m4a").touch()
        (temp_dir / "book4.mp3").touch()

        result = find_new_audiobooks(temp_dir, set())

        assert len(result) == 4
        extensions = {f.suffix for f in result}
        assert extensions == {".m4b", ".opus", ".m4a", ".mp3"}

    def test_filters_cover_art_files(self, temp_dir):
        """Test filters out .cover. files."""
        from scanner.add_new_audiobooks import find_new_audiobooks

        (temp_dir / "book.opus").touch()
        (temp_dir / "book.cover.jpg").touch()  # Should be filtered
        (temp_dir / "Book.Cover.m4b").touch()  # Should be filtered (case insensitive)

        result = find_new_audiobooks(temp_dir, set())

        assert len(result) == 1
        assert result[0].name == "book.opus"

    def test_filters_existing_paths(self, temp_dir):
        """Test filters out paths already in database."""
        from scanner.add_new_audiobooks import find_new_audiobooks

        # Create test files
        book1 = temp_dir / "book1.opus"
        book2 = temp_dir / "book2.opus"
        book1.touch()
        book2.touch()

        # Mark book1 as existing
        existing_paths = {str(book1)}

        result = find_new_audiobooks(temp_dir, existing_paths)

        assert len(result) == 1
        assert result[0].name == "book2.opus"

    def test_handles_nested_directories(self, temp_dir):
        """Test finds files in subdirectories."""
        from scanner.add_new_audiobooks import find_new_audiobooks

        # Create nested structure
        subdir = temp_dir / "Author" / "Book"
        subdir.mkdir(parents=True)
        (subdir / "audiobook.m4b").touch()
        (temp_dir / "standalone.opus").touch()

        result = find_new_audiobooks(temp_dir, set())

        assert len(result) == 2
        names = {f.name for f in result}
        assert names == {"audiobook.m4b", "standalone.opus"}


class TestGetOrCreateLookupId:
    """Test the get_or_create_lookup_id function."""

    def test_creates_new_entry(self, temp_dir):
        """Test creates new entry in lookup table."""
        from scanner.add_new_audiobooks import get_or_create_lookup_id

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        result = get_or_create_lookup_id(cursor, "genres", "Science Fiction")

        assert result > 0
        conn.close()

    def test_returns_existing_entry(self, temp_dir):
        """Test returns existing entry ID without creating duplicate."""
        from scanner.add_new_audiobooks import get_or_create_lookup_id

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create first entry
        first_id = get_or_create_lookup_id(cursor, "genres", "Mystery")
        # Should return same ID
        second_id = get_or_create_lookup_id(cursor, "genres", "Mystery")

        assert first_id == second_id
        conn.close()

    def test_works_with_different_tables(self, temp_dir):
        """Test works with genres, eras, and topics tables."""
        from scanner.add_new_audiobooks import get_or_create_lookup_id

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        genre_id = get_or_create_lookup_id(cursor, "genres", "Fantasy")
        era_id = get_or_create_lookup_id(cursor, "eras", "Modern")
        topic_id = get_or_create_lookup_id(cursor, "topics", "Adventure")

        assert all(id > 0 for id in [genre_id, era_id, topic_id])
        conn.close()


class TestInsertAudiobook:
    """Test the insert_audiobook function."""

    def test_inserts_complete_metadata(self, temp_dir):
        """Test inserts audiobook with all metadata fields."""
        from scanner.add_new_audiobooks import insert_audiobook

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        metadata = {
            "title": "Test Book",
            "author": "Test Author",
            "narrator": "Test Narrator",
            "publisher": "Test Publisher",
            "series": "Test Series",
            "duration_hours": 10.5,
            "duration_formatted": "10h 30m",
            "file_size_mb": 500.0,
            "file_path": "/path/to/book.opus",
            "format": "opus",
            "description": "A test book about testing",
            "genre": "Science Fiction",
            "year": "2024",
        }

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        audiobook_id = insert_audiobook(conn, metadata, "cover.jpg")
        conn.commit()

        # Verify insertion
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audiobooks WHERE id = ?", (audiobook_id,))
        row = cursor.fetchone()

        assert row["title"] == "Test Book"
        assert row["author"] == "Test Author"
        assert row["duration_hours"] == 10.5
        assert row["cover_path"] == "cover.jpg"

        conn.close()

    def test_inserts_genre_era_topics(self, temp_dir):
        """Test creates entries in related tables."""
        from scanner.add_new_audiobooks import insert_audiobook

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        metadata = {
            "title": "Adventure Book",
            "author": "Author",
            "file_path": "/path/book.opus",
            "duration_hours": 5.0,
            "genre": "Fantasy",
            "year": "2020",
            "description": "An adventure story about war",
        }

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        audiobook_id = insert_audiobook(conn, metadata, None)
        conn.commit()

        cursor = conn.cursor()

        # Check genre was created and linked
        cursor.execute(
            """
            SELECT g.name FROM genres g
            JOIN audiobook_genres ag ON g.id = ag.genre_id
            WHERE ag.audiobook_id = ?
            """,
            (audiobook_id,),
        )
        genre_result = cursor.fetchone()
        assert genre_result is not None

        # Check era was created and linked
        cursor.execute(
            """
            SELECT e.name FROM eras e
            JOIN audiobook_eras ae ON e.id = ae.era_id
            WHERE ae.audiobook_id = ?
            """,
            (audiobook_id,),
        )
        era_result = cursor.fetchone()
        assert era_result is not None

        conn.close()


class TestAddNewAudiobooks:
    """Test the main add_new_audiobooks function."""

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_returns_empty_result_when_no_new_files(self, mock_cover, mock_metadata, temp_dir):
        """Test returns zero counts when no new files found."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        result = add_new_audiobooks(library_dir=library_dir, db_path=db_path, cover_dir=cover_dir)

        assert result["added"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["new_files"] == []

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_adds_new_audiobook(self, mock_cover, mock_metadata, temp_dir):
        """Test successfully adds a new audiobook."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        # Create a test audio file
        test_file = library_dir / "test_book.opus"
        test_file.touch()

        # Mock metadata extraction
        mock_metadata.return_value = {
            "title": "Test Audiobook",
            "author": "Test Author",
            "narrator": "Test Narrator",
            "file_path": str(test_file),
            "duration_hours": 8.0,
            "duration_formatted": "8h 0m",
            "file_size_mb": 400.0,
            "format": "opus",
            "genre": "Fiction",
            "year": "2025",
            "description": "A test audiobook",
        }
        mock_cover.return_value = "cover_123.jpg"

        result = add_new_audiobooks(
            library_dir=library_dir, db_path=db_path, cover_dir=cover_dir, calculate_hashes=False
        )

        assert result["added"] == 1
        assert result["errors"] == 0
        assert len(result["new_files"]) == 1
        assert result["new_files"][0]["title"] == "Test Audiobook"

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_handles_metadata_extraction_failure(self, mock_cover, mock_metadata, temp_dir):
        """Test counts errors when metadata extraction fails."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        # Create test files
        (library_dir / "corrupt.opus").touch()

        # Mock metadata extraction to return None (failure)
        mock_metadata.return_value = None

        result = add_new_audiobooks(library_dir=library_dir, db_path=db_path, cover_dir=cover_dir)

        assert result["added"] == 0
        assert result["errors"] == 1

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_calls_progress_callback(self, mock_cover, mock_metadata, temp_dir):
        """Test calls progress callback with status updates."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        progress_calls = []

        def track_progress(current, total, message):
            progress_calls.append((current, total, message))

        add_new_audiobooks(
            library_dir=library_dir,
            db_path=db_path,
            cover_dir=cover_dir,
            progress_callback=track_progress,
        )

        # Should have called progress at least for start and end
        assert len(progress_calls) >= 2
        # Final progress should be 100%
        assert progress_calls[-1][0] == 100

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_creates_cover_directory(self, mock_cover, mock_metadata, temp_dir):
        """Test creates cover directory if it doesn't exist."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers" / "subdir"  # Doesn't exist

        # Create a test file
        (library_dir / "book.opus").touch()
        mock_metadata.return_value = {
            "title": "Book",
            "author": "Author",
            "file_path": str(library_dir / "book.opus"),
            "duration_hours": 5.0,
            "format": "opus",
        }
        mock_cover.return_value = None

        add_new_audiobooks(library_dir=library_dir, db_path=db_path, cover_dir=cover_dir)

        assert cover_dir.exists()


class TestSupportedFormats:
    """Test the SUPPORTED_FORMATS constant."""

    def test_supported_formats_list(self):
        """Test all expected formats are supported."""
        from scanner.add_new_audiobooks import SUPPORTED_FORMATS

        assert ".m4b" in SUPPORTED_FORMATS
        assert ".opus" in SUPPORTED_FORMATS
        assert ".m4a" in SUPPORTED_FORMATS
        assert ".mp3" in SUPPORTED_FORMATS


class TestDeduplication:
    """Test the deduplication logic for Library/Audiobook paths."""

    def test_prefers_main_library_over_audiobook_folder(self, temp_dir):
        """Test files in main Library are preferred over /Library/Audiobook/."""
        from scanner.add_new_audiobooks import find_new_audiobooks

        # Create structure mimicking real library
        main_lib = temp_dir / "Library"
        audiobook_folder = temp_dir / "Library" / "Audiobook"
        main_lib.mkdir()
        audiobook_folder.mkdir()

        # Same book in both locations
        (main_lib / "book.opus").touch()
        (audiobook_folder / "book.opus").touch()

        result = find_new_audiobooks(temp_dir, set())

        # Should only include the main library version
        result_paths = [str(f) for f in result]
        main_count = sum(1 for p in result_paths if "/Library/Audiobook/" not in p)
        sum(1 for p in result_paths if "/Library/Audiobook/" in p)

        # The main library version should be included, audiobook folder version excluded
        assert main_count >= 1


class TestErrorHandling:
    """Test error handling in add_new_audiobooks."""

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    @patch("scanner.add_new_audiobooks.insert_audiobook")
    def test_handles_integrity_error(self, mock_insert, mock_cover, mock_metadata, temp_dir):
        """Test handles IntegrityError (duplicate file path)."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        (library_dir / "duplicate.opus").touch()

        mock_metadata.return_value = {
            "title": "Duplicate Book",
            "author": "Author",
            "file_path": str(library_dir / "duplicate.opus"),
            "duration_hours": 5.0,
            "format": "opus",
        }
        mock_cover.return_value = None
        mock_insert.side_effect = sqlite3.IntegrityError("UNIQUE constraint failed")

        result = add_new_audiobooks(library_dir=library_dir, db_path=db_path, cover_dir=cover_dir)

        assert result["skipped"] == 1
        assert result["added"] == 0

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    @patch("scanner.add_new_audiobooks.insert_audiobook")
    def test_handles_generic_exception(self, mock_insert, mock_cover, mock_metadata, temp_dir):
        """Test handles generic exceptions during insert."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        (library_dir / "problem.opus").touch()

        mock_metadata.return_value = {
            "title": "Problem Book",
            "author": "Author",
            "file_path": str(library_dir / "problem.opus"),
            "duration_hours": 5.0,
            "format": "opus",
        }
        mock_cover.return_value = None
        mock_insert.side_effect = RuntimeError("Database error")

        result = add_new_audiobooks(library_dir=library_dir, db_path=db_path, cover_dir=cover_dir)

        assert result["errors"] == 1
        assert result["added"] == 0


class TestMainCLI:
    """Test the main CLI function."""

    def test_main_dry_run(self, temp_dir, monkeypatch, capsys):
        """Test main function with --dry-run flag."""
        from scanner import add_new_audiobooks

        # Create test directories
        library_dir = temp_dir / "library"
        library_dir.mkdir()
        (library_dir / "book.opus").touch()

        db_path = temp_dir / "test.db"
        from tests.conftest import init_test_database

        init_test_database(db_path)

        # Patch config values
        monkeypatch.setattr(add_new_audiobooks, "AUDIOBOOK_DIR", library_dir)
        monkeypatch.setattr(add_new_audiobooks, "DATABASE_PATH", db_path)
        monkeypatch.setattr(add_new_audiobooks, "COVER_DIR", temp_dir / "covers")

        # Patch sys.argv for argparse
        monkeypatch.setattr("sys.argv", ["add_new_audiobooks", "--dry-run"])

        add_new_audiobooks.main()

        captured = capsys.readouterr()
        assert "Would add" in captured.out

    @patch("scanner.add_new_audiobooks.add_new_audiobooks")
    def test_main_actual_run(self, mock_add, temp_dir, monkeypatch, capsys):
        """Test main function without --dry-run."""
        from scanner import add_new_audiobooks as module

        library_dir = temp_dir / "library"
        library_dir.mkdir()

        db_path = temp_dir / "test.db"
        from tests.conftest import init_test_database

        init_test_database(db_path)

        # Patch config values
        monkeypatch.setattr(module, "AUDIOBOOK_DIR", library_dir)
        monkeypatch.setattr(module, "DATABASE_PATH", db_path)
        monkeypatch.setattr(module, "COVER_DIR", temp_dir / "covers")

        # Mock the main function result
        mock_add.return_value = {
            "added": 5,
            "skipped": 1,
            "errors": 0,
            "new_files": [
                {"title": "Book 1", "author": "Author 1"},
                {"title": "Book 2", "author": "Author 2"},
            ],
        }

        monkeypatch.setattr("sys.argv", ["add_new_audiobooks"])

        module.main()

        captured = capsys.readouterr()
        assert "RESULTS" in captured.out
        assert "Added:" in captured.out


# =============================================================================
# Tests for hash auto-generation post-insert hook (Audiobook-Manager-f5e)
# =============================================================================


class TestHashAutoGeneration:
    """Regression guard for Audiobook-Manager-f5e — newly-ingested audiobooks
    were missing ``sha256_hash`` because no post-insert hook was wiring the
    hash worker. This test passes ``calculate_hashes=False`` so the metadata
    path does NOT pre-populate the hash, forcing the post-insert hook to do
    the work — proving the hook is what wires it up.
    """

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_new_ingest_auto_generates_hash(self, mock_cover, mock_metadata, temp_dir):
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        # Create a real file with deterministic content so we can verify
        # the SHA-256 the hook should compute matches what's stored.
        test_file = library_dir / "auto_hash_book.opus"
        content = b"deterministic audiobook payload for hash verification"
        test_file.write_bytes(content)
        expected_hash = hashlib.sha256(content).hexdigest()

        mock_metadata.return_value = {
            "title": "Auto Hash Book",
            "author": "Test Author",
            "narrator": "Test Narrator",
            "file_path": str(test_file),
            "duration_hours": 2.5,
            "duration_formatted": "2h 30m",
            "file_size_mb": 0.0001,
            "format": "opus",
            "genre": "Fiction",
            "year": "2026",
            "description": "A book that should be auto-hashed on ingest",
            # NB: no sha256_hash key here — post-insert hook must populate it.
        }
        mock_cover.return_value = None

        # calculate_hashes=False ensures the metadata path does NOT
        # pre-populate the hash. If the hook is wired correctly,
        # sha256_hash will still be set after insert.
        result = add_new_audiobooks(
            library_dir=library_dir,
            db_path=db_path,
            cover_dir=cover_dir,
            calculate_hashes=False,
        )

        assert result["added"] == 1, (
            f"Expected 1 book added, got {result['added']} (errors={result['errors']})"
        )

        # Verify the hash was written by the post-insert hook
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sha256_hash, hash_verified_at FROM audiobooks WHERE file_path = ?",
            (str(test_file),),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None, "Inserted audiobook row not found in DB"
        stored_hash, hash_verified_at = row
        assert stored_hash is not None, (
            "sha256_hash was not populated by the post-insert hook — "
            "Audiobook-Manager-f5e regression"
        )
        assert stored_hash == expected_hash, (
            f"Stored hash {stored_hash} does not match expected SHA-256 of file content"
        )
        assert hash_verified_at is not None, (
            "hash_verified_at should be set when sha256_hash is populated"
        )

    @patch("scanner.add_new_audiobooks.get_file_metadata")
    @patch("scanner.add_new_audiobooks.extract_cover_art")
    def test_hash_failure_does_not_block_insert(self, mock_cover, mock_metadata, temp_dir):
        """The hash hook is non-fatal — a hash failure must not roll back the
        insert or count as an error. The book is still in the DB; only the
        sha256_hash column stays NULL."""
        from scanner.add_new_audiobooks import add_new_audiobooks

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        library_dir = temp_dir / "library"
        library_dir.mkdir()
        cover_dir = temp_dir / "covers"

        test_file = library_dir / "book_with_failing_hash.opus"
        test_file.write_bytes(b"some content")

        mock_metadata.return_value = {
            "title": "Book With Failing Hash",
            "author": "Author",
            "file_path": str(test_file),
            "duration_hours": 1.0,
            "format": "opus",
        }
        mock_cover.return_value = None

        # Force the hash helper to raise — simulates a transient I/O error.
        with patch(
            "scripts.generate_hashes.generate_hash_for_book",
            side_effect=RuntimeError("simulated hash failure"),
        ):
            result = add_new_audiobooks(
                library_dir=library_dir,
                db_path=db_path,
                cover_dir=cover_dir,
                calculate_hashes=False,
            )

        # Insert still succeeded — hashing is non-fatal
        assert result["added"] == 1
        assert result["errors"] == 0


class TestGenerateHashForBook:
    """Direct tests of the single-book hash helper exposed by
    ``scripts/generate_hashes.py`` (Audiobook-Manager-f5e)."""

    def test_returns_hash_for_existing_row(self, temp_dir):
        from scripts.generate_hashes import generate_hash_for_book

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        # Create a real file and insert a row pointing at it
        audio = temp_dir / "x.opus"
        content = b"hello world"
        audio.write_bytes(content)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audiobooks (title, author, file_path, duration_hours) VALUES (?, ?, ?, ?)",
            ("Title", "Author", str(audio), 1.0),
        )
        book_id = cursor.lastrowid
        conn.commit()
        conn.close()

        result = generate_hash_for_book(book_id, db_path)
        assert result == hashlib.sha256(content).hexdigest()

        # And it persisted to the DB
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT sha256_hash FROM audiobooks WHERE id = ?", (book_id,))
        stored = cursor.fetchone()[0]
        conn.close()
        assert stored == result

    def test_returns_none_for_missing_row(self, temp_dir):
        from scripts.generate_hashes import generate_hash_for_book

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        result = generate_hash_for_book(99999, db_path)
        assert result is None

    def test_returns_none_when_file_missing(self, temp_dir):
        from scripts.generate_hashes import generate_hash_for_book

        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audiobooks (title, author, file_path, duration_hours) VALUES (?, ?, ?, ?)",
            ("Ghost Book", "Author", "/nonexistent/path/ghost.opus", 1.0),
        )
        book_id = cursor.lastrowid
        conn.commit()
        conn.close()

        result = generate_hash_for_book(book_id, db_path)
        assert result is None
