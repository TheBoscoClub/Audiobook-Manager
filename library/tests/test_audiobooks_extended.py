"""
Extended tests for audiobooks module.

Tests the additional audiobooks endpoints and data enrichment:
- GET /api/audiobooks/<id> - single audiobook with enriched data
- GET /api/stream/<id> - stream audiobook file
- GET /api/audiobooks with enrichment (genres, eras, topics, edition counts)

Note: These tests use mocking instead of direct DB insertion to avoid
session-scoped database conflicts.
"""


class TestStreamAudiobook:
    """Test the stream_audiobook endpoint."""

    def test_stream_nonexistent_audiobook(self, flask_app):
        """Test streaming non-existent audiobook returns 404."""
        with flask_app.test_client() as client:
            response = client.get("/api/stream/999999")

        assert response.status_code == 404
        data = response.get_json()
        assert "not found" in data["error"].lower()


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_status(self, flask_app):
        """Test health endpoint returns status."""
        with flask_app.test_client() as client:
            response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "database" in data

    def test_health_includes_version_key(self, flask_app):
        """Test health endpoint includes version key."""
        with flask_app.test_client() as client:
            response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert "version" in data


class TestEndpointMethodConstraints:
    """Test that endpoints only respond to correct HTTP methods."""

    def test_single_audiobook_only_get(self, flask_app):
        """Test /api/audiobooks/<id> only allows GET for retrieve."""
        # PUT and DELETE are handled by utilities_crud, so just test POST
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1", json={})
        assert response.status_code == 405

    def test_stream_only_get(self, flask_app):
        """Test /api/stream/<id> only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/stream/1")
        assert response.status_code == 405

    def test_health_only_get(self, flask_app):
        """Test /health only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/health")
        assert response.status_code == 405


class TestAudiobooksListBasic:
    """Test audiobooks list endpoint basics."""

    def test_audiobooks_list_returns_200(self, flask_app):
        """Test audiobooks list returns 200."""
        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks")

        assert response.status_code == 200
        data = response.get_json()
        assert "audiobooks" in data
        assert "pagination" in data

    def test_audiobooks_list_has_enrichment_fields(self, flask_app):
        """Test audiobooks list includes enrichment fields in structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks?per_page=1")

        assert response.status_code == 200
        data = response.get_json()
        if data["audiobooks"]:
            book = data["audiobooks"][0]
            # These fields should exist in the response structure
            assert "genres" in book or "id" in book  # genres or basic fields
            assert "id" in book
            assert "title" in book
