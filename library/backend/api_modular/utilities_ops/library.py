"""
Library content management operations.

Handles adding new audiobooks, rescanning the library, and reimporting to database.
"""

import re
import sys

from flask import Blueprint, request
from operation_status import create_progress_callback

from ..auth import admin_if_enabled
from ..core import FlaskResponse
from ._helpers import handle_result, run_async_operation
from ._subprocess import run_with_progress

utilities_ops_library_bp = Blueprint("utilities_ops_library", __name__)

# Strip ANSI escape codes before regex matching
_ansi_escape = re.compile(r"\033\[[0-9;]*m")


def init_library_routes(db_path, project_root):
    """Initialize library management routes."""

    @utilities_ops_library_bp.route("/api/utilities/add-new", methods=["POST"])
    @admin_if_enabled
    def add_new_audiobooks_endpoint() -> FlaskResponse:
        """Add new audiobooks incrementally (only files not in database)."""
        data = request.get_json() or {}
        calculate_hashes = data.get("calculate_hashes", True)

        def work(tracker, operation_id):
            progress_cb = create_progress_callback(operation_id)
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

        return run_async_operation(
            "add_new",
            "Adding new audiobooks to database",
            "Add operation already in progress",
            "Add operation started",
            work,
        )

    @utilities_ops_library_bp.route("/api/utilities/rescan-async", methods=["POST"])
    @admin_if_enabled
    def rescan_library_async() -> FlaskResponse:
        """Trigger a library rescan with progress tracking."""

        def work(tracker, operation_id):
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

            tracker.update_progress(operation_id, 5, "Starting scanner...")
            result = run_with_progress(
                [sys.executable, "-u", str(scanner_path)],
                line_callback=on_line,
                timeout_secs=7200,
                operation_name="Scan",
            )
            handle_result(
                tracker,
                operation_id,
                result,
                {"files_found": files_found, "output": result["output"]},
                "Scanner failed",
            )

        return run_async_operation(
            "rescan",
            "Scanning audiobook library",
            "Rescan already in progress",
            "Rescan started",
            work,
        )

    @utilities_ops_library_bp.route("/api/utilities/reimport-async", methods=["POST"])
    @admin_if_enabled
    def reimport_database_async() -> FlaskResponse:
        """Reimport audiobooks to database with progress tracking."""

        def work(tracker, operation_id):
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

            tracker.update_progress(operation_id, 2, "Starting database import...")
            result = run_with_progress(
                [sys.executable, "-u", str(import_path)],
                line_callback=on_line,
                timeout_secs=1800,
                operation_name="Import",
            )
            handle_result(
                tracker,
                operation_id,
                result,
                {
                    "imported_count": imported_count,
                    "total_audiobooks": total_audiobooks,
                    "output": result["output"],
                },
                "Import failed",
            )

        return run_async_operation(
            "reimport",
            "Importing audiobooks to database",
            "Reimport already in progress",
            "Reimport started",
            work,
        )

    return utilities_ops_library_bp
