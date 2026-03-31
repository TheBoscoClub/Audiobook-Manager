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
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from .auth import admin_or_localhost
from .core import FlaskResponse

utilities_system_bp = Blueprint("utilities_system", __name__)

# Paths for privilege-separated helper communication
# Using $AUDIOBOOKS_VAR_DIR/.control/ to avoid /run namespace issues with sandboxing
_var_dir = os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
CONTROL_DIR = Path(_var_dir) / ".control"
HELPER_REQUEST_FILE = CONTROL_DIR / "upgrade-request"
HELPER_STATUS_FILE = CONTROL_DIR / "upgrade-status"
PREFLIGHT_FILE = CONTROL_DIR / "upgrade-preflight.json"

# Preflight report is considered stale after this many seconds
PREFLIGHT_STALE_SECONDS = 30 * 60  # 30 minutes

# Module-level state set by init_system_routes
_project_root: str = ""

# List of services that can be controlled
# Note: audiobook-api and audiobook-proxy are intentionally excluded -
# they are core infrastructure that should not be stopped via the UI
SERVICES = [
    "audiobook-converter",
    "audiobook-mover",
    "audiobook-downloader.timer",
]


# =========================================================================
# Private helpers
# =========================================================================


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


def _read_preflight() -> dict | None:
    """Read preflight report and compute staleness.

    Returns the preflight dict with an added 'stale' field, or None if
    no report exists or it cannot be parsed.
    """
    if not PREFLIGHT_FILE.exists():
        return None

    try:
        content = PREFLIGHT_FILE.read_text()
        data = json.loads(content)
    except (json.JSONDecodeError, PermissionError, OSError):
        return None

    # Compute staleness from the timestamp field
    timestamp_str = data.get("timestamp", "")
    stale = True  # Default to stale if we can't parse
    if timestamp_str:
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            stale = age > PREFLIGHT_STALE_SECONDS
        except (ValueError, TypeError):
            stale = True

    data["stale"] = stale
    return data


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


def _check_not_running() -> FlaskResponse | None:
    """Return an error response if an operation is already running, else None."""
    current_status = _read_status()
    if current_status.get("running"):
        return jsonify({"error": "An operation is already in progress"}), 400
    return None


def _validate_project_path_basic(
    project_path: str | None, source: str
) -> FlaskResponse | None:
    """Validate project_path for check_upgrade (basic validation).

    Returns an error response tuple if invalid, or None if OK.
    """
    if source == "project" and not project_path:
        return jsonify({"error": "project_path required for project source"}), 400

    if source == "project" and project_path:
        project_path_obj = Path(project_path)
        if not project_path_obj.is_dir():
            return (
                jsonify({"error": "Project path not found or not a directory"}),
                400,
            )
        if not (project_path_obj / "VERSION").exists():
            return (
                jsonify({"error": "Invalid project: no VERSION file found"}),
                400,
            )
    return None


def _validate_project_path_strict(
    project_path: str | None, source: str
) -> tuple[FlaskResponse | None, str | None]:
    """Validate project_path for start_upgrade (strict security validation).

    Returns (error_response, resolved_path). If error_response is not None,
    resolved_path is None and the error should be returned to the caller.
    """
    if source == "project" and not project_path:
        return (
            jsonify({"error": "project_path required for project source"}),
            400,
        ), None

    if source != "project" or not project_path:
        return None, project_path

    # SECURITY: Validate project_path is a real project directory
    # Resolve symlinks and normalize to prevent path traversal attacks
    project_path_obj = Path(project_path).resolve()
    # Block null bytes and relative path components in the original input
    if "\0" in project_path or ".." in Path(project_path).parts:
        return (jsonify({"error": "Invalid project path"}), 400), None
    if not project_path_obj.is_dir():
        return (
            jsonify({"error": "Project path not found or not a directory"}),
            400,
        ), None
    # Verify it's an actual audiobooks project (has VERSION file)
    version_file = project_path_obj / "VERSION"
    if not version_file.resolve().parent == project_path_obj:
        return (jsonify({"error": "Invalid project path"}), 400), None
    if not version_file.exists():
        return (
            jsonify({"error": "Invalid project: no VERSION file found"}),
            400,
        ), None
    # Use the resolved path from here on
    return None, str(project_path_obj)


def _validate_version_source(version: str | None, source: str) -> FlaskResponse | None:
    """Return error if version is set but source is not 'github'."""
    if version and source != "github":
        return (
            jsonify({"error": "version field is only valid with source 'github'"}),
            400,
        )
    return None


def _build_upgrade_check_request(
    source: str, project_path: str | None, version: str | None
) -> dict:
    """Build the request dict for an upgrade check."""
    request_data = {"type": "upgrade_check", "source": source}
    if project_path:
        request_data["project_path"] = project_path
    if version:
        request_data["version"] = version
    return request_data


def _build_upgrade_request(
    source: str,
    project_path: str | None,
    force: bool,
    major_version: bool,
    version: str | None,
) -> dict:
    """Build the request dict for an upgrade."""
    request_data = {
        "type": "upgrade",
        "source": source,
        "force": force,
        "major_version": major_version,
    }
    if project_path:
        request_data["project_path"] = project_path
    if version:
        request_data["version"] = version
    return request_data


def _check_preflight_gate(force: bool) -> FlaskResponse | None:
    """Enforce the preflight gate for upgrades. Returns error if gate fails."""
    if force:
        return None

    preflight = _read_preflight()
    if preflight is None or preflight.get("stale", True):
        return (
            jsonify(
                {"error": ("Preflight check required. Run 'Check for Updates' first.")}
            ),
            400,
        )
    return None


def _write_request_or_error() -> FlaskResponse:
    """Shared error response for failed request writes."""
    return (
        jsonify({"error": "Failed to write request (permission denied)"}),
        500,
    )


def _get_service_status_entry(service: str) -> dict:
    """Get the status of a single systemd service."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=5,
        )
        is_active = result.stdout.strip() == "active"

        result_enabled = subprocess.run(
            ["systemctl", "is-enabled", service],
            capture_output=True,
            text=True,
            timeout=5,
        )
        is_enabled = result_enabled.stdout.strip() == "enabled"

        return {
            "name": service,
            "active": is_active,
            "enabled": is_enabled,
            "status": result.stdout.strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "name": service,
            "active": False,
            "enabled": False,
            "status": "timeout",
            "error": "Timeout checking service status",
        }
    except Exception:
        logging.exception("Error checking service status for %s", service)
        return {
            "name": service,
            "active": False,
            "enabled": False,
            "status": "error",
            "error": "Service status check failed",
        }


def _service_control(service_name: str, action: str) -> FlaskResponse:
    """Handle start/stop/restart for a single service."""
    if service_name not in SERVICES:
        return jsonify({"error": f"Unknown service: {service_name}"}), 400

    if not _write_request({"type": f"service_{action}", "service": service_name}):
        return _write_request_or_error()

    status = _wait_for_completion(timeout=30.0)
    past_tense = {"start": "Started", "stop": "Stopped", "restart": "Restarted"}
    verb = past_tense.get(action, action.capitalize() + "ed")

    if status.get("success"):
        return jsonify({"success": True, "message": f"{verb} {service_name}"})
    return (
        jsonify(
            {
                "success": False,
                "error": status.get("message", f"Failed to {action} service"),
            }
        ),
        500,
    )


def _read_version_file() -> str:
    """Read version from the VERSION file relative to project_root."""
    version_file = Path(_project_root).parent / "VERSION"
    try:
        if version_file.exists():
            return version_file.read_text().strip()
    except Exception:
        pass
    return "unknown"


def _scan_projects_in_dir(
    base_dir: str,
    seen_paths: set[str],
) -> list[dict]:
    """Scan a single directory for audiobook projects.

    Only called with pre-validated, allowlisted directories — never
    with raw user input.  This separation satisfies static analysis
    taint tracking (CodeQL py/path-injection).
    """
    results: list[dict] = []
    if not base_dir or not os.path.isdir(base_dir):
        return results
    try:
        entries = sorted(os.listdir(base_dir))
    except Exception:
        return results  # Skip inaccessible directories
    for name in entries:
        entry = _scan_single_project(base_dir, name, seen_paths)
        if entry is not None:
            results.append(entry)
    return results


def _scan_single_project(
    base_dir: str,
    name: str,
    seen_paths: set[str],
) -> dict | None:
    """Check if a directory entry is an audiobook project and return its info."""
    proj_path = os.path.join(base_dir, name)
    if proj_path in seen_paths or not os.path.isdir(proj_path):
        return None
    ver_file = os.path.join(proj_path, "VERSION")
    has_version = os.path.exists(ver_file)
    if not has_version and not name.startswith("Audiobook"):
        return None
    seen_paths.add(proj_path)
    version = None
    if has_version:
        try:
            with open(ver_file) as f:
                version = f.read().strip()
        except Exception:
            pass
    return {"name": name, "path": proj_path, "version": version}


def _load_cf_credentials_from_file(token_file: str) -> tuple[str | None, str | None]:
    """Read Cloudflare API key and email from token file."""
    api_key = None
    auth_email = None
    if not os.path.isfile(token_file):
        return api_key, auth_email
    try:
        with open(token_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key == "CF_GLOBAL_API_KEY":
                    api_key = val
                elif key == "CF_AUTH_EMAIL":
                    auth_email = val
    except (PermissionError, OSError):
        pass
    return api_key, auth_email


def _resolve_cf_credentials() -> tuple[str | None, str | None]:
    """Resolve Cloudflare credentials from file then environment."""
    token_file = os.environ.get("CF_TOKEN_FILE", "/etc/audiobooks/cloudflare-api-token")
    api_key, auth_email = _load_cf_credentials_from_file(token_file)

    # Fall back to env vars
    if not api_key:
        api_key = os.environ.get("CF_GLOBAL_API_KEY")
    if not auth_email:
        auth_email = os.environ.get("CF_AUTH_EMAIL")

    return api_key, auth_email


def _execute_cf_purge(zone_id: str, api_key: str, auth_email: str) -> FlaskResponse:
    """Send the purge request to Cloudflare and return a response."""
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache"
    if not url.startswith("https://"):
        return jsonify({"success": False, "error": "Invalid URL scheme"}), 400
    data = b'{"purge_everything":true}'
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("X-Auth-Key", api_key)
    req.add_header("X-Auth-Email", auth_email)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL scheme validated above
            result = json.loads(resp.read())
            if result.get("success"):
                return jsonify({"success": True})
            return jsonify(
                {"success": False, "error": "Cloudflare API returned failure"}
            ), 502
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        logging.error("Cloudflare API error: %s", e)
        return jsonify(
            {"success": False, "error": "Cloudflare API request failed"}
        ), 502
    except TimeoutError:
        return jsonify({"success": False, "error": "Cloudflare API timeout"}), 504


# =========================================================================
# Route handler functions (module-level)
# =========================================================================


@utilities_system_bp.route("/api/system/services", methods=["GET"])
@admin_or_localhost
def get_services_status() -> FlaskResponse:
    """Get status of all audiobook services."""
    services = [_get_service_status_entry(svc) for svc in SERVICES]
    return jsonify(
        {
            "services": services,
            "all_active": all(s["active"] for s in services),
        }
    )


@utilities_system_bp.route(
    "/api/system/services/<service_name>/start", methods=["POST"]
)
@admin_or_localhost
def start_service(service_name: str) -> FlaskResponse:
    """Start a specific service."""
    return _service_control(service_name, "start")


@utilities_system_bp.route("/api/system/services/<service_name>/stop", methods=["POST"])
@admin_or_localhost
def stop_service(service_name: str) -> FlaskResponse:
    """Stop a specific service."""
    return _service_control(service_name, "stop")


@utilities_system_bp.route(
    "/api/system/services/<service_name>/restart", methods=["POST"]
)
@admin_or_localhost
def restart_service(service_name: str) -> FlaskResponse:
    """Restart a specific service."""
    return _service_control(service_name, "restart")


@utilities_system_bp.route("/api/system/services/start-all", methods=["POST"])
@admin_or_localhost
def start_all_services() -> FlaskResponse:
    """Start all audiobook services."""
    if not _write_request({"type": "services_start_all"}):
        return _write_request_or_error()

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
@admin_or_localhost
def stop_all_services() -> FlaskResponse:
    """Stop audiobook services. By default keeps API and proxy for web access."""
    include_api = request.args.get("include_api", "false").lower() == "true"

    if not _write_request({"type": "services_stop_all", "include_api": include_api}):
        return _write_request_or_error()

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


@utilities_system_bp.route("/api/system/upgrade/preflight", methods=["GET"])
@admin_or_localhost
def get_upgrade_preflight() -> FlaskResponse:
    """Get the most recent preflight check report.

    Returns the preflight data with a computed 'stale' field (true if
    the report timestamp is older than 30 minutes), or null if no
    report exists.
    """
    preflight = _read_preflight()
    return jsonify({"preflight": preflight})


@utilities_system_bp.route("/api/system/upgrade/status", methods=["GET"])
@admin_or_localhost
def get_upgrade_status() -> FlaskResponse:
    """Get current upgrade/operation status."""
    status = _read_status()
    return jsonify(status)


@utilities_system_bp.route("/api/system/upgrade/check", methods=["POST"])
@admin_or_localhost
def check_upgrade() -> FlaskResponse:
    """
    Check for available upgrades (dry-run mode with verbose output).

    This runs the upgrade script with --dry-run to show what would happen
    without making any changes. Returns detailed output including version
    comparison and files that would be updated.

    Request body:
    {
        "source": "github" | "project",
        "project_path": "/path/to/project",  // Required if source is "project"
        "version": "7.3.0"  // Optional: specific version to check (github only)
    }
    """
    busy = _check_not_running()
    if busy:
        return busy

    data = request.get_json() or {}
    source = data.get("source", "github")
    project_path = data.get("project_path")
    version = data.get("version")

    path_err = _validate_project_path_basic(project_path, source)
    if path_err:
        return path_err

    ver_err = _validate_version_source(version, source)
    if ver_err:
        return ver_err

    request_data = _build_upgrade_check_request(source, project_path, version)

    if not _write_request(request_data):
        return _write_request_or_error()

    return jsonify(
        {
            "success": True,
            "message": "Upgrade check started",
            "source": source,
        }
    )


@utilities_system_bp.route("/api/system/upgrade", methods=["POST"])
@admin_or_localhost
def start_upgrade() -> FlaskResponse:
    """
    Start an upgrade operation.

    Request body:
    {
        "source": "github" | "project",
        "project_path": "/path/to/project",  // Required if source is "project"
        "force": false,           // Skip preflight gate
        "major_version": false,   // Allow major version upgrades
        "version": "7.3.0"       // Specific version (github source only)
    }
    """
    busy = _check_not_running()
    if busy:
        return busy

    data = request.get_json() or {}
    source = data.get("source", "github")
    project_path = data.get("project_path")
    force = data.get("force", False)
    major_version = data.get("major_version", False)
    version = data.get("version")

    path_err, resolved_path = _validate_project_path_strict(project_path, source)
    if path_err:
        return path_err

    ver_err = _validate_version_source(version, source)
    if ver_err:
        return ver_err

    preflight_err = _check_preflight_gate(force)
    if preflight_err:
        return preflight_err

    request_data = _build_upgrade_request(
        source, resolved_path, force, major_version, version
    )

    if not _write_request(request_data):
        return _write_request_or_error()

    return jsonify(
        {
            "success": True,
            "message": "Upgrade started",
            "source": source,
        }
    )


@utilities_system_bp.route("/api/system/version", methods=["GET"])
def get_version() -> FlaskResponse:
    """Get current application version. No auth required."""
    version = _read_version_file()
    response = {"version": version}
    instance_badge = os.environ.get("INSTANCE_BADGE", "")
    if instance_badge:
        response["instance_badge"] = instance_badge
    return jsonify(response)


@utilities_system_bp.route("/api/system/health", methods=["GET"])
def get_health() -> FlaskResponse:
    """Health check endpoint for monitoring and orchestration.

    No authentication required — monitoring tools need unauthenticated access.
    """
    from flask import current_app

    version = _read_version_file()
    database_path = current_app.config.get("DATABASE_PATH")
    db_ok = Path(database_path).exists() if database_path else False

    return jsonify(
        {
            "status": "ok",
            "version": version,
            "database": db_ok,
        }
    )


@utilities_system_bp.route("/api/system/projects", methods=["GET"])
@admin_or_localhost
def list_projects() -> FlaskResponse:
    """List available project directories for upgrade source."""
    # Allowlisted base directories — only these may be scanned
    allowed_bases = [
        os.environ.get("AUDIOBOOKS_PROJECT_DIR", ""),
        os.path.expanduser("~/projects"),
        "/opt/projects",
    ]
    allowed_bases = [os.path.realpath(p) for p in allowed_bases if p]

    # Accept user-specified base path via query parameter.
    # SECURITY: only accept paths that are under an allowed base.
    user_path = request.args.get("base_path", "").strip()
    extra_base: str | None = None
    if user_path:
        resolved = os.path.realpath(user_path)
        if any(
            resolved == ab or resolved.startswith(ab + os.sep) for ab in allowed_bases
        ):
            extra_base = resolved

    # Scan allowed directories (user-validated path first if any)
    seen: set[str] = set()
    projects: list[dict] = []
    if extra_base:
        projects.extend(_scan_projects_in_dir(extra_base, seen))
    for base in allowed_bases:
        projects.extend(_scan_projects_in_dir(base, seen))

    return jsonify({"projects": projects})


@utilities_system_bp.route("/api/system/purge-cache", methods=["POST"])
@admin_or_localhost
def purge_cdn_cache() -> FlaskResponse:
    """Purge Cloudflare CDN cache for the application domain.

    Reads credentials from CF_TOKEN_FILE (default:
    /etc/audiobooks/cloudflare-api-token) or falls back to
    CF_GLOBAL_API_KEY + CF_AUTH_EMAIL environment variables.
    """
    zone_id = os.environ.get("CF_ZONE_ID", "24558cb1f70c1a803c249d79a56bde7c")
    api_key, auth_email = _resolve_cf_credentials()

    if not api_key or not auth_email:
        return jsonify(
            {
                "success": False,
                "error": "Cloudflare credentials not configured",
            }
        ), 503

    return _execute_cf_purge(zone_id, api_key, auth_email)


# =========================================================================
# Initialization
# =========================================================================


def init_system_routes(project_root):
    """Initialize system administration routes.

    Sets the module-level project_root used by version/health endpoints
    and returns the configured blueprint.
    """
    global _project_root
    _project_root = project_root
    return utilities_system_bp
