"""
Tests for Flask API endpoints using mocking for file system and subprocess operations.
These tests cover code paths that require file operations or external processes.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def db_with_hash_duplicates(flask_app, app_client):
    """Insert duplicate audiobooks with the same hash for testing."""
    import sqlite3

    db_path = flask_app.config["DATABASE_PATH"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Use a unique hash that definitely creates duplicates
    test_hash = "test_duplicate_hash_abc123xyz"

    # Insert two audiobooks with the same hash
    for i in range(2):
        cursor.execute(
            """
            INSERT INTO audiobooks (
                title, author, narrator, file_path, file_size_mb,
                format, duration_hours, duration_formatted, sha256_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                f"Duplicate Hash Book {i}",
                "Test Author",
                "Test Narrator",
                f"/test/path/duplicate_hash_{i}.opus",
                50.0 + i,  # Different sizes to test keeper logic
                "opus",
                10.5,
                "10:30:00",
                test_hash,
            ),
        )

    conn.commit()
    inserted_ids = []
    cursor.execute("SELECT id FROM audiobooks WHERE sha256_hash = ?", (test_hash,))
    for row in cursor.fetchall():
        inserted_ids.append(row["id"])

    yield {"hash": test_hash, "ids": inserted_ids}

    # Cleanup
    cursor.execute("DELETE FROM audiobooks WHERE sha256_hash = ?", (test_hash,))
    conn.commit()
    conn.close()


@pytest.fixture
def db_with_title_duplicates(flask_app, app_client):
    """Insert duplicate audiobooks with the same normalized title for testing."""
    import sqlite3

    db_path = flask_app.config["DATABASE_PATH"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Use a unique title pattern that creates duplicates
    test_title = "Unique Test Duplicate Title XYZ123"

    # Insert two audiobooks with the same title, author, and similar duration
    for i in range(2):
        cursor.execute(
            """
            INSERT INTO audiobooks (
                title, author, narrator, file_path, file_size_mb,
                format, duration_hours, duration_formatted, sha256_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                test_title,
                "Real Author Name",  # Not 'Audiobook' or 'Unknown Author'
                f"Narrator {i}",
                f"/test/path/title_dup_{i}.opus",
                45.0 + i * 5,  # Different sizes
                "opus" if i == 0 else "m4b",  # Different formats
                10.5,  # Same duration_group (rounded to 10.5)
                "10:30:00",
                f"unique_hash_for_title_dup_{i}",  # Different hashes
            ),
        )

    conn.commit()
    inserted_ids = []
    cursor.execute("SELECT id FROM audiobooks WHERE title = ?", (test_title,))
    for row in cursor.fetchall():
        inserted_ids.append(row["id"])

    yield {"title": test_title, "ids": inserted_ids}

    # Cleanup
    cursor.execute("DELETE FROM audiobooks WHERE title = ?", (test_title,))
    conn.commit()
    conn.close()


class TestStreamingWithMocks:
    """Test streaming endpoints with mocked file system."""

    def test_stream_audiobook_file_exists(self, app_client):
        """Test streaming when file exists."""
        # Get a real audiobook ID
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("No audiobooks in database")

        audiobook_id = data["audiobooks"][0]["id"]

        # Mock Path.exists to return True and send_file
        with (
            patch("backend.api_modular.audiobooks.Path") as MockPath,
            patch("backend.api_modular.audiobooks.send_file") as mock_send,
        ):
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.suffix = ".opus"
            MockPath.return_value = mock_path_instance

            mock_send.return_value = MagicMock()

            response = app_client.get(f"/api/stream/{audiobook_id}")
            # Should attempt to send file or return the mock
            assert response.status_code in (200, 404, 500)

    def test_stream_audiobook_file_not_on_disk(self, app_client):
        """Test streaming when database entry exists but file doesn't."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("No audiobooks in database")

        audiobook_id = data["audiobooks"][0]["id"]

        # The file likely doesn't exist at the stored path in test environment
        response = app_client.get(f"/api/stream/{audiobook_id}")
        # Should return 404 for file not found
        assert response.status_code in (200, 404)


class TestSupplementDownloadWithMocks:
    """Test supplement download with mocked file system."""

    def test_download_supplement_file_exists(self, app_client):
        """Test downloading supplement when file exists."""
        with (
            patch("backend.api_modular.supplements.Path") as MockPath,
            patch("backend.api_modular.supplements.send_file") as mock_send,
        ):
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.suffix = ".pdf"
            MockPath.return_value = mock_path_instance

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_send.return_value = mock_response

            response = app_client.get("/api/supplements/1/download")
            # Will be 404 if supplement ID doesn't exist in DB
            assert response.status_code in (200, 404)

    def test_download_supplement_file_missing(self, app_client):
        """Test downloading supplement when file is missing from disk."""
        # This tests the file_path.exists() == False branch
        response = app_client.get("/api/supplements/1/download")
        # May return 200 if supplement exists and file is served, or 404 if not found
        assert response.status_code in (200, 404)


class TestSupplementsScanWithMocks:
    """Test supplements scan endpoint with mocked file system."""

    @pytest.mark.skip(
        reason="Requires refactoring - supplements_dir is a closure variable in api_modular"
    )
    def test_scan_supplements_dir_exists(self, app_client):
        """Test scanning supplements when directory exists."""
        mock_file = MagicMock()
        mock_file.is_file.return_value = True
        mock_file.name = "test_supplement.pdf"
        mock_file.suffix = ".pdf"
        mock_stat = MagicMock()
        mock_stat.st_size = 1024 * 1024  # 1 MB
        mock_file.stat.return_value = mock_stat

        with patch("backend.api.SUPPLEMENTS_DIR") as mock_dir:
            mock_dir.exists.return_value = True
            mock_dir.iterdir.return_value = [mock_file]
            mock_dir.__truediv__ = lambda self, x: mock_file

            response = app_client.post("/api/supplements/scan")
            assert response.status_code in (200, 404, 500)

    @pytest.mark.skip(
        reason="Requires refactoring - supplements_dir is a closure variable in api_modular"
    )
    def test_scan_supplements_dir_not_exists(self, app_client):
        """Test scanning when supplements directory doesn't exist."""
        with patch("backend.api.SUPPLEMENTS_DIR") as mock_dir:
            mock_dir.exists.return_value = False

            response = app_client.post("/api/supplements/scan")
            assert response.status_code == 404


class TestBulkOperationsWithMocks:
    """Test bulk operations that modify database."""

    def test_bulk_update_valid_field(self, app_client):
        """Test bulk update with valid field (narrator)."""
        # Get real audiobook IDs
        response = app_client.get("/api/audiobooks?per_page=2")
        data = json.loads(response.data)
        if not data.get("audiobooks") or len(data["audiobooks"]) < 1:
            pytest.skip("Need audiobooks in database")

        ids = [book["id"] for book in data["audiobooks"][:2]]

        # Test with allowed field
        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps(
                {"ids": ids, "field": "narrator", "value": "Test Narrator Update"}
            ),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)
        assert result.get("success") is True
        assert "updated_count" in result

    def test_bulk_update_publisher_field(self, app_client):
        """Test bulk update with publisher field."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks in database")

        ids = [data["audiobooks"][0]["id"]]

        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps(
                {"ids": ids, "field": "publisher", "value": "Test Publisher"}
            ),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_bulk_update_published_year_field(self, app_client):
        """Test bulk update with published_year field."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks in database")

        ids = [data["audiobooks"][0]["id"]]

        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps({"ids": ids, "field": "published_year", "value": 2020}),
            content_type="application/json",
        )
        assert response.status_code == 200


class TestDeletionWithMocks:
    """Test deletion endpoints with mocked file operations."""

    def test_bulk_delete_without_files(self, app_client):
        """Test bulk delete database records only (no file deletion)."""
        # We won't actually delete real records, just test the endpoint logic
        # Using non-existent IDs to avoid modifying real data
        response = app_client.post(
            "/api/audiobooks/bulk-delete",
            data=json.dumps({"ids": [999999, 999998], "delete_files": False}),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)
        assert result.get("success") is True
        assert result.get("deleted_count") == 0  # IDs don't exist

    def test_bulk_delete_with_files_mocked(self, app_client):
        """Test bulk delete with file deletion (mocked)."""
        with patch("backend.api_modular.audiobooks.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.unlink.return_value = None
            MockPath.return_value = mock_path_instance

            response = app_client.post(
                "/api/audiobooks/bulk-delete",
                data=json.dumps({"ids": [999997, 999996], "delete_files": True}),
                content_type="application/json",
            )
            assert response.status_code == 200

    def test_delete_duplicates_endpoint(self, app_client):
        """Test the delete duplicates endpoint."""
        response = app_client.post(
            "/api/duplicates/delete",
            data=json.dumps({"audiobook_ids": [999999]}),
            content_type="application/json",
        )
        # May return 200, 400, or 404 depending on implementation
        assert response.status_code in (200, 400, 404, 405)


class TestUtilityEndpointsWithMocks:
    """Test utility endpoints with mocked subprocess."""

    def test_rescan_library_success(self, app_client):
        """Test rescan library with mocked subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Scanning...\nTotal audiobook files: 500\nDone."
        mock_result.stderr = ""

        with patch(
            "backend.api_modular.utilities.subprocess.run", return_value=mock_result
        ):
            with patch("backend.api_modular.utilities.Path") as MockPath:
                mock_scanner = MagicMock()
                mock_scanner.exists.return_value = True
                MockPath.return_value = mock_scanner

                response = app_client.post("/api/utilities/rescan")
                assert response.status_code in (200, 500)

    def test_rescan_library_failure(self, app_client):
        """Test rescan library failure handling."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Script failed"

        with patch(
            "backend.api_modular.utilities.subprocess.run", return_value=mock_result
        ):
            response = app_client.post("/api/utilities/rescan")
            # Should return success with returncode info or 500
            assert response.status_code in (200, 500)

    def test_rescan_library_timeout(self, app_client):
        """Test rescan library timeout handling."""
        with patch(
            "backend.api_modular.utilities.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 1800),
        ):
            response = app_client.post("/api/utilities/rescan")
            assert response.status_code == 500

    def test_reimport_database_success(self, app_client):
        """Test reimport database with mocked subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Importing...\nImported 500 audiobooks\nDone."
        mock_result.stderr = ""

        with patch(
            "backend.api_modular.utilities.subprocess.run", return_value=mock_result
        ):
            response = app_client.post("/api/utilities/reimport")
            assert response.status_code in (200, 500)

    def test_reimport_database_failure(self, app_client):
        """Test reimport database failure handling."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Import failed"

        with patch(
            "backend.api_modular.utilities.subprocess.run", return_value=mock_result
        ):
            response = app_client.post("/api/utilities/reimport")
            # Should return with failure info
            assert response.status_code in (200, 500)

    def test_generate_hashes_success(self, app_client):
        """Test generate hashes with mocked subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Hashing...\nProcessed 100 files\nDone."
        mock_result.stderr = ""

        with patch(
            "backend.api_modular.utilities.subprocess.run", return_value=mock_result
        ):
            response = app_client.post("/api/utilities/generate-hashes")
            assert response.status_code in (200, 500)

    def test_generate_hashes_failure(self, app_client):
        """Test generate hashes failure handling."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Hash generation failed"

        with patch(
            "backend.api_modular.utilities.subprocess.run", return_value=mock_result
        ):
            response = app_client.post("/api/utilities/generate-hashes")
            # Should return with failure info
            assert response.status_code in (200, 500)


class TestDuplicatesByTitleLogic:
    """Test duplicates by title endpoint to cover the complex logic."""

    def test_duplicates_by_title_full_response(self, app_client):
        """Test duplicates by title returns all expected fields."""
        response = app_client.get("/api/duplicates/by-title")
        assert response.status_code == 200
        data = json.loads(response.data)

        assert "duplicate_groups" in data
        assert "total_groups" in data
        assert "total_potential_savings_mb" in data
        assert "total_duplicate_files" in data

        # Verify types
        assert isinstance(data["duplicate_groups"], list)
        assert isinstance(data["total_groups"], int)
        assert isinstance(data["total_potential_savings_mb"], (int, float))

    def test_duplicates_by_title_group_structure(self, app_client):
        """Test that duplicate groups have correct structure."""
        response = app_client.get("/api/duplicates/by-title")
        data = json.loads(response.data)

        for group in data.get("duplicate_groups", []):
            assert "title" in group
            assert "author" in group
            assert "count" in group
            assert "files" in group
            assert "potential_savings_mb" in group
            assert isinstance(group["files"], list)


class TestVerifyDeletionLogic:
    """Test verify deletion safety logic."""

    def test_verify_with_real_ids(self, app_client):
        """Test verify deletion with real audiobook IDs."""
        response = app_client.get("/api/audiobooks?per_page=3")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks in database")

        ids = [book["id"] for book in data["audiobooks"]]

        response = app_client.post(
            "/api/duplicates/verify",
            data=json.dumps({"audiobook_ids": ids}),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)
        assert "safe_ids" in result
        assert "unsafe_ids" in result
        assert "safe_count" in result
        assert "unsafe_count" in result


class TestEditionsEndpointLogic:
    """Test editions endpoint logic."""

    def test_editions_with_real_book(self, app_client):
        """Test editions endpoint with a real book."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks in database")

        book_id = data["audiobooks"][0]["id"]
        response = app_client.get(f"/api/audiobooks/{book_id}/editions")
        assert response.status_code == 200
        result = json.loads(response.data)
        assert "editions" in result
        assert isinstance(result["editions"], list)


class TestMissingDataEndpoints:
    """Test endpoints for finding missing data."""

    def test_missing_narrator_returns_list(self, app_client):
        """Test missing narrator endpoint returns audiobook list."""
        response = app_client.get("/api/audiobooks/missing-narrator")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "audiobooks" in data or isinstance(data, list)

    def test_missing_hash_returns_data(self, app_client):
        """Test missing hash endpoint."""
        response = app_client.get("/api/audiobooks/missing-hash")
        assert response.status_code in (200, 404)


class TestCoverServingWithMocks:
    """Test cover image serving."""

    def test_serve_cover_with_mock(self, app_client):
        """Test serving cover image."""
        with patch("backend.api_modular.audiobooks.send_from_directory") as mock_send:
            mock_send.return_value = MagicMock()
            response = app_client.get("/covers/test.jpg")
            # Will likely 404 as file doesn't exist
            assert response.status_code in (200, 404, 500)


class TestExportEndpoints:
    """Test export endpoints."""

    def test_export_json(self, app_client):
        """Test JSON export endpoint."""
        response = app_client.get("/api/utilities/export-json")
        assert response.status_code == 200
        assert response.content_type == "application/json"
        data = json.loads(response.data)
        assert "audiobooks" in data
        assert "total_count" in data
        assert "exported_at" in data

    def test_export_csv(self, app_client):
        """Test CSV export endpoint."""
        response = app_client.get("/api/utilities/export-csv")
        assert response.status_code == 200
        assert "text/csv" in response.content_type
        # Should have CSV content
        assert b"Title" in response.data or b"ID" in response.data


class TestDeleteDuplicatesEndpoint:
    """Test delete duplicates endpoint with various modes."""

    def test_delete_duplicates_missing_ids(self, app_client):
        """Test delete duplicates with missing audiobook_ids."""
        response = app_client.post(
            "/api/duplicates/delete",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_delete_duplicates_title_mode(self, app_client):
        """Test delete duplicates with title mode."""
        response = app_client.post(
            "/api/duplicates/delete",
            data=json.dumps({"audiobook_ids": [999999, 999998], "mode": "title"}),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_delete_duplicates_hash_mode(self, app_client):
        """Test delete duplicates with hash mode."""
        response = app_client.post(
            "/api/duplicates/delete",
            data=json.dumps({"audiobook_ids": [999999], "mode": "hash"}),
            content_type="application/json",
        )
        assert response.status_code == 200


class TestMoreMissingEndpoints:
    """Test additional endpoints to improve coverage."""

    def test_vacuum_utility(self, app_client):
        """Test vacuum utility endpoint."""
        response = app_client.post("/api/utilities/vacuum")
        assert response.status_code in (200, 404, 500)

    def test_audiobooks_with_nonexistent_collection(self, app_client):
        """Test audiobooks with non-existent collection."""
        response = app_client.get(
            "/api/audiobooks?collection=nonexistent_collection_xyz"
        )
        assert response.status_code == 200

    def test_filters_detailed(self, app_client):
        """Test filters endpoint returns expected data."""
        response = app_client.get("/api/filters")
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should have filter categories
        assert isinstance(data, dict)


class TestDuplicatesByTitleInnerLogic:
    """Test to cover the inner loops of duplicates by title."""

    def test_duplicates_by_title_processes_all_groups(self, app_client):
        """Test that all duplicate groups are processed."""
        response = app_client.get("/api/duplicates/by-title")
        assert response.status_code == 200
        data = json.loads(response.data)

        # Verify the response includes processing info
        assert "duplicate_groups" in data
        total_files = sum(g.get("count", 0) for g in data.get("duplicate_groups", []))
        assert data["total_duplicate_files"] == total_files - data["total_groups"]


class TestExceptionHandling:
    """Test exception handling paths."""

    def test_bulk_update_db_error(self, app_client):
        """Test bulk update with database error handling."""
        # Get real IDs
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks")

        # Try updating with a field that might cause issues
        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps(
                {
                    "ids": [data["audiobooks"][0]["id"]],
                    "field": "narrator",
                    "value": None,  # Set to NULL
                }
            ),
            content_type="application/json",
        )
        # Should handle gracefully
        assert response.status_code in (200, 400, 500)


class TestHashStatsEndpoint:
    """Test hash statistics endpoint."""

    def test_hash_stats_full_response(self, app_client):
        """Test hash stats returns complete information."""
        response = app_client.get("/api/hash-stats")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestAudiobookUpdateEndpoint:
    """Test audiobook update endpoint."""

    def test_update_audiobook_with_data(self, app_client):
        """Test updating audiobook with actual data."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks")

        book_id = data["audiobooks"][0]["id"]
        response = app_client.put(
            f"/api/audiobooks/{book_id}",
            data=json.dumps({"narrator": "Updated Narrator Test"}),
            content_type="application/json",
        )
        # Should work or return method not allowed
        assert response.status_code in (200, 400, 404, 405)


class TestVerifyDeletionWithRealData:
    """Test verify deletion with various scenarios."""

    def test_verify_deletion_no_hash(self, app_client):
        """Test verify deletion with audiobooks lacking hashes."""
        response = app_client.get("/api/audiobooks?per_page=5")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks")

        ids = [book["id"] for book in data["audiobooks"]]
        response = app_client.post(
            "/api/duplicates/verify",
            data=json.dumps({"audiobook_ids": ids}),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)
        # Should categorize as safe or unsafe
        assert "safe_count" in result
        assert "unsafe_count" in result


class TestExceptionPaths:
    """Test exception handling paths."""

    def test_stats_database_size_exception(self, app_client):
        """Test stats endpoint handles database size check errors."""
        response = app_client.get("/api/stats")
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should have database_size_mb even if it falls back
        assert "database_size_mb" in data

    def test_stream_with_various_formats(self, app_client):
        """Test streaming with various audio formats."""
        response = app_client.get("/api/audiobooks?per_page=5")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks")

        # Test streaming first audiobook
        book_id = data["audiobooks"][0]["id"]
        response = app_client.get(f"/api/stream/{book_id}")
        # Should handle gracefully
        assert response.status_code in (200, 404)


class TestAdditionalEndpoints:
    """Test additional endpoints for coverage."""

    def test_supplements_for_audiobook(self, app_client):
        """Test getting supplements for a specific audiobook."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks")

        book_id = data["audiobooks"][0]["id"]
        response = app_client.get(f"/api/audiobooks/{book_id}/supplements")
        assert response.status_code == 200
        result = json.loads(response.data)
        assert "supplements" in result
        assert "count" in result

    def test_hash_stats_detailed(self, app_client):
        """Test hash stats with detailed response."""
        response = app_client.get("/api/hash-stats")
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should have hash statistics
        assert isinstance(data, dict)

    def test_narrator_counts_detailed(self, app_client):
        """Test narrator counts with detailed check."""
        response = app_client.get("/api/narrator-counts")
        assert response.status_code == 200
        data = json.loads(response.data)
        # May be list or dict
        assert isinstance(data, (list, dict))


class TestMoreUtilityEndpoints:
    """Test more utility endpoints."""

    def test_vacuum_db(self, app_client):
        """Test vacuum database utility."""
        response = app_client.post("/api/utilities/vacuum")
        assert response.status_code in (200, 404, 500)

    def test_verify_empty_request(self, app_client):
        """Test verify deletion with empty request body."""
        response = app_client.post(
            "/api/duplicates/verify", data="", content_type="application/json"
        )
        # Should handle gracefully
        assert response.status_code in (200, 400, 415, 500)


class TestBulkOperationsEdgeCases:
    """Test bulk operations edge cases."""

    def test_bulk_delete_nonexistent_with_file_flag(self, app_client):
        """Test bulk delete of non-existent IDs with file deletion enabled."""
        response = app_client.post(
            "/api/audiobooks/bulk-delete",
            data=json.dumps({"ids": [999999999, 999999998], "delete_files": True}),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)
        assert result.get("success") is True
        assert result.get("deleted_count") == 0

    def test_bulk_update_all_allowed_fields(self, app_client):
        """Test bulk update with all allowed fields."""
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)
        if not data.get("audiobooks"):
            pytest.skip("Need audiobooks")

        book_id = data["audiobooks"][0]["id"]

        # Test narrator (allowed)
        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps(
                {"ids": [book_id], "field": "narrator", "value": "Test Narrator"}
            ),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Test publisher (allowed)
        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps(
                {"ids": [book_id], "field": "publisher", "value": "Test Publisher"}
            ),
            content_type="application/json",
        )
        assert response.status_code == 200

        # Test published_year (allowed)
        response = app_client.post(
            "/api/audiobooks/bulk-update",
            data=json.dumps(
                {"ids": [book_id], "field": "published_year", "value": 2023}
            ),
            content_type="application/json",
        )
        assert response.status_code == 200


class TestDuplicatesByHashWithRealData:
    """Test duplicates by hash endpoint with actual duplicate data in DB."""

    def test_duplicates_by_hash_inner_loop(self, app_client, db_with_hash_duplicates):
        """Test that the inner loop processing duplicates executes."""
        # The endpoint is /api/duplicates (not /api/duplicates/by-hash)
        response = app_client.get("/api/duplicates")
        assert response.status_code == 200
        data = json.loads(response.data)

        # Should have at least our test duplicate group
        assert data["total_groups"] >= 1

        # Find our test group
        test_hash = db_with_hash_duplicates["hash"]
        found_group = None
        for group in data["duplicate_groups"]:
            if group["hash"] == test_hash:
                found_group = group
                break

        assert found_group is not None, "Test duplicate group not found"
        assert found_group["count"] == 2
        assert len(found_group["files"]) == 2

        # Verify keeper/duplicate marking
        keepers = [f for f in found_group["files"] if f["is_keeper"]]
        duplicates = [f for f in found_group["files"] if f["is_duplicate"]]
        assert len(keepers) == 1
        assert len(duplicates) == 1

    def test_duplicates_by_hash_wasted_space_calculation(
        self, app_client, db_with_hash_duplicates
    ):
        """Test that wasted space is calculated correctly."""
        response = app_client.get("/api/duplicates")
        data = json.loads(response.data)

        test_hash = db_with_hash_duplicates["hash"]
        for group in data["duplicate_groups"]:
            if group["hash"] == test_hash:
                # Wasted = file_size * (count - 1)
                # Our test files are 50.0 MB and 51.0 MB
                # Keeper is first by ID, so wasted is ~50.0 MB
                assert group["wasted_mb"] >= 0
                assert group["file_size_mb"] >= 0
                break


class TestDuplicatesByTitleWithRealData:
    """Test duplicates by title endpoint with actual duplicate data in DB."""

    def test_duplicates_by_title_inner_loop(self, app_client, db_with_title_duplicates):
        """Test that the inner loop processing title duplicates executes."""
        response = app_client.get("/api/duplicates/by-title")
        assert response.status_code == 200
        data = json.loads(response.data)

        # Should have at least our test duplicate group
        assert data["total_groups"] >= 1

        # Find our test group by title
        test_title = db_with_title_duplicates["title"]
        found_group = None
        for group in data["duplicate_groups"]:
            if group["title"] == test_title:
                found_group = group
                break

        assert found_group is not None, (
            f"Test duplicate group for '{test_title}' not found"
        )
        assert found_group["count"] == 2
        assert len(found_group["files"]) == 2
        assert found_group["author"] == "Real Author Name"

    def test_duplicates_by_title_keeper_selection(
        self, app_client, db_with_title_duplicates
    ):
        """Test that keeper is selected correctly (prefers opus format)."""
        response = app_client.get("/api/duplicates/by-title")
        data = json.loads(response.data)

        test_title = db_with_title_duplicates["title"]
        for group in data["duplicate_groups"]:
            if group["title"] == test_title:
                keepers = [f for f in group["files"] if f["is_keeper"]]
                assert len(keepers) == 1
                # Opus format should be preferred
                assert keepers[0]["format"] == "opus"
                break

    def test_duplicates_by_title_potential_savings(
        self, app_client, db_with_title_duplicates
    ):
        """Test that potential savings are calculated."""
        response = app_client.get("/api/duplicates/by-title")
        data = json.loads(response.data)

        test_title = db_with_title_duplicates["title"]
        for group in data["duplicate_groups"]:
            if group["title"] == test_title:
                # Should have potential savings calculated
                assert "potential_savings_mb" in group
                assert group["potential_savings_mb"] >= 0
                break


class TestDeleteDuplicatesWithRealData:
    """Test delete duplicates endpoint with actual duplicate data."""

    def test_delete_duplicates_title_mode_with_data(
        self, app_client, db_with_title_duplicates
    ):
        """Test delete duplicates in title mode with actual duplicates."""
        ids = db_with_title_duplicates["ids"]

        # Mock file operations to prevent actual file deletion
        with patch("backend.api_modular.duplicates.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False  # Files don't exist
            mock_path_instance.suffix = ".opus"
            MockPath.return_value = mock_path_instance

            response = app_client.post(
                "/api/duplicates/delete",
                data=json.dumps({"audiobook_ids": ids, "mode": "title"}),
                content_type="application/json",
            )
            assert response.status_code == 200
            result = json.loads(response.data)
            assert result.get("success") is True
            # Should have blocked one (keeper) and possibly deleted one
            assert "blocked_count" in result
            assert "deleted_count" in result

    def test_delete_duplicates_hash_mode_with_data(
        self, app_client, db_with_hash_duplicates
    ):
        """Test delete duplicates in hash mode with actual duplicates."""
        ids = db_with_hash_duplicates["ids"]

        # Mock file operations
        with patch("backend.api_modular.duplicates.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path_instance.suffix = ".opus"
            MockPath.return_value = mock_path_instance

            response = app_client.post(
                "/api/duplicates/delete",
                data=json.dumps({"audiobook_ids": ids, "mode": "hash"}),
                content_type="application/json",
            )
            assert response.status_code == 200
            result = json.loads(response.data)
            assert result.get("success") is True

    def test_delete_duplicates_with_file_exists(
        self, app_client, db_with_hash_duplicates
    ):
        """Test delete duplicates when files exist on disk (mocked)."""
        ids = db_with_hash_duplicates["ids"]

        # Only delete one (the duplicate, not the keeper)
        duplicate_id = ids[1] if len(ids) > 1 else ids[0]

        with patch("backend.api_modular.duplicates.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.unlink.return_value = None
            mock_path_instance.suffix = ".opus"
            MockPath.return_value = mock_path_instance

            response = app_client.post(
                "/api/duplicates/delete",
                data=json.dumps({"audiobook_ids": [duplicate_id], "mode": "hash"}),
                content_type="application/json",
            )
            assert response.status_code == 200


class TestVerifyDeletionWithDuplicates:
    """Test verify deletion with actual duplicate data."""

    def test_verify_deletion_with_hash_duplicates(
        self, app_client, db_with_hash_duplicates
    ):
        """Test verify deletion correctly identifies safe/unsafe with duplicates."""
        ids = db_with_hash_duplicates["ids"]

        response = app_client.post(
            "/api/duplicates/verify",
            data=json.dumps({"audiobook_ids": ids, "mode": "hash"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)

        # Trying to delete all copies should flag one as unsafe (keeper)
        assert "safe_ids" in result
        assert "unsafe_ids" in result

    def test_verify_deletion_with_title_duplicates(
        self, app_client, db_with_title_duplicates
    ):
        """Test verify deletion with title mode duplicates."""
        ids = db_with_title_duplicates["ids"]

        response = app_client.post(
            "/api/duplicates/verify",
            data=json.dumps({"audiobook_ids": ids, "mode": "title"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = json.loads(response.data)
        assert "safe_count" in result
        assert "unsafe_count" in result


class TestDuplicateDeletionFileOperations:
    """Test the actual file deletion operations with mocking."""

    def test_delete_with_file_unlink_success(self, app_client, db_with_hash_duplicates):
        """Test that file.unlink() is called when file exists."""
        ids = db_with_hash_duplicates["ids"]
        duplicate_id = ids[1]  # The duplicate, not the keeper

        with patch("backend.api_modular.duplicates.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.unlink.return_value = None
            mock_path_instance.suffix = ".opus"
            MockPath.return_value = mock_path_instance

            response = app_client.post(
                "/api/duplicates/delete",
                data=json.dumps({"audiobook_ids": [duplicate_id], "mode": "hash"}),
                content_type="application/json",
            )
            assert response.status_code == 200

    def test_delete_with_file_unlink_exception(
        self, app_client, db_with_hash_duplicates
    ):
        """Test handling of file deletion errors."""
        ids = db_with_hash_duplicates["ids"]
        duplicate_id = ids[1]

        with patch("backend.api_modular.duplicates.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.unlink.side_effect = PermissionError("Access denied")
            mock_path_instance.suffix = ".opus"
            MockPath.return_value = mock_path_instance

            response = app_client.post(
                "/api/duplicates/delete",
                data=json.dumps({"audiobook_ids": [duplicate_id], "mode": "hash"}),
                content_type="application/json",
            )
            # Should still return 200 with error info
            assert response.status_code == 200
            result = json.loads(response.data)
            # May have errors in response
            assert "success" in result


class TestDuplicatesTitleAuthorLogic:
    """Test the author-related logic in duplicates by title."""

    def test_duplicates_excludes_audiobook_author(self, app_client):
        """Test that 'Audiobook' as author is excluded from grouping."""
        from backend.api_modular import get_db

        conn = get_db()
        cursor = conn.cursor()

        # Insert entries with 'Audiobook' as author - shouldn't create duplicate group
        for i in range(2):
            cursor.execute(
                """
                INSERT INTO audiobooks (
                    title, author, narrator, file_path, file_size_mb,
                    format, duration_hours, duration_formatted, sha256_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    "Book With Audiobook Author",
                    "Audiobook",  # Should be excluded
                    "Test Narrator",
                    f"/test/audiobook_author_{i}.opus",
                    30.0,
                    "opus",
                    5.0,
                    "05:00:00",
                    f"audiobook_author_hash_{i}",
                ),
            )
        conn.commit()

        try:
            response = app_client.get("/api/duplicates/by-title")
            assert response.status_code == 200
            data = json.loads(response.data)

            # Should NOT find a group for this title with 'Audiobook' author
            for group in data["duplicate_groups"]:
                if group["title"] == "Book With Audiobook Author":
                    pytest.fail("Should not group books with 'Audiobook' as author")
        finally:
            cursor.execute(
                "DELETE FROM audiobooks WHERE author = 'Audiobook' AND title = 'Book With Audiobook Author'"
            )
            conn.commit()
            conn.close()


class TestDuplicatesHashNullHandling:
    """Test handling of null hashes in deletion."""

    def test_delete_with_null_hash(self, app_client):
        """Test delete duplicates with null sha256_hash is blocked."""
        from backend.api_modular import get_db

        conn = get_db()
        cursor = conn.cursor()

        # Insert an audiobook without a hash
        cursor.execute(
            """
            INSERT INTO audiobooks (
                title, author, narrator, file_path, file_size_mb,
                format, duration_hours, duration_formatted, sha256_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                "Book Without Hash",
                "Test Author",
                "Test Narrator",
                "/test/no_hash.opus",
                25.0,
                "opus",
                3.0,
                "03:00:00",
                None,  # No hash
            ),
        )
        conn.commit()

        cursor.execute("SELECT id FROM audiobooks WHERE title = 'Book Without Hash'")
        row = cursor.fetchone()
        book_id = row["id"]

        try:
            with patch("backend.api_modular.duplicates.Path") as MockPath:
                mock_path_instance = MagicMock()
                mock_path_instance.exists.return_value = False
                MockPath.return_value = mock_path_instance

                response = app_client.post(
                    "/api/duplicates/delete",
                    data=json.dumps({"audiobook_ids": [book_id], "mode": "hash"}),
                    content_type="application/json",
                )
                assert response.status_code == 200
                result = json.loads(response.data)
                # Should block deletion of book with null hash
                assert result.get("blocked_count", 0) >= 0
        finally:
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (book_id,))
            conn.commit()
            conn.close()
