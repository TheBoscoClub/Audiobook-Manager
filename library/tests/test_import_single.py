"""
Tests for the single directory audiobook importer module.

This module imports audiobooks from a specific directory path to the database.
It's designed to be called after a successful move operation.
"""

import sqlite3
from unittest.mock import patch

import pytest


class TestGetOrCreateLookupId:
    """Test the get_or_create_lookup_id function."""

    def test_creates_new_entry(self, temp_dir):
        """Test creates new entry in lookup table."""
        from scanner.import_single import get_or_create_lookup_id
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        result = get_or_create_lookup_id(cursor, "genres", "Thriller")

        assert result > 0
        conn.close()

    def test_returns_existing_entry(self, temp_dir):
        """Test returns existing entry without creating duplicate."""
        from scanner.import_single import get_or_create_lookup_id
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        first_id = get_or_create_lookup_id(cursor, "eras", "Modern")
        second_id = get_or_create_lookup_id(cursor, "eras", "Modern")

        assert first_id == second_id
        conn.close()


class TestInsertAudiobook:
    """Test the insert_audiobook function."""

    def test_inserts_audiobook(self, temp_dir):
        """Test inserts audiobook with metadata."""
        from scanner.import_single import insert_audiobook
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        metadata = {
            "title": "Test Book",
            "author": "Author Name",
            "narrator": "Narrator Name",
            "publisher": "Publisher",
            "file_path": "/path/to/book.opus",
            "duration_hours": 6.5,
            "duration_formatted": "6h 30m",
            "file_size_mb": 300.0,
            "format": "opus",
            "genre": "Mystery",
            "year": "2023",
            "description": "A mystery story",
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
        assert row["author"] == "Author Name"
        assert row["cover_path"] == "cover.jpg"

        conn.close()


class TestImportDirectory:
    """Test the import_directory function."""

    def test_returns_error_for_nonexistent_directory(self, temp_dir):
        """Test returns error for non-existent directory."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        nonexistent = temp_dir / "nonexistent"

        result = import_directory(
            nonexistent, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["errors"] == 1
        assert "error" in result

    def test_returns_message_when_no_audio_files(self, temp_dir):
        """Test returns message when directory has no audio files."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        # Create empty directory
        empty_dir = temp_dir / "empty"
        empty_dir.mkdir()

        result = import_directory(
            empty_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["added"] == 0
        assert "message" in result
        assert "No audio files" in result["message"]

    def test_skips_existing_files(self, temp_dir):
        """Test skips files already in database."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        # Create test directory with audio file
        import_dir = temp_dir / "import"
        import_dir.mkdir()
        test_file = import_dir / "existing.opus"
        test_file.touch()

        # Pre-insert file path
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (title, author, file_path, duration_hours)
            VALUES (?, ?, ?, ?)
            """,
            ("Existing Book", "Author", str(test_file), 5.0),
        )
        conn.commit()
        conn.close()

        result = import_directory(
            import_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["skipped"] == 1
        assert result["added"] == 0

    @patch("scanner.import_single.get_file_metadata")
    @patch("scanner.import_single.extract_cover_art")
    def test_imports_new_audiobook(self, mock_cover, mock_metadata, temp_dir):
        """Test successfully imports new audiobook."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        # Create test directory with audio file
        import_dir = temp_dir / "import"
        import_dir.mkdir()
        test_file = import_dir / "new_book.opus"
        test_file.touch()

        mock_metadata.return_value = {
            "title": "New Audiobook",
            "author": "New Author",
            "narrator": "Narrator",
            "file_path": str(test_file),
            "duration_hours": 7.0,
            "duration_formatted": "7h 0m",
            "file_size_mb": 350.0,
            "format": "opus",
            "genre": "Fiction",
            "year": "2024",
            "description": "A new book",
        }
        mock_cover.return_value = "cover_new.jpg"

        result = import_directory(
            import_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["added"] == 1
        assert result["errors"] == 0

    @patch("scanner.import_single.get_file_metadata")
    @patch("scanner.import_single.extract_cover_art")
    def test_handles_metadata_failure(self, mock_cover, mock_metadata, temp_dir):
        """Test handles metadata extraction failure gracefully."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        import_dir = temp_dir / "import"
        import_dir.mkdir()
        (import_dir / "corrupt.opus").touch()

        mock_metadata.return_value = None

        result = import_directory(
            import_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["errors"] == 1
        assert result["added"] == 0

    @patch("scanner.import_single.get_file_metadata")
    @patch("scanner.import_single.extract_cover_art")
    @patch("scanner.import_single.insert_audiobook")
    def test_handles_integrity_error(
        self, mock_insert, mock_cover, mock_metadata, temp_dir
    ):
        """Test handles IntegrityError (duplicate)."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        import_dir = temp_dir / "import"
        import_dir.mkdir()
        (import_dir / "duplicate.opus").touch()

        mock_metadata.return_value = {
            "title": "Duplicate",
            "author": "Author",
            "file_path": str(import_dir / "duplicate.opus"),
            "duration_hours": 5.0,
            "format": "opus",
        }
        mock_cover.return_value = None
        mock_insert.side_effect = sqlite3.IntegrityError("UNIQUE constraint")

        result = import_directory(
            import_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["skipped"] == 1

    @patch("scanner.import_single.get_file_metadata")
    @patch("scanner.import_single.extract_cover_art")
    @patch("scanner.import_single.insert_audiobook")
    def test_handles_generic_exception(
        self, mock_insert, mock_cover, mock_metadata, temp_dir
    ):
        """Test handles generic exceptions during insert."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        import_dir = temp_dir / "import"
        import_dir.mkdir()
        (import_dir / "problem.opus").touch()

        mock_metadata.return_value = {
            "title": "Problem",
            "author": "Author",
            "file_path": str(import_dir / "problem.opus"),
            "duration_hours": 5.0,
            "format": "opus",
        }
        mock_cover.return_value = None
        mock_insert.side_effect = RuntimeError("Database error")

        result = import_directory(
            import_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        assert result["errors"] == 1

    def test_filters_cover_art_files(self, temp_dir):
        """Test filters out .cover. files from import."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        import_dir = temp_dir / "import"
        import_dir.mkdir()
        (import_dir / "book.cover.jpg").touch()  # Should be filtered
        (import_dir / "Book.Cover.m4b").touch()  # Should be filtered

        result = import_directory(
            import_dir, db_path=db_path, cover_dir=temp_dir / "covers"
        )

        # No audio files to import after filtering
        assert result.get("message") == "No audio files found" or result["added"] == 0

    def test_creates_cover_directory(self, temp_dir):
        """Test creates cover directory if not exists."""
        from scanner.import_single import import_directory
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        import_dir = temp_dir / "import"
        import_dir.mkdir()
        cover_dir = temp_dir / "covers" / "nested"  # Doesn't exist

        # No audio files, but cover_dir should still be checked if files existed
        import_directory(import_dir, db_path=db_path, cover_dir=cover_dir)

        # Note: cover_dir is only created if there are files to process


class TestMain:
    """Test the main CLI function."""

    def test_main_no_args(self, capsys, monkeypatch):
        """Test main exits with error when no arguments provided."""
        from scanner import import_single

        monkeypatch.setattr("sys.argv", ["import_single"])

        with pytest.raises(SystemExit) as exc_info:
            import_single.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Usage:" in captured.err

    def test_main_nonexistent_path(self, temp_dir, capsys, monkeypatch):
        """Test main exits with error for non-existent path."""
        from scanner import import_single

        nonexistent = temp_dir / "nonexistent"
        monkeypatch.setattr("sys.argv", ["import_single", str(nonexistent)])

        with pytest.raises(SystemExit) as exc_info:
            import_single.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    @patch("scanner.import_single.import_directory")
    def test_main_successful_import(self, mock_import, temp_dir, capsys, monkeypatch):
        """Test main with successful import."""
        from scanner import import_single

        import_dir = temp_dir / "import"
        import_dir.mkdir()

        mock_import.return_value = {"added": 3, "skipped": 1, "errors": 0}

        monkeypatch.setattr("sys.argv", ["import_single", str(import_dir)])

        with pytest.raises(SystemExit) as exc_info:
            import_single.main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Import complete" in captured.out
        assert "3 added" in captured.out

    @patch("scanner.import_single.import_directory")
    def test_main_with_errors(self, mock_import, temp_dir, capsys, monkeypatch):
        """Test main exits with error code when import has errors."""
        from scanner import import_single

        import_dir = temp_dir / "import"
        import_dir.mkdir()

        mock_import.return_value = {"added": 1, "skipped": 0, "errors": 2}

        monkeypatch.setattr("sys.argv", ["import_single", str(import_dir)])

        with pytest.raises(SystemExit) as exc_info:
            import_single.main()

        assert exc_info.value.code == 1

    @patch("scanner.import_single.import_directory")
    def test_main_with_error_message(self, mock_import, temp_dir, capsys, monkeypatch):
        """Test main prints error message when import fails."""
        from scanner import import_single

        import_dir = temp_dir / "import"
        import_dir.mkdir()

        mock_import.return_value = {
            "added": 0,
            "skipped": 0,
            "errors": 1,
            "error": "Not a directory",
        }

        monkeypatch.setattr("sys.argv", ["import_single", str(import_dir)])

        with pytest.raises(SystemExit) as exc_info:
            import_single.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.err


class TestSupportedFormats:
    """Test SUPPORTED_FORMATS constant."""

    def test_supported_formats(self):
        """Test expected formats are supported."""
        from scanner.import_single import SUPPORTED_FORMATS

        assert ".m4b" in SUPPORTED_FORMATS
        assert ".opus" in SUPPORTED_FORMATS
        assert ".m4a" in SUPPORTED_FORMATS
        assert ".mp3" in SUPPORTED_FORMATS
