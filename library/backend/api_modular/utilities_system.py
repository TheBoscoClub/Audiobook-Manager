"""
System administration utilities - service control and application upgrades.

Uses a privilege-separated helper service pattern:
- API writes request to $AUDIOBOOKS_VAR_DIR/.control/upgrade-request
- audiobook-upgrade-helper.path unit detects the file
- audiobook-upgrade-helper.service runs operations with root privileges
- API polls $AUDIOBOOKS_VAR_DIR/.control/upgrade-status for progress

Using $AUDIOBOOKS_VAR_DIR/.control/ because:
- It's in the API's ReadWritePaths (works with ProtectSystem=strict)
- The audiobooks user owns $AUDIOBOOKS_VAR_DIR
- Avoids /run namespace isolation issues with systemd sandboxing

This allows the API to run with NoNewPrivileges=yes while still supporting
privileged operations like service control and application upgrades.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from .auth import admin_if_enabled, auth_if_enabled, localhost_only
from .core import FlaskResponse

utilities_system_bp = Blueprint("utilities_system", __name__)

# Paths for privilege-separated helper communication
# Using $AUDIOBOOKS_VAR_DIR/.control/ to avoid /run namespace issues with sandboxing
_var_dir = os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
CONTROL_DIR = Path(_var_dir) / ".control"
HELPER_REQUEST_FILE = CONTROL_DIR / "upgrade-request"
HELPER_STATUS_FILE = CONTROL_DIR / "upgrade-status"


def _ensure_control_dir():
    """Ensure control directory exists and is writable."""
    if not CONTROL_DIR.exists():
        try:
            CONTROL_DIR.mkdir(mode=0o755, parents=True)
        except PermissionError:
            pass  # Will fail if not owner, but helper or upgrade will create it


def _write_request(request_data: dict) -> bool:
    """Write a request for the privileged helper to process."""
    _ensure_control_dir()

    # Clear any stale status (truncate instead of delete - more permission-friendly)
    if HELPER_STATUS_FILE.exists():
        try:
            # Try to truncate the file instead of deleting
            HELPER_STATUS_FILE.write_text("")
        except (PermissionError, OSError):
            # If we can't even truncate, just leave it - helper will overwrite
            pass

    try:
        # Write request file - this triggers the path unit
        HELPER_REQUEST_FILE.write_text(json.dumps(request_data))
        return True
    except PermissionError:
        return False
    except Exception:
        return False


def _read_status() -> dict:
    """Read the current status from the helper."""
    default_status = {
        "running": False,
        "stage": "",
        "message": "",
        "success": None,
        "output": [],
        "result": None,
    }

    if not HELPER_STATUS_FILE.exists():
        return default_status

    try:
        content = HELPER_STATUS_FILE.read_text()
        status = json.loads(content)
        return status
    except (json.JSONDecodeError, PermissionError):
        return default_status


def _wait_for_completion(timeout: float = 30.0, poll_interval: float = 0.5) -> dict:
    """
    Wait for the helper to complete and return final status.
    Used for synchronous operations like single service control.
    """
    start = time.time()

    # Wait for valid status file (not empty, valid JSON, has 'success' field)
    while (time.time() - start) < timeout:
        if HELPER_STATUS_FILE.exists():
            try:
                content = HELPER_STATUS_FILE.read_text().strip()
                if content:  # Not empty
                    status = json.loads(content)
                    # Only return if we have a completed operation (success is not None)
                    if status.get("success") is not None and not status.get(
                        "running", True
                    ):
                        return status
            except (json.JSONDecodeError, PermissionError, OSError):
                pass  # Keep waiting
        time.sleep(poll_interval)

    return {
        "running": False,
        "stage": "timeout",
        "message": "Operation timed out",
        "success": False,
        "output": [],
        "result": None,
    }


def init_system_routes(project_root):
    """Initialize system administration routes."""

    # List of services that can be controlled
    # Note: audiobook-api and audiobook-proxy are intentionally excluded -
    # they are core infrastructure that should not be stopped via the UI
    SERVICES = [
        "audiobook-converter",
        "audiobook-mover",
        "audiobook-downloader.timer",
    ]

    # =========================================================================
    # Service Status Endpoint (read-only, no privilege needed)
    # =========================================================================

    @utilities_system_bp.route("/api/system/services", methods=["GET"])
    @localhost_only
    @admin_if_enabled
    def get_services_status() -> FlaskResponse:
        """Get status of all audiobook services."""
        services = []
        for service in SERVICES:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                is_active = result.stdout.strip() == "active"

                # Get enabled status
                result_enabled = subprocess.run(
                    ["systemctl", "is-enabled", service],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                is_enabled = result_enabled.stdout.strip() == "enabled"

                services.append(
                    {
                        "name": service,
                        "active": is_active,
                        "enabled": is_enabled,
                        "status": result.stdout.strip(),
                    }
                )
            except subprocess.TimeoutExpired:
                services.append(
                    {
                        "name": service,
                        "active": False,
                        "enabled": False,
                        "status": "timeout",
                        "error": "Timeout checking service status",
                    }
                )
            except Exception:
                import logging

                logging.exception("Error checking service status for %s", service)
                services.append(
                    {
                        "name": service,
                        "active": False,
                        "enabled": False,
                        "status": "error",
                        "error": "Service status check failed",
                    }
                )

        return jsonify(
            {
                "services": services,
                "all_active": all(s["active"] for s in services),
            }
        )

    # =========================================================================
    # Service Control Endpoints (via privileged helper)
    # =========================================================================

    @utilities_system_bp.route(
        "/api/system/services/<service_name>/start", methods=["POST"]
    )
    @localhost_only
    @admin_if_enabled
    def start_service(service_name: str) -> FlaskResponse:
        """Start a specific service."""
        if service_name not in SERVICES:
            return jsonify({"error": f"Unknown service: {service_name}"}), 400

        if not _write_request({"type": "service_start", "service": service_name}):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        status = _wait_for_completion(timeout=30.0)

        if status.get("success"):
            return jsonify({"success": True, "message": f"Started {service_name}"})
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": status.get("message", "Failed to start service"),
                    }
                ),
                500,
            )

    @utilities_system_bp.route(
        "/api/system/services/<service_name>/stop", methods=["POST"]
    )
    @localhost_only
    @admin_if_enabled
    def stop_service(service_name: str) -> FlaskResponse:
        """Stop a specific service."""
        if service_name not in SERVICES:
            return jsonify({"error": f"Unknown service: {service_name}"}), 400

        if not _write_request({"type": "service_stop", "service": service_name}):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        status = _wait_for_completion(timeout=30.0)

        if status.get("success"):
            return jsonify({"success": True, "message": f"Stopped {service_name}"})
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": status.get("message", "Failed to stop service"),
                    }
                ),
                500,
            )

    @utilities_system_bp.route(
        "/api/system/services/<service_name>/restart", methods=["POST"]
    )
    @localhost_only
    @admin_if_enabled
    def restart_service(service_name: str) -> FlaskResponse:
        """Restart a specific service."""
        if service_name not in SERVICES:
            return jsonify({"error": f"Unknown service: {service_name}"}), 400

        if not _write_request({"type": "service_restart", "service": service_name}):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        status = _wait_for_completion(timeout=30.0)

        if status.get("success"):
            return jsonify({"success": True, "message": f"Restarted {service_name}"})
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": status.get("message", "Failed to restart service"),
                    }
                ),
                500,
            )

    @utilities_system_bp.route("/api/system/services/start-all", methods=["POST"])
    @localhost_only
    @admin_if_enabled
    def start_all_services() -> FlaskResponse:
        """Start all audiobook services."""
        if not _write_request({"type": "services_start_all"}):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        status = _wait_for_completion(timeout=60.0)

        result = status.get("result") or {}
        return jsonify(
            {
                "success": status.get("success", False),
                "results": result.get("results", []),
                "message": status.get("message", ""),
            }
        )

    @utilities_system_bp.route("/api/system/services/stop-all", methods=["POST"])
    @localhost_only
    @admin_if_enabled
    def stop_all_services() -> FlaskResponse:
        """Stop audiobook services. By default keeps API and proxy for web access."""
        include_api = request.args.get("include_api", "false").lower() == "true"

        if not _write_request(
            {"type": "services_stop_all", "include_api": include_api}
        ):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        status = _wait_for_completion(timeout=60.0)

        result = status.get("result") or {}
        return jsonify(
            {
                "success": status.get("success", False),
                "results": result.get("results", []),
                "note": result.get("note", ""),
                "message": status.get("message", ""),
            }
        )

    # =========================================================================
    # Upgrade Endpoints (via privileged helper, async with polling)
    # =========================================================================

    @utilities_system_bp.route("/api/system/upgrade/status", methods=["GET"])
    @localhost_only
    @admin_if_enabled
    def get_upgrade_status() -> FlaskResponse:
        """Get current upgrade/operation status."""
        status = _read_status()
        return jsonify(status)

    @utilities_system_bp.route("/api/system/upgrade/check", methods=["POST"])
    def check_upgrade() -> FlaskResponse:
        """
        Check for available upgrades (dry-run mode with verbose output).

        This runs the upgrade script with --dry-run to show what would happen
        without making any changes. Returns detailed output including version
        comparison and files that would be updated.

        Request body:
        {
            "source": "github" | "project",
            "project_path": "/path/to/project"  // Required if source is "project"
        }
        """
        # Check if an operation is already running
        current_status = _read_status()
        if current_status.get("running"):
            return jsonify({"error": "An operation is already in progress"}), 400

        data = request.get_json() or {}
        source = data.get("source", "github")
        project_path = data.get("project_path")

        if source == "project" and not project_path:
            return jsonify({"error": "project_path required for project source"}), 400

        if source == "project" and project_path:
            # SECURITY: Validate project_path is a real project directory
            project_path_obj = Path(project_path)
            if not project_path_obj.is_dir():
                return jsonify(
                    {"error": "Project path not found or not a directory"}
                ), 400
            if not (project_path_obj / "VERSION").exists():
                return (
                    jsonify({"error": "Invalid project: no VERSION file found"}),
                    400,
                )

        # Write upgrade check request
        request_data = {"type": "upgrade_check", "source": source}
        if project_path:
            request_data["project_path"] = project_path

        if not _write_request(request_data):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        return jsonify(
            {
                "success": True,
                "message": "Upgrade check started",
                "source": source,
            }
        )

    @utilities_system_bp.route("/api/system/upgrade", methods=["POST"])
    @localhost_only
    @admin_if_enabled
    def start_upgrade() -> FlaskResponse:
        """
        Start an upgrade operation.

        Request body:
        {
            "source": "github" | "project",
            "project_path": "/path/to/project"  // Required if source is "project"
        }
        """
        # Check if an operation is already running
        current_status = _read_status()
        if current_status.get("running"):
            return jsonify({"error": "An operation is already in progress"}), 400

        data = request.get_json() or {}
        source = data.get("source", "github")
        project_path = data.get("project_path")

        if source == "project" and not project_path:
            return jsonify({"error": "project_path required for project source"}), 400

        if source == "project" and project_path:
            # SECURITY: Validate project_path is a real project directory
            # Must exist, be a directory, and contain a VERSION file (valid project marker)
            # CodeQL: Path is validated via is_dir() and VERSION file check before use
            project_path_obj = Path(project_path)
            if not project_path_obj.is_dir():  # lgtm[py/path-injection]
                return jsonify(
                    {"error": "Project path not found or not a directory"}
                ), 400
            # Verify it's an actual audiobooks project (has VERSION file)
            if not (project_path_obj / "VERSION").exists():  # lgtm[py/path-injection]
                return (
                    jsonify({"error": "Invalid project: no VERSION file found"}),
                    400,
                )

        # Write upgrade request
        request_data = {"type": "upgrade", "source": source}
        if project_path:
            request_data["project_path"] = project_path

        if not _write_request(request_data):
            return (
                jsonify({"error": "Failed to write request (permission denied)"}),
                500,
            )

        return jsonify(
            {
                "success": True,
                "message": "Upgrade started",
                "source": source,
            }
        )

    # =========================================================================
    # Version and Project Info (no privilege needed)
    # =========================================================================

    @utilities_system_bp.route("/api/system/version", methods=["GET"])
    @auth_if_enabled
    def get_version() -> FlaskResponse:
        """Get current application version."""
        version_file = Path(project_root).parent / "VERSION"
        try:
            if version_file.exists():
                version = version_file.read_text().strip()
            else:
                version = "unknown"
        except Exception:
            version = "unknown"

        return jsonify(
            {
                "version": version,
                "project_root": str(project_root),
            }
        )

    @utilities_system_bp.route("/api/system/projects", methods=["GET"])
    @localhost_only
    @admin_if_enabled
    def list_projects() -> FlaskResponse:
        """List available project directories for upgrade source."""
        # Check common development project locations
        search_paths = [
            os.environ.get("AUDIOBOOKS_PROJECT_DIR", ""),
            os.path.expanduser("~/ClaudeCodeProjects"),
            "/raid0/ClaudeCodeProjects",
            os.path.expanduser("~/projects"),
            "/opt/projects",
        ]
        projects = []
        seen_paths = set()

        for projects_base in search_paths:
            if not projects_base or not os.path.isdir(projects_base):
                continue

            try:
                for name in sorted(os.listdir(projects_base)):
                    project_path = os.path.join(projects_base, name)
                    if project_path in seen_paths:
                        continue
                    if os.path.isdir(project_path) and name.startswith("Audiobook"):
                        seen_paths.add(project_path)
                        version_file = os.path.join(project_path, "VERSION")
                        version = None
                        if os.path.exists(version_file):
                            try:
                                with open(version_file) as f:
                                    version = f.read().strip()
                            except Exception:
                                pass  # Non-critical: version stays None
                        projects.append(
                            {
                                "name": name,
                                "path": project_path,
                                "version": version,
                            }
                        )
            except Exception:
                continue  # Skip inaccessible directories

        return jsonify({"projects": projects})

    return utilities_system_bp
