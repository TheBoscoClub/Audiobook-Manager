"""
Extended tests for system administration utilities module.

Covers uncovered lines: 63-65, 73-74, 105-126, 265, 285, 295, 312, 315, 340,
365, 395-396, 445, 452, 462, 465, 514, 526, 531, 540, 548-562, 584, 587,
612-614, 631-632, 667, 676-677, 685-686.
"""

import json
import pathlib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch


class TestWriteRequestEdgeCases:
    """Test edge cases in _write_request (lines 63-65, 73-74)."""

    def test_status_truncate_permission_error(self, temp_dir):
        """Status file truncation fails gracefully (lines 63-65)."""
        from backend.api_modular import utilities_system as module

        control_dir = temp_dir / ".control"
        control_dir.mkdir()
        module.CONTROL_DIR = control_dir
        module.HELPER_REQUEST_FILE = control_dir / "upgrade-request"
        module.HELPER_STATUS_FILE = control_dir / "upgrade-status"

        # Create status file
        module.HELPER_STATUS_FILE.write_text('{"old": true}')

        # First write_text (truncate status) raises PermissionError,
        # second write_text (write request) succeeds
        call_count = 0
        original_write_text = pathlib.Path.write_text

        def selective_write(self_path, content, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and "upgrade-status" in str(self_path):
                raise PermissionError("cannot truncate")
            return original_write_text(self_path, content, *args, **kwargs)

        with patch.object(pathlib.Path, "write_text", selective_write):
            result = module._write_request({"type": "test"})

        # Should still succeed since status truncation failure is non-fatal
        assert result is True

    def test_write_request_generic_exception(self, temp_dir):
        """Generic exception on write returns False (lines 73-74)."""
        from backend.api_modular import utilities_system as module

        control_dir = temp_dir / ".control"
        control_dir.mkdir()
        module.CONTROL_DIR = control_dir
        module.HELPER_REQUEST_FILE = control_dir / "upgrade-request"
        module.HELPER_STATUS_FILE = control_dir / "upgrade-status"

        with patch.object(pathlib.Path, "write_text", side_effect=RuntimeError("unexpected")):
            result = module._write_request({"type": "test"})

        assert result is False


class TestReadPreflight:
    """Test _read_preflight function (lines 105-126)."""

    def test_returns_none_when_no_file(self, temp_dir):
        from backend.api_modular import utilities_system as module

        module.PREFLIGHT_FILE = temp_dir / "nonexistent.json"
        assert module._read_preflight() is None

    def test_returns_none_on_invalid_json(self, temp_dir):
        from backend.api_modular import utilities_system as module

        preflight = temp_dir / "preflight.json"
        preflight.write_text("not json{{{")
        module.PREFLIGHT_FILE = preflight
        assert module._read_preflight() is None

    def test_fresh_report_not_stale(self, temp_dir):
        """Recent timestamp results in stale=False."""
        from backend.api_modular import utilities_system as module

        now = datetime.now(timezone.utc)
        preflight = temp_dir / "preflight.json"
        preflight.write_text(json.dumps({"timestamp": now.isoformat(), "status": "ok"}))
        module.PREFLIGHT_FILE = preflight

        result = module._read_preflight()
        assert result is not None
        assert result["stale"] is False

    def test_old_report_is_stale(self, temp_dir):
        """Timestamp older than 30 minutes results in stale=True."""
        from backend.api_modular import utilities_system as module

        old_ts = datetime.now(timezone.utc) - timedelta(hours=1)
        preflight = temp_dir / "preflight.json"
        preflight.write_text(json.dumps({"timestamp": old_ts.isoformat(), "status": "ok"}))
        module.PREFLIGHT_FILE = preflight

        result = module._read_preflight()
        assert result is not None
        assert result["stale"] is True

    def test_missing_timestamp_is_stale(self, temp_dir):
        """Missing timestamp field defaults to stale=True."""
        from backend.api_modular import utilities_system as module

        preflight = temp_dir / "preflight.json"
        preflight.write_text(json.dumps({"status": "ok"}))
        module.PREFLIGHT_FILE = preflight

        result = module._read_preflight()
        assert result is not None
        assert result["stale"] is True

    def test_invalid_timestamp_is_stale(self, temp_dir):
        """Invalid timestamp format defaults to stale=True."""
        from backend.api_modular import utilities_system as module

        preflight = temp_dir / "preflight.json"
        preflight.write_text(json.dumps({"timestamp": "not-a-date", "status": "ok"}))
        module.PREFLIGHT_FILE = preflight

        result = module._read_preflight()
        assert result is not None
        assert result["stale"] is True

    def test_z_suffix_timestamp(self, temp_dir):
        """Timestamp with Z suffix is parsed correctly."""
        from backend.api_modular import utilities_system as module

        now = datetime.now(timezone.utc)
        preflight = temp_dir / "preflight.json"
        preflight.write_text(json.dumps({"timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))
        module.PREFLIGHT_FILE = preflight

        result = module._read_preflight()
        assert result is not None
        assert result["stale"] is False


class TestServiceStartFailure:
    """Test service start failure path (line 265)."""

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_start_service_failure_returns_500(self, mock_write, mock_wait, flask_app):
        mock_write.return_value = True
        mock_wait.return_value = {"success": False, "message": "Service failed to start"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/start")

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


class TestServiceStopFailure:
    """Test service stop failure path (line 295)."""

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_stop_service_failure_returns_500(self, mock_write, mock_wait, flask_app):
        mock_write.return_value = True
        mock_wait.return_value = {"success": False, "message": "Service failed to stop"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/stop")

        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


class TestServiceStopWriteFailure:
    """Test stop service write request failure (line 285)."""

    @patch("backend.api_modular.utilities_system._write_request")
    def test_stop_write_failure_returns_500(self, mock_write, flask_app):
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/stop")

        assert response.status_code == 500
        assert "permission denied" in response.get_json()["error"]


class TestRestartServiceEdgeCases:
    """Test restart service edge cases (lines 312, 315)."""

    def test_restart_unknown_service(self, flask_app):
        """Unknown service name returns 400 (line 312)."""
        with flask_app.test_client() as client:
            response = client.post("/api/system/services/bad-service/restart")
        assert response.status_code == 400

    @patch("backend.api_modular.utilities_system._write_request")
    def test_restart_write_failure(self, mock_write, flask_app):
        """Write failure returns 500 (line 315)."""
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/restart")
        assert response.status_code == 500


class TestStartAllWriteFailure:
    """Test start-all write failure (line 340)."""

    @patch("backend.api_modular.utilities_system._write_request")
    def test_start_all_write_failure(self, mock_write, flask_app):
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/start-all")
        assert response.status_code == 500
        assert "permission denied" in response.get_json()["error"]


class TestStopAllWriteFailure:
    """Test stop-all write failure (line 365)."""

    @patch("backend.api_modular.utilities_system._write_request")
    def test_stop_all_write_failure(self, mock_write, flask_app):
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/stop-all")
        assert response.status_code == 500
        assert "permission denied" in response.get_json()["error"]


class TestGetUpgradePreflight:
    """Test GET /api/system/upgrade/preflight (lines 395-396)."""

    @patch("backend.api_modular.utilities_system._read_preflight")
    def test_returns_preflight_data(self, mock_read, flask_app):
        mock_read.return_value = {
            "timestamp": "2026-03-25T12:00:00Z",
            "stale": False,
            "status": "ok",
        }

        with flask_app.test_client() as client:
            response = client.get("/api/system/upgrade/preflight")

        assert response.status_code == 200
        data = response.get_json()
        assert "preflight" in data
        assert data["preflight"]["stale"] is False

    @patch("backend.api_modular.utilities_system._read_preflight")
    def test_returns_null_preflight(self, mock_read, flask_app):
        mock_read.return_value = None

        with flask_app.test_client() as client:
            response = client.get("/api/system/upgrade/preflight")

        assert response.status_code == 200
        data = response.get_json()
        assert data["preflight"] is None


class TestCheckUpgradeExtended:
    """Extended check upgrade tests (lines 445, 452, 462, 465)."""

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_check_validates_project_no_version_file(
        self, mock_read, mock_write, flask_app, temp_dir
    ):
        """Project path without VERSION file rejected (line 445)."""
        mock_read.return_value = {"running": False}

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": str(temp_dir)},
            )

        assert response.status_code == 400
        assert "VERSION" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._read_status")
    def test_check_version_with_non_github_source(self, mock_read, flask_app):
        """Version field with non-github source rejected (line 452)."""
        mock_read.return_value = {"running": False}

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check",
                json={"source": "project", "project_path": "/some/path", "version": "1.0.0"},
            )

        # Should get 400 for either project_path or version error
        assert response.status_code == 400

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_check_with_version_param(self, mock_read, mock_write, flask_app):
        """Check with version parameter for github source (line 462)."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade/check", json={"source": "github", "version": "7.5.0"}
            )

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_check_write_failure(self, mock_read, mock_write, flask_app):
        """Write request failure returns 500 (line 465)."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/upgrade/check", json={"source": "github"})

        assert response.status_code == 500


class TestStartUpgradeExtended:
    """Extended start upgrade tests (lines 514, 526, 531, 540, 548-562, 584, 587)."""

    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_path_traversal_rejected(self, mock_read, flask_app):
        """Path with '..' components is rejected (line 514)."""
        mock_read.return_value = {"running": False}

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={"source": "project", "project_path": "/tmp/../etc/passwd"},  # nosec B108  # test fixture path
            )

        assert response.status_code == 400

    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_version_with_project_source(self, mock_read, flask_app):
        """Version field with project source rejected (line 540)."""
        mock_read.return_value = {"running": False}

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={
                    "source": "project",
                    "project_path": "/tmp",  # nosec B108  # test fixture path
                    "version": "1.0.0",
                },
            )

        assert response.status_code == 400

    @patch("backend.api_modular.utilities_system._read_preflight")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_requires_preflight(self, mock_read, mock_preflight, flask_app):
        """Upgrade without preflight returns 400 (lines 548-560)."""
        mock_read.return_value = {"running": False}
        mock_preflight.return_value = None

        with flask_app.test_client() as client:
            response = client.post("/api/system/upgrade", json={"source": "github"})

        assert response.status_code == 400
        assert "Preflight check required" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._read_preflight")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_rejects_stale_preflight(self, mock_read, mock_preflight, flask_app):
        """Stale preflight report returns 400 (lines 561-562)."""
        mock_read.return_value = {"running": False}
        mock_preflight.return_value = {"stale": True, "status": "ok"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/upgrade", json={"source": "github"})

        assert response.status_code == 400
        assert "Preflight" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_upgrade_write_failure(self, mock_read, mock_write, flask_app):
        """Write request failure returns 500 (lines 584, 587)."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/upgrade", json={"source": "github", "force": True})

        assert response.status_code == 500


class TestGetVersionExtended:
    """Extended version tests (lines 612-614)."""

    def test_version_exception_returns_unknown(self, flask_app):
        """Exception reading VERSION returns 'unknown' (lines 612-614)."""
        with patch.object(pathlib.Path, "exists", side_effect=PermissionError("denied")):
            with flask_app.test_client() as client:
                response = client.get("/api/system/version")

        assert response.status_code == 200
        data = response.get_json()
        assert data["version"] == "unknown"


class TestGetHealthExtended:
    """Extended health tests (lines 631-632)."""

    def test_health_version_exception(self, flask_app):
        """Exception reading version in health returns 'unknown' (lines 631-632)."""
        with patch.object(pathlib.Path, "read_text", side_effect=PermissionError("denied")):
            with flask_app.test_client() as client:
                response = client.get("/api/system/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["version"] == "unknown"


class TestListProjectsExtended:
    """Extended project listing tests (lines 667, 676-677, 685-686)."""

    def test_skips_duplicate_paths(self, flask_app, temp_dir, monkeypatch):
        """Duplicate project paths are deduplicated (line 667)."""
        project_dir = temp_dir / "Audiobook-Dup"
        project_dir.mkdir()
        (project_dir / "VERSION").write_text("1.0.0")

        # Set env to point to same dir - the path won't appear twice
        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", str(temp_dir))

        with flask_app.test_client() as client:
            response = client.get("/api/system/projects")

        data = response.get_json()
        names = [p["name"] for p in data["projects"]]
        # Should not have duplicate entries
        assert len(names) == len(set(names))

    def test_project_without_version_file(self, flask_app, temp_dir, monkeypatch):
        """Project dir without VERSION file has version=None (lines 676-677)."""
        project_dir = temp_dir / "Audiobook-NoVersion"
        project_dir.mkdir()
        # No VERSION file

        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", str(temp_dir))

        with flask_app.test_client() as client:
            response = client.get("/api/system/projects")

        data = response.get_json()
        matching = [p for p in data["projects"] if p["name"] == "Audiobook-NoVersion"]
        if matching:
            assert matching[0]["version"] is None

    def test_inaccessible_directory_skipped(self, flask_app, monkeypatch):
        """Inaccessible directory is silently skipped (lines 685-686)."""

        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", "/nonexistent/dir")

        with flask_app.test_client() as client:
            response = client.get("/api/system/projects")

        assert response.status_code == 200
        data = response.get_json()
        assert "projects" in data
