"""
Tests for duplicates detection module.

Tests the duplicates endpoints:
- GET /api/hash-stats - hash generation statistics
- GET /api/duplicates - hash-based duplicate groups
- GET /api/duplicates/by-title - title-based duplicates
- GET /api/duplicates/by-checksum - checksum-based duplicates from index files
- POST /api/duplicates/regenerate-checksums - regenerate checksum indexes
- POST /api/duplicates/delete-by-path - delete files by path
- POST /api/duplicates/verify - verify deletion safety
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestGetHashStats:
    """Test the get_hash_stats endpoint."""

    def test_returns_stats_structure(self, flask_app):
        """Test returns proper stats structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/hash-stats")

        assert response.status_code == 200
        data = response.get_json()
        assert "hash_column_exists" in data
        assert "total_audiobooks" in data
        assert "hashed_count" in data
        assert "unhashed_count" in data


class TestGetDuplicates:
    """Test the get_duplicates endpoint (hash-based)."""

    def test_returns_proper_structure(self, flask_app):
        """Test returns proper response structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates")

        assert response.status_code == 200
        data = response.get_json()
        assert "duplicate_groups" in data
        # API returns "total_duplicate_files" not "total_duplicates"
        assert "total_duplicate_files" in data or "total_duplicates" in data
        assert "total_wasted_mb" in data or "total_wasted_space_mb" in data


class TestGetDuplicatesByTitle:
    """Test the get_duplicates_by_title endpoint."""

    def test_returns_proper_structure(self, flask_app):
        """Test returns proper response structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates/by-title")

        assert response.status_code == 200
        data = response.get_json()
        assert "duplicate_groups" in data


class TestDuplicatesByChecksum:
    """Test the find_duplicates_from_checksums endpoint."""

    def test_returns_structure_when_index_missing(self, flask_app):
        """Test returns proper structure when index files don't exist."""
        with patch.dict(os.environ, {"AUDIOBOOKS_DATA": "/nonexistent/path"}):
            with flask_app.test_client() as client:
                response = client.get("/api/duplicates/by-checksum")

        assert response.status_code == 200
        data = response.get_json()
        assert "sources" in data or "library" in data

    def test_with_type_sources(self, flask_app):
        """Test filtering by type=sources."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates/by-checksum?type=sources")

        assert response.status_code == 200
        data = response.get_json()
        assert "sources" in data

    def test_with_type_library(self, flask_app):
        """Test filtering by type=library."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates/by-checksum?type=library")

        assert response.status_code == 200
        data = response.get_json()
        assert "library" in data

    def test_parses_index_file(self, flask_app, session_temp_dir):
        """Test parses checksum index file correctly."""
        # Create mock index file
        index_dir = session_temp_dir / ".index"
        index_dir.mkdir(exist_ok=True)
        index_file = index_dir / "source_checksums.idx"
        # Create index with duplicates
        index_content = """abc123|/path/to/file1.aaxc
abc123|/path/to/file2.aaxc
def456|/path/to/unique.aaxc
"""
        index_file.write_text(index_content)

        with patch.dict(os.environ, {"AUDIOBOOKS_DATA": str(session_temp_dir)}):
            with flask_app.test_client() as client:
                response = client.get("/api/duplicates/by-checksum?type=sources")

        assert response.status_code == 200
        data = response.get_json()
        sources = data.get("sources", {})
        if sources and sources.get("exists"):
            assert "duplicate_groups" in sources


class TestRegenerateChecksums:
    """Test the regenerate_checksums endpoint."""

    def test_regenerate_with_type_both(self, flask_app):
        """Test regenerate with default type=both."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("builtins.open", MagicMock()):
                with patch.dict(
                    os.environ,
                    {
                        "AUDIOBOOKS_DATA": "/tmp/test",
                        "AUDIOBOOKS_SOURCES": "/tmp/sources",
                        "AUDIOBOOKS_LIBRARY": "/tmp/library",
                    },
                ):
                    with flask_app.test_client() as client:
                        response = client.post(
                            "/api/duplicates/regenerate-checksums", json={}
                        )

        assert response.status_code == 200

    @patch("subprocess.run")
    def test_handles_timeout(self, mock_run, flask_app):
        """Test handles subprocess timeout gracefully."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=600)

        with patch.dict(
            os.environ,
            {
                "AUDIOBOOKS_DATA": "/tmp/test",
                "AUDIOBOOKS_SOURCES": "/tmp/sources",
                "AUDIOBOOKS_LIBRARY": "/tmp/library",
            },
        ):
            with flask_app.test_client() as client:
                response = client.post(
                    "/api/duplicates/regenerate-checksums", json={"type": "sources"}
                )

        assert response.status_code == 200
        data = response.get_json()
        sources = data.get("sources", {})
        if sources:
            assert sources.get("success") is False or "error" in sources


class TestDeleteByPath:
    """Test the delete_duplicates_by_path endpoint."""

    def test_missing_paths_returns_400(self, flask_app):
        """Test returns 400 when paths not provided."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates/delete-by-path", json={})

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_empty_paths_returns_400(self, flask_app):
        """Test returns 400 when paths list is empty."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates/delete-by-path", json={"paths": []})

        assert response.status_code == 400

    def test_delete_path_outside_allowed_directory(self, flask_app):
        """Test deleting file outside allowed directory is rejected as unsafe."""
        with flask_app.test_client() as client:
            response = client.post(
                "/api/duplicates/delete-by-path",
                json={"paths": ["/nonexistent/file.opus"], "type": "library"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        # Path outside allowed directory goes to skipped_unsafe, not skipped_not_found
        assert len(data["skipped_unsafe"]) >= 1


class TestVerifyDeletion:
    """Test the verify endpoint."""

    def test_missing_ids_returns_400(self, flask_app):
        """Test returns 400 when ids not provided."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates/verify", json={})

        assert response.status_code == 400

    def test_empty_ids_returns_400(self, flask_app):
        """Test returns 400 when ids list is empty."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates/verify", json={"ids": []})

        assert response.status_code == 400


class TestRemoveFromIndexes:
    """Test the remove_from_indexes helper function."""

    def test_removes_from_existing_index(self, session_temp_dir):
        """Test removes filepath from index file."""
        from backend.api_modular.duplicates import remove_from_indexes

        # Create mock index
        index_dir = session_temp_dir / ".index"
        index_dir.mkdir(exist_ok=True)
        index_file = index_dir / "source_checksums.idx"
        index_file.write_text("abc|/path/to/keep.aaxc\ndef|/path/to/remove.aaxc\n")

        with patch.dict(os.environ, {"AUDIOBOOKS_DATA": str(session_temp_dir)}):
            remove_from_indexes(Path("/path/to/remove.aaxc"))

        # Verify removal
        content = index_file.read_text()
        assert "/path/to/remove.aaxc" not in content
        assert "/path/to/keep.aaxc" in content

    def test_handles_missing_index(self, session_temp_dir):
        """Test handles missing index files gracefully."""
        from backend.api_modular.duplicates import remove_from_indexes

        with patch.dict(os.environ, {"AUDIOBOOKS_DATA": str(session_temp_dir)}):
            # Should not raise even if index doesn't exist
            result = remove_from_indexes(Path("/path/to/file.aaxc"))

        assert isinstance(result, dict)


class TestEndpointMethodConstraints:
    """Test that endpoints only respond to correct HTTP methods."""

    def test_hash_stats_only_get(self, flask_app):
        """Test /api/hash-stats only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/hash-stats")
        assert response.status_code == 405

    def test_duplicates_only_get(self, flask_app):
        """Test /api/duplicates only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates")
        assert response.status_code == 405

    def test_duplicates_by_title_only_get(self, flask_app):
        """Test /api/duplicates/by-title only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates/by-title")
        assert response.status_code == 405

    def test_duplicates_by_checksum_only_get(self, flask_app):
        """Test /api/duplicates/by-checksum only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/duplicates/by-checksum")
        assert response.status_code == 405

    def test_regenerate_checksums_only_post(self, flask_app):
        """Test /api/duplicates/regenerate-checksums only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates/regenerate-checksums")
        assert response.status_code == 405

    def test_delete_by_path_only_post(self, flask_app):
        """Test /api/duplicates/delete-by-path only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates/delete-by-path")
        assert response.status_code == 405

    def test_verify_deletion_only_post(self, flask_app):
        """Test /api/duplicates/verify only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/duplicates/verify")
        assert response.status_code == 405
