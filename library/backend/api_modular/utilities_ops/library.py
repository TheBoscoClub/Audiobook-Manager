"""
Library content management operations.

Handles adding new audiobooks, rescanning the library, and reimporting to database.
"""

import re
import sys
import threading

from flask import Blueprint, jsonify, request
from operation_status import create_progress_callback, get_tracker

from ..auth import admin_if_enabled
from ..core import FlaskResponse
from ._subprocess import run_with_progress

utilities_ops_library_bp = Blueprint("utilities_ops_library", __name__)

# Strip ANSI escape codes before regex matching
_ansi_escape = re.compile(r"\033\[[0-9;]*m")


def init_library_routes(db_path, project_root):
    """Initialize library management routes."""

    @utilities_ops_library_bp.route("/api/utilities/add-new", methods=["POST"])
    @admin_if_enabled
    def add_new_audiobooks_endpoint() -> FlaskResponse:
        """
        Add new audiobooks incrementally (only files not in database).
        Runs in background thread with progress tracking.
        """
        tracker = get_tracker()

        # Check if already running
        existing = tracker.is_operation_running("add_new")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Add operation already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        # Create operation
        operation_id = tracker.create_operation(
            "add_new", "Adding new audiobooks to database"
        )

        # Get options from request
        data = request.get_json() or {}
        calculate_hashes = data.get("calculate_hashes", True)

        def run_add_new():
            """Background thread function."""
            tracker.start_operation(operation_id)
            progress_cb = create_progress_callback(operation_id)

            try:
                # Import here to avoid circular imports
                sys.path.insert(0, str(project_root / "scanner"))
                from add_new_audiobooks import (
                    AUDIOBOOK_DIR,
                    COVER_DIR,
                    add_new_audiobooks,
                )

                results = add_new_audiobooks(
                    library_dir=AUDIOBOOK_DIR,
                    db_path=db_path,
                    cover_dir=COVER_DIR,
                    calculate_hashes=calculate_hashes,
                    progress_callback=progress_cb,
                )

                tracker.complete_operation(operation_id, results)

            except Exception as e:
                import traceback

                traceback.print_exc()
                tracker.fail_operation(operation_id, str(e))

        # Start background thread
        thread = threading.Thread(target=run_add_new, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Add operation started",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_library_bp.route("/api/utilities/rescan-async", methods=["POST"])
    @admin_if_enabled
    def rescan_library_async() -> FlaskResponse:
        """
        Trigger a library rescan with progress tracking.
        This is the async version that runs in background.
        """
        tracker = get_tracker()

        # Check if already running
        existing = tracker.is_operation_running("rescan")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Rescan already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation("rescan", "Scanning audiobook library")

        def run_rescan():
            tracker.start_operation(operation_id)
            scanner_path = project_root / "scanner" / "scan_audiobooks.py"

            files_found = 0
            last_progress = 5

            progress_pattern = re.compile(r"(\d+)%\s*\|\s*(\d+)/(\d+)")

            def on_line(buf):
                nonlocal files_found, last_progress
                clean = _ansi_escape.sub("", buf)
                match = progress_pattern.search(clean)
                if match:
                    percent = int(match.group(1))
                    current = int(match.group(2))
                    total = int(match.group(3))
                    files_found = total
                    if percent > last_progress:
                        scaled = 5 + int(percent * 0.9)
                        tracker.update_progress(
                            operation_id,
                            scaled,
                            f"Scanning: {current}/{total} files ({percent}%)",
                        )
                        last_progress = percent
                if "Total files:" in buf or "Total audiobooks:" in buf:
                    try:
                        files_found = int(buf.split(":")[1].strip())
                    except (ValueError, IndexError):
                        pass

            try:
                tracker.update_progress(operation_id, 5, "Starting scanner...")

                result = run_with_progress(
                    [sys.executable, "-u", str(scanner_path)],
                    line_callback=on_line,
                    timeout_secs=7200,  # 2 hours for large libraries
                    operation_name="Scan",
                )

                if result["timed_out"]:
                    tracker.fail_operation(operation_id, result["error"])
                elif result["success"]:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "files_found": files_found,
                            "output": result["output"],
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result["error"] or "Scanner failed"
                    )

            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_rescan, daemon=True)
        thread.start()

        return jsonify(
            {"success": True, "message": "Rescan started", "operation_id": operation_id}
        )

    @utilities_ops_library_bp.route("/api/utilities/reimport-async", methods=["POST"])
    @admin_if_enabled
    def reimport_database_async() -> FlaskResponse:
        """Reimport audiobooks to database with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("reimport")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Reimport already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "reimport", "Importing audiobooks to database"
        )

        def run_reimport():
            tracker.start_operation(operation_id)
            import_path = project_root / "backend" / "import_to_db.py"

            imported_count = 0
            total_audiobooks = 0
            last_progress = 2

            found_pattern = re.compile(r"Found\s+(\d+)\s+audiobooks")
            processed_pattern = re.compile(r"Processed\s+(\d+)/(\d+)\s+audiobooks")
            imported_pattern = re.compile(r"Imported\s+(\d+)\s+audiobooks")
            optimizing_pattern = re.compile(r"Optimizing database")

            def on_line(line):
                nonlocal imported_count, total_audiobooks, last_progress
                line = line.strip()
                if not line:
                    return

                match = found_pattern.search(line)
                if match:
                    total_audiobooks = int(match.group(1))
                    tracker.update_progress(
                        operation_id,
                        5,
                        f"Found {total_audiobooks:,} audiobooks to import",
                    )
                    last_progress = 5
                    return

                match = processed_pattern.search(line)
                if match:
                    current = int(match.group(1))
                    total = int(match.group(2))
                    if total > 0:
                        progress = 10 + int((current / total) * 75)
                        if progress > last_progress:
                            tracker.update_progress(
                                operation_id,
                                progress,
                                f"Importing: {current:,}/{total:,} audiobooks",
                            )
                            last_progress = progress
                    return

                if "Preserving existing metadata" in line:
                    tracker.update_progress(
                        operation_id, 8, "Preserving existing metadata..."
                    )
                    return

                match = imported_pattern.search(line)
                if match:
                    imported_count = int(match.group(1))
                    tracker.update_progress(
                        operation_id, 90, f"Imported {imported_count:,} audiobooks"
                    )
                    last_progress = 90
                    return

                if optimizing_pattern.search(line):
                    tracker.update_progress(
                        operation_id, 95, "Optimizing database..."
                    )
                    last_progress = 95
                    return

                if "Creating database" in line:
                    tracker.update_progress(
                        operation_id, 3, "Creating database schema..."
                    )
                    return

                if "Database schema created" in line:
                    tracker.update_progress(
                        operation_id, 5, "Database schema ready"
                    )

            try:
                tracker.update_progress(operation_id, 2, "Starting database import...")

                result = run_with_progress(
                    [sys.executable, "-u", str(import_path)],
                    line_callback=on_line,
                    timeout_secs=1800,  # 30 minutes
                    operation_name="Import",
                )

                if result["timed_out"]:
                    tracker.fail_operation(operation_id, result["error"])
                elif result["success"]:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "imported_count": imported_count,
                            "total_audiobooks": total_audiobooks,
                            "output": result["output"],
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result["error"] or "Import failed"
                    )

            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_reimport, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Reimport started",
                "operation_id": operation_id,
            }
        )

    return utilities_ops_library_bp
