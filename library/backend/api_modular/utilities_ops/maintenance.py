"""
System maintenance operations.

Handles queue rebuilding, index cleanup, sort field population, and duplicate detection.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from config import AUDIOBOOKS_DATABASE
from flask import Blueprint, request

from ..auth import admin_if_enabled
from ..core import FlaskResponse
from ._helpers import handle_result, run_async_operation
from ._subprocess import run_with_progress

utilities_ops_maintenance_bp = Blueprint("utilities_ops_maintenance", __name__)

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")


def _resolve_script(name, project_root):
    """Resolve a script path, preferring installed location over project."""
    installed = Path(f"{_audiobooks_home}/scripts/{name}")
    if installed.exists():
        return installed
    return project_root.parent / "scripts" / name


def init_maintenance_routes(project_root):
    """Initialize maintenance operation routes."""

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/rebuild-queue-async", methods=["POST"]
    )
    @admin_if_enabled
    def rebuild_queue_async() -> FlaskResponse:
        """Rebuild the conversion queue with progress tracking."""

        def work(tracker, operation_id):
            script_path = _resolve_script("build-conversion-queue", project_root)
            queue_size = 0
            files_scanned = 0
            last_progress = 5

            scanning_pattern = re.compile(r"(?:Scanning|Processing).*?(\d+)")
            found_pattern = re.compile(r"Found\s*(\d+)\s*(?:files|items)")
            queue_pattern = re.compile(r"Queue.*?(\d+)")

            def on_line(line):
                nonlocal queue_size, files_scanned, last_progress
                line = line.strip()
                if not line:
                    return

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

                match = found_pattern.search(line)
                if match:
                    found = int(match.group(1))
                    tracker.update_progress(
                        operation_id, 85, f"Found {found} files to process"
                    )

                match = queue_pattern.search(line)
                if match:
                    queue_size = int(match.group(1))

            tracker.update_progress(operation_id, 5, "Scanning source directory...")
            result = run_with_progress(
                ["bash", str(script_path), "--rebuild"],
                line_callback=on_line,
                timeout_secs=300,
                operation_name="Queue rebuild",
                env={**os.environ, "TERM": "dumb"},
            )
            handle_result(
                tracker,
                operation_id,
                result,
                {"queue_size": queue_size, "output": result["output"]},
                "Queue rebuild failed",
            )

        return run_async_operation(
            "rebuild_queue",
            "Rebuilding conversion queue",
            "Queue rebuild already in progress",
            "Queue rebuild started",
            work,
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/cleanup-indexes-async", methods=["POST"]
    )
    @admin_if_enabled
    def cleanup_indexes_async() -> FlaskResponse:
        """Cleanup stale index entries for deleted files."""
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        def work(tracker, operation_id):
            script_path = _resolve_script("cleanup-stale-indexes", project_root)
            removed_count = 0
            checked_count = 0
            last_progress = 5

            progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
            checking_pattern = re.compile(r"(?:Checking|Verifying).*?(\d+)")
            removed_pattern = re.compile(
                r"(?:removed|would remove|stale)\D*(\d+)", re.I
            )

            def on_line(line):
                nonlocal removed_count, checked_count, last_progress
                line = line.strip()
                if not line:
                    return

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
                    return

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

                match = removed_pattern.search(line)
                if match:
                    removed_count = int(match.group(1))

            cmd = ["bash", str(script_path)]
            if dry_run:
                cmd.append("--dry-run")

            tracker.update_progress(operation_id, 5, "Loading index files...")
            result = run_with_progress(
                cmd,
                line_callback=on_line,
                timeout_secs=600,
                operation_name="Cleanup",
                env={**os.environ, "TERM": "dumb"},
            )
            handle_result(
                tracker,
                operation_id,
                result,
                {
                    "entries_removed": removed_count,
                    "dry_run": dry_run,
                    "output": result["output"],
                },
                "Cleanup failed",
            )

        return run_async_operation(
            "cleanup_indexes",
            f"Cleaning up stale indexes {'(dry run)' if dry_run else ''}",
            "Index cleanup already in progress",
            f"Index cleanup started {'(dry run)' if dry_run else ''}",
            work,
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/populate-sort-fields-async", methods=["POST"]
    )
    @admin_if_enabled
    def populate_sort_fields_async() -> FlaskResponse:
        """Populate sort fields for proper alphabetization with progress tracking."""
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        def work(tracker, operation_id):
            script_path = project_root / "scripts" / "populate_sort_fields.py"
            updated_count = 0
            processed_count = 0
            last_progress = 5

            loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)
            progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
            processing_pattern = re.compile(r"Processing.*?(\d+)")
            update_pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)

            def on_line(line):
                nonlocal updated_count, processed_count, last_progress
                line = line.strip()
                if not line:
                    return

                match = loading_pattern.search(line)
                if match:
                    total = int(match.group(1))
                    tracker.update_progress(
                        operation_id, 10, f"Found {total} audiobooks to process"
                    )
                    return

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
                    return

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

                match = update_pattern.search(line)
                if match:
                    updated_count = int(match.group(1))

            cmd = [sys.executable, "-u", str(script_path)]
            if not dry_run:
                cmd.append("--execute")

            tracker.update_progress(
                operation_id, 5, "Loading audiobooks from database..."
            )
            result = run_with_progress(
                cmd,
                line_callback=on_line,
                timeout_secs=300,
                operation_name="Sort field population",
            )
            handle_result(
                tracker,
                operation_id,
                result,
                {
                    "fields_updated": updated_count,
                    "dry_run": dry_run,
                    "output": result["output"],
                },
                "Sort field population failed",
            )

        return run_async_operation(
            "sort_fields",
            f"Populating sort fields {'(dry run)' if dry_run else ''}",
            "Sort field population already in progress",
            f"Sort field population started {'(dry run)' if dry_run else ''}",
            work,
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/populate-asins-async", methods=["POST"]
    )
    @admin_if_enabled
    def populate_asins_async() -> FlaskResponse:
        """Populate ASINs by matching local audiobooks against Audible library."""
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        def work(tracker, operation_id):
            library_script = (
                project_root
                / "backend"
                / "migrations"
                / "populate_asins_from_library.py"
            )
            fd, library_export_str = tempfile.mkstemp(
                suffix=".json", prefix="audible-export-"
            )
            os.close(fd)
            library_export = Path(library_export_str)

            try:
                # Step 1: Export Audible library from Amazon
                tracker.update_progress(operation_id, 5, "Connecting to Audible API...")
                _audible_home = os.environ.get(
                    "AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks"
                )
                try:
                    export_result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "audible_cli",
                            "library",
                            "export",
                            "--format",
                            "json",
                            "--output",
                            str(library_export),
                            "--timeout",
                            "120",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        env={
                            **os.environ,
                            "HOME": _audible_home,
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
                    error_msg = (
                        export_result.stderr or export_result.stdout or "Unknown error"
                    )
                    tracker.fail_operation(
                        operation_id,
                        f"Failed to export Audible library"
                        f" (code {export_result.returncode}): {error_msg}",
                    )
                    return

                if not library_export.exists():
                    tracker.fail_operation(
                        operation_id,
                        "Audible export completed but output file not found",
                    )
                    return

                tracker.update_progress(
                    operation_id, 30, "Library exported, starting match process..."
                )

                # Step 2: Match using library export
                matched_count = 0
                unmatched_count = 0
                last_progress = 30

                progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
                matched_pattern = re.compile(r"Matched:\s*(\d+)")
                unmatched_pattern = re.compile(r"Unmatched:\s*(\d+)")
                processing_pattern = re.compile(
                    r"(?:Processing|Matching).*?(\d+)"
                )

                def on_line(line):
                    nonlocal matched_count, unmatched_count, last_progress
                    line = line.strip()
                    if not line:
                        return

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
                        return

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

                    match = matched_pattern.search(line)
                    if match:
                        matched_count = int(match.group(1))

                    match = unmatched_pattern.search(line)
                    if match:
                        unmatched_count = int(match.group(1))

                cmd = [sys.executable, "-u", str(library_script)]
                cmd.extend(["--library", str(library_export)])
                cmd.extend(["--db", str(AUDIOBOOKS_DATABASE)])
                cmd.extend(["--threshold", "0.6"])
                if dry_run:
                    cmd.append("--dry-run")

                result = run_with_progress(
                    cmd,
                    line_callback=on_line,
                    timeout_secs=300,
                    operation_name="ASIN matching",
                )
                handle_result(
                    tracker,
                    operation_id,
                    result,
                    {
                        "asins_matched": matched_count,
                        "unmatched": unmatched_count,
                        "dry_run": dry_run,
                        "output": result["output"],
                    },
                    "ASIN population failed",
                )
            finally:
                # Clean up temp file
                try:
                    library_export.unlink(missing_ok=True)
                except OSError:
                    pass

        return run_async_operation(
            "populate_asins",
            f"Populating ASINs from Audible {'(dry run)' if dry_run else ''}",
            "ASIN population already in progress",
            f"ASIN population started {'(dry run)' if dry_run else ''}",
            work,
        )

    @utilities_ops_maintenance_bp.route(
        "/api/utilities/find-source-duplicates-async", methods=["POST"]
    )
    @admin_if_enabled
    def find_source_duplicates_async() -> FlaskResponse:
        """Find duplicate source files (.aaxc) with progress tracking."""
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        def work(tracker, operation_id):
            script_path = _resolve_script("find-duplicate-sources", project_root)
            duplicates_found = 0
            files_scanned = 0
            last_progress = 5

            scanning_pattern = re.compile(r"(?:Scanning|Checking).*?(\d+)")
            progress_pattern = re.compile(r"\[(\d+)/(\d+)\]")
            found_pattern = re.compile(r"Found\s*(\d+)\s*(?:files|sources)")
            duplicate_pattern = re.compile(r"(?:duplicate|dup).*?(\d+)", re.I)

            def on_line(line):
                nonlocal duplicates_found, files_scanned, last_progress
                line = line.strip()
                if not line:
                    return

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
                    return

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

                match = found_pattern.search(line)
                if match:
                    found = int(match.group(1))
                    tracker.update_progress(
                        operation_id, 20, f"Found {found} source files to analyze"
                    )

                match = duplicate_pattern.search(line)
                if match:
                    duplicates_found = int(match.group(1))

            cmd = ["bash", str(script_path)]
            if dry_run:
                cmd.append("--dry-run")

            tracker.update_progress(operation_id, 5, "Scanning source directory...")
            result = run_with_progress(
                cmd,
                line_callback=on_line,
                timeout_secs=600,
                operation_name="Duplicate scan",
                env={**os.environ, "TERM": "dumb"},
            )
            handle_result(
                tracker,
                operation_id,
                result,
                {
                    "duplicates_found": duplicates_found,
                    "dry_run": dry_run,
                    "output": result["output"],
                },
                "Duplicate scan failed",
            )

        return run_async_operation(
            "source_duplicates",
            f"Finding duplicate source files {'(dry run)' if dry_run else ''}",
            "Duplicate scan already in progress",
            f"Duplicate scan started {'(dry run)' if dry_run else ''}",
            work,
        )

    return utilities_ops_maintenance_bp
