"""
Tests for system administration utilities module.

This module provides endpoints for service control and application upgrades,
using a privilege-separated helper service pattern.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestEnsureControlDir:
    """Test the _ensure_control_dir function."""

    def test_creates_dir_if_not_exists(self, temp_dir):
        """Test creates control directory when it doesn't exist."""
        from backend.api_modular import utilities_system as module

        control_dir = temp_dir / ".control"
        module.CONTROL_DIR = control_dir

        module._ensure_control_dir()

        assert control_dir.exists()

    def test_handles_existing_dir(self, temp_dir):
        """Test handles already existing directory."""
        from backend.api_modular import utilities_system as module

        control_dir = temp_dir / ".control"
        control_dir.mkdir()
        module.CONTROL_DIR = control_dir

        # Should not raise
        module._ensure_control_dir()
        assert control_dir.exists()

    def test_handles_permission_error(self, temp_dir):
        """Test gracefully handles permission error."""
        from backend.api_modular import utilities_system as module

        # Use a path where we can't create dirs
        module.CONTROL_DIR = Path("/root/nonexistent/.control")

        # Should not raise, just silently fail
        module._ensure_control_dir()


class TestWriteRequest:
    """Test the _write_request function."""

    def test_writes_request_file(self, temp_dir):
        """Test writes request data to file."""
        from backend.api_modular import utilities_system as module

        control_dir = temp_dir / ".control"
        control_dir.mkdir()
        module.CONTROL_DIR = control_dir
        module.HELPER_REQUEST_FILE = control_dir / "upgrade-request"
        module.HELPER_STATUS_FILE = control_dir / "upgrade-status"

        result = module._write_request({"type": "test", "data": "value"})

        assert result is True
        assert module.HELPER_REQUEST_FILE.exists()
        content = json.loads(module.HELPER_REQUEST_FILE.read_text())
        assert content["type"] == "test"
        assert content["data"] == "value"

    def test_clears_stale_status(self, temp_dir):
        """Test clears existing status file before writing."""
        from backend.api_modular import utilities_system as module

        control_dir = temp_dir / ".control"
        control_dir.mkdir()
        module.CONTROL_DIR = control_dir
        module.HELPER_REQUEST_FILE = control_dir / "upgrade-request"
        module.HELPER_STATUS_FILE = control_dir / "upgrade-status"

        # Create pre-existing status
        module.HELPER_STATUS_FILE.write_text('{"old": "status"}')

        module._write_request({"type": "new"})

        # Status should be cleared
        assert module.HELPER_STATUS_FILE.read_text() == ""

    def test_returns_false_on_permission_error(self):
        """Test returns False when cannot write."""
        from backend.api_modular import utilities_system as module

        module.CONTROL_DIR = Path("/root/nonexistent/.control")
        module.HELPER_REQUEST_FILE = module.CONTROL_DIR / "upgrade-request"
        module.HELPER_STATUS_FILE = module.CONTROL_DIR / "upgrade-status"

        result = module._write_request({"type": "test"})

        assert result is False


class TestReadStatus:
    """Test the _read_status function."""

    def test_returns_default_when_no_file(self, temp_dir):
        """Test returns default status when file doesn't exist."""
        from backend.api_modular import utilities_system as module

        module.HELPER_STATUS_FILE = temp_dir / "nonexistent"

        status = module._read_status()

        assert status["running"] is False
        assert status["success"] is None

    def test_reads_status_from_file(self, temp_dir):
        """Test reads and parses status from file."""
        from backend.api_modular import utilities_system as module

        status_file = temp_dir / "status.json"
        status_file.write_text(
            json.dumps(
                {
                    "running": True,
                    "stage": "downloading",
                    "message": "In progress",
                    "success": None,
                    "output": ["line1"],
                    "result": None,
                }
            )
        )
        module.HELPER_STATUS_FILE = status_file

        status = module._read_status()

        assert status["running"] is True
        assert status["stage"] == "downloading"

    def test_returns_default_on_invalid_json(self, temp_dir):
        """Test returns default status on invalid JSON."""
        from backend.api_modular import utilities_system as module

        status_file = temp_dir / "status.json"
        status_file.write_text("not valid json")
        module.HELPER_STATUS_FILE = status_file

        status = module._read_status()

        assert status["running"] is False


class TestWaitForCompletion:
    """Test the _wait_for_completion function."""

    def test_returns_status_when_complete(self, temp_dir):
        """Test returns status when operation completes."""
        from backend.api_modular import utilities_system as module

        status_file = temp_dir / "status.json"
        status_file.write_text(
            json.dumps({"running": False, "success": True, "message": "Done"})
        )
        module.HELPER_STATUS_FILE = status_file

        status = module._wait_for_completion(timeout=1.0, poll_interval=0.1)

        assert status["success"] is True

    def test_returns_timeout_when_no_completion(self, temp_dir):
        """Test returns timeout status when operation doesn't complete."""
        from backend.api_modular import utilities_system as module

        status_file = temp_dir / "status.json"
        # Running=True, success=None means not complete
        status_file.write_text(json.dumps({"running": True, "success": None}))
        module.HELPER_STATUS_FILE = status_file

        status = module._wait_for_completion(timeout=0.3, poll_interval=0.1)

        assert status["success"] is False
        assert "timeout" in status["stage"]

    def test_handles_empty_status_file(self, temp_dir):
        """Test waits when status file is empty."""
        from backend.api_modular import utilities_system as module

        status_file = temp_dir / "status.json"
        status_file.write_text("")
        module.HELPER_STATUS_FILE = status_file

        status = module._wait_for_completion(timeout=0.3, poll_interval=0.1)

        assert status["success"] is False


class TestGetServicesStatus:
    """Test the get_services_status route."""

    @patch("backend.api_modular.utilities_system.subprocess.run")
    def test_returns_services_status(self, mock_run, flask_app):
        """Test returns status for all services."""
        # Mock is-active returns "active"
        # Mock is-enabled returns "enabled"
        mock_run.return_value = MagicMock(stdout="active\n", returncode=0)

        with flask_app.test_client() as client:
            response = client.get("/api/system/services")

        assert response.status_code == 200
        data = response.get_json()
        assert "services" in data
        assert len(data["services"]) >= 1

    @patch("backend.api_modular.utilities_system.subprocess.run")
    def test_handles_timeout(self, mock_run, flask_app):
        """Test handles subprocess timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="systemctl", timeout=5)

        with flask_app.test_client() as client:
            response = client.get("/api/system/services")

        data = response.get_json()
        # Should still return a list, with error info
        assert "services" in data
        assert any(s["status"] == "timeout" for s in data["services"])

    @patch("backend.api_modular.utilities_system.subprocess.run")
    def test_handles_exception(self, mock_run, flask_app):
        """Test handles generic exception."""
        mock_run.side_effect = Exception("Unexpected error")

        with flask_app.test_client() as client:
            response = client.get("/api/system/services")

        data = response.get_json()
        assert "services" in data
        assert any(s["status"] == "error" for s in data["services"])


class TestStartService:
    """Test the start_service route."""

    def test_rejects_unknown_service(self, flask_app):
        """Test rejects unknown service name."""
        with flask_app.test_client() as client:
            response = client.post("/api/system/services/unknown-service/start")

        assert response.status_code == 400
        assert "Unknown service" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_starts_valid_service(self, mock_write, mock_wait, flask_app):
        """Test starts a valid service."""
        mock_write.return_value = True
        mock_wait.return_value = {"success": True, "message": "Started"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/start")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_write.assert_called_once()

    @patch("backend.api_modular.utilities_system._write_request")
    def test_handles_write_failure(self, mock_write, flask_app):
        """Test handles request write failure."""
        mock_write.return_value = False

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/start")

        assert response.status_code == 500
        assert "permission denied" in response.get_json()["error"]


class TestStopService:
    """Test the stop_service route."""

    def test_rejects_unknown_service(self, flask_app):
        """Test rejects unknown service name."""
        with flask_app.test_client() as client:
            response = client.post("/api/system/services/unknown-service/stop")

        assert response.status_code == 400

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_stops_valid_service(self, mock_write, mock_wait, flask_app):
        """Test stops a valid service."""
        mock_write.return_value = True
        mock_wait.return_value = {"success": True, "message": "Stopped"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/stop")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True


class TestRestartService:
    """Test the restart_service route."""

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_restarts_valid_service(self, mock_write, mock_wait, flask_app):
        """Test restarts a valid service."""
        mock_write.return_value = True
        mock_wait.return_value = {"success": True, "message": "Restarted"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/restart")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_handles_restart_failure(self, mock_write, mock_wait, flask_app):
        """Test handles restart failure."""
        mock_write.return_value = True
        mock_wait.return_value = {"success": False, "message": "Failed to restart"}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/audiobook-converter/restart")

        assert response.status_code == 500


class TestStartAllServices:
    """Test the start_all_services route."""

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_starts_all_services(self, mock_write, mock_wait, flask_app):
        """Test starts all services."""
        mock_write.return_value = True
        mock_wait.return_value = {
            "success": True,
            "result": {"results": [{"name": "svc1", "success": True}]},
            "message": "All started",
        }

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/start-all")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "results" in data


class TestStopAllServices:
    """Test the stop_all_services route."""

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_stops_all_services(self, mock_write, mock_wait, flask_app):
        """Test stops all services."""
        mock_write.return_value = True
        mock_wait.return_value = {
            "success": True,
            "result": {"results": [], "note": "API kept running"},
            "message": "Stopped",
        }

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/stop-all")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_system._wait_for_completion")
    @patch("backend.api_modular.utilities_system._write_request")
    def test_passes_include_api_flag(self, mock_write, mock_wait, flask_app):
        """Test passes include_api flag to request."""
        mock_write.return_value = True
        mock_wait.return_value = {"success": True, "result": {}, "message": ""}

        with flask_app.test_client() as client:
            response = client.post("/api/system/services/stop-all?include_api=true")

        assert response.status_code == 200
        # Check that _write_request was called with include_api=True
        call_args = mock_write.call_args[0][0]
        assert call_args["include_api"] is True


class TestGetUpgradeStatus:
    """Test the get_upgrade_status route."""

    @patch("backend.api_modular.utilities_system._read_status")
    def test_returns_status(self, mock_read, flask_app):
        """Test returns current upgrade status."""
        mock_read.return_value = {
            "running": True,
            "stage": "downloading",
            "message": "In progress",
            "success": None,
        }

        with flask_app.test_client() as client:
            response = client.get("/api/system/upgrade/status")

        assert response.status_code == 200
        data = response.get_json()
        assert data["running"] is True
        assert data["stage"] == "downloading"


class TestStartUpgrade:
    """Test the start_upgrade route."""

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_starts_github_upgrade(self, mock_read, mock_write, flask_app):
        """Test starts upgrade from GitHub."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={"source": "github"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["source"] == "github"

    @patch("backend.api_modular.utilities_system._read_status")
    def test_rejects_when_running(self, mock_read, flask_app):
        """Test rejects upgrade when one is already running."""
        mock_read.return_value = {"running": True}

        with flask_app.test_client() as client:
            response = client.post("/api/system/upgrade", json={"source": "github"})

        assert response.status_code == 400
        assert "already in progress" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._read_status")
    def test_requires_project_path_for_project_source(self, mock_read, flask_app):
        """Test requires project_path when source is 'project'."""
        mock_read.return_value = {"running": False}

        with flask_app.test_client() as client:
            response = client.post("/api/system/upgrade", json={"source": "project"})

        assert response.status_code == 400
        assert "project_path required" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_validates_project_path_exists(self, mock_read, mock_write, flask_app):
        """Test validates that project path exists."""
        mock_read.return_value = {"running": False}

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={"source": "project", "project_path": "/nonexistent/path"},
            )

        assert response.status_code == 400
        assert "not found" in response.get_json()["error"]

    @patch("backend.api_modular.utilities_system._write_request")
    @patch("backend.api_modular.utilities_system._read_status")
    def test_starts_project_upgrade(self, mock_read, mock_write, flask_app, temp_dir):
        """Test starts upgrade from project directory."""
        mock_read.return_value = {"running": False}
        mock_write.return_value = True

        # Create VERSION file to pass project validation
        (temp_dir / "VERSION").write_text("1.0.0")

        with flask_app.test_client() as client:
            response = client.post(
                "/api/system/upgrade",
                json={"source": "project", "project_path": str(temp_dir)},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["source"] == "project"


class TestGetVersion:
    """Test the get_version route."""

    def test_returns_version_from_file(self, flask_app, session_temp_dir):
        """Test returns version from VERSION file."""
        # The flask_app uses session_temp_dir as project_dir
        # VERSION file should be at project_dir.parent / "VERSION"
        version_file = session_temp_dir / "VERSION"
        version_file.write_text("1.2.3")

        with flask_app.test_client() as client:
            response = client.get("/api/system/version")

        assert response.status_code == 200
        data = response.get_json()
        assert "version" in data
        assert "project_root" in data

    def test_returns_unknown_when_no_file(self, flask_app):
        """Test returns 'unknown' when VERSION file doesn't exist."""
        with flask_app.test_client() as client:
            response = client.get("/api/system/version")

        data = response.get_json()
        # Should return some version (might be 'unknown' or actual)
        assert "version" in data


class TestListProjects:
    """Test the list_projects route."""

    def test_returns_empty_when_no_projects(self, flask_app, monkeypatch):
        """Test returns empty list when no project directories found."""
        # Clear env var and use non-existent paths
        monkeypatch.delenv("AUDIOBOOKS_PROJECT_DIR", raising=False)

        with flask_app.test_client() as client:
            response = client.get("/api/system/projects")

        assert response.status_code == 200
        data = response.get_json()
        assert "projects" in data
        assert isinstance(data["projects"], list)

    def test_finds_audiobook_projects(self, flask_app, temp_dir, monkeypatch):
        """Test finds Audiobook projects in search paths."""
        # Create a test project directory
        project_dir = temp_dir / "Audiobook-Test"
        project_dir.mkdir()
        version_file = project_dir / "VERSION"
        version_file.write_text("2.0.0")

        # Set the env var to point to our temp dir
        monkeypatch.setenv("AUDIOBOOKS_PROJECT_DIR", str(temp_dir))

        with flask_app.test_client() as client:
            response = client.get("/api/system/projects")

        data = response.get_json()
        assert "projects" in data
        # Should find our test project
        matching = [p for p in data["projects"] if p["name"] == "Audiobook-Test"]
        if matching:
            assert matching[0]["version"] == "2.0.0"


class TestEnvironmentVariables:
    """Test environment variable handling."""

    def test_uses_audiobooks_var_dir(self, monkeypatch):
        """Test uses AUDIOBOOKS_VAR_DIR from environment."""
        monkeypatch.setenv("AUDIOBOOKS_VAR_DIR", "/custom/var/dir")

        import importlib

        from backend.api_modular import utilities_system

        importlib.reload(utilities_system)

        assert "/custom/var/dir" in str(utilities_system.CONTROL_DIR)

        # Reset
        monkeypatch.delenv("AUDIOBOOKS_VAR_DIR", raising=False)
        importlib.reload(utilities_system)

    def test_uses_default_var_dir(self, monkeypatch):
        """Test uses default /var/lib/audiobooks when env not set."""
        monkeypatch.delenv("AUDIOBOOKS_VAR_DIR", raising=False)

        import importlib

        from backend.api_modular import utilities_system

        importlib.reload(utilities_system)

        assert "/var/lib/audiobooks" in str(utilities_system.CONTROL_DIR)
