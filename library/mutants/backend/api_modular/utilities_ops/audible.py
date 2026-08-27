"""
Audible integration operations.

Handles downloading from Audible and syncing metadata (genres, narrators).
"""

import os
import re
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..auth import admin_if_enabled
from ..core import FlaskResponse
from ._helpers import handle_result, run_async_operation
from ._subprocess import run_with_progress

utilities_ops_audible_bp = Blueprint("utilities_ops_audible", __name__)

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")

# Module-level state set by init_audible_routes
_project_root: Path = Path()

# Compiled regex patterns for download progress parsing
_item_pattern = re.compile(r"\[(\d+)/(\d+)\]\s*Downloading:\s*(.+)")
_success_pattern = re.compile(r"[✓✔]\s*Downloaded.*:\s*(.+)")
_fail_pattern = re.compile(r"[✗✘]\s*Failed.*:\s*(.+)")
_complete_pattern = re.compile(r"Download complete:\s*(\d+)\s*succeeded.*(\d+)\s*failed")

# Compiled regex patterns for genre/narrator sync
_processing_pattern = re.compile(r"\[(\d+)/(\d+)\].*Processing")
_update_pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)
_loading_pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)


def _resolve_download_script():
    """Resolve the download script path, checking installed then project."""
    script_path = Path(f"{_audiobooks_home}/scripts/download-new-audiobooks")
    if not script_path.exists():
        script_path = _project_root.parent / "scripts" / "download-new-audiobooks"
    return script_path


def _parse_download_line(line, state, tracker, operation_id):
    """Parse a single line of download output and update progress.

    Args:
        line: Raw output line from the download script.
        state: Mutable dict with keys: downloaded, failed, current, total, last_progress.
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
    """
    line = line.strip()
    if not line:
        return

    match = _item_pattern.search(line)
    if match:
        state["current"] = int(match.group(1))
        state["total"] = int(match.group(2))
        title = match.group(3).strip()[:50]
        if state["total"] > 0:
            progress = 2 + int((state["current"] / state["total"]) * 88)
            if progress > state["last_progress"]:
                tracker.update_progress(
                    operation_id,
                    progress,
                    f"[{state['current']}/{state['total']}] Downloading: {title}",
                )
                state["last_progress"] = progress
        return

    success_match = _success_pattern.search(line)
    if success_match:
        state["downloaded"] += 1
        title = success_match.group(1).strip()[:40]
        tracker.update_progress(operation_id, state["last_progress"], f"✓ Downloaded: {title}")
        return

    if _fail_pattern.search(line):
        state["failed"] += 1
        return

    match = _complete_pattern.search(line)
    if match:
        state["downloaded"] = int(match.group(1))
        state["failed"] = int(match.group(2))


def _download_work(tracker, operation_id):
    """Worker for the download audiobooks operation."""
    script_path = _resolve_download_script()
    state = {"downloaded": 0, "failed": 0, "current": 0, "total": 0, "last_progress": 2}

    def on_line(line):
        _parse_download_line(line, state, tracker, operation_id)

    tracker.update_progress(operation_id, 2, "Initializing download process...")
    result = run_with_progress(
        ["bash", str(script_path)],
        line_callback=on_line,
        timeout_secs=3600,
        operation_name="Download",
        env={**os.environ, "TERM": "dumb"},
    )
    handle_result(
        tracker,
        operation_id,
        result,
        {
            "downloaded_count": state["downloaded"],
            "failed_count": state["failed"],
            "total_attempted": state["total"],
            "output": result["output"],
        },
        "Download failed",
    )


def _parse_sync_line(line, state, tracker, operation_id, label):
    """Parse a single line of genre/narrator sync output.

    Args:
        line: Raw output line from the sync script.
        state: Mutable dict with keys: updated, processed, total, last_progress.
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
        label: Display label ('genres' or 'narrators').
    """
    line = line.strip()
    if not line:
        return

    match = _loading_pattern.search(line)
    if match:
        state["total"] = int(match.group(1))
        tracker.update_progress(operation_id, 10, f"Found {state['total']} audiobooks to process")
        return

    match = _processing_pattern.search(line)
    if match:
        state["processed"] = int(match.group(1))
        total = int(match.group(2))
        if total > 0:
            progress = 10 + int((state["processed"] / total) * 80)
            if progress > state["last_progress"]:
                tracker.update_progress(
                    operation_id, progress, f"Processing {label}: {state['processed']}/{total}"
                )
                state["last_progress"] = progress
        return

    match = _update_pattern.search(line)
    if match:
        state["updated"] = int(match.group(1))


def _run_sync_operation(tracker, operation_id, script_name, label, singular_label, dry_run):
    """Run a genre or narrator sync operation.

    Args:
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
        script_name: Filename of the sync script under project_root/scripts/.
        label: Plural label for progress/result keys ('genres' or 'narrators').
        singular_label: Singular label for operation name/errors ('Genre' or 'Narrator').
        dry_run: Whether to run in dry-run mode.
    """
    script_path = _project_root / "scripts" / script_name
    state = {"updated": 0, "processed": 0, "total": 0, "last_progress": 5}

    def on_line(line):
        _parse_sync_line(line, state, tracker, operation_id, label)

    tracker.update_progress(operation_id, 5, "Loading Audible metadata...")
    cmd = [sys.executable, "-u", str(script_path)]
    if not dry_run:
        cmd.append("--execute")

    result = run_with_progress(
        cmd, line_callback=on_line, timeout_secs=600, operation_name=f"{singular_label} sync"
    )
    handle_result(
        tracker,
        operation_id,
        result,
        {f"{label}_updated": state["updated"], "dry_run": dry_run, "output": result["output"]},
        f"{singular_label} sync failed",
    )


@utilities_ops_audible_bp.route("/api/utilities/download-audiobooks-async", methods=["POST"])
@admin_if_enabled
def download_audiobooks_async() -> FlaskResponse:
    """Download new audiobooks from Audible with progress tracking."""
    return run_async_operation(
        "download",
        "Downloading new audiobooks from Audible",
        "Download already in progress",
        "Download started",
        _download_work,
    )


@utilities_ops_audible_bp.route("/api/utilities/sync-genres-async", methods=["POST"])
@admin_if_enabled
def sync_genres_async() -> FlaskResponse:
    """Sync genres from Audible metadata with progress tracking."""
    data = request.get_json() or {}
    dry_run = data.get("dry_run", True)

    def work(tracker, operation_id):
        _run_sync_operation(tracker, operation_id, "populate_genres.py", "genres", "Genre", dry_run)

    return run_async_operation(
        "sync_genres",
        f"Syncing genres from Audible {'(dry run)' if dry_run else ''}",
        "Genre sync already in progress",
        f"Genre sync started {'(dry run)' if dry_run else ''}",
        work,
    )


@utilities_ops_audible_bp.route("/api/utilities/sync-narrators-async", methods=["POST"])
@admin_if_enabled
def sync_narrators_async() -> FlaskResponse:
    """Update narrator info from Audible metadata with progress tracking."""
    data = request.get_json() or {}
    dry_run = data.get("dry_run", True)

    def work(tracker, operation_id):
        _run_sync_operation(
            tracker,
            operation_id,
            "update_narrators_from_audible.py",
            "narrators",
            "Narrator",
            dry_run,
        )

    return run_async_operation(
        "sync_narrators",
        f"Updating narrators from Audible {'(dry run)' if dry_run else ''}",
        "Narrator sync already in progress",
        f"Narrator sync started {'(dry run)' if dry_run else ''}",
        work,
    )


@utilities_ops_audible_bp.route("/api/utilities/check-audible-prereqs", methods=["GET"])
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


def init_audible_routes(project_root):
    """Initialize Audible-related routes by setting module-level project root."""
    global _project_root
    _project_root = project_root
    return utilities_ops_audible_bp
