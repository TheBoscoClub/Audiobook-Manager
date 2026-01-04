"""
Async operations with progress tracking.
Handles background operations like add-new, rescan, reimport, and hash generation.
"""

import subprocess
import threading
import sys
from flask import Blueprint, jsonify, request
from pathlib import Path

from .core import FlaskResponse

# Import operation tracking
sys.path.insert(0, str(Path(__file__).parent.parent))
from operation_status import get_tracker, create_progress_callback

utilities_ops_bp = Blueprint("utilities_ops", __name__)


def init_ops_routes(db_path, project_root):
    """Initialize async operation routes with database path and project root."""

    # =========================================================================
    # Operation Status Endpoints
    # =========================================================================

    @utilities_ops_bp.route("/api/operations/status/<operation_id>", methods=["GET"])
    def get_operation_status(operation_id: str) -> FlaskResponse:
        """Get status of a specific operation."""
        tracker = get_tracker()
        status = tracker.get_status(operation_id)

        if not status:
            return jsonify({"error": "Operation not found"}), 404

        return jsonify(status)

    @utilities_ops_bp.route("/api/operations/active", methods=["GET"])
    def get_active_operations() -> FlaskResponse:
        """Get all active (running) operations."""
        tracker = get_tracker()
        operations = tracker.get_active_operations()
        return jsonify({"operations": operations, "count": len(operations)})

    @utilities_ops_bp.route("/api/operations/all", methods=["GET"])
    def get_all_operations() -> FlaskResponse:
        """Get all tracked operations (including completed)."""
        tracker = get_tracker()
        operations = tracker.get_all_operations()
        return jsonify({"operations": operations, "count": len(operations)})

    @utilities_ops_bp.route("/api/operations/cancel/<operation_id>", methods=["POST"])
    def cancel_operation(operation_id: str) -> FlaskResponse:
        """Cancel an operation (sets flag, actual cancellation depends on operation)."""
        tracker = get_tracker()
        if tracker.cancel_operation(operation_id):
            return jsonify({"success": True, "message": "Operation marked for cancellation"})
        return jsonify({"error": "Operation not found"}), 404

    # =========================================================================
    # Incremental Add Endpoint (Async with Progress)
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/add-new", methods=["POST"])
    def add_new_audiobooks_endpoint() -> FlaskResponse:
        """
        Add new audiobooks incrementally (only files not in database).
        Runs in background thread with progress tracking.
        """
        tracker = get_tracker()

        # Check if already running
        existing = tracker.is_operation_running("add_new")
        if existing:
            return jsonify({
                "success": False,
                "error": "Add operation already in progress",
                "operation_id": existing
            }), 409

        # Create operation
        operation_id = tracker.create_operation(
            "add_new",
            "Adding new audiobooks to database"
        )

        # Get options from request
        data = request.get_json() or {}
        calculate_hashes = data.get("calculate_hashes", True)

        def run_add_new():
            """Background thread function."""
            tracker.start_operation(operation_id)
            progress_cb = create_progress_callback(operation_id)

            try:
                # Import here to avoid circular imports
                sys.path.insert(0, str(project_root / "scanner"))
                from add_new_audiobooks import add_new_audiobooks, AUDIOBOOK_DIR, COVER_DIR

                results = add_new_audiobooks(
                    library_dir=AUDIOBOOK_DIR,
                    db_path=db_path,
                    cover_dir=COVER_DIR,
                    calculate_hashes=calculate_hashes,
                    progress_callback=progress_cb
                )

                tracker.complete_operation(operation_id, results)

            except Exception as e:
                import traceback
                traceback.print_exc()
                tracker.fail_operation(operation_id, str(e))

        # Start background thread
        thread = threading.Thread(target=run_add_new, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Add operation started",
            "operation_id": operation_id
        })

    # =========================================================================
    # Updated Rescan with Progress Tracking
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/rescan-async", methods=["POST"])
    def rescan_library_async() -> FlaskResponse:
        """
        Trigger a library rescan with progress tracking.
        This is the async version that runs in background.
        """
        tracker = get_tracker()

        # Check if already running
        existing = tracker.is_operation_running("rescan")
        if existing:
            return jsonify({
                "success": False,
                "error": "Rescan already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation("rescan", "Scanning audiobook library")

        def run_rescan():
            tracker.start_operation(operation_id)
            scanner_path = project_root / "scanner" / "scan_audiobooks.py"

            try:
                tracker.update_progress(operation_id, 10, "Starting scanner...")

                result = subprocess.run(
                    ["python3", str(scanner_path)],
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )

                output = result.stdout
                files_found = 0
                for line in output.split("\n"):
                    if "Total audiobook files:" in line:
                        try:
                            files_found = int(line.split(":")[1].strip())
                        except (ValueError, IndexError):
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "files_found": files_found,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Scanner failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Scan timed out after 30 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_rescan, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Rescan started",
            "operation_id": operation_id
        })

    @utilities_ops_bp.route("/api/utilities/reimport-async", methods=["POST"])
    def reimport_database_async() -> FlaskResponse:
        """Reimport audiobooks to database with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("reimport")
        if existing:
            return jsonify({
                "success": False,
                "error": "Reimport already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation("reimport", "Importing audiobooks to database")

        def run_reimport():
            tracker.start_operation(operation_id)
            import_path = project_root / "backend" / "import_to_db.py"

            try:
                tracker.update_progress(operation_id, 10, "Starting import...")

                result = subprocess.run(
                    ["python3", str(import_path)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                output = result.stdout
                imported_count = 0
                for line in output.split("\n"):
                    if "Imported" in line and "audiobooks" in line:
                        try:
                            parts = line.split()
                            for i, part in enumerate(parts):
                                if part == "Imported" and i + 1 < len(parts):
                                    imported_count = int(parts[i + 1])
                                    break
                        except (ValueError, IndexError):
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "imported_count": imported_count,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Import failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Import timed out after 5 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_reimport, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Reimport started",
            "operation_id": operation_id
        })

    @utilities_ops_bp.route("/api/utilities/generate-hashes-async", methods=["POST"])
    def generate_hashes_async() -> FlaskResponse:
        """Generate SHA-256 hashes with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("hash")
        if existing:
            return jsonify({
                "success": False,
                "error": "Hash generation already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation("hash", "Generating SHA-256 hashes")

        def run_hash_gen():
            tracker.start_operation(operation_id)
            import re as regex
            hash_script = project_root / "scripts" / "generate_hashes.py"

            try:
                tracker.update_progress(operation_id, 10, "Starting hash generation...")

                result = subprocess.run(
                    ["python3", str(hash_script), "--parallel"],
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )

                output = result.stdout
                hashes_generated = 0
                for line in output.split("\n"):
                    if "Generated" in line or "hashes" in line.lower():
                        try:
                            numbers = regex.findall(r"\d+", line)
                            if numbers:
                                hashes_generated = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "hashes_generated": hashes_generated,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Hash generation failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Hash generation timed out after 30 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_hash_gen, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Hash generation started",
            "operation_id": operation_id
        })

    @utilities_ops_bp.route("/api/utilities/generate-checksums-async", methods=["POST"])
    def generate_checksums_async() -> FlaskResponse:
        """Generate MD5 checksums for Sources and Library with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("checksum")
        if existing:
            return jsonify({
                "success": False,
                "error": "Checksum generation already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation("checksum", "Generating MD5 checksums")

        def run_checksum_gen():
            tracker.start_operation(operation_id)
            import os
            import hashlib
            from pathlib import Path

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
                        return hashlib.md5(data).hexdigest()
                    except (IOError, OSError):
                        return None

                # Count files first for progress
                tracker.update_progress(operation_id, 5, "Counting files...")
                source_files = list(sources_dir.rglob("*.aaxc")) if sources_dir.exists() else []
                library_files = [f for f in library_dir.rglob("*.opus") if ".cover.opus" not in f.name] if library_dir.exists() else []
                total_files = len(source_files) + len(library_files)

                if total_files == 0:
                    tracker.complete_operation(operation_id, {
                        "source_checksums": 0,
                        "library_checksums": 0,
                        "message": "No files found to checksum"
                    })
                    return

                processed = 0

                # Process source files
                tracker.update_progress(operation_id, 10, f"Processing {len(source_files)} source files...")
                for filepath in source_files:
                    checksum = checksum_first_mb(filepath)
                    if checksum:
                        source_checksums.append(f"{checksum}|{filepath}")
                    processed += 1
                    if processed % 50 == 0:
                        pct = 10 + int((processed / total_files) * 80)
                        tracker.update_progress(operation_id, pct, f"Processed {processed}/{total_files} files...")

                # Process library files
                tracker.update_progress(operation_id, 50, f"Processing {len(library_files)} library files...")
                for filepath in library_files:
                    checksum = checksum_first_mb(filepath)
                    if checksum:
                        library_checksums.append(f"{checksum}|{filepath}")
                    processed += 1
                    if processed % 50 == 0:
                        pct = 10 + int((processed / total_files) * 80)
                        tracker.update_progress(operation_id, pct, f"Processed {processed}/{total_files} files...")

                # Write index files
                tracker.update_progress(operation_id, 95, "Writing index files...")

                source_idx_path = index_dir / "source_checksums.idx"
                with open(source_idx_path, "w") as f:
                    f.write("\n".join(source_checksums) + "\n" if source_checksums else "")

                library_idx_path = index_dir / "library_checksums.idx"
                with open(library_idx_path, "w") as f:
                    f.write("\n".join(library_checksums) + "\n" if library_checksums else "")

                tracker.complete_operation(operation_id, {
                    "source_checksums": len(source_checksums),
                    "library_checksums": len(library_checksums),
                    "total_files": total_files
                })

            except Exception as e:
                import traceback
                traceback.print_exc()
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_checksum_gen, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Checksum generation started",
            "operation_id": operation_id
        })

    return utilities_ops_bp
