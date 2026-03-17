"""Verify Audible sync endpoints are removed and position endpoints remain."""


class TestAudibleSyncRemoved:
    """Audible sync endpoints should no longer exist."""

    def test_sync_single_endpoint_removed(self, flask_app):
        """POST /api/position/sync/<id> should not exist."""
        with flask_app.test_client() as client:
            response = client.post("/api/position/sync/1")
        # 404 (no route) or 405 (blueprint prefix matches but no handler)
        assert response.status_code in (404, 405)

    def test_sync_all_endpoint_removed(self, flask_app):
        """POST /api/position/sync-all should not exist."""
        with flask_app.test_client() as client:
            response = client.post("/api/position/sync-all")
        assert response.status_code in (404, 405)

    def test_syncable_endpoint_removed(self, flask_app):
        """GET /api/position/syncable should not exist."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/syncable")
        assert response.status_code in (404, 405)

    def test_position_status_no_audible_fields(self, flask_app):
        """GET /api/position/status should not mention Audible."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/status")
        if response.status_code == 200:
            data = response.get_json()
            assert "audible_available" not in data
            assert "credential_stored" not in data
            assert "auth_file_exists" not in data

    def test_history_endpoint_removed(self, flask_app):
        """GET /api/position/history/<id> should not exist (replaced by per-user)."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/history/1")
        assert response.status_code in (404, 405)


class TestPositionEndpointsRemain:
    """Core position endpoints still work."""

    def test_get_position_exists(self, flask_app):
        """GET /api/position/<id> still works."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/1")
        assert response.status_code in (200, 404)

    def test_put_position_exists(self, flask_app):
        """PUT /api/position/<id> still accepts requests."""
        with flask_app.test_client() as client:
            response = client.put(
                "/api/position/1",
                json={"position_ms": 60000},
                content_type="application/json",
            )
        assert response.status_code in (200, 400, 401, 404, 422)

    def test_get_position_response_no_audible_fields(self, flask_app, session_temp_dir):
        """GET /api/position/<id> should not include Audible-specific fields."""
        import sqlite3

        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO audiobooks (
                id, title, author, asin, duration_hours,
                playback_position_ms, file_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                8888,
                "Cleanup Test Book",
                "Test Author",
                "B99999",
                5.0,
                1000000,
                "/test/cleanup.opus",
            ),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/8888")
        assert response.status_code == 200
        data = response.get_json()
        # These Audible-specific fields should be gone
        assert "audible_position_ms" not in data
        assert "audible_position_human" not in data
        assert "audible_position_updated" not in data
        assert "position_synced_at" not in data
        assert "syncable" not in data
        # Core fields should still be present
        assert "local_position_ms" in data
        assert "percent_complete" in data


class TestNoAudibleImports:
    """Verify Audible library is no longer imported in position_sync."""

    def test_no_audible_import_in_position_sync(self):
        import inspect

        from backend.api_modular import position_sync

        source = inspect.getsource(position_sync)
        assert "import audible" not in source
        assert "get_audible_client" not in source
        assert "fetch_audible_position" not in source
        assert "AUDIBLE_AVAILABLE" not in source
        assert "run_async" not in source
