"""Tests for maintenance scheduling API endpoints."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app_with_db(tmp_path):
    """Create a Flask test app with fresh database."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
    from api_modular import create_app

    db_path = tmp_path / "test.db"
    schema_path = Path(__file__).parent.parent / "backend" / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_path.read_text())
    conn.close()

    app = create_app(
        database_path=db_path,
        project_dir=tmp_path,
        supplements_dir=tmp_path / "supplements",
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app_with_db):
    return app_with_db.test_client()


class TestMaintenanceWindows:
    def test_create_window(self, client):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Nightly Vacuum",
                "task_type": "db_vacuum",
                "schedule_type": "recurring",
                "cron_expression": "0 3 * * *",
                "lead_time_hours": 48,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Nightly Vacuum"
        assert data["id"] is not None

    def test_list_windows(self, client):
        client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Test",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T03:00:00Z",
            },
        )
        resp = client.get("/api/admin/maintenance/windows")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1

    def test_update_window(self, client):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Test",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T03:00:00Z",
            },
        )
        wid = resp.get_json()["id"]
        resp = client.put(
            f"/api/admin/maintenance/windows/{wid}",
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated Name"

    def test_delete_window_no_history(self, client):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Deletable",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T03:00:00Z",
            },
        )
        wid = resp.get_json()["id"]
        resp = client.delete(f"/api/admin/maintenance/windows/{wid}")
        assert resp.status_code == 200


class TestMaintenanceMessages:
    def test_create_message(self, client):
        resp = client.post(
            "/api/admin/maintenance/messages",
            json={"message": "Planned downtime tonight"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["message"] == "Planned downtime tonight"

    def test_dismiss_message(self, client):
        resp = client.post(
            "/api/admin/maintenance/messages",
            json={"message": "Test"},
        )
        mid = resp.get_json()["id"]
        resp = client.delete(f"/api/admin/maintenance/messages/{mid}")
        assert resp.status_code == 200


class TestPublicAnnouncements:
    def test_announcements_returns_active(self, client):
        client.post(
            "/api/admin/maintenance/messages",
            json={"message": "Server restarting"},
        )
        resp = client.get("/api/maintenance/announcements")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["messages"]) >= 1


class TestTaskList:
    def test_list_tasks(self, client):
        resp = client.get("/api/admin/maintenance/tasks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)


class TestHistory:
    def test_empty_history(self, client):
        resp = client.get("/api/admin/maintenance/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 0
