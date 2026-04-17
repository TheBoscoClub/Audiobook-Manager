"""
Extended tests for CRUD operations module — covers uncovered lines.

Targets: topics, eras, editorial reviews, audible categories,
enrichment stats, cover file cleanup on delete, bulk delete with
file failure and cover cleanup, and empty-name skip paths in
genre/topic/era operations.
"""

from unittest.mock import MagicMock, patch


# ── Delete with cover file cleanup (line 148) ────────────────────────


class TestDeleteAudiobookCoverCleanup:
    """Cover file cleanup during single audiobook delete."""

    @patch("backend.api_modular.utilities_crud.COVER_DIR")
    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_cleans_up_cover_file(self, mock_get_db, mock_cover_dir, flask_app, tmp_path):
        """Line 148: cover_file.unlink() is called when cover exists."""
        cover_file = tmp_path / "cover123.jpg"
        cover_file.write_text("fake cover")

        mock_cover_dir.__truediv__ = lambda self, name: tmp_path / name

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # fetchone for cover_path lookup
        mock_cursor.fetchone.return_value = {"cover_path": "cover123.jpg"}
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/audiobooks/1")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        # Cover file should have been deleted
        assert not cover_file.exists()


# ── Bulk delete: covers_to_delete + file failure + cover cleanup ─────


class TestBulkDeleteCoverAndFileFailure:
    """Bulk delete with cover cleanup (lines 285, 334-336) and
    file deletion failure (lines 322-325)."""

    @patch("backend.api_modular.utilities_crud.COVER_DIR")
    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_delete_cleans_up_covers(self, mock_get_db, mock_cover_dir, flask_app, tmp_path):
        """Lines 285, 334-336: cover files are collected and deleted."""
        cover_file = tmp_path / "bulk_cover.jpg"
        cover_file.write_text("cover data")

        mock_cover_dir.__truediv__ = lambda self, name: tmp_path / name

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "file_path": None, "cover_path": "bulk_cover.jpg"}
        ]
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/bulk-delete", json={"ids": [1]})

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert not cover_file.exists()

    @patch("backend.api_modular.utilities_crud.COVER_DIR")
    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_delete_file_deletion_failure(
        self, mock_get_db, mock_cover_dir, flask_app, tmp_path
    ):
        """Lines 322-325: file deletion fails but DB deletion succeeds."""
        # Create a file that we'll make undeletable
        bad_file = tmp_path / "protected.opus"
        bad_file.write_text("audio data")

        mock_cover_dir.__truediv__ = lambda self, name: tmp_path / name

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "file_path": str(bad_file), "cover_path": None}
        ]
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        # Make unlink raise an error
        with patch("pathlib.Path.unlink", side_effect=PermissionError("denied")):
            with flask_app.test_client() as client:
                response = client.post(
                    "/api/audiobooks/bulk-delete", json={"ids": [1], "delete_files": True}
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["files_deleted"] == 0
        assert data["files_failed"] is not None
        assert len(data["files_failed"]) == 1
        assert "File deletion failed" in data["files_failed"][0]["error"]


# ── Empty genre name skip (line 453 in set_genres, 528/547 in bulk) ──


class TestEmptyNameSkipPaths:
    """Empty and whitespace-only names are skipped in genre/topic/era ops."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_genres_skips_empty_names(self, mock_get_db, flask_app):
        """Line 453: empty genre names are skipped."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # audiobook exists
            {"id": 10},  # genre id for "Fiction"
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/1/genres", json={"genres": ["Fiction", "", "  "]}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_add_genres_skips_empty_names(self, mock_get_db, flask_app):
        """Line 528: empty genre names skipped in bulk add."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 10}
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-genres",
                json={"ids": [1], "genres": ["Drama", ""], "mode": "add"},
            )

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_remove_genres_skips_empty_names(self, mock_get_db, flask_app):
        """Line 547: empty genre names skipped in bulk remove."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 10}
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-genres",
                json={"ids": [1], "genres": ["Drama", " "], "mode": "remove"},
            )

        assert response.status_code == 200
        assert response.get_json()["success"] is True


# ── Topics: list, set, bulk (lines 591-710) ─────────────────────────


class TestListTopics:
    """Test GET /api/topics (lines 591-602)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_list_topics_returns_data(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "Adventure", "book_count": 3},
            {"id": 2, "name": "History", "book_count": 7},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/topics")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "Adventure"

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_list_topics_empty(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/topics")

        assert response.status_code == 200
        assert response.get_json() == []


class TestSetAudiobookTopics:
    """Test PUT /api/audiobooks/<id>/topics (lines 608-645)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_topics_success(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # audiobook exists
            {"id": 20},  # topic id for "Adventure"
            {"id": 21},  # topic id for "Survival"
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/1/topics", json={"topics": ["Adventure", "Survival"]}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["topics"] == ["Adventure", "Survival"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_topics_audiobook_not_found(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/999/topics", json={"topics": ["Adventure"]})

        assert response.status_code == 404
        assert "not found" in response.get_json()["error"]

    def test_set_topics_missing_field(self, flask_app):
        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/topics", json={"wrong": "data"})
        assert response.status_code == 400
        assert "topics" in response.get_json()["error"]

    def test_set_topics_not_a_list(self, flask_app):
        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/topics", json={"topics": "not-a-list"})
        assert response.status_code == 400
        assert "list" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_topics_skips_empty_names(self, mock_get_db, flask_app):
        """Line 628-629: empty topic names skipped."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # audiobook exists
            {"id": 20},  # topic id
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put(
                "/api/audiobooks/1/topics", json={"topics": ["Adventure", "", "  "]}
            )

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_topics_database_error(self, mock_get_db, flask_app):
        """Lines 640-645: exception path."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1}
        mock_cursor.execute.side_effect = [
            None,  # SELECT audiobook
            None,  # fetchone
            Exception("DB Error"),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/topics", json={"topics": ["Adventure"]})

        assert response.status_code == 500
        assert response.get_json()["success"] is False


class TestBulkManageTopics:
    """Test POST /api/audiobooks/bulk-topics (lines 651-710)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_add_topics_success(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 20}
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics",
                json={"ids": [1, 2], "topics": ["Adventure"], "mode": "add"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "add"

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_remove_topics_success(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 20}
        mock_cursor.rowcount = 2
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics",
                json={"ids": [1, 2], "topics": ["Adventure"], "mode": "remove"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "remove"

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_remove_topics_nonexistent(self, mock_get_db, flask_app):
        """Topic doesn't exist in DB — gracefully skipped."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics",
                json={"ids": [1], "topics": ["Nonexistent"], "mode": "remove"},
            )

        assert response.status_code == 200
        assert response.get_json()["affected"] == 0

    def test_bulk_topics_no_data(self, flask_app):
        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics", content_type="application/json", data="null"
            )
        assert response.status_code == 400

    def test_bulk_topics_no_ids(self, flask_app):
        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics", json={"ids": [], "topics": ["X"], "mode": "add"}
            )
        assert response.status_code == 400
        assert "No audiobook IDs" in response.get_json()["error"]

    def test_bulk_topics_no_topics(self, flask_app):
        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics", json={"ids": [1], "topics": [], "mode": "add"}
            )
        assert response.status_code == 400
        assert "No topics" in response.get_json()["error"]

    def test_bulk_topics_invalid_mode(self, flask_app):
        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics", json={"ids": [1], "topics": ["X"], "mode": "replace"}
            )
        assert response.status_code == 400
        assert "mode" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_add_topics_skips_empty_names(self, mock_get_db, flask_app):
        """Lines 674-675: empty topic names skipped in add mode."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 20}
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics",
                json={"ids": [1], "topics": ["Valid", ""], "mode": "add"},
            )

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_remove_topics_skips_empty_names(self, mock_get_db, flask_app):
        """Lines 688-689: empty topic names skipped in remove mode."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 20}
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics",
                json={"ids": [1], "topics": ["Valid", " "], "mode": "remove"},
            )

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_bulk_topics_database_error(self, mock_get_db, flask_app):
        """Lines 705-710: exception path."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/bulk-topics", json={"ids": [1], "topics": ["X"], "mode": "add"}
            )

        assert response.status_code == 500
        assert response.get_json()["success"] is False


# ── Eras: list, set, bulk (lines 718-772) ────────────────────────────


class TestListEras:
    """Test GET /api/eras (lines 718-729)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_list_eras_returns_data(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "Victorian", "book_count": 5},
            {"id": 2, "name": "Modern", "book_count": 12},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/eras")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "Victorian"

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_list_eras_empty(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/eras")

        assert response.status_code == 200
        assert response.get_json() == []


class TestSetAudiobookEras:
    """Test PUT /api/audiobooks/<id>/eras (lines 735-772)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_eras_success(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # audiobook exists
            {"id": 30},  # era id for "Victorian"
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/eras", json={"eras": ["Victorian"]})

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["eras"] == ["Victorian"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_eras_audiobook_not_found(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/999/eras", json={"eras": ["Victorian"]})

        assert response.status_code == 404
        assert "not found" in response.get_json()["error"]

    def test_set_eras_missing_field(self, flask_app):
        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/eras", json={"wrong": "data"})
        assert response.status_code == 400
        assert "eras" in response.get_json()["error"]

    def test_set_eras_not_a_list(self, flask_app):
        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/eras", json={"eras": "not-a-list"})
        assert response.status_code == 400
        assert "list" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_eras_skips_empty_names(self, mock_get_db, flask_app):
        """Lines 755-756: empty era names skipped."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            {"id": 1},  # audiobook exists
            {"id": 30},  # era id
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/eras", json={"eras": ["Victorian", "", "  "]})

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_set_eras_database_error(self, mock_get_db, flask_app):
        """Lines 767-772: exception path."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1}
        mock_cursor.execute.side_effect = [
            None,  # SELECT audiobook
            None,  # fetchone
            Exception("DB Error"),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.put("/api/audiobooks/1/eras", json={"eras": ["Victorian"]})

        assert response.status_code == 500
        assert response.get_json()["success"] is False


# ── Editorial reviews (lines 780-831) ────────────────────────────────


class TestGetEditorialReviews:
    """Test GET /api/audiobooks/<id>/reviews (lines 780-788)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_get_reviews_returns_data(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "review_text": "Great book!", "source": "NYT"},
            {"id": 2, "review_text": "Must read.", "source": "Publisher"},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/1/reviews")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["review_text"] == "Great book!"

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_get_reviews_empty(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/1/reviews")

        assert response.status_code == 200
        assert response.get_json() == []


class TestAddEditorialReview:
    """Test POST /api/audiobooks/<id>/reviews (lines 794-817)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_add_review_success(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1}  # audiobook exists
        mock_cursor.lastrowid = 42
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/1/reviews",
                json={"review_text": "Excellent narration!", "source": "Audible"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["id"] == 42

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_add_review_without_source(self, mock_get_db, flask_app):
        """Source defaults to empty string."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1}
        mock_cursor.lastrowid = 43
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1/reviews", json={"review_text": "Great!"})

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_add_review_audiobook_not_found(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post(
                "/api/audiobooks/999/reviews", json={"review_text": "No book here"}
            )

        assert response.status_code == 404
        assert "not found" in response.get_json()["error"]

    def test_add_review_missing_text(self, flask_app):
        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1/reviews", json={"source": "NYT"})
        assert response.status_code == 400
        assert "review_text" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_add_review_database_error(self, mock_get_db, flask_app):
        """Lines 813-817: exception path."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1}
        # First execute succeeds (SELECT), second raises
        call_count = [0]

        def execute_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise Exception("DB Error")

        mock_cursor.execute.side_effect = execute_side_effect
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.post("/api/audiobooks/1/reviews", json={"review_text": "Will fail"})

        assert response.status_code == 500
        assert response.get_json()["success"] is False


class TestDeleteEditorialReview:
    """Test DELETE /api/reviews/<review_id> (lines 823-831)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_review_success(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/reviews/42")

        assert response.status_code == 200
        assert response.get_json()["success"] is True

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_delete_review_not_found(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.delete("/api/reviews/999")

        assert response.status_code == 404
        assert "not found" in response.get_json()["error"]


# ── Audible categories (lines 839-849) ───────────────────────────────


class TestGetAudibleCategories:
    """Test GET /api/audiobooks/<id>/categories (lines 839-849)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_get_categories_returns_data(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {
                "id": 1,
                "category_path": "Fiction > Thriller",
                "category_name": "Thriller",
                "root_category": "Fiction",
                "depth": 1,
                "audible_category_id": "cat123",
            }
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/1/categories")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["category_name"] == "Thriller"

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_get_categories_empty(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/1/categories")

        assert response.status_code == 200
        assert response.get_json() == []


# ── Enrichment stats (lines 857-897) ─────────────────────────────────


class TestGetEnrichmentStats:
    """Test GET /api/audiobooks/enrichment-stats (lines 857-897)."""

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_enrichment_stats_full(self, mock_get_db, flask_app):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Each cursor.execute + fetchone pair returns a count;
        # fetchall for content_types and GROUP BY queries.
        # Order matches the source code:
        #  1. COUNT(*) total
        #  2. COUNT audible_enriched
        #  3. COUNT isbn_enriched
        #  4. COUNT DISTINCT genres
        #  5. COUNT DISTINCT topics
        #  6. COUNT DISTINCT eras
        #  7. COUNT DISTINCT reviews
        #  8. COUNT DISTINCT categories
        #  9. content_types GROUP BY (fetchall)
        # 10. with_subtitle
        # 11. with_language
        fetchone_values = [
            (100,),  # total
            (50,),  # audible_enriched
            (30,),  # isbn_enriched
            (40,),  # with_genres
            (25,),  # with_topics
            (15,),  # with_eras
            (10,),  # with_reviews
            (35,),  # with_categories
            # content_types uses fetchall — skip
            (60,),  # with_subtitle — after fetchall
            (70,),  # with_language
        ]
        fetchone_iter = iter(fetchone_values)
        mock_cursor.fetchone.side_effect = lambda: next(fetchone_iter)

        # content_types fetchall
        mock_cursor.fetchall.return_value = [("Product", 80), ("Podcast", 15), ("Lecture", 5)]

        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/enrichment-stats")

        assert response.status_code == 200
        data = response.get_json()
        assert data["total"] == 100
        assert data["audible_enriched"] == 50
        assert data["isbn_enriched"] == 30
        assert data["with_genres"] == 40
        assert data["with_topics"] == 25
        assert data["with_eras"] == 15
        assert data["with_reviews"] == 10
        assert data["with_categories"] == 35
        assert data["content_types"]["Product"] == 80
        assert data["content_types"]["Podcast"] == 15
        assert data["with_subtitle"] == 60
        assert data["with_language"] == 70

    @patch("backend.api_modular.utilities_crud.get_db")
    def test_enrichment_stats_empty_db(self, mock_get_db, flask_app):
        """All counts zero, no content types."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn

        with flask_app.test_client() as client:
            response = client.get("/api/audiobooks/enrichment-stats")

        assert response.status_code == 200
        data = response.get_json()
        assert data["total"] == 0
        assert data["content_types"] == {}
