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

# Module-level project root, set by init_maintenance_routes
_project_root = None


def _resolve_script(name, project_root):
    """Resolve a script path, preferring installed location over project."""
    installed = Path(f"{_audiobooks_home}/scripts/{name}")
    if installed.exists():
        return installed
    return project_root.parent / "scripts" / name


# ---------------------------------------------------------------------------
# Progress-line parser helpers (stateful, shared pattern)
# ---------------------------------------------------------------------------


class _ProgressState:
    """Mutable state container for progress-tracking callbacks."""

    __slots__ = ("last_progress",)

    def __init__(self, initial=5):
        self.last_progress = initial


def _update_if_advanced(tracker, op_id, state, progress, message):
    """Update tracker only when progress exceeds the last-reported value."""
    if progress > state.last_progress:
        tracker.update_progress(op_id, progress, message)
        state.last_progress = progress


def _parse_ratio_progress(line, pattern, base, span, tracker, op_id, state, fmt):
    """Parse [current/total] style lines and update progress.

    Returns True if a match was found (caller should stop further matching).
    """
    match = pattern.search(line)
    if not match:
        return False
    current = int(match.group(1))
    total = int(match.group(2))
    if total > 0:
        progress = base + int((current / total) * span)
        _update_if_advanced(
            tracker, op_id, state, progress, fmt.format(current=current, total=total)
        )
    return True


def _parse_count_progress(
    line, pattern, base, divisor, cap, tracker, op_id, state, fmt
):
    """Parse a single-count line and update progress.

    Returns the extracted count, or None if no match.
    """
    match = pattern.search(line)
    if not match:
        return None
    count = int(match.group(1))
    progress = min(base + (count // divisor), cap)
    _update_if_advanced(tracker, op_id, state, progress, fmt.format(count=count))
    return count


def _extract_count(line, pattern):
    """Extract a single integer from a regex match, or return None."""
    match = pattern.search(line)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Rebuild queue
# ---------------------------------------------------------------------------

_SCANNING_RE = re.compile(r"(?:Scanning|Processing).*?(\d+)")
_FOUND_RE = re.compile(r"Found\s*(\d+)\s*(?:files|items)")
_QUEUE_RE = re.compile(r"Queue.*?(\d+)")


def _rebuild_queue_work(tracker, operation_id):
    """Worker for rebuild-queue async operation."""
    script_path = _resolve_script("build-conversion-queue", _project_root)
    state = _ProgressState(5)
    counters = {"queue_size": 0, "files_scanned": 0}

    def on_line(line):
        line = line.strip()
        if not line:
            return
        scanned = _parse_count_progress(
            line,
            _SCANNING_RE,
            5,
            50,
            80,
            tracker,
            operation_id,
            state,
            "Scanning files: {count} processed",
        )
        if scanned is not None:
            counters["files_scanned"] = scanned
            return
        found = _extract_count(line, _FOUND_RE)
        if found is not None:
            tracker.update_progress(operation_id, 85, f"Found {found} files to process")
            return
        qs = _extract_count(line, _QUEUE_RE)
        if qs is not None:
            counters["queue_size"] = qs

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
        {"queue_size": counters["queue_size"], "output": result["output"]},
        "Queue rebuild failed",
    )


@utilities_ops_maintenance_bp.route(
    "/api/utilities/rebuild-queue-async", methods=["POST"]
)
@admin_if_enabled
def rebuild_queue_async() -> FlaskResponse:
    """Rebuild the conversion queue with progress tracking."""
    return run_async_operation(
        "rebuild_queue",
        "Rebuilding conversion queue",
        "Queue rebuild already in progress",
        "Queue rebuild started",
        _rebuild_queue_work,
    )


# ---------------------------------------------------------------------------
# Cleanup indexes
# ---------------------------------------------------------------------------

_IDX_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_CHECKING_RE = re.compile(r"(?:Checking|Verifying).*?(\d+)")
_REMOVED_RE = re.compile(r"(?:removed|would remove|stale)\D*(\d+)", re.I)


def _cleanup_indexes_work(tracker, operation_id, *, dry_run):
    """Worker for cleanup-indexes async operation."""
    script_path = _resolve_script("cleanup-stale-indexes", _project_root)
    state = _ProgressState(5)
    counters = {"removed": 0, "checked": 0}

    def on_line(line):
        line = line.strip()
        if not line:
            return
        if _parse_ratio_progress(
            line,
            _IDX_PROGRESS_RE,
            5,
            85,
            tracker,
            operation_id,
            state,
            "Checking entries: {current}/{total}",
        ):
            return
        checked = _parse_count_progress(
            line,
            _CHECKING_RE,
            5,
            100,
            85,
            tracker,
            operation_id,
            state,
            "Verified {count} entries",
        )
        if checked is not None:
            counters["checked"] = checked
            return
        removed = _extract_count(line, _REMOVED_RE)
        if removed is not None:
            counters["removed"] = removed

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
            "entries_removed": counters["removed"],
            "dry_run": dry_run,
            "output": result["output"],
        },
        "Cleanup failed",
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
        _cleanup_indexes_work(tracker, operation_id, dry_run=dry_run)

    return run_async_operation(
        "cleanup_indexes",
        f"Cleaning up stale indexes {'(dry run)' if dry_run else ''}",
        "Index cleanup already in progress",
        f"Index cleanup started {'(dry run)' if dry_run else ''}",
        work,
    )


# ---------------------------------------------------------------------------
# Populate sort fields
# ---------------------------------------------------------------------------

_LOADING_RE = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)
_SORT_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_PROCESSING_RE = re.compile(r"Processing.*?(\d+)")
_UPDATE_RE = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)


def _sort_fields_work(tracker, operation_id, *, dry_run):
    """Worker for populate-sort-fields async operation."""
    script_path = _project_root / "scripts" / "populate_sort_fields.py"
    state = _ProgressState(5)
    counters = {"updated": 0, "processed": 0}

    def on_line(line):
        line = line.strip()
        if not line:
            return
        loaded = _extract_count(line, _LOADING_RE)
        if loaded is not None:
            tracker.update_progress(
                operation_id, 10, f"Found {loaded} audiobooks to process"
            )
            return
        if _parse_ratio_progress(
            line,
            _SORT_PROGRESS_RE,
            10,
            80,
            tracker,
            operation_id,
            state,
            "Analyzing: {current}/{total}",
        ):
            return
        processed = _parse_count_progress(
            line,
            _PROCESSING_RE,
            10,
            20,
            85,
            tracker,
            operation_id,
            state,
            "Processed {count} titles",
        )
        if processed is not None:
            counters["processed"] = processed
            return
        updated = _extract_count(line, _UPDATE_RE)
        if updated is not None:
            counters["updated"] = updated

    cmd = [sys.executable, "-u", str(script_path)]
    if not dry_run:
        cmd.append("--execute")

    tracker.update_progress(operation_id, 5, "Loading audiobooks from database...")
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
            "fields_updated": counters["updated"],
            "dry_run": dry_run,
            "output": result["output"],
        },
        "Sort field population failed",
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
        _sort_fields_work(tracker, operation_id, dry_run=dry_run)

    return run_async_operation(
        "sort_fields",
        f"Populating sort fields {'(dry run)' if dry_run else ''}",
        "Sort field population already in progress",
        f"Sort field population started {'(dry run)' if dry_run else ''}",
        work,
    )


# ---------------------------------------------------------------------------
# Populate ASINs
# ---------------------------------------------------------------------------

_ASIN_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_MATCHED_RE = re.compile(r"Matched:\s*(\d+)")
_UNMATCHED_RE = re.compile(r"Unmatched:\s*(\d+)")
_ASIN_PROCESSING_RE = re.compile(r"(?:Processing|Matching).*?(\d+)")


def _export_audible_library(library_export):
    """Export Audible library to a JSON file via audible-cli.

    Returns (success: bool, error_message: str | None).
    """
    _audible_home = os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
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
        return False, "Audible export timed out after 5 minutes"

    if export_result.returncode != 0:
        error_msg = export_result.stderr or export_result.stdout or "Unknown error"
        return (
            False,
            f"Failed to export Audible library (code {export_result.returncode}): {error_msg}",
        )

    if not library_export.exists():
        return False, "Audible export completed but output file not found"

    return True, None


def _asin_match_on_line(tracker, operation_id, state, counters, line):
    """Parse a single output line from the ASIN matching script."""
    line = line.strip()
    if not line:
        return
    if _parse_ratio_progress(
        line,
        _ASIN_PROGRESS_RE,
        30,
        60,
        tracker,
        operation_id,
        state,
        "Matching: {current}/{total} audiobooks",
    ):
        return
    count = _parse_count_progress(
        line,
        _ASIN_PROCESSING_RE,
        30,
        10,
        85,
        tracker,
        operation_id,
        state,
        "Processing audiobook {count}",
    )
    if count is not None:
        return
    matched = _extract_count(line, _MATCHED_RE)
    if matched is not None:
        counters["matched"] = matched
    unmatched = _extract_count(line, _UNMATCHED_RE)
    if unmatched is not None:
        counters["unmatched"] = unmatched


def _run_asin_matching(tracker, operation_id, library_export, dry_run):
    """Run the ASIN matching script against an exported library."""
    library_script = (
        _project_root / "backend" / "migrations" / "populate_asins_from_library.py"
    )
    state = _ProgressState(30)
    counters = {"matched": 0, "unmatched": 0}

    def on_line(line):
        _asin_match_on_line(tracker, operation_id, state, counters, line)

    cmd = [
        sys.executable,
        "-u",
        str(library_script),
        "--library",
        str(library_export),
        "--db",
        str(AUDIOBOOKS_DATABASE),
        "--threshold",
        "0.6",
    ]
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
            "asins_matched": counters["matched"],
            "unmatched": counters["unmatched"],
            "dry_run": dry_run,
            "output": result["output"],
        },
        "ASIN population failed",
    )


def _populate_asins_work(tracker, operation_id, *, dry_run):
    """Worker for populate-asins async operation."""
    fd, library_export_str = tempfile.mkstemp(suffix=".json", prefix="audible-export-")
    os.close(fd)
    library_export = Path(library_export_str)

    try:
        tracker.update_progress(operation_id, 5, "Connecting to Audible API...")
        success, error_msg = _export_audible_library(library_export)
        if not success:
            tracker.fail_operation(operation_id, error_msg)
            return

        tracker.update_progress(
            operation_id, 30, "Library exported, starting match process..."
        )
        _run_asin_matching(tracker, operation_id, library_export, dry_run)
    finally:
        try:
            library_export.unlink(missing_ok=True)
        except OSError:
            pass


@utilities_ops_maintenance_bp.route(
    "/api/utilities/populate-asins-async", methods=["POST"]
)
@admin_if_enabled
def populate_asins_async() -> FlaskResponse:
    """Populate ASINs by matching local audiobooks against Audible library."""
    data = request.get_json() or {}
    dry_run = data.get("dry_run", True)

    def work(tracker, operation_id):
        _populate_asins_work(tracker, operation_id, dry_run=dry_run)

    return run_async_operation(
        "populate_asins",
        f"Populating ASINs from Audible {'(dry run)' if dry_run else ''}",
        "ASIN population already in progress",
        f"ASIN population started {'(dry run)' if dry_run else ''}",
        work,
    )


# ---------------------------------------------------------------------------
# Find source duplicates
# ---------------------------------------------------------------------------

_DUP_SCANNING_RE = re.compile(r"(?:Scanning|Checking).*?(\d+)")
_DUP_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]")
_DUP_FOUND_RE = re.compile(r"Found\s*(\d+)\s*(?:files|sources)")
_DUPLICATE_RE = re.compile(r"(?:duplicate|dup).*?(\d+)", re.I)


def _find_duplicates_work(tracker, operation_id, *, dry_run):
    """Worker for find-source-duplicates async operation."""
    script_path = _resolve_script("find-duplicate-sources", _project_root)
    state = _ProgressState(5)
    counters = {"duplicates": 0, "scanned": 0}

    def on_line(line):
        line = line.strip()
        if not line:
            return
        if _parse_ratio_progress(
            line,
            _DUP_PROGRESS_RE,
            5,
            85,
            tracker,
            operation_id,
            state,
            "Comparing: {current}/{total} files",
        ):
            return
        scanned = _parse_count_progress(
            line,
            _DUP_SCANNING_RE,
            5,
            50,
            80,
            tracker,
            operation_id,
            state,
            "Scanned {count} files",
        )
        if scanned is not None:
            counters["scanned"] = scanned
            return
        found = _extract_count(line, _DUP_FOUND_RE)
        if found is not None:
            tracker.update_progress(
                operation_id, 20, f"Found {found} source files to analyze"
            )
            return
        dups = _extract_count(line, _DUPLICATE_RE)
        if dups is not None:
            counters["duplicates"] = dups

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
            "duplicates_found": counters["duplicates"],
            "dry_run": dry_run,
            "output": result["output"],
        },
        "Duplicate scan failed",
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
        _find_duplicates_work(tracker, operation_id, dry_run=dry_run)

    return run_async_operation(
        "source_duplicates",
        f"Finding duplicate source files {'(dry run)' if dry_run else ''}",
        "Duplicate scan already in progress",
        f"Duplicate scan started {'(dry run)' if dry_run else ''}",
        work,
    )


# ---------------------------------------------------------------------------
# Route initialization
# ---------------------------------------------------------------------------


def init_maintenance_routes(project_root):
    """Initialize maintenance operation routes by storing project root."""
    global _project_root  # noqa: PLW0603
    _project_root = project_root
    return utilities_ops_maintenance_bp
