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


def init_hashing_routes(project_root):
    """Initialize hash/checksum generation routes."""

    @utilities_ops_hashing_bp.route(
        "/api/utilities/generate-hashes-async", methods=["POST"]
    )
    @admin_if_enabled
    def generate_hashes_async() -> FlaskResponse:
        """Generate SHA-256 hashes with progress tracking."""

        def work(tracker, operation_id):
            hash_script = project_root / "scripts" / "generate_hashes.py"
            hashes_generated = 0
            last_progress = 5

            progress_pattern = regex.compile(r"\[(\d+)/(\d+)\]")
            processing_pattern = regex.compile(r"(?:Processing|Hashing).*?(\d+)")
            generated_pattern = regex.compile(
                r"(?:Generated|Completed)\s*(\d+)", regex.I
            )
            file_pattern = regex.compile(r"Hashing:\s*(.+)")

            def on_line(line):
                nonlocal hashes_generated, last_progress
                line = line.strip()
                if not line:
                    return

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
                    return

                match = file_pattern.search(line)
                if match:
                    filename = match.group(1).strip()[:40]
                    tracker.update_progress(
                        operation_id,
                        last_progress,
                        f"Hashing: {filename}",
                    )

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

                match = generated_pattern.search(line)
                if match:
                    hashes_generated = int(match.group(1))

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
                {"hashes_generated": hashes_generated, "output": result["output"]},
                "Hash generation failed",
            )

        return run_async_operation(
            "hash",
            "Generating SHA-256 hashes",
            "Hash generation already in progress",
            "Hash generation started",
            work,
        )

    @utilities_ops_hashing_bp.route(
        "/api/utilities/generate-checksums-async", methods=["POST"]
    )
    @admin_if_enabled
    def generate_checksums_async() -> FlaskResponse:
        """Generate MD5 checksums for Sources and Library with progress tracking."""

        def work(tracker, operation_id):
            audiobooks_data = os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks")
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
                        data = f.read(1048576)
                    return hashlib.md5(data, usedforsecurity=False).hexdigest()
                except (IOError, OSError):
                    return None

            tracker.update_progress(operation_id, 5, "Counting files...")
            source_files = (
                list(sources_dir.rglob("*.aaxc")) if sources_dir.exists() else []
            )
            library_files = (
                [f for f in library_dir.rglob("*.opus") if ".cover.opus" not in f.name]
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

            tracker.update_progress(operation_id, 95, "Writing index files...")

            source_idx_path = index_dir / "source_checksums.idx"
            with open(source_idx_path, "w") as f:
                f.write("\n".join(source_checksums) + "\n" if source_checksums else "")

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

        return run_async_operation(
            "checksum",
            "Generating MD5 checksums",
            "Checksum generation already in progress",
            "Checksum generation started",
            work,
        )

    return utilities_ops_hashing_bp
