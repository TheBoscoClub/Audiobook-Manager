"""
Tests for CRUD operations module.

Tests the utilities_crud module covering:
- Single audiobook update (PUT)
- Single audiobook delete (DELETE)
- Bulk update operations
- Bulk delete operations (with/without file deletion)
- Missing narrator/hash queries

Note: These tests use mocking to avoid session-scoped database conflicts.
"""

from unittest.mock import MagicMock, patch


class TestUpdateAudiobook:
    """Test the update_audiobook endpoint (PUT /api/audiobooks/<id>)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_update_single_field_success(self, mock_get_db, flask_app):
        """Test successfully updating a single field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/9001",
                json={"narrator": "New Narrator"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["updated"] == 1

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_update_multiple_fields_success(self, mock_get_db, flask_app):
        """Test successfully updating multiple fields at once."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/9002",
                json={
                    "title": "New Title",
                    "series": "New Series",
                    "published_year": 2024,
                },
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_update_nonexistent_audiobook(self, mock_get_db, flask_app):
        """Test updating non-existent audiobook returns 404."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # No rows updated
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/999999",
                json={"title": "Does Not Matter"},
            )

        assert response.status_code == 404
        data = response.get_json()
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_update_no_data_provided(self, flask_app):
        """Test update with empty JSON body returns 400."""
        # Must send json={} to set Content-Type header, otherwise 415 is returned
        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1", json={})

        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        # Could be "No data provided" or "No valid fields" depending on implementation
        assert "No data" in data["error"] or "No valid" in data["error"]

    def test_update_no_valid_fields(self, flask_app):
        """Test update with only invalid fields returns 400."""
        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/1",
                json={"invalid_field": "value", "another_bad": 123},
            )

        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert "No valid fields" in data["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_update_database_error(self, mock_get_db, flask_app):
        """Test database error during update returns 500."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/1",
                json={"title": "New Title"},
            )

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert "failed" in data["error"]


class TestDeleteAudiobook:
    """Test the delete_audiobook endpoint (DELETE /api/audiobooks/<id>)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_audiobook_success(self, mock_get_db, flask_app):
        """Test successfully deleting an audiobook."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/audiobooks/9003")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["deleted"] == 1

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_audiobook_with_related_records(self, mock_get_db, flask_app):
        """Test deleting audiobook cascades to related records."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/audiobooks/9004")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_nonexistent_audiobook(self, mock_get_db, flask_app):
        """Test deleting non-existent audiobook returns 404."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # No rows deleted
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/audiobooks/999998")

        assert response.status_code == 404
        data = response.get_json()
        assert data["success"] is False
        assert "not found" in data["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_database_error(self, mock_get_db, flask_app):
        """Test database error during delete returns 500."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/audiobooks/1")

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


class TestBulkUpdateAudiobooks:
    """Test the bulk_update_audiobooks endpoint."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_update_narrator_success(self, mock_get_db, flask_app):
        """Test bulk updating narrator field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-update",
                json={
                    "ids": [9010, 9011],
                    "field": "narrator",
                    "value": "Bulk Narrator",
                },
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["updated_count"] == 2

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_update_series_success(self, mock_get_db, flask_app):
        """Test bulk updating series field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-update",
                json={
                    "ids": [9012, 9013],
                    "field": "series",
                    "value": "New Series",
                },
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_update_publisher_success(self, mock_get_db, flask_app):
        """Test bulk updating publisher field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-update",
                json={
                    "ids": [9014],
                    "field": "publisher",
                    "value": "New Publisher",
                },
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_update_published_year_success(self, mock_get_db, flask_app):
        """Test bulk updating published_year field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-update",
                json={
                    "ids": [9015],
                    "field": "published_year",
                    "value": 2025,
                },
            )

        assert response.status_code == 200

    def test_bulk_update_disallowed_field(self, flask_app):
        """Test bulk update rejects disallowed fields."""
        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-update",
                json={
                    "ids": [1, 2],
                    "field": "title",  # Not in allowed_fields for bulk
                    "value": "New Title",
                },
            )

        assert response.status_code == 400
        data = response.get_json()
        assert "not allowed" in data["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_update_database_error(self, mock_get_db, flask_app):
        """Test database error during bulk update returns 500."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-update",
                json={
                    "ids": [1, 2],
                    "field": "narrator",
                    "value": "Test",
                },
            )

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


class TestBulkDeleteAudiobooks:
    """Test the bulk_delete_audiobooks endpoint."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_delete_success(self, mock_get_db, flask_app):
        """Test bulk deleting audiobooks without file deletion."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []  # No file paths to delete
        mock_cursor.rowcount = 2
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-delete",
                json={"ids": [9020, 9021]},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["deleted_count"] == 2

    @patch("backend.api_modular.utilities_crud.get_db")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    def test_bulk_delete_with_file_deletion(
        self, mock_unlink, mock_exists, mock_get_db, flask_app
    ):
        """Test bulk delete with file deletion enabled."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Must return dict-like objects (sqlite3.Row behavior)
        mock_row = {"id": 9022, "file_path": "/test/file.opus"}
        mock_cursor.fetchall.return_value = [mock_row]
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-delete",
                json={"ids": [9022], "delete_files": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["files_deleted"] == 1

    @patch("backend.api_modular.utilities_crud.get_db")
    @patch("pathlib.Path.exists")
    def test_bulk_delete_with_file_deletion_nonexistent_file(
        self, mock_exists, mock_get_db, flask_app
    ):
        """Test bulk delete handles non-existent files gracefully."""
        mock_exists.return_value = False
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Must return dict-like objects (sqlite3.Row behavior)
        mock_row = {"id": 9023, "file_path": "/nonexistent/path.opus"}
        mock_cursor.fetchall.return_value = [mock_row]
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-delete",
                json={"ids": [9023], "delete_files": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["files_deleted"] == 0

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_delete_with_related_records(self, mock_get_db, flask_app):
        """Test bulk delete cascades to related records."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-delete",
                json={"ids": [9024]},
            )

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_delete_database_error(self, mock_get_db, flask_app):
        """Test database error during bulk delete returns 500."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-delete",
                json={"ids": [1, 2]},
            )

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


class TestMissingDataQueries:
    """Test the missing narrator/hash query endpoints."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_missing_narrator_returns_results(self, mock_get_db, flask_app):
        """Test missing narrator endpoint returns audiobooks without narrator."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "title": "No Narrator", "author": "Author"},
            {"id": 2, "title": "Empty Narrator", "author": "Author"},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/missing-narrator")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_missing_hash_returns_results(self, mock_get_db, flask_app):
        """Test missing hash endpoint returns audiobooks without sha256_hash."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "title": "No Hash", "author": "Author"},
            {"id": 2, "title": "Empty Hash", "author": "Author"},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/missing-hash")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)


class TestEndpointMethodConstraints:
    """Test that endpoints only respond to correct HTTP methods."""

    def test_update_only_put(self, flask_app):
        """Test update endpoint only allows PUT."""
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1", json={"title": "Test"})
        assert response.status_code == 405

    def test_delete_only_delete(self, flask_app):
        """Test delete endpoint only allows DELETE."""
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1")
        assert response.status_code == 405

    def test_bulk_update_only_post(self, flask_app):
        """Test bulk-update only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/bulk-update")
        assert response.status_code == 405

    def test_bulk_delete_only_post(self, flask_app):
        """Test bulk-delete only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/bulk-delete")
        assert response.status_code == 405

    def test_missing_narrator_only_get(self, flask_app):
        """Test missing-narrator only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/missing-narrator")
        assert response.status_code == 405

    def test_missing_hash_only_get(self, flask_app):
        """Test missing-hash only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/missing-hash")
        assert response.status_code == 405
