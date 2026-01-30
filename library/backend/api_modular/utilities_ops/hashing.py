"""
Hash and checksum generation operations.

Handles SHA-256 hash generation and MD5 checksum operations for file integrity.
"""

import hashlib
import os
import re as regex
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, jsonify
from operation_status import get_tracker

from ..auth import admin_if_enabled
from ..core import FlaskResponse

utilities_ops_hashing_bp = Blueprint("utilities_ops_hashing", __name__)


def init_hashing_routes(project_root):
    """Initialize hash/checksum generation routes."""

    @utilities_ops_hashing_bp.route(
        "/api/utilities/generate-hashes-async", methods=["POST"]
    )
    @admin_if_enabled
    def generate_hashes_async() -> FlaskResponse:
        """Generate SHA-256 hashes with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("hash")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Hash generation already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation("hash", "Generating SHA-256 hashes")

        def run_hash_gen():
            tracker.start_operation(operation_id)

            hash_script = project_root / "scripts" / "generate_hashes.py"

            try:
                tracker.update_progress(
                    operation_id, 5, "Starting hash generation..."
                )

                # Use Popen for streaming progress
                process = subprocess.Popen(
                    ["python3", "-u", str(hash_script), "--parallel"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                output_lines = []
                hashes_generated = 0
                last_progress = 5

                # Patterns for hash generation output
                progress_pattern = regex.compile(r"\[(\d+)/(\d+)\]")
                processing_pattern = regex.compile(
                    r"(?:Processing|Hashing).*?(\d+)"
                )
                generated_pattern = regex.compile(
                    r"(?:Generated|Completed)\s*(\d+)", regex.I
                )
                file_pattern = regex.compile(r"Hashing:\s*(.+)")

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
                                progress = 5 + int((current / total) * 90)
                                if progress > last_progress:
                                    tracker.update_progress(
                                        operation_id,
                                        progress,
                                        f"Hashing: {current}/{total} files",
                                    )
                                    last_progress = progress
                            continue

                        # Check for file being hashed
                        match = file_pattern.search(line)
                        if match:
                            filename = match.group(1).strip()[:40]
                            tracker.update_progress(
                                operation_id,
                                last_progress,
                                f"Hashing: {filename}",
                            )

                        # Check for processing count
                        match = processing_pattern.search(line)
                        if match:
                            count = int(match.group(1))
                            progress = min(5 + (count // 10), 90)
                            if progress > last_progress:
                                tracker.update_progress(
                                    operation_id,
                                    progress,
                                    f"Processed {count} files",
                                )
                                last_progress = progress

                        # Check for generated count
                        match = generated_pattern.search(line)
                        if match:
                            hashes_generated = int(match.group(1))

                process.wait(timeout=1800)
                stderr = process.stderr.read()
                output = "\n".join(output_lines)

                if process.returncode == 0:
                    tracker.complete_operation(
                        operation_id,
                        {
                            "hashes_generated": hashes_generated,
                            "output": output[-2000:] if len(output) > 2000 else output,
                        },
                    )
                else:
                    tracker.fail_operation(
                        operation_id, stderr or "Hash generation failed"
                    )

            except subprocess.TimeoutExpired:
                process.kill()
                tracker.fail_operation(
                    operation_id, "Hash generation timed out after 30 minutes"
                )
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_hash_gen, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Hash generation started",
                "operation_id": operation_id,
            }
        )

    @utilities_ops_hashing_bp.route(
        "/api/utilities/generate-checksums-async", methods=["POST"]
    )
    @admin_if_enabled
    def generate_checksums_async() -> FlaskResponse:
        """Generate MD5 checksums for Sources and Library with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("checksum")
        if existing:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Checksum generation already in progress",
                        "operation_id": existing,
                    }
                ),
                409,
            )

        operation_id = tracker.create_operation("checksum", "Generating MD5 checksums")

        def run_checksum_gen():
            tracker.start_operation(operation_id)

            try:
                # Get paths from environment or defaults
                audiobooks_data = os.environ.get("AUDIOBOOKS_DATA", "/raid0/Audiobooks")
                sources_dir = Path(audiobooks_data) / "Sources"
                library_dir = Path(audiobooks_data) / "Library"
                index_dir = Path(audiobooks_data) / ".index"

                index_dir.mkdir(parents=True, exist_ok=True)

                source_checksums = []
                library_checksums = []

                def checksum_first_mb(filepath):
                    """Calculate MD5 of first 1MB of file."""
                    try:
                        with open(filepath, "rb") as f:
                            data = f.read(1048576)  # 1MB
                        return hashlib.md5(data, usedforsecurity=False).hexdigest()
                    except (IOError, OSError):
                        return None

                # Count files first for progress
                tracker.update_progress(operation_id, 5, "Counting files...")
                source_files = (
                    list(sources_dir.rglob("*.aaxc")) if sources_dir.exists() else []
                )
                library_files = (
                    [
                        f
                        for f in library_dir.rglob("*.opus")
                        if ".cover.opus" not in f.name
                    ]
                    if library_dir.exists()
                    else []
                )
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

                processed = 0

                # Process source files
                tracker.update_progress(
                    operation_id, 10, f"Processing {len(source_files)} source files..."
                )
                for filepath in source_files:
                    checksum = checksum_first_mb(filepath)
                    if checksum:
                        source_checksums.append(f"{checksum}|{filepath}")
                    processed += 1
                    if processed % 50 == 0:
                        pct = 10 + int((processed / total_files) * 80)
                        tracker.update_progress(
                            operation_id,
                            pct,
                            f"Processed {processed}/{total_files} files...",
                        )

                # Process library files
                tracker.update_progress(
                    operation_id,
                    50,
                    f"Processing {len(library_files)} library files...",
                )
                for filepath in library_files:
                    checksum = checksum_first_mb(filepath)
                    if checksum:
                        library_checksums.append(f"{checksum}|{filepath}")
                    processed += 1
                    if processed % 50 == 0:
                        pct = 10 + int((processed / total_files) * 80)
                        tracker.update_progress(
                            operation_id,
                            pct,
                            f"Processed {processed}/{total_files} files...",
                        )

                # Write index files
                tracker.update_progress(operation_id, 95, "Writing index files...")

                source_idx_path = index_dir / "source_checksums.idx"
                with open(source_idx_path, "w") as f:
                    f.write(
                        "\n".join(source_checksums) + "\n" if source_checksums else ""
                    )

                library_idx_path = index_dir / "library_checksums.idx"
                with open(library_idx_path, "w") as f:
                    f.write(
                        "\n".join(library_checksums) + "\n" if library_checksums else ""
                    )

                tracker.complete_operation(
                    operation_id,
                    {
                        "source_checksums": len(source_checksums),
                        "library_checksums": len(library_checksums),
                        "total_files": total_files,
                    },
                )

            except Exception as e:
                import traceback

                traceback.print_exc()
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_checksum_gen, daemon=True)
        thread.start()

        return jsonify(
            {
                "success": True,
                "message": "Checksum generation started",
                "operation_id": operation_id,
            }
        )

    return utilities_ops_hashing_bp
