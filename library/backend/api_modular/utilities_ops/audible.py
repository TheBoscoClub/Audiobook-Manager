"""
Audible integration operations.

Handles downloading from Audible and syncing metadata (genres, narrators).
"""

import os
import re
import sys
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request
from operation_status import get_tracker

from ..auth import admin_if_enabled
from ..core import FlaskResponse
from ._subprocess import run_with_progress

utilities_ops_audible_bp = Blueprint("utilities_ops_audible", __name__)

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")


def init_audible_routes(project_root):
    """Initialize Audible-related routes."""

    @utilities_ops_audible_bp.route(
        "/api/utilities/download-audiobooks-async", methods=["POST"]
    )
    @admin_if_enabled
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

            # State variables for the callback
            downloaded_count = 0
            failed_count = 0
            current_item = 0
            total_items = 0
            last_progress = 2

            # Patterns to parse download script output
            item_pattern = re.compile(r"\[(\d+)/(\d+)\]\s*Downloading:\s*(.+)")
            success_pattern = re.compile(r"[✓✔]\s*Downloaded.*:\s*(.+)")
            fail_pattern = re.compile(r"[✗✘]\s*Failed.*:\s*(.+)")
            complete_pattern = re.compile(
                r"Download complete:\s*(\d+)\s*succeeded.*(\d+)\s*failed"
            )

            def on_line(line):
                nonlocal downloaded_count, failed_count, current_item
                nonlocal total_items, last_progress
                line = line.strip()
                if not line:
                    return

                # Check for [X/Y] Downloading pattern
                match = item_pattern.search(line)
                if match:
                    current_item = int(match.group(1))
                    total_items = int(match.group(2))
                    title = match.group(3).strip()[:50]

                    # Scale progress: 2-90% for downloads
                    if total_items > 0:
                        progress = 2 + int(
                            (current_item / total_items) * 88
                        )
                        if progress > last_progress:
                            tracker.update_progress(
                                operation_id,
                                progress,
                                f"[{current_item}/{total_items}] "
                                f"Downloading: {title}",
                            )
                            last_progress = progress
                    return

                # Check for success
                if success_pattern.search(line):
                    downloaded_count += 1
                    title = (
                        success_pattern.search(line).group(1).strip()[:40]
                    )
                    tracker.update_progress(
                        operation_id,
                        last_progress,
                        f"✓ Downloaded: {title}",
                    )
                    return

                # Check for failure
                if fail_pattern.search(line):
                    failed_count += 1
                    return

                # Check for completion summary
                match = complete_pattern.search(line)
                if match:
                    downloaded_count = int(match.group(1))
                    failed_count = int(match.group(2))

            try:
                tracker.update_progress(
                    operation_id, 2, "Initializing download process..."
                )

                result = run_with_progress(
                    ["bash", str(script_path)],
                    line_callback=on_line,
                    timeout_secs=3600,
                    operation_name="Download",
                    env={**os.environ, "TERM": "dumb"},
                )

                if result["timed_out"]:
                    tracker.fail_operation(operation_id, result["error"])
                elif result["success"]:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "downloaded_count": downloaded_count,
                            "failed_count": failed_count,
                            "total_attempted": total_items,
                            "output": result["output"],
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result["error"] or "Download failed"
                    )

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
    @admin_if_enabled
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

            # State variables for the callback
            updated_count = 0
            processed_count = 0
            total_count = 0
            last_progress = 5

            # Patterns for genre sync output
            processing_pattern = re.compile(r"\[(\d+)/(\d+)\].*Processing")
            update_pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)
            loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)

            def on_line(line):
                nonlocal updated_count, processed_count, total_count, last_progress
                line = line.strip()
                if not line:
                    return

                # Check for loading count
                match = loading_pattern.search(line)
                if match:
                    total_count = int(match.group(1))
                    tracker.update_progress(
                        operation_id,
                        10,
                        f"Found {total_count} audiobooks to process",
                    )
                    return

                # Check for processing progress
                match = processing_pattern.search(line)
                if match:
                    processed_count = int(match.group(1))
                    total = int(match.group(2))
                    if total > 0:
                        progress = 10 + int((processed_count / total) * 80)
                        if progress > last_progress:
                            tracker.update_progress(
                                operation_id,
                                progress,
                                f"Processing genres: {processed_count}/{total}",
                            )
                            last_progress = progress
                    return

                # Check for update count
                match = update_pattern.search(line)
                if match:
                    updated_count = int(match.group(1))

            try:
                tracker.update_progress(operation_id, 5, "Loading Audible metadata...")

                cmd = [sys.executable, "-u", str(script_path)]
                if not dry_run:
                    cmd.append("--execute")

                result = run_with_progress(
                    cmd,
                    line_callback=on_line,
                    timeout_secs=600,
                    operation_name="Genre sync",
                )

                if result["timed_out"]:
                    tracker.fail_operation(operation_id, result["error"])
                elif result["success"]:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "genres_updated": updated_count,
                            "dry_run": dry_run,
                            "output": result["output"],
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result["error"] or "Genre sync failed"
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
    @admin_if_enabled
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

            # State variables for the callback
            updated_count = 0
            processed_count = 0
            last_progress = 5

            # Patterns for narrator sync output
            processing_pattern = re.compile(r"\[(\d+)/(\d+)\].*Processing")
            update_pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)
            loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)

            def on_line(line):
                nonlocal updated_count, processed_count, last_progress
                line = line.strip()
                if not line:
                    return

                # Check for loading count
                match = loading_pattern.search(line)
                if match:
                    total_count = int(match.group(1))
                    tracker.update_progress(
                        operation_id,
                        10,
                        f"Found {total_count} audiobooks to process",
                    )
                    return

                # Check for processing progress
                match = processing_pattern.search(line)
                if match:
                    processed_count = int(match.group(1))
                    total = int(match.group(2))
                    if total > 0:
                        progress = 10 + int((processed_count / total) * 80)
                        if progress > last_progress:
                            tracker.update_progress(
                                operation_id,
                                progress,
                                f"Processing narrators:"
                                f" {processed_count}/{total}",
                            )
                            last_progress = progress
                    return

                # Check for update count
                match = update_pattern.search(line)
                if match:
                    updated_count = int(match.group(1))

            try:
                tracker.update_progress(operation_id, 5, "Loading Audible metadata...")

                cmd = [sys.executable, "-u", str(script_path)]
                if not dry_run:
                    cmd.append("--execute")

                result = run_with_progress(
                    cmd,
                    line_callback=on_line,
                    timeout_secs=600,
                    operation_name="Narrator sync",
                )

                if result["timed_out"]:
                    tracker.fail_operation(operation_id, result["error"])
                elif result["success"]:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "narrators_updated": updated_count,
                            "dry_run": dry_run,
                            "output": result["output"],
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, result["error"] or "Narrator sync failed"
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
    @admin_if_enabled
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
