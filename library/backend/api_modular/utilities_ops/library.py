"""
Library content management operations.

Handles adding new audiobooks, rescanning the library, and reimporting to database.
"""

import subprocess
import sys
import threading

from flask import Blueprint, jsonify, request
from operation_status import create_progress_callback, get_tracker

from ..auth import admin_if_enabled
from ..core import FlaskResponse

utilities_ops_library_bp = Blueprint("utilities_ops_library", __name__)


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
                from add_new_audiobooks import (AUDIOBOOK_DIR, COVER_DIR,
                                                add_new_audiobooks)

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
            import re

            tracker.start_operation(operation_id)
            scanner_path = project_root / "scanner" / "scan_audiobooks.py"

            try:
                tracker.update_progress(operation_id, 5, "Starting scanner...")

                # Use Popen for streaming progress instead of blocking run()
                process = subprocess.Popen(
                    ["python3", "-u", str(scanner_path)],  # -u for unbuffered
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # Line buffered
                )

                output_lines = []
                files_found = 0
                last_progress = 5

                # Pattern to extract progress: "99% | 1821/1828"
                progress_pattern = re.compile(r"(\d+)%\s*\|\s*(\d+)/(\d+)")

                # Read stdout char-by-char to handle \r progress updates
                # Scanner uses \r for in-place terminal updates, not \n
                buffer = ""
                while True:
                    char = process.stdout.read(1)
                    if not char:  # EOF
                        if buffer:
                            output_lines.append(buffer)
                        break

                    if char in ("\r", "\n"):
                        if buffer:
                            output_lines.append(buffer)

                            # Parse progress from ANSI output
                            match = progress_pattern.search(buffer)
                            if match:
                                percent = int(match.group(1))
                                current = int(match.group(2))
                                total = int(match.group(3))
                                files_found = total

                                # Only update if progress changed (avoid spam)
                                if percent > last_progress:
                                    # Scale to 5-95% range
                                    scaled = 5 + int(percent * 0.9)
                                    tracker.update_progress(
                                        operation_id,
                                        scaled,
                                        f"Scanning: {current}/{total} files ({percent}%)",
                                    )
                                    last_progress = percent

                            # Check for completion message
                            if "Total files:" in buffer or "Total audiobooks:" in buffer:
                                try:
                                    files_found = int(buffer.split(":")[1].strip())
                                except (ValueError, IndexError):
                                    pass

                            buffer = ""
                    else:
                        buffer += char

                process.wait(timeout=1800)
                stderr = process.stderr.read()

                output = "".join(output_lines)

                if process.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "files_found": files_found,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, stderr or "Scanner failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
                tracker.fail_operation(operation_id, "Scan timed out after 30 minutes")
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
        import re

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

            try:
                tracker.update_progress(operation_id, 2, "Starting database import...")

                # Use Popen for streaming progress instead of blocking run()
                process = subprocess.Popen(
                    ["python3", "-u", str(import_path)],  # -u for unbuffered
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                output_lines = []
                imported_count = 0
                total_audiobooks = 0
                last_progress = 2

                # Patterns for import script output
                found_pattern = re.compile(r"Found\s+(\d+)\s+audiobooks")
                processed_pattern = re.compile(r"Processed\s+(\d+)/(\d+)\s+audiobooks")
                imported_pattern = re.compile(r"Imported\s+(\d+)\s+audiobooks")
                _preserved_pattern = re.compile(r"Preserved\s+(\d+)")  # noqa: F841
                optimizing_pattern = re.compile(r"Optimizing database")

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for total count
                        match = found_pattern.search(line)
                        if match:
                            total_audiobooks = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                5,
                                f"Found {total_audiobooks:,} audiobooks to import",
                            )
                            last_progress = 5
                            continue

                        # Check for progress updates (every 100 audiobooks)
                        match = processed_pattern.search(line)
                        if match:
                            current = int(match.group(1))
                            total = int(match.group(2))
                            if total > 0:
                                # Scale progress: 10-85% for main import
                                progress = 10 + int((current / total) * 75)
                                if progress > last_progress:
                                    tracker.update_progress(
                                        operation_id,
                                        progress,
                                        f"Importing: {current:,}/{total:,} audiobooks",
                                    )
                                    last_progress = progress
                            continue

                        # Check for preserved metadata
                        if "Preserving existing metadata" in line:
                            tracker.update_progress(
                                operation_id, 8, "Preserving existing metadata..."
                            )
                            continue

                        # Check for import completion
                        match = imported_pattern.search(line)
                        if match:
                            imported_count = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                90,
                                f"Imported {imported_count:,} audiobooks",
                            )
                            last_progress = 90
                            continue

                        # Check for optimization phase
                        if optimizing_pattern.search(line):
                            tracker.update_progress(
                                operation_id, 95, "Optimizing database..."
                            )
                            last_progress = 95
                            continue

                        # Check for database creation
                        if "Creating database" in line:
                            tracker.update_progress(
                                operation_id, 3, "Creating database schema..."
                            )
                            continue

                        if "Database schema created" in line:
                            tracker.update_progress(
                                operation_id, 5, "Database schema ready"
                            )
                            continue

                process.wait(timeout=600)  # 10 minute timeout
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "imported_count": imported_count,
                            "total_audiobooks": total_audiobooks,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, stderr or "Import failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
                tracker.fail_operation(operation_id, "Import timed out after 10 minutes")
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
