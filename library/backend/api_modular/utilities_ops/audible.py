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

from ..auth import admin_if_enabled
from ..core import FlaskResponse

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

            try:
                tracker.update_progress(
                    operation_id, 2, "Initializing download process..."
                )

                # Use Popen for streaming progress instead of blocking run()
                process = subprocess.Popen(
                    ["bash", str(script_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # Line buffered
                    env={**os.environ, "TERM": "dumb"},
                )

                output_lines = []
                downloaded_count = 0
                failed_count = 0
                current_item = 0
                total_items = 0
                last_progress = 2

                # Patterns to parse download script output
                # [1/16] Downloading: Book Title
                item_pattern = re.compile(r"\[(\d+)/(\d+)\]\s*Downloading:\s*(.+)")
                # ✓ Downloaded: Book Title
                success_pattern = re.compile(r"[✓✔]\s*Downloaded.*:\s*(.+)")
                # ✗ Failed: Book Title
                fail_pattern = re.compile(r"[✗✘]\s*Failed.*:\s*(.+)")
                # Download complete: X succeeded, Y failed
                complete_pattern = re.compile(
                    r"Download complete:\s*(\d+)\s*succeeded.*(\d+)\s*failed"
                )

                # Read stdout line by line
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

                            # Parse progress from output
                            # Check for [X/Y] Downloading pattern
                            match = item_pattern.search(buffer)
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

                            # Check for success
                            elif success_pattern.search(buffer):
                                downloaded_count += 1
                                title = (
                                    success_pattern.search(buffer).group(1).strip()[:40]
                                )
                                tracker.update_progress(
                                    operation_id,
                                    last_progress,
                                    f"✓ Downloaded: {title}",
                                )

                            # Check for failure
                            elif fail_pattern.search(buffer):
                                failed_count += 1

                            # Check for completion summary
                            elif complete_pattern.search(buffer):
                                match = complete_pattern.search(buffer)
                                downloaded_count = int(match.group(1))
                                failed_count = int(match.group(2))

                            buffer = ""
                    else:
                        buffer += char

                process.wait(timeout=3600)  # 1 hour timeout
                stderr = process.stderr.read()

                output = "\n".join(output_lines)

                if process.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "downloaded_count": downloaded_count,
                            "failed_count": failed_count,
                            "total_attempted": total_items,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, stderr or "Download failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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

            try:
                tracker.update_progress(
                    operation_id, 5, "Loading Audible metadata..."
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
                total_count = 0
                last_progress = 5

                # Patterns for genre sync output
                # Processing: Book Title or [X/Y] Processing...
                processing_pattern = re.compile(r"\[(\d+)/(\d+)\].*Processing")
                # Updated X / Would update X
                update_pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)
                # Loading X audiobooks
                loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for loading count
                        match = loading_pattern.search(line)
                        if match:
                            total_count = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                10,
                                f"Found {total_count} audiobooks to process",
                            )
                            continue

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
                            continue

                        # Check for update count
                        match = update_pattern.search(line)
                        if match:
                            updated_count = int(match.group(1))

                process.wait(timeout=600)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
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
                        operation_id, stderr or "Genre sync failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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

            try:
                tracker.update_progress(
                    operation_id, 5, "Loading Audible metadata..."
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

                # Patterns for narrator sync output
                processing_pattern = re.compile(r"\[(\d+)/(\d+)\].*Processing")
                update_pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)
                loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        output_lines.append(line)

                        # Check for loading count
                        match = loading_pattern.search(line)
                        if match:
                            total_count = int(match.group(1))
                            tracker.update_progress(
                                operation_id,
                                10,
                                f"Found {total_count} audiobooks to process",
                            )
                            continue

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
                                        f"Processing narrators: {processed_count}/{total}",
                                    )
                                    last_progress = progress
                            continue

                        # Check for update count
                        match = update_pattern.search(line)
                        if match:
                            updated_count = int(match.group(1))

                process.wait(timeout=600)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
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
                        operation_id, stderr or "Narrator sync failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
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
