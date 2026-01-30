"""
System maintenance operations.

Handles queue rebuilding, index cleanup, sort field population, and duplicate detection.
"""

import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from config import AUDIOBOOKS_DATABASE
from flask import Blueprint, jsonify, request
from operation_status import get_tracker

from ..auth import admin_if_enabled
from ..core import FlaskResponse

utilities_ops_maintenance_bp = Blueprint("utilities_ops_maintenance", __name__)

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")


def init_maintenance_routes(project_root):
    """Initialize maintenance operation routes."""

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/rebuild-queue-async", methods=["POST"]
    )
    @admin_if_enabled
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
                tracker.update_progress(
                    operation_id, 5, "Scanning source directory..."
                )

                # Use Popen for streaming progress
                process = subprocess.Popen(
                    ["bash", str(script_path), "--rebuild"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "TERM": "dumb"},
                )

                output_lines = []
                queue_size = 0
                files_scanned = 0
                last_progress = 5

                # Patterns for queue build output
                scanning_pattern = re.compile(r"(?:Scanning|Processing).*?(\d+)")
                found_pattern = re.compile(r"Found\s*(\d+)\s*(?:files|items)")
                queue_pattern = re.compile(r"Queue.*?(\d+)")

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for scanning progress
                        match = scanning_pattern.search(line)
                        if match:
                            files_scanned = int(match.group(1))
                            progress = min(5 + (files_scanned // 50), 80)
                            if progress > last_progress:
                                tracker.update_progress(
                                    operation_id,
                                    progress,
                                    f"Scanning files: {files_scanned} processed",
                                )
                                last_progress = progress

                        # Check for found files
                        match = found_pattern.search(line)
                        if match:
                            found = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                85,
                                f"Found {found} files to process",
                            )

                        # Check for queue size
                        match = queue_pattern.search(line)
                        if match:
                            queue_size = int(match.group(1))

                process.wait(timeout=300)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "queue_size": queue_size,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, stderr or "Queue rebuild failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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
    @admin_if_enabled
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
                    operation_id, 5, "Loading index files..."
                )

                cmd = ["bash", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                # Use Popen for streaming progress
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "TERM": "dumb"},
                )

                output_lines = []
                removed_count = 0
                checked_count = 0
                last_progress = 5

                # Patterns for cleanup output
                checking_pattern = re.compile(r"(?:Checking|Verifying).*?(\d+)")
                progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
                removed_pattern = re.compile(
                    r"(?:removed|would remove|stale)\D*(\d+)", re.I
                )

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for [X/Y] progress
                        match = progress_pattern.search(line)
                        if match:
                            current = int(match.group(1))
                            total = int(match.group(2))
                            if total > 0:
                                progress = 5 + int((current / total) * 85)
                                if progress > last_progress:
                                    tracker.update_progress(
                                        operation_id,
                                        progress,
                                        f"Checking entries: {current}/{total}",
                                    )
                                    last_progress = progress
                            continue

                        # Check for checking progress
                        match = checking_pattern.search(line)
                        if match:
                            checked_count = int(match.group(1))
                            progress = min(5 + (checked_count // 100), 85)
                            if progress > last_progress:
                                tracker.update_progress(
                                    operation_id,
                                    progress,
                                    f"Verified {checked_count} entries",
                                )
                                last_progress = progress

                        # Check for removed count
                        match = removed_pattern.search(line)
                        if match:
                            removed_count = int(match.group(1))

                process.wait(timeout=600)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
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
                        operation_id, stderr or "Cleanup failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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
    @admin_if_enabled
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
                    operation_id, 5, "Loading audiobooks from database..."
                )

                cmd = ["python3", "-u", str(script_path)]  # -u for unbuffered
                if not dry_run:
                    cmd.append("--execute")

                # Use Popen for streaming progress
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                output_lines = []
                updated_count = 0
                processed_count = 0
                last_progress = 5

                # Patterns for sort field output
                loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)
                progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
                processing_pattern = re.compile(r"Processing.*?(\d+)")
                update_pattern = re.compile(
                    r"(?:would update|updated)\s*(\d+)", re.I
                )

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for loading count
                        match = loading_pattern.search(line)
                        if match:
                            total = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                10,
                                f"Found {total} audiobooks to process",
                            )
                            continue

                        # Check for [X/Y] progress
                        match = progress_pattern.search(line)
                        if match:
                            current = int(match.group(1))
                            total = int(match.group(2))
                            if total > 0:
                                progress = 10 + int((current / total) * 80)
                                if progress > last_progress:
                                    tracker.update_progress(
                                        operation_id,
                                        progress,
                                        f"Analyzing: {current}/{total}",
                                    )
                                    last_progress = progress
                            continue

                        # Check for processing progress
                        match = processing_pattern.search(line)
                        if match:
                            processed_count = int(match.group(1))
                            progress = min(10 + (processed_count // 20), 85)
                            if progress > last_progress:
                                tracker.update_progress(
                                    operation_id,
                                    progress,
                                    f"Processed {processed_count} titles",
                                )
                                last_progress = progress

                        # Check for update count
                        match = update_pattern.search(line)
                        if match:
                            updated_count = int(match.group(1))

                process.wait(timeout=300)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
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
                        operation_id, stderr or "Sort field population failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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
    @admin_if_enabled
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
            library_script = (
                project_root.parent / "rnd" / "populate_asins_from_library.py"
            )
            # Use a secure temp file to avoid race conditions (CVE: insecure-temporary-file)
            # mkstemp() atomically creates the file, preventing TOCTOU race conditions
            fd, library_export_str = tempfile.mkstemp(suffix=".json", prefix="audible-export-")
            os.close(fd)  # Close the file descriptor, subprocess will write to it
            library_export = Path(library_export_str)

            try:
                # Step 1: Export Audible library from Amazon
                tracker.update_progress(
                    operation_id, 5, "Connecting to Audible API..."
                )

                # Call audible-cli directly via Python module
                # This bypasses the wrapper script and PATH issues
                try:
                    export_result = subprocess.run(
                        [
                            "python3", "-m", "audible_cli",
                            "library", "export",
                            "--format", "json",
                            "--output", str(library_export),
                            "--timeout", "120",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        env={
                            **os.environ,
                            # HOME for audible to find ~/.audible config
                            "HOME": os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks"),
                            "AUDIBLE_CONFIG_DIR": "/etc/audiobooks/audible",
                        },
                    )
                except subprocess.TimeoutExpired:
                    tracker.fail_operation(
                        operation_id,
                        "Audible export timed out after 5 minutes",
                    )
                    return

                if export_result.returncode != 0:
                    error_msg = export_result.stderr or export_result.stdout or "Unknown error"
                    tracker.fail_operation(
                        operation_id,
                        f"Failed to export Audible library (code {export_result.returncode}): {error_msg}",
                    )
                    return

                # Verify export file was created
                if not library_export.exists():
                    tracker.fail_operation(
                        operation_id,
                        "Audible export completed but output file not found",
                    )
                    return

                tracker.update_progress(
                    operation_id, 30, "Library exported, starting match process..."
                )

                # Step 2: Match using library export (conservative threshold)
                cmd = ["python3", "-u", str(library_script)]
                cmd.extend(["--library", str(library_export)])
                cmd.extend(["--db", str(AUDIOBOOKS_DATABASE)])
                cmd.extend(["--threshold", "0.6"])  # Conservative threshold
                if dry_run:
                    cmd.append("--dry-run")

                # Use Popen for matching step
                match_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                output_lines = []
                matched_count = 0
                unmatched_count = 0
                last_progress = 30

                # Patterns for matching output
                progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
                matched_pattern = re.compile(r"Matched:\s*(\d+)")
                unmatched_pattern = re.compile(r"Unmatched:\s*(\d+)")
                processing_pattern = re.compile(r"(?:Processing|Matching).*?(\d+)")

                for line in iter(match_process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for [X/Y] progress
                        match = progress_pattern.search(line)
                        if match:
                            current = int(match.group(1))
                            total = int(match.group(2))
                            if total > 0:
                                progress = 30 + int((current / total) * 60)
                                if progress > last_progress:
                                    tracker.update_progress(
                                        operation_id,
                                        progress,
                                        f"Matching: {current}/{total} audiobooks",
                                    )
                                    last_progress = progress
                            continue

                        # Check for processing progress
                        match = processing_pattern.search(line)
                        if match:
                            count = int(match.group(1))
                            progress = min(30 + (count // 10), 85)
                            if progress > last_progress:
                                tracker.update_progress(
                                    operation_id,
                                    progress,
                                    f"Processing audiobook {count}",
                                )
                                last_progress = progress

                        # Check for matched count
                        match = matched_pattern.search(line)
                        if match:
                            matched_count = int(match.group(1))

                        # Check for unmatched count
                        match = unmatched_pattern.search(line)
                        if match:
                            unmatched_count = int(match.group(1))

                match_process.wait(timeout=300)
                stderr = match_process.stderr.read()
                output = "\n".join(output_lines)

                if match_process.returncode == 0:
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
                        operation_id, stderr or "ASIN population failed"
                    )

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "ASIN population timed out")
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
    @admin_if_enabled
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
                tracker.update_progress(
                    operation_id, 5, "Scanning source directory..."
                )

                cmd = ["bash", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                # Use Popen for streaming progress
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "TERM": "dumb"},
                )

                output_lines = []
                duplicates_found = 0
                files_scanned = 0
                last_progress = 5

                # Patterns for duplicate scan output
                scanning_pattern = re.compile(r"(?:Scanning|Checking).*?(\d+)")
                progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
                found_pattern = re.compile(r"Found\s*(\d+)\s*(?:files|sources)")
                duplicate_pattern = re.compile(r"(?:duplicate|dup).*?(\d+)", re.I)

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for [X/Y] progress
                        match = progress_pattern.search(line)
                        if match:
                            current = int(match.group(1))
                            total = int(match.group(2))
                            if total > 0:
                                progress = 5 + int((current / total) * 85)
                                if progress > last_progress:
                                    tracker.update_progress(
                                        operation_id,
                                        progress,
                                        f"Comparing: {current}/{total} files",
                                    )
                                    last_progress = progress
                            continue

                        # Check for scanning progress
                        match = scanning_pattern.search(line)
                        if match:
                            files_scanned = int(match.group(1))
                            progress = min(5 + (files_scanned // 50), 80)
                            if progress > last_progress:
                                tracker.update_progress(
                                    operation_id,
                                    progress,
                                    f"Scanned {files_scanned} files",
                                )
                                last_progress = progress

                        # Check for found files
                        match = found_pattern.search(line)
                        if match:
                            found = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                20,
                                f"Found {found} source files to analyze",
                            )

                        # Check for duplicates
                        match = duplicate_pattern.search(line)
                        if match:
                            duplicates_found = int(match.group(1))

                process.wait(timeout=600)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
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
                        operation_id, stderr or "Duplicate scan failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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
