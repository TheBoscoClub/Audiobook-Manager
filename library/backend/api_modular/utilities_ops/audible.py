"""
Audible integration operations.

Handles downloading from Audible and syncing metadata (genres, narrators).
"""

import os
import re
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request
from operation_status import get_tracker

from ..core import FlaskResponse

utilities_ops_audible_bp = Blueprint("utilities_ops_audible", __name__)

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")


def init_audible_routes(project_root):
    """Initialize Audible-related routes."""

    @utilities_ops_audible_bp.route(
        "/api/utilities/download-audiobooks-async", methods=["POST"]
    )
    def download_audiobooks_async() -> FlaskResponse:
        """Download new audiobooks from Audible with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("download")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Download already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "download", "Downloading new audiobooks from Audible"
        )

        def run_download():
            tracker.start_operation(operation_id)

            # Use installed script path
            script_path = Path(f"{_audiobooks_home}/scripts/download-new-audiobooks")
            if not script_path.exists():
                script_path = (
                    project_root.parent / "scripts" / "download-new-audiobooks"
                )

            try:
                tracker.update_progress(operation_id, 5, "Starting download process...")

                result = subprocess.run(
                    ["bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=3600,  # 1 hour timeout for downloads
                    env={**os.environ, "TERM": "dumb"},  # Avoid terminal control chars
                )

                output = result.stdout
                downloaded_count = 0
                for line in output.split("\n"):
                    if "Downloaded" in line or "downloaded" in line:
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                downloaded_count = int(numbers[0])
                        except ValueError:
                            pass  # Non-critical: continue with default count

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "downloaded_count": downloaded_count,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Download failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Download timed out after 1 hour")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_download, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Download started",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_audible_bp.route(
        "/api/utilities/sync-genres-async", methods=["POST"]
    )
    def sync_genres_async() -> FlaskResponse:
        """Sync genres from Audible metadata with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("sync_genres")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Genre sync already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "sync_genres",
            f"Syncing genres from Audible {'(dry run)' if dry_run else ''}",
        )

        def run_sync():
            tracker.start_operation(operation_id)
            script_path = project_root / "scripts" / "populate_genres.py"

            try:
                tracker.update_progress(operation_id, 10, "Loading Audible metadata...")

                cmd = ["python3", str(script_path)]
                if not dry_run:
                    cmd.append("--execute")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )

                output = result.stdout
                updated_count = 0
                for line in output.split("\n"):
                    if "updated" in line.lower() or "would update" in line.lower():
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                updated_count = int(numbers[0])
                        except ValueError:
                            pass  # Non-critical: continue with default count

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "genres_updated": updated_count,
                            "dry_run": dry_run,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Genre sync failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "Genre sync timed out after 10 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_sync, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": f"Genre sync started {'(dry run)' if dry_run else ''}",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_audible_bp.route(
        "/api/utilities/sync-narrators-async", methods=["POST"]
    )
    def sync_narrators_async() -> FlaskResponse:
        """Update narrator info from Audible metadata with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("sync_narrators")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Narrator sync already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "sync_narrators",
            f"Updating narrators from Audible {'(dry run)' if dry_run else ''}",
        )

        def run_sync():
            tracker.start_operation(operation_id)
            script_path = project_root / "scripts" / "update_narrators_from_audible.py"

            try:
                tracker.update_progress(operation_id, 10, "Loading Audible metadata...")

                cmd = ["python3", str(script_path)]
                if not dry_run:
                    cmd.append("--execute")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )

                output = result.stdout
                updated_count = 0
                for line in output.split("\n"):
                    if "updated" in line.lower() or "would update" in line.lower():
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                updated_count = int(numbers[0])
                        except ValueError:
                            pass  # Non-critical: continue with default count

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "narrators_updated": updated_count,
                            "dry_run": dry_run,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Narrator sync failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "Narrator sync timed out after 10 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_sync, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": f"Narrator sync started {'(dry run)' if dry_run else ''}",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_audible_bp.route(
        "/api/utilities/check-audible-prereqs", methods=["GET"]
    )
    def check_audible_prereqs() -> FlaskResponse:
        """Check if Audible library metadata file exists."""
        data_dir = os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks")
        metadata_path = os.path.join(data_dir, "library_metadata.json")

        exists = os.path.isfile(metadata_path)

        return jsonify(
            {
                "library_metadata_exists": exists,
                "library_metadata_path": metadata_path if exists else None,
                "data_dir": data_dir,
            }
        )

    return utilities_ops_audible_bp
