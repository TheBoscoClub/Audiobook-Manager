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

# Module-level state set by init_library_routes
_db_path = None
_project_root = None

# Compiled regex patterns for rescan progress
_rescan_progress_pattern = re.compile(r"(\d+)%\s*\|\s*(\d+)/(\d+)")

# Compiled regex patterns for reimport progress
_found_pattern = re.compile(r"Found\s+(\d+)\s+audiobooks")
_processed_pattern = re.compile(r"Processed\s+(\d+)/(\d+)\s+audiobooks")
_imported_pattern = re.compile(r"Imported\s+(\d+)\s+audiobooks")
_optimizing_pattern = re.compile(r"Optimizing database")


def _parse_rescan_line(buf, state, tracker, operation_id):
    """Parse a single line of scanner output and update progress.

    Args:
        buf: Raw output line (may contain ANSI codes).
        state: Mutable dict with keys: files_found, last_progress.
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
    """
    clean = _ansi_escape.sub("", buf)
    match = _rescan_progress_pattern.search(clean)
    if match:
        percent = int(match.group(1))
        current = int(match.group(2))
        total = int(match.group(3))
        state["files_found"] = total
        if percent > state["last_progress"]:
            scaled = 5 + int(percent * 0.9)
            tracker.update_progress(
                operation_id,
                scaled,
                f"Scanning: {current}/{total} files ({percent}%)",
            )
            state["last_progress"] = percent

    if "Total files:" in buf or "Total audiobooks:" in buf:
        try:
            state["files_found"] = int(buf.split(":")[1].strip())
        except (ValueError, IndexError):
            pass


def _rescan_work(tracker, operation_id):
    """Worker for the library rescan operation."""
    scanner_path = _project_root / "scanner" / "scan_audiobooks.py"
    state = {"files_found": 0, "last_progress": 5}

    def on_line(buf):
        _parse_rescan_line(buf, state, tracker, operation_id)

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
        {"files_found": state["files_found"], "output": result["output"]},
        "Scanner failed",
    )


def _handle_reimport_regex(line, state, tracker, operation_id):
    """Handle regex-based reimport progress lines (found, processed, imported).

    Returns True if the line was handled, False otherwise.
    """
    match = _found_pattern.search(line)
    if match:
        state["total"] = int(match.group(1))
        tracker.update_progress(
            operation_id,
            5,
            f"Found {state['total']:,} audiobooks to import",
        )
        state["last_progress"] = 5
        return True

    match = _processed_pattern.search(line)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        if total > 0:
            progress = 10 + int((current / total) * 75)
            if progress > state["last_progress"]:
                tracker.update_progress(
                    operation_id,
                    progress,
                    f"Importing: {current:,}/{total:,} audiobooks",
                )
                state["last_progress"] = progress
        return True

    match = _imported_pattern.search(line)
    if match:
        state["imported"] = int(match.group(1))
        tracker.update_progress(
            operation_id, 90, f"Imported {state['imported']:,} audiobooks"
        )
        state["last_progress"] = 90
        return True

    return False


def _handle_reimport_status(line, state, tracker, operation_id):
    """Handle string-match reimport status lines (metadata, optimizing, schema)."""
    if "Preserving existing metadata" in line:
        tracker.update_progress(
            operation_id, 8, "Preserving existing metadata..."
        )
        return

    if _optimizing_pattern.search(line):
        tracker.update_progress(operation_id, 95, "Optimizing database...")
        state["last_progress"] = 95
        return

    if "Creating database" in line:
        tracker.update_progress(
            operation_id, 3, "Creating database schema..."
        )
        return

    if "Database schema created" in line:
        tracker.update_progress(operation_id, 5, "Database schema ready")


def _parse_reimport_line(line, state, tracker, operation_id):
    """Parse a single line of reimport output and update progress.

    Args:
        line: Raw output line from the import script.
        state: Mutable dict with keys: imported, total, last_progress.
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
    """
    line = line.strip()
    if not line:
        return

    if not _handle_reimport_regex(line, state, tracker, operation_id):
        _handle_reimport_status(line, state, tracker, operation_id)


def _reimport_work(tracker, operation_id):
    """Worker for the database reimport operation."""
    import_path = _project_root / "backend" / "import_to_db.py"
    state = {"imported": 0, "total": 0, "last_progress": 2}

    def on_line(line):
        _parse_reimport_line(line, state, tracker, operation_id)

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
            "imported_count": state["imported"],
            "total_audiobooks": state["total"],
            "output": result["output"],
        },
        "Import failed",
    )


@utilities_ops_library_bp.route("/api/utilities/add-new", methods=["POST"])
@admin_if_enabled
def add_new_audiobooks_endpoint() -> FlaskResponse:
    """Add new audiobooks incrementally (only files not in database)."""
    data = request.get_json() or {}
    calculate_hashes = data.get("calculate_hashes", True)

    def work(tracker, operation_id):
        progress_cb = create_progress_callback(operation_id)
        sys.path.insert(0, str(_project_root / "scanner"))
        from add_new_audiobooks import (
            AUDIOBOOK_DIR,
            COVER_DIR,
            add_new_audiobooks,
        )

        results = add_new_audiobooks(
            library_dir=AUDIOBOOK_DIR,
            db_path=_db_path,
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
    return run_async_operation(
        "rescan",
        "Scanning audiobook library",
        "Rescan already in progress",
        "Rescan started",
        _rescan_work,
    )


@utilities_ops_library_bp.route("/api/utilities/reimport-async", methods=["POST"])
@admin_if_enabled
def reimport_database_async() -> FlaskResponse:
    """Reimport audiobooks to database with progress tracking."""
    return run_async_operation(
        "reimport",
        "Importing audiobooks to database",
        "Reimport already in progress",
        "Reimport started",
        _reimport_work,
    )


def init_library_routes(db_path, project_root):
    """Initialize library management routes by setting module-level state."""
    global _db_path, _project_root
    _db_path = db_path
    _project_root = project_root
    return utilities_ops_library_bp
