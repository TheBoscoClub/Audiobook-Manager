"""
System administration utilities - service control and application upgrades.
"""

import os
import subprocess
import threading
from typing import Optional
from flask import Blueprint, jsonify, request
from pathlib import Path

from .core import FlaskResponse

utilities_system_bp = Blueprint("utilities_system", __name__)

# Track active upgrade operation
_upgrade_thread: Optional[threading.Thread] = None
_upgrade_status = {
    "running": False,
    "stage": "",
    "message": "",
    "success": None,
    "output": [],
}


def init_system_routes(project_root):
    """Initialize system administration routes."""

    # List of services that can be controlled
    SERVICES = [
        "audiobooks-api",
        "audiobooks-proxy",
        "audiobooks-converter",
        "audiobooks-mover",
        "audiobooks-scanner.timer",
    ]

    # =========================================================================
    # Service Control Endpoints
    # =========================================================================

    @utilities_system_bp.route("/api/system/services", methods=["GET"])
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

                services.append({
                    "name": service,
                    "active": is_active,
                    "enabled": is_enabled,
                    "status": result.stdout.strip(),
                })
            except subprocess.TimeoutExpired:
                services.append({
                    "name": service,
                    "active": False,
                    "enabled": False,
                    "status": "timeout",
                    "error": "Timeout checking service status",
                })
            except Exception as e:
                services.append({
                    "name": service,
                    "active": False,
                    "enabled": False,
                    "status": "error",
                    "error": str(e),
                })

        return jsonify({
            "services": services,
            "all_active": all(s["active"] for s in services),
        })

    @utilities_system_bp.route("/api/system/services/<service_name>/start", methods=["POST"])
    def start_service(service_name: str) -> FlaskResponse:
        """Start a specific service."""
        if service_name not in SERVICES:
            return jsonify({"error": f"Unknown service: {service_name}"}), 400

        try:
            result = subprocess.run(
                ["sudo", "systemctl", "start", service_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return jsonify({"success": True, "message": f"Started {service_name}"})
            else:
                return jsonify({
                    "success": False,
                    "error": result.stderr or "Failed to start service"
                }), 500
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Timeout starting service"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @utilities_system_bp.route("/api/system/services/<service_name>/stop", methods=["POST"])
    def stop_service(service_name: str) -> FlaskResponse:
        """Stop a specific service."""
        if service_name not in SERVICES:
            return jsonify({"error": f"Unknown service: {service_name}"}), 400

        try:
            result = subprocess.run(
                ["sudo", "systemctl", "stop", service_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return jsonify({"success": True, "message": f"Stopped {service_name}"})
            else:
                return jsonify({
                    "success": False,
                    "error": result.stderr or "Failed to stop service"
                }), 500
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Timeout stopping service"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @utilities_system_bp.route("/api/system/services/<service_name>/restart", methods=["POST"])
    def restart_service(service_name: str) -> FlaskResponse:
        """Restart a specific service."""
        if service_name not in SERVICES:
            return jsonify({"error": f"Unknown service: {service_name}"}), 400

        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return jsonify({"success": True, "message": f"Restarted {service_name}"})
            else:
                return jsonify({
                    "success": False,
                    "error": result.stderr or "Failed to restart service"
                }), 500
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Timeout restarting service"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @utilities_system_bp.route("/api/system/services/start-all", methods=["POST"])
    def start_all_services() -> FlaskResponse:
        """Start all audiobook services."""
        results = []
        for service in SERVICES:
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "start", service],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                results.append({
                    "service": service,
                    "success": result.returncode == 0,
                    "error": result.stderr if result.returncode != 0 else None,
                })
            except Exception as e:
                results.append({
                    "service": service,
                    "success": False,
                    "error": str(e),
                })

        all_success = all(r["success"] for r in results)
        return jsonify({
            "success": all_success,
            "results": results,
        })

    @utilities_system_bp.route("/api/system/services/stop-all", methods=["POST"])
    def stop_all_services() -> FlaskResponse:
        """Stop audiobook services. By default keeps API and proxy for web access."""
        include_api = request.args.get("include_api", "false").lower() == "true"

        # Stop non-essential services first
        stop_order = [
            "audiobooks-scanner.timer",
            "audiobooks-mover",
            "audiobooks-converter",
        ]

        # If include_api, add API services at the end (proxy first, then API)
        if include_api:
            stop_order.extend(["audiobooks-proxy", "audiobooks-api"])

        results = []
        for service in stop_order:
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "stop", service],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                results.append({
                    "service": service,
                    "success": result.returncode == 0,
                    "error": result.stderr if result.returncode != 0 else None,
                })
            except Exception as e:
                results.append({
                    "service": service,
                    "success": False,
                    "error": str(e),
                })

        all_success = all(r["success"] for r in results)
        note = "All services stopped" if include_api else "API and proxy services kept running for web access"
        return jsonify({
            "success": all_success,
            "results": results,
            "note": note,
        })

    # =========================================================================
    # Upgrade Endpoints
    # =========================================================================

    @utilities_system_bp.route("/api/system/upgrade/status", methods=["GET"])
    def get_upgrade_status() -> FlaskResponse:
        """Get current upgrade status."""
        return jsonify(_upgrade_status)

    @utilities_system_bp.route("/api/system/upgrade", methods=["POST"])
    def start_upgrade() -> FlaskResponse:
        """
        Start an upgrade operation.

        Request body:
        {
            "source": "github" | "project",
            "project_path": "/path/to/project"  // Required if source is "project"
        }
        """
        global _upgrade_thread, _upgrade_status

        if _upgrade_status["running"]:
            return jsonify({"error": "Upgrade already in progress"}), 400

        data = request.get_json() or {}
        source = data.get("source", "github")
        project_path = data.get("project_path")

        if source == "project" and not project_path:
            return jsonify({"error": "project_path required for project source"}), 400

        if source == "project" and not os.path.isdir(project_path):
            return jsonify({"error": f"Project path not found: {project_path}"}), 400

        def run_upgrade():
            global _upgrade_status
            _upgrade_status = {
                "running": True,
                "stage": "starting",
                "message": "Starting upgrade...",
                "success": None,
                "output": [],
            }

            try:
                # Stage 1: Stop services
                _upgrade_status["stage"] = "stopping_services"
                _upgrade_status["message"] = "Stopping services..."

                stop_services = [
                    "audiobooks-scanner.timer",
                    "audiobooks-mover",
                    "audiobooks-converter",
                ]
                for service in stop_services:
                    result = subprocess.run(
                        ["sudo", "systemctl", "stop", service],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    _upgrade_status["output"].append(f"Stopped {service}")

                # Stage 2: Run upgrade script
                _upgrade_status["stage"] = "upgrading"
                _upgrade_status["message"] = "Running upgrade..."

                upgrade_script = "/usr/local/bin/audiobooks-upgrade"
                if source == "github":
                    cmd = ["sudo", upgrade_script, "--from-github"]
                else:
                    cmd = ["sudo", upgrade_script, "--from-project", project_path]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                )

                if result.stdout:
                    for line in result.stdout.split("\n"):
                        if line.strip():
                            _upgrade_status["output"].append(line)

                if result.returncode != 0:
                    _upgrade_status["success"] = False
                    _upgrade_status["message"] = "Upgrade failed"
                    if result.stderr:
                        _upgrade_status["output"].append(f"ERROR: {result.stderr}")
                else:
                    _upgrade_status["success"] = True
                    _upgrade_status["message"] = "Upgrade completed successfully"

                # Stage 3: Start services
                _upgrade_status["stage"] = "starting_services"
                _upgrade_status["message"] = "Starting services..."

                for service in reversed(stop_services):
                    result = subprocess.run(
                        ["sudo", "systemctl", "start", service],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    _upgrade_status["output"].append(f"Started {service}")

                # Stage 4: Restart API (will cause a brief disconnect)
                _upgrade_status["stage"] = "restarting_api"
                _upgrade_status["message"] = "Restarting API (browser will reload)..."

                # Small delay to allow status to be read
                import time
                time.sleep(2)

                # Restart API service - this will kill this process
                subprocess.Popen(
                    ["sudo", "systemctl", "restart", "audiobooks-api"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            except subprocess.TimeoutExpired:
                _upgrade_status["success"] = False
                _upgrade_status["message"] = "Upgrade timed out"
                _upgrade_status["output"].append("ERROR: Operation timed out")
            except Exception as e:
                _upgrade_status["success"] = False
                _upgrade_status["message"] = f"Upgrade failed: {str(e)}"
                _upgrade_status["output"].append(f"ERROR: {str(e)}")
            finally:
                _upgrade_status["running"] = False
                _upgrade_status["stage"] = "complete"

        _upgrade_thread = threading.Thread(target=run_upgrade, daemon=True)
        _upgrade_thread.start()

        return jsonify({
            "success": True,
            "message": "Upgrade started",
            "source": source,
        })

    @utilities_system_bp.route("/api/system/version", methods=["GET"])
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

        return jsonify({
            "version": version,
            "project_root": str(project_root),
        })

    @utilities_system_bp.route("/api/system/projects", methods=["GET"])
    def list_projects() -> FlaskResponse:
        """List available project directories for upgrade source."""
        projects_base = "/raid0/ClaudeCodeProjects"
        projects = []

        try:
            if os.path.isdir(projects_base):
                for name in sorted(os.listdir(projects_base)):
                    project_path = os.path.join(projects_base, name)
                    if os.path.isdir(project_path) and name.startswith("Audiobook"):
                        version_file = os.path.join(project_path, "VERSION")
                        version = None
                        if os.path.exists(version_file):
                            try:
                                with open(version_file) as f:
                                    version = f.read().strip()
                            except Exception:
                                pass
                        projects.append({
                            "name": name,
                            "path": project_path,
                            "version": version,
                        })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        return jsonify({"projects": projects})

    return utilities_system_bp
