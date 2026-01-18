"""
Tests for database utility operations module.

This module provides Flask routes for database maintenance:
- Rescan library (subprocess)
- Reimport database (subprocess)
- Generate hashes (subprocess)
- Vacuum database (direct SQL)
- Export database/JSON/CSV
"""

import subprocess
from unittest.mock import MagicMock, patch


class TestRescanLibrary:
    """Test the rescan_library endpoint."""

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_rescan_success(self, mock_run, flask_app, session_temp_dir):
        """Test successful library rescan."""
        # Create the scanner script path (project_root = project_dir / "library")
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        scanner_path.parent.mkdir(parents=True, exist_ok=True)
        scanner_path.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Scanning...\nTotal audiobook files: 150\nComplete!",
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["files_found"] == 150

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_rescan_parses_file_count(self, mock_run, flask_app, session_temp_dir):
        """Test that rescan parses file count from output."""
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        scanner_path.parent.mkdir(parents=True, exist_ok=True)
        scanner_path.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Found files...\nTotal audiobook files: 42\nDone",
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        data = response.get_json()
        assert data["files_found"] == 42

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_rescan_handles_timeout(self, mock_run, flask_app, session_temp_dir):
        """Test rescan handles timeout gracefully."""
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        scanner_path.parent.mkdir(parents=True, exist_ok=True)
        scanner_path.touch()

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="python3", timeout=1800)

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert "timed out" in data["error"]

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_rescan_handles_exception(self, mock_run, flask_app, session_temp_dir):
        """Test rescan handles generic exceptions."""
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        scanner_path.parent.mkdir(parents=True, exist_ok=True)
        scanner_path.touch()

        mock_run.side_effect = RuntimeError("Unexpected error")

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False

    def test_rescan_missing_script(self, flask_app, session_temp_dir):
        """Test rescan returns error when script not found."""
        # Remove the scanner script if it exists from previous tests
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        if scanner_path.exists():
            scanner_path.unlink()

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        assert response.status_code == 500
        data = response.get_json()
        assert "not found" in data["error"]


class TestReimportDatabase:
    """Test the reimport_database endpoint."""

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_reimport_success(self, mock_run, flask_app, session_temp_dir):
        """Test successful database reimport."""
        import_path = session_temp_dir / "library" / "backend" / "import_to_db.py"
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Importing...\nImported 25 audiobooks\nComplete!",
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["imported_count"] == 25

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_reimport_handles_timeout(self, mock_run, flask_app, session_temp_dir):
        """Test reimport handles timeout gracefully."""
        import_path = session_temp_dir / "library" / "backend" / "import_to_db.py"
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.touch()

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="python3", timeout=300)

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport")

        assert response.status_code == 500
        data = response.get_json()
        assert "timed out" in data["error"]

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_reimport_handles_exception(self, mock_run, flask_app, session_temp_dir):
        """Test reimport handles generic exceptions."""
        import_path = session_temp_dir / "library" / "backend" / "import_to_db.py"
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.touch()

        mock_run.side_effect = Exception("Database locked")

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport")

        assert response.status_code == 500

    def test_reimport_missing_script(self, flask_app, session_temp_dir):
        """Test reimport returns error when script not found."""
        # Remove the import script if it exists from previous tests
        import_path = session_temp_dir / "library" / "backend" / "import_to_db.py"
        if import_path.exists():
            import_path.unlink()

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport")

        assert response.status_code == 500
        data = response.get_json()
        assert "not found" in data["error"]


class TestGenerateHashes:
    """Test the generate_hashes endpoint."""

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_generate_hashes_success(self, mock_run, flask_app, session_temp_dir):
        """Test successful hash generation."""
        hash_script = session_temp_dir / "library" / "scripts" / "generate_hashes.py"
        hash_script.parent.mkdir(parents=True, exist_ok=True)
        hash_script.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Processing...\nGenerated 100 hashes\nComplete!",
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["hashes_generated"] == 100

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_generate_hashes_handles_timeout(
        self, mock_run, flask_app, session_temp_dir
    ):
        """Test hash generation handles timeout."""
        hash_script = session_temp_dir / "library" / "scripts" / "generate_hashes.py"
        hash_script.parent.mkdir(parents=True, exist_ok=True)
        hash_script.touch()

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="python3", timeout=1800)

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes")

        assert response.status_code == 500
        data = response.get_json()
        assert "timed out" in data["error"]

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_generate_hashes_handles_exception(
        self, mock_run, flask_app, session_temp_dir
    ):
        """Test hash generation handles generic exceptions."""
        hash_script = session_temp_dir / "library" / "scripts" / "generate_hashes.py"
        hash_script.parent.mkdir(parents=True, exist_ok=True)
        hash_script.touch()

        mock_run.side_effect = RuntimeError("I/O error")

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes")

        assert response.status_code == 500

    def test_generate_hashes_missing_script(self, flask_app, session_temp_dir):
        """Test returns error when script not found."""
        # Remove the hash script if it exists from previous tests
        hash_script = session_temp_dir / "library" / "scripts" / "generate_hashes.py"
        if hash_script.exists():
            hash_script.unlink()

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes")

        assert response.status_code == 500
        data = response.get_json()
        assert "not found" in data["error"]


class TestVacuumDatabase:
    """Test the vacuum_database endpoint."""

    def test_vacuum_success(self, flask_app):
        """Test successful database vacuum."""
        with flask_app.test_client() as client:
            response = client.post("/api/utilities/vacuum")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "size_before_mb" in data
        assert "size_after_mb" in data
        assert "space_reclaimed_mb" in data

    def test_vacuum_returns_sizes(self, flask_app):
        """Test vacuum returns file size information."""
        with flask_app.test_client() as client:
            response = client.post("/api/utilities/vacuum")

        data = response.get_json()
        assert isinstance(data["size_before_mb"], (int, float))
        assert isinstance(data["size_after_mb"], (int, float))
        assert data["space_reclaimed_mb"] >= 0

    @patch("backend.api_modular.utilities_db.get_db")
    def test_vacuum_handles_exception(self, mock_get_db, flask_app):
        """Test vacuum handles database exceptions."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database locked")
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/vacuum")

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert "vacuum failed" in data["error"]


class TestExportDatabase:
    """Test the export_database endpoint."""

    def test_export_db_success(self, flask_app, session_temp_dir):
        """Test successful database export."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-db")

        assert response.status_code == 200
        assert response.mimetype == "application/x-sqlite3"
        assert "attachment" in response.headers.get("Content-Disposition", "")

    def test_export_db_filename(self, flask_app):
        """Test export includes correct filename."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-db")

        disposition = response.headers.get("Content-Disposition", "")
        assert "audiobooks.db" in disposition


class TestExportJson:
    """Test the export_json endpoint."""

    def test_export_json_success(self, flask_app):
        """Test successful JSON export."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-json")

        assert response.status_code == 200
        assert response.mimetype == "application/json"

    def test_export_json_structure(self, flask_app):
        """Test JSON export has correct structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-json")

        data = response.get_json()
        assert "exported_at" in data
        assert "total_count" in data
        assert "audiobooks" in data
        assert isinstance(data["audiobooks"], list)

    def test_export_json_attachment_header(self, flask_app):
        """Test JSON export has attachment header."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-json")

        disposition = response.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert ".json" in disposition


class TestExportCsv:
    """Test the export_csv endpoint."""

    def test_export_csv_success(self, flask_app):
        """Test successful CSV export."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-csv")

        assert response.status_code == 200
        assert response.mimetype == "text/csv"

    def test_export_csv_with_data(self, flask_app, session_temp_dir):
        """Test CSV export includes audiobook data rows."""
        import sqlite3

        # Insert a test audiobook
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (
                title, author, narrator, publisher, series, series_sequence,
                duration_hours, duration_formatted, file_size_mb, published_year,
                asin, isbn, file_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Test Export Book",
                "Export Author",
                "Export Narrator",
                "Export Publisher",
                "Export Series",
                1,
                5.5,
                "5h 30m",
                250.0,
                "2024",
                "B12345",
                "978-123456",
                "/path/to/export_test.opus",
            ),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-csv")

        csv_content = response.data.decode("utf-8")
        assert "Test Export Book" in csv_content
        assert "Export Author" in csv_content

    def test_export_csv_has_header(self, flask_app):
        """Test CSV export includes header row."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-csv")

        csv_content = response.data.decode("utf-8")
        # Check for expected header columns
        assert "ID" in csv_content
        assert "Title" in csv_content
        assert "Author" in csv_content

    def test_export_csv_attachment_header(self, flask_app):
        """Test CSV export has attachment header with date."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/export-csv")

        disposition = response.headers.get("Content-Disposition", "")
        assert "attachment" in disposition
        assert ".csv" in disposition


class TestOutputTruncation:
    """Test output truncation for large outputs."""

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_rescan_truncates_large_output(self, mock_run, flask_app, session_temp_dir):
        """Test large output is truncated to last 2000 chars."""
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        scanner_path.parent.mkdir(parents=True, exist_ok=True)
        scanner_path.touch()

        # Create output larger than 2000 chars
        large_output = "x" * 5000 + "\nTotal audiobook files: 50"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=large_output,
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        data = response.get_json()
        # Output should be truncated to last 2000 chars
        assert len(data["output"]) <= 2000


class TestParsingEdgeCases:
    """Test parsing edge cases in subprocess output."""

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_rescan_handles_malformed_file_count(
        self, mock_run, flask_app, session_temp_dir
    ):
        """Test rescan handles malformed file count line."""
        scanner_path = session_temp_dir / "library" / "scanner" / "scan_audiobooks.py"
        scanner_path.parent.mkdir(parents=True, exist_ok=True)
        scanner_path.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Total audiobook files: not-a-number\nDone",
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan")

        data = response.get_json()
        # Should default to 0 when parsing fails
        assert data["files_found"] == 0

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_reimport_handles_malformed_count(
        self, mock_run, flask_app, session_temp_dir
    ):
        """Test reimport handles malformed import count."""
        import_path = session_temp_dir / "library" / "backend" / "import_to_db.py"
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Imported many audiobooks done",  # Malformed
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport")

        data = response.get_json()
        assert data["imported_count"] == 0

    @patch("backend.api_modular.utilities_db.subprocess.run")
    def test_generate_hashes_parses_various_formats(
        self, mock_run, flask_app, session_temp_dir
    ):
        """Test hash generation parses different output formats."""
        hash_script = session_temp_dir / "library" / "scripts" / "generate_hashes.py"
        hash_script.parent.mkdir(parents=True, exist_ok=True)
        hash_script.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Processing 50 hashes completed",
            stderr="",
        )

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes")

        data = response.get_json()
        assert data["hashes_generated"] == 50
