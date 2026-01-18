"""
Tests for supplements module.

Tests the supplements endpoints:
- GET /api/supplements - list all supplements
- GET /api/supplements/stats - supplement statistics
- GET /api/audiobooks/<id>/supplements - supplements for audiobook
- GET /api/supplements/<id>/download - download supplement file
- POST /api/supplements/scan - scan and import supplements
"""


class TestGetAllSupplements:
    """Test the get_all_supplements endpoint."""

    def test_returns_proper_structure(self, flask_app):
        """Test returns proper response structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/supplements")

        assert response.status_code == 200
        data = response.get_json()
        assert "supplements" in data
        assert "total" in data
        assert isinstance(data["supplements"], list)


class TestGetSupplementStats:
    """Test the get_supplement_stats endpoint."""

    def test_returns_stats_structure(self, flask_app):
        """Test returns proper stats structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/supplements/stats")

        assert response.status_code == 200
        data = response.get_json()
        assert "total_supplements" in data
        assert "linked_to_audiobooks" in data
        assert "unlinked" in data
        assert "total_size_mb" in data
        assert "by_type" in data


class TestGetAudiobookSupplements:
    """Test the get_audiobook_supplements endpoint."""

    def test_returns_proper_structure(self, flask_app):
        """Test returns proper response structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/1/supplements")

        assert response.status_code == 200
        data = response.get_json()
        assert "audiobook_id" in data
        assert "count" in data
        assert "supplements" in data
        assert isinstance(data["supplements"], list)

    def test_returns_empty_for_nonexistent_audiobook(self, flask_app):
        """Test returns empty list for non-existent audiobook."""
        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/99999/supplements")

        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 0
        assert data["supplements"] == []


class TestDownloadSupplement:
    """Test the download_supplement endpoint."""

    def test_download_nonexistent_supplement(self, flask_app):
        """Test downloading non-existent supplement returns 404."""
        with flask_app.test_client() as client:
            response = client.get("/api/supplements/99999/download")

        assert response.status_code == 404
        data = response.get_json()
        assert "not found" in data["error"].lower()


class TestScanSupplements:
    """Test the scan_supplements endpoint."""

    def test_scan_empty_directory(self, flask_app, session_temp_dir):
        """Test scanning empty directory returns success with zero additions."""
        # The supplements_dir was created by the fixture
        # Ensure it's empty (delete any existing files)
        supplements_dir = session_temp_dir / "supplements"
        for f in supplements_dir.iterdir():
            f.unlink()

        with flask_app.test_client() as client:
            response = client.post("/api/supplements/scan")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["added"] == 0

    def test_scan_adds_new_pdf(self, flask_app, session_temp_dir):
        """Test scanning adds new PDF files."""
        supplements_dir = session_temp_dir / "supplements"
        test_pdf = supplements_dir / "new_book.pdf"
        test_pdf.write_bytes(b"%PDF-1.4 test content")

        with flask_app.test_client() as client:
            response = client.post("/api/supplements/scan")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["added"] >= 1
        assert "new_book.pdf" in data["added_files"]


class TestEndpointMethodConstraints:
    """Test that endpoints only respond to correct HTTP methods."""

    def test_supplements_only_get(self, flask_app):
        """Test /api/supplements only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/supplements")
        assert response.status_code == 405

    def test_supplements_stats_only_get(self, flask_app):
        """Test /api/supplements/stats only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/supplements/stats")
        assert response.status_code == 405

    def test_audiobook_supplements_only_get(self, flask_app):
        """Test /api/audiobooks/<id>/supplements only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1/supplements")
        assert response.status_code == 405

    def test_download_only_get(self, flask_app):
        """Test /api/supplements/<id>/download only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/supplements/1/download")
        assert response.status_code == 405

    def test_scan_only_post(self, flask_app):
        """Test /api/supplements/scan only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/supplements/scan")
        assert response.status_code == 405
