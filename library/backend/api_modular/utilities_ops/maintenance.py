"""
System maintenance operations.

Handles queue rebuilding, index cleanup, sort field population, and duplicate detection.
"""

import os
import re
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request

from config import AUDIOBOOKS_DATABASE
from operation_status import get_tracker

from ..core import FlaskResponse

utilities_ops_maintenance_bp = Blueprint("utilities_ops_maintenance", __name__)

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")


def init_maintenance_routes(project_root):
    """Initialize maintenance operation routes."""

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/rebuild-queue-async", methods=["POST"]
    )
    def rebuild_queue_async() -> FlaskResponse:
        """Rebuild the conversion queue with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("rebuild_queue")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Queue rebuild already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "rebuild_queue", "Rebuilding conversion queue"
        )

        def run_rebuild():
            tracker.start_operation(operation_id)

            script_path = Path(f"{_audiobooks_home}/scripts/build-conversion-queue")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "build-conversion-queue"

            try:
                tracker.update_progress(operation_id, 10, "Rebuilding queue...")

                result = subprocess.run(
                    ["bash", str(script_path), "--rebuild"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**os.environ, "TERM": "dumb"},
                )

                output = result.stdout
                queue_size = 0
                for line in output.split("\n"):
                    if "queue" in line.lower() and any(c.isdigit() for c in line):
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                queue_size = int(numbers[-1])
                        except ValueError:
                            pass  # Non-critical: continue with default count

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "queue_size": queue_size,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Queue rebuild failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "Queue rebuild timed out after 5 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_rebuild, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Queue rebuild started",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/cleanup-indexes-async", methods=["POST"]
    )
    def cleanup_indexes_async() -> FlaskResponse:
        """Cleanup stale index entries for deleted files."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("cleanup_indexes")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Index cleanup already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "cleanup_indexes",
            f"Cleaning up stale indexes {'(dry run)' if dry_run else ''}",
        )

        def run_cleanup():
            tracker.start_operation(operation_id)

            script_path = Path(f"{_audiobooks_home}/scripts/cleanup-stale-indexes")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "cleanup-stale-indexes"

            try:
                tracker.update_progress(
                    operation_id, 10, "Scanning indexes for stale entries..."
                )

                cmd = ["bash", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env={**os.environ, "TERM": "dumb"},
                )

                output = result.stdout
                removed_count = 0
                for line in output.split("\n"):
                    if "removed" in line.lower() or "would remove" in line.lower():
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                removed_count += int(numbers[0])
                        except ValueError:
                            pass  # Non-critical: continue with default count

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "entries_removed": removed_count,
                            "dry_run": dry_run,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Cleanup failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "Cleanup timed out after 10 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_cleanup, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": f"Index cleanup started {'(dry run)' if dry_run else ''}",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/populate-sort-fields-async", methods=["POST"]
    )
    def populate_sort_fields_async() -> FlaskResponse:
        """Populate sort fields for proper alphabetization with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("sort_fields")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Sort field population already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "sort_fields", f"Populating sort fields {'(dry run)' if dry_run else ''}"
        )

        def run_populate():
            tracker.start_operation(operation_id)
            script_path = project_root / "scripts" / "populate_sort_fields.py"

            try:
                tracker.update_progress(
                    operation_id, 10, "Analyzing titles and authors..."
                )

                cmd = ["python3", str(script_path)]
                if not dry_run:
                    cmd.append("--execute")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
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
                            "fields_updated": updated_count,
                            "dry_run": dry_run,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Sort field population failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "Sort field population timed out after 5 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_populate, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": f"Sort field population started {'(dry run)' if dry_run else ''}",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/populate-asins-async", methods=["POST"]
    )
    def populate_asins_async() -> FlaskResponse:
        """Populate ASINs by matching local audiobooks against Audible library."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("populate_asins")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "ASIN population already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "populate_asins",
            f"Populating ASINs from Audible {'(dry run)' if dry_run else ''}",
        )

        def run_populate():
            tracker.start_operation(operation_id)
            # Two-step approach using Amazon as source of truth:
            # 1. Export Audible library (gets ASINs directly from Amazon)
            # 2. Match local audiobooks to library entries
            library_script = project_root.parent / "rnd" / "populate_asins_from_library.py"
            library_export = Path("/tmp/audible-library-export.json")

            try:
                # Step 1: Export Audible library from Amazon
                tracker.update_progress(
                    operation_id, 10, "Exporting Audible library from Amazon..."
                )

                export_result = subprocess.run(
                    [
                        "audible", "library", "export",
                        "--format", "json",
                        "--output", str(library_export),
                        "--timeout", "120",
                        "--resolve-podcasts",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if export_result.returncode != 0:
                    tracker.fail_operation(
                        operation_id,
                        f"Failed to export Audible library: {export_result.stderr}",
                    )
                    return

                tracker.update_progress(
                    operation_id, 40, "Matching audiobooks to library..."
                )

                # Step 2: Match using library export (conservative threshold)
                cmd = ["python3", str(library_script)]
                cmd.extend(["--library", str(library_export)])
                cmd.extend(["--db", str(AUDIOBOOKS_DATABASE)])
                cmd.extend(["--threshold", "0.6"])  # Conservative threshold
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                output = result.stdout
                matched_count = 0
                unmatched_count = 0

                # Parse output for match statistics
                for line in output.split("\n"):
                    if "Matched:" in line:
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                matched_count = int(numbers[0])
                        except ValueError:
                            pass
                    elif "Unmatched:" in line:
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                unmatched_count = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "asins_matched": matched_count,
                            "unmatched": unmatched_count,
                            "dry_run": dry_run,
                            "output": output[-3000:] if len(output) > 3000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "ASIN population failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "ASIN population timed out"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_populate, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": f"ASIN population started {'(dry run)' if dry_run else ''}",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/find-source-duplicates-async", methods=["POST"]
    )
    def find_source_duplicates_async() -> FlaskResponse:
        """Find duplicate source files (.aaxc) with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("source_duplicates")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Duplicate scan already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation(
            "source_duplicates",
            f"Finding duplicate source files {'(dry run)' if dry_run else ''}",
        )

        def run_scan():
            tracker.start_operation(operation_id)

            script_path = Path(f"{_audiobooks_home}/scripts/find-duplicate-sources")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "find-duplicate-sources"

            try:
                tracker.update_progress(operation_id, 10, "Scanning source files...")

                cmd = ["bash", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env={**os.environ, "TERM": "dumb"},
                )

                output = result.stdout
                duplicates_found = 0
                for line in output.split("\n"):
                    if "duplicate" in line.lower():
                        try:
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                duplicates_found = int(numbers[0])
                        except ValueError:
                            pass  # Non-critical: continue with default count

                if result.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "duplicates_found": duplicates_found,
                            "dry_run": dry_run,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result.stderr or "Duplicate scan failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(
                    operation_id, "Duplicate scan timed out after 10 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": f"Duplicate scan started {'(dry run)' if dry_run else ''}",
                "operation_id": operation_id,
            }
        )

    return utilities_ops_maintenance_bp
