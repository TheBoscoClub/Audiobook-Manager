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

    # =========================================================================
    # Download New Audiobooks Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/download-audiobooks-async", methods=["POST"])
    def download_audiobooks_async() -> FlaskResponse:
        """Download new audiobooks from Audible with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("download")
        if existing:
            return jsonify({
                "success": False,
                "error": "Download already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation("download", "Downloading new audiobooks from Audible")

        def run_download():
            tracker.start_operation(operation_id)
            import os
            # Use installed script path
            script_path = Path("/opt/audiobooks/scripts/download-new-audiobooks")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "download-new-audiobooks"

            try:
                tracker.update_progress(operation_id, 5, "Starting download process...")

                result = subprocess.run(
                    ["bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=3600,  # 1 hour timeout for downloads
                    env={**os.environ, "TERM": "dumb"}  # Avoid terminal control chars
                )

                output = result.stdout
                downloaded_count = 0
                for line in output.split("\n"):
                    if "Downloaded" in line or "downloaded" in line:
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                downloaded_count = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "downloaded_count": downloaded_count,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Download failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Download timed out after 1 hour")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_download, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Download started",
            "operation_id": operation_id
        })

    # =========================================================================
    # Rebuild Conversion Queue Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/rebuild-queue-async", methods=["POST"])
    def rebuild_queue_async() -> FlaskResponse:
        """Rebuild the conversion queue with progress tracking."""
        tracker = get_tracker()

        existing = tracker.is_operation_running("rebuild_queue")
        if existing:
            return jsonify({
                "success": False,
                "error": "Queue rebuild already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation("rebuild_queue", "Rebuilding conversion queue")

        def run_rebuild():
            tracker.start_operation(operation_id)
            import os
            script_path = Path("/opt/audiobooks/scripts/build-conversion-queue")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "build-conversion-queue"

            try:
                tracker.update_progress(operation_id, 10, "Rebuilding queue...")

                result = subprocess.run(
                    ["bash", str(script_path), "--rebuild"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**os.environ, "TERM": "dumb"}
                )

                output = result.stdout
                queue_size = 0
                for line in output.split("\n"):
                    if "queue" in line.lower() and any(c.isdigit() for c in line):
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                queue_size = int(numbers[-1])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "queue_size": queue_size,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Queue rebuild failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Queue rebuild timed out after 5 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_rebuild, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Queue rebuild started",
            "operation_id": operation_id
        })

    # =========================================================================
    # Cleanup Stale Indexes Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/cleanup-indexes-async", methods=["POST"])
    def cleanup_indexes_async() -> FlaskResponse:
        """Cleanup stale index entries for deleted files."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("cleanup_indexes")
        if existing:
            return jsonify({
                "success": False,
                "error": "Index cleanup already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation(
            "cleanup_indexes",
            f"Cleaning up stale indexes {'(dry run)' if dry_run else ''}"
        )

        def run_cleanup():
            tracker.start_operation(operation_id)
            import os
            script_path = Path("/opt/audiobooks/scripts/cleanup-stale-indexes")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "cleanup-stale-indexes"

            try:
                tracker.update_progress(operation_id, 10, "Scanning indexes for stale entries...")

                cmd = ["bash", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env={**os.environ, "TERM": "dumb"}
                )

                output = result.stdout
                removed_count = 0
                for line in output.split("\n"):
                    if "removed" in line.lower() or "would remove" in line.lower():
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                removed_count += int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "entries_removed": removed_count,
                        "dry_run": dry_run,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Cleanup failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Cleanup timed out after 10 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_cleanup, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"Index cleanup started {'(dry run)' if dry_run else ''}",
            "operation_id": operation_id
        })

    # =========================================================================
    # Sync Genres from Audible Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/sync-genres-async", methods=["POST"])
    def sync_genres_async() -> FlaskResponse:
        """Sync genres from Audible metadata with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("sync_genres")
        if existing:
            return jsonify({
                "success": False,
                "error": "Genre sync already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation(
            "sync_genres",
            f"Syncing genres from Audible {'(dry run)' if dry_run else ''}"
        )

        def run_sync():
            tracker.start_operation(operation_id)
            script_path = project_root / "scripts" / "populate_genres.py"

            try:
                tracker.update_progress(operation_id, 10, "Loading Audible metadata...")

                cmd = ["python3", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )

                output = result.stdout
                updated_count = 0
                for line in output.split("\n"):
                    if "updated" in line.lower() or "would update" in line.lower():
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                updated_count = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "genres_updated": updated_count,
                        "dry_run": dry_run,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Genre sync failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Genre sync timed out after 10 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_sync, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"Genre sync started {'(dry run)' if dry_run else ''}",
            "operation_id": operation_id
        })

    # =========================================================================
    # Update Narrators from Audible Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/sync-narrators-async", methods=["POST"])
    def sync_narrators_async() -> FlaskResponse:
        """Update narrator info from Audible metadata with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("sync_narrators")
        if existing:
            return jsonify({
                "success": False,
                "error": "Narrator sync already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation(
            "sync_narrators",
            f"Updating narrators from Audible {'(dry run)' if dry_run else ''}"
        )

        def run_sync():
            tracker.start_operation(operation_id)
            script_path = project_root / "scripts" / "update_narrators_from_audible.py"

            try:
                tracker.update_progress(operation_id, 10, "Loading Audible metadata...")

                cmd = ["python3", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )

                output = result.stdout
                updated_count = 0
                for line in output.split("\n"):
                    if "updated" in line.lower() or "would update" in line.lower():
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                updated_count = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "narrators_updated": updated_count,
                        "dry_run": dry_run,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Narrator sync failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Narrator sync timed out after 10 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_sync, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"Narrator sync started {'(dry run)' if dry_run else ''}",
            "operation_id": operation_id
        })

    # =========================================================================
    # Populate Sort Fields Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/populate-sort-fields-async", methods=["POST"])
    def populate_sort_fields_async() -> FlaskResponse:
        """Populate sort fields for proper alphabetization with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("sort_fields")
        if existing:
            return jsonify({
                "success": False,
                "error": "Sort field population already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation(
            "sort_fields",
            f"Populating sort fields {'(dry run)' if dry_run else ''}"
        )

        def run_populate():
            tracker.start_operation(operation_id)
            script_path = project_root / "scripts" / "populate_sort_fields.py"

            try:
                tracker.update_progress(operation_id, 10, "Analyzing titles and authors...")

                cmd = ["python3", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                output = result.stdout
                updated_count = 0
                for line in output.split("\n"):
                    if "updated" in line.lower() or "would update" in line.lower():
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                updated_count = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "fields_updated": updated_count,
                        "dry_run": dry_run,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Sort field population failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Sort field population timed out after 5 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_populate, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"Sort field population started {'(dry run)' if dry_run else ''}",
            "operation_id": operation_id
        })

    # =========================================================================
    # Find Duplicate Sources Endpoint
    # =========================================================================

    @utilities_ops_bp.route("/api/utilities/find-source-duplicates-async", methods=["POST"])
    def find_source_duplicates_async() -> FlaskResponse:
        """Find duplicate source files (.aaxc) with progress tracking."""
        tracker = get_tracker()
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)

        existing = tracker.is_operation_running("source_duplicates")
        if existing:
            return jsonify({
                "success": False,
                "error": "Duplicate scan already in progress",
                "operation_id": existing
            }), 409

        operation_id = tracker.create_operation(
            "source_duplicates",
            f"Finding duplicate source files {'(dry run)' if dry_run else ''}"
        )

        def run_scan():
            tracker.start_operation(operation_id)
            import os
            script_path = Path("/opt/audiobooks/scripts/find-duplicate-sources")
            if not script_path.exists():
                script_path = project_root.parent / "scripts" / "find-duplicate-sources"

            try:
                tracker.update_progress(operation_id, 10, "Scanning source files...")

                cmd = ["bash", str(script_path)]
                if dry_run:
                    cmd.append("--dry-run")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env={**os.environ, "TERM": "dumb"}
                )

                output = result.stdout
                duplicates_found = 0
                for line in output.split("\n"):
                    if "duplicate" in line.lower():
                        try:
                            import re
                            numbers = re.findall(r"\d+", line)
                            if numbers:
                                duplicates_found = int(numbers[0])
                        except ValueError:
                            pass

                if result.returncode == 0:
                    tracker.complete_operation(operation_id, {
                        "duplicates_found": duplicates_found,
                        "dry_run": dry_run,
                        "output": output[-2000:] if len(output) > 2000 else output
                    })
                else:
                    tracker.fail_operation(operation_id, result.stderr or "Duplicate scan failed")

            except subprocess.TimeoutExpired:
                tracker.fail_operation(operation_id, "Duplicate scan timed out after 10 minutes")
            except Exception as e:
                tracker.fail_operation(operation_id, str(e))

        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": f"Duplicate scan started {'(dry run)' if dry_run else ''}",
            "operation_id": operation_id
        })

    return utilities_ops_bp
