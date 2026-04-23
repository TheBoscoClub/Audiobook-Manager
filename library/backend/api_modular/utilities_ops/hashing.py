"""
Hash and checksum generation operations.

Handles SHA-256 hash generation and MD5 checksum operations for file integrity.
"""

import hashlib
import os
import re as regex
import sys
from pathlib import Path

from flask import Blueprint

from ..auth import admin_if_enabled
from ..core import FlaskResponse
from ._helpers import handle_result, run_async_operation
from ._subprocess import run_with_progress

utilities_ops_hashing_bp = Blueprint("utilities_ops_hashing", __name__)

# Module-level state set by init_hashing_routes
_project_root: Path = Path()

# Compiled regex patterns for hash progress parsing
_progress_pattern = regex.compile(r"\[(\d+)/(\d+)\]")
_processing_pattern = regex.compile(r"(?:Processing|Hashing).*?(\d+)")
_generated_pattern = regex.compile(r"(?:Generated|Completed)\s*(\d+)", regex.I)
_file_pattern = regex.compile(r"Hashing:\s*(.+)")


def _parse_hash_line(line, state, tracker, operation_id):
    """Parse a single line of hash generation output and update progress.

    Args:
        line: Raw output line from the hash script.
        state: Mutable dict with keys: generated, last_progress.
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
    """
    line = line.strip()
    if not line:
        return

    match = _progress_pattern.search(line)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        if total > 0:
            progress = 5 + int((current / total) * 90)
            if progress > state["last_progress"]:
                tracker.update_progress(operation_id, progress, f"Hashing: {current}/{total} files")
                state["last_progress"] = progress
        return

    match = _file_pattern.search(line)
    if match:
        filename = match.group(1).strip()[:40]
        tracker.update_progress(operation_id, state["last_progress"], f"Hashing: {filename}")

    match = _processing_pattern.search(line)
    if match:
        count = int(match.group(1))
        progress = min(5 + (count // 10), 90)
        if progress > state["last_progress"]:
            tracker.update_progress(operation_id, progress, f"Processed {count} files")
            state["last_progress"] = progress

    match = _generated_pattern.search(line)
    if match:
        state["generated"] = int(match.group(1))


def _hash_work(tracker, operation_id):
    """Worker for the hash generation operation."""
    hash_script = _project_root / "scripts" / "generate_hashes.py"
    state = {"generated": 0, "last_progress": 5}

    def on_line(line):
        _parse_hash_line(line, state, tracker, operation_id)

    tracker.update_progress(operation_id, 5, "Starting hash generation...")
    result = run_with_progress(
        [sys.executable, "-u", str(hash_script), "--parallel"],
        line_callback=on_line,
        timeout_secs=1800,
        operation_name="Hash generation",
    )
    handle_result(
        tracker,
        operation_id,
        result,
        {"hashes_generated": state["generated"], "output": result["output"]},
        "Hash generation failed",
    )


def _checksum_first_mb(filepath):
    """Calculate MD5 of first 1MB of file.

    Returns:
        Hex digest string, or None on I/O error.
    """
    try:
        with open(filepath, "rb") as f:
            data = f.read(1048576)
        return hashlib.md5(data, usedforsecurity=False).hexdigest()
    except (IOError, OSError):
        return None


def _collect_checksum_files(sources_dir, library_dir):
    """Collect source and library files for checksumming.

    Returns:
        Tuple of (source_files, library_files).
    """
    source_files = list(sources_dir.rglob("*.aaxc")) if sources_dir.exists() else []
    library_files = (
        [f for f in library_dir.rglob("*.opus") if ".cover.opus" not in f.name]
        if library_dir.exists()
        else []
    )
    return source_files, library_files


def _process_file_checksums(files, tracker, operation_id, processed, total_files):
    """Checksum a list of files, updating progress periodically.

    Args:
        files: List of Path objects to checksum.
        tracker: Progress tracker instance.
        operation_id: Current operation ID.
        processed: Starting count of already-processed files.
        total_files: Total file count for progress calculation.

    Returns:
        Tuple of (checksums_list, new_processed_count).
    """
    checksums = []
    for filepath in files:
        checksum = _checksum_first_mb(filepath)
        if checksum:
            checksums.append(f"{checksum}|{filepath}")
        processed += 1
        if processed % 50 == 0:
            pct = 10 + int((processed / total_files) * 80)
            tracker.update_progress(
                operation_id, pct, f"Processed {processed}/{total_files} files..."
            )
    return checksums, processed


def _write_index_file(path, checksums):
    """Write checksum index to disk."""
    with open(path, "w") as f:
        f.write("\n".join(checksums) + "\n" if checksums else "")


def _checksum_work(tracker, operation_id):
    """Worker for the checksum generation operation."""
    audiobooks_data = os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks")
    sources_dir = Path(audiobooks_data) / "Sources"
    library_dir = Path(audiobooks_data) / "Library"
    index_dir = Path(audiobooks_data) / ".index"
    index_dir.mkdir(parents=True, exist_ok=True)

    tracker.update_progress(operation_id, 5, "Counting files...")
    source_files, library_files = _collect_checksum_files(sources_dir, library_dir)
    total_files = len(source_files) + len(library_files)

    if total_files == 0:
        tracker.complete_operation(
            operation_id,
            {
                "source_checksums": 0,
                "library_checksums": 0,
                "message": "No files found to checksum",
            },
        )
        return

    tracker.update_progress(operation_id, 10, f"Processing {len(source_files)} source files...")
    source_checksums, processed = _process_file_checksums(
        source_files, tracker, operation_id, 0, total_files
    )

    tracker.update_progress(operation_id, 50, f"Processing {len(library_files)} library files...")
    library_checksums, _ = _process_file_checksums(
        library_files, tracker, operation_id, processed, total_files
    )

    tracker.update_progress(operation_id, 95, "Writing index files...")
    _write_index_file(index_dir / "source_checksums.idx", source_checksums)
    _write_index_file(index_dir / "library_checksums.idx", library_checksums)

    tracker.complete_operation(
        operation_id,
        {
            "source_checksums": len(source_checksums),
            "library_checksums": len(library_checksums),
            "total_files": total_files,
        },
    )


@utilities_ops_hashing_bp.route("/api/utilities/generate-hashes-async", methods=["POST"])
@admin_if_enabled
def generate_hashes_async() -> FlaskResponse:
    """Generate SHA-256 hashes with progress tracking."""
    return run_async_operation(
        "hash",
        "Generating SHA-256 hashes",
        "Hash generation already in progress",
        "Hash generation started",
        _hash_work,
    )


@utilities_ops_hashing_bp.route("/api/utilities/generate-checksums-async", methods=["POST"])
@admin_if_enabled
def generate_checksums_async() -> FlaskResponse:
    """Generate MD5 checksums for Sources and Library with progress tracking."""
    return run_async_operation(
        "checksum",
        "Generating MD5 checksums",
        "Checksum generation already in progress",
        "Checksum generation started",
        _checksum_work,
    )


def init_hashing_routes(project_root):
    """Initialize hash/checksum generation routes by setting module-level project root."""
    global _project_root
    _project_root = project_root
    return utilities_ops_hashing_bp
