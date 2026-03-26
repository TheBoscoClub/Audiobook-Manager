"""
Extended tests for maintenance scheduling API endpoints.

Covers uncovered lines: 43-44, 70, 76, 79, 98-99, 106-117, 155, 163,
179, 183-190, 193, 237, 256-263, 272, 297-298, 330-331, 388-389.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def maint_app(tmp_path):
    """Create a Flask test app with fresh database for maintenance tests."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
    from api_modular import create_app

    db_path = tmp_path / "maint_ext.db"
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
def mclient(maint_app):
    return maint_app.test_client()


class TestGetUsernameEdgeCases:
    """Test _get_username helper (lines 43-44)."""

    def test_returns_system_on_exception(self):
        from backend.api_modular.maintenance import _get_username

        with patch(
            "backend.api_modular.maintenance.get_current_user",
            side_effect=Exception("no context"),
        ):
            assert _get_username() == "system"

    def test_returns_system_when_no_user(self):
        from backend.api_modular.maintenance import _get_username

        with patch(
            "backend.api_modular.maintenance.get_current_user",
            return_value=None,
        ):
            assert _get_username() == "system"


class TestCreateWindowValidation:
    """Test create window validation (lines 70, 76, 79)."""

    def test_no_json_body_returns_400(self, mclient):
        """Missing JSON body returns 400 (line 70)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            content_type="application/json",
            data="null",
        )
        assert resp.status_code == 400
        assert "JSON body required" in resp.get_json()["error"]

    def test_missing_required_fields_returns_400(self, mclient):
        """Missing name/task_type/schedule_type returns 400 (line 76)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={"name": "Test"},
        )
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    def test_invalid_schedule_type_returns_400(self, mclient):
        """Invalid schedule_type returns 400 (line 79)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Test",
                "task_type": "db_vacuum",
                "schedule_type": "invalid",
            },
        )
        assert resp.status_code == 400
        assert "once" in resp.get_json()["error"]

    def test_invalid_cron_expression_returns_400(self, mclient):
        """Invalid cron expression returns 400 (lines 98-99)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Bad Cron",
                "task_type": "db_vacuum",
                "schedule_type": "recurring",
                "cron_expression": "invalid cron",
            },
        )
        assert resp.status_code == 400
        assert "cron" in resp.get_json()["error"].lower()


class TestCreateWindowTaskValidation:
    """Test task type validation against registry (lines 106-117)."""

    def test_unknown_task_type_returns_400(self, mclient):
        """Unknown task type returns available tasks (lines 106-117)."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        mock_registry.list_all.return_value = [
            {"name": "db_vacuum"},
            {"name": "checksum_audit"},
        ]

        with patch.dict(
            "sys.modules",
            {
                "backend.api_modular.maintenance_tasks": MagicMock(
                    registry=mock_registry
                )
            },
        ):
            resp = mclient.post(
                "/api/admin/maintenance/windows",
                json={
                    "name": "Test",
                    "task_type": "nonexistent_task",
                    "schedule_type": "once",
                    "scheduled_at": "2026-04-01T03:00:00Z",
                },
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "Unknown task_type" in data["error"]
        assert "available" in data

    def test_once_with_scheduled_at(self, mclient):
        """Once-type window uses scheduled_at for next_run_at."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Once Window",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-06-01T03:00:00Z",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["next_run_at"] == "2026-06-01T03:00:00Z"


class TestUpdateWindowExtended:
    """Extended update window tests (lines 155, 163, 179, 183-190, 193)."""

    def test_update_no_json_returns_400(self, mclient):
        """Missing JSON body returns 400 (line 155)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Updatable",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T00:00:00Z",
            },
        )
        wid = resp.get_json()["id"]

        resp = mclient.put(
            f"/api/admin/maintenance/windows/{wid}",
            content_type="application/json",
            data="",
        )
        assert resp.status_code == 400

    def test_update_nonexistent_returns_404(self, mclient):
        """Updating nonexistent window returns 404 (line 163)."""
        resp = mclient.put(
            "/api/admin/maintenance/windows/99999",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_update_task_params_dict(self, mclient):
        """Dict task_params is serialized to JSON (line 179)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Params Test",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T00:00:00Z",
            },
        )
        wid = resp.get_json()["id"]

        resp = mclient.put(
            f"/api/admin/maintenance/windows/{wid}",
            json={"task_params": {"key": "value"}},
        )
        assert resp.status_code == 200

    def test_update_cron_recomputes_next_run(self, mclient):
        """Updating cron_expression recomputes next_run_at (lines 183-190)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Cron Update",
                "task_type": "db_vacuum",
                "schedule_type": "recurring",
                "cron_expression": "0 3 * * *",
            },
        )
        wid = resp.get_json()["id"]

        resp = mclient.put(
            f"/api/admin/maintenance/windows/{wid}",
            json={"cron_expression": "0 4 * * *"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["next_run_at"] is not None

    def test_update_scheduled_at_for_once(self, mclient):
        """Updating scheduled_at for once-type recomputes next_run_at (line 190)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Sched Update",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T00:00:00Z",
            },
        )
        wid = resp.get_json()["id"]

        resp = mclient.put(
            f"/api/admin/maintenance/windows/{wid}",
            json={"scheduled_at": "2026-05-01T00:00:00Z"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["next_run_at"] == "2026-05-01T00:00:00Z"

    def test_update_no_valid_fields_returns_400(self, mclient):
        """No valid fields to update returns 400 (line 193)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "No Update",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T00:00:00Z",
            },
        )
        wid = resp.get_json()["id"]

        resp = mclient.put(
            f"/api/admin/maintenance/windows/{wid}",
            json={"invalid_field": "value"},
        )
        assert resp.status_code == 400
        assert "No valid fields" in resp.get_json()["error"]


class TestDeleteWindowWithHistory:
    """Test soft delete when history exists (line 237)."""

    def test_soft_delete_with_history(self, mclient, tmp_path):
        """Window with history is soft-deleted (cancelled) (line 237)."""
        resp = mclient.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "Has History",
                "task_type": "db_vacuum",
                "schedule_type": "once",
                "scheduled_at": "2026-04-01T00:00:00Z",
            },
        )
        wid = resp.get_json()["id"]

        # Insert fake history record
        db_path = tmp_path / "maint_ext.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_history (window_id, started_at, status) "
            "VALUES (?, datetime('now'), 'completed')",
            (wid,),
        )
        conn.commit()
        conn.close()

        resp = mclient.delete(f"/api/admin/maintenance/windows/{wid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["soft_deleted"] is True


class TestListMessages:
    """Test list messages endpoint (lines 256-263)."""

    def test_list_returns_all_messages(self, mclient):
        """List returns messages ordered by created_at desc."""
        mclient.post(
            "/api/admin/maintenance/messages",
            json={"message": "First"},
        )
        mclient.post(
            "/api/admin/maintenance/messages",
            json={"message": "Second"},
        )
        resp = mclient.get("/api/admin/maintenance/messages")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 2


class TestCreateMessageValidation:
    """Test create message validation (line 272)."""

    def test_missing_message_field_returns_400(self, mclient):
        """Missing message field returns 400."""
        resp = mclient.post(
            "/api/admin/maintenance/messages",
            json={"not_message": "test"},
        )
        assert resp.status_code == 400
        assert "message field required" in resp.get_json()["error"]

    def test_no_json_returns_400(self, mclient):
        """No JSON body returns 400."""
        resp = mclient.post(
            "/api/admin/maintenance/messages",
            content_type="application/json",
            data="",
        )
        assert resp.status_code == 400


class TestCreateMessageWebSocketBroadcast:
    """Test WebSocket broadcast on message creation (lines 297-298)."""

    def test_broadcast_failure_handled(self, mclient):
        """WebSocket broadcast failure doesn't break message creation."""
        mock_cm = MagicMock()
        mock_cm.broadcast.side_effect = Exception("ws error")

        with patch(
            "backend.api_modular.websocket.connection_manager", mock_cm
        ):
            resp = mclient.post(
                "/api/admin/maintenance/messages",
                json={"message": "Broadcast fail test"},
            )

        assert resp.status_code == 201


class TestDismissMessageWebSocketBroadcast:
    """Test WebSocket broadcast on message dismissal (lines 330-331)."""

    def test_dismiss_broadcast_failure_handled(self, mclient):
        """WebSocket broadcast failure on dismiss doesn't break endpoint."""
        resp = mclient.post(
            "/api/admin/maintenance/messages",
            json={"message": "Dismiss test"},
        )
        mid = resp.get_json()["id"]

        mock_cm = MagicMock()
        mock_cm.broadcast.side_effect = Exception("ws error")

        with patch(
            "backend.api_modular.websocket.connection_manager", mock_cm
        ):
            resp = mclient.delete(f"/api/admin/maintenance/messages/{mid}")

        assert resp.status_code == 200


class TestTaskListImportError:
    """Test task list when registry import fails (lines 388-389)."""

    def test_returns_empty_on_import_error(self, mclient):
        """Returns empty list when maintenance_tasks module unavailable."""
        # The import happens inside the route function, so we mock the module
        # to make it unavailable
        with patch.dict("sys.modules", {"backend.api_modular.maintenance_tasks": None}):
            resp = mclient.get("/api/admin/maintenance/tasks")

        assert resp.status_code == 200


class TestPublicAnnouncementsExtended:
    """Test public announcements with windows (lines related to announcements)."""

    def test_announcements_includes_windows(self, mclient):
        """Announcements endpoint returns both messages and windows."""
        resp = mclient.get("/api/maintenance/announcements")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "messages" in data
        assert "windows" in data

    def test_announcements_dismissed_excluded(self, mclient):
        """Dismissed messages don't appear in announcements."""
        resp = mclient.post(
            "/api/admin/maintenance/messages",
            json={"message": "Will dismiss"},
        )
        mid = resp.get_json()["id"]

        mclient.delete(f"/api/admin/maintenance/messages/{mid}")

        resp = mclient.get("/api/maintenance/announcements")
        data = resp.get_json()
        message_ids = [m["id"] for m in data["messages"]]
        assert mid not in message_ids
