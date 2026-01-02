"""
Library administration utilities - CRUD operations, imports, exports, and maintenance.
"""

import subprocess
import threading
from flask import Blueprint, Response, jsonify, request, send_file
from pathlib import Path

from .core import get_db, FlaskResponse

# Import operation tracking
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from operation_status import get_tracker, create_progress_callback

utilities_bp = Blueprint("utilities", __name__)


def init_utilities_routes(db_path, project_root):
    """Initialize routes with database path and project root."""

    @utilities_bp.route("/api/audiobooks/<int:id>", methods=["PUT"])
    def update_audiobook(id: int) -> FlaskResponse:
        """Update audiobook metadata"""
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        # Build update query dynamically based on provided fields
        allowed_fields = [
            "title",
            "author",
            "narrator",
            "publisher",
            "series",
            "series_sequence",
            "published_year",
            "asin",
            "isbn",
            "description",
        ]
        updates = []
        values = []

        for field in allowed_fields:
            if field in data:
                updates.append(f"{field} = ?")
                values.append(data[field])

        if not updates:
            conn.close()
            return jsonify(
                {"success": False, "error": "No valid fields to update"}
            ), 400

        values.append(id)
        query = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"

        try:
            cursor.execute(query, values)
            conn.commit()
            rows_affected = cursor.rowcount
            conn.close()

            if rows_affected > 0:
                return jsonify({"success": True, "updated": rows_affected})
            else:
                return jsonify({"success": False, "error": "Audiobook not found"}), 404
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/audiobooks/<int:id>", methods=["DELETE"])
    def delete_audiobook(id: int) -> FlaskResponse:
        """Delete audiobook from database (does not delete file)"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            # Delete related records first
            cursor.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM audiobook_topics WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM supplements WHERE audiobook_id = ?", (id,))

            # Delete the audiobook
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (id,))
            rows_affected = cursor.rowcount
            conn.commit()
            conn.close()

            if rows_affected > 0:
                return jsonify({"success": True, "deleted": rows_affected})
            else:
                return jsonify({"success": False, "error": "Audiobook not found"}), 404
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/audiobooks/bulk-update", methods=["POST"])
    def bulk_update_audiobooks() -> FlaskResponse:
        """Update a field for multiple audiobooks"""
        data = request.get_json()

        if not data or "ids" not in data or "field" not in data:
            return jsonify(
                {
                    "success": False,
                    "error": "Missing required fields: ids, field, value",
                }
            ), 400

        ids = data["ids"]
        field = data["field"]
        value = data.get("value")

        # Whitelist allowed fields for bulk update
        allowed_fields = ["narrator", "series", "publisher", "published_year"]
        if field not in allowed_fields:
            return jsonify(
                {
                    "success": False,
                    "error": f"Field not allowed for bulk update: {field}",
                }
            ), 400

        if not ids:
            return jsonify(
                {"success": False, "error": "No audiobook IDs provided"}
            ), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            placeholders = ",".join("?" * len(ids))
            query = f"UPDATE audiobooks SET {field} = ? WHERE id IN ({placeholders})"
            cursor.execute(query, [value] + ids)
            conn.commit()
            updated_count = cursor.rowcount
            conn.close()

            return jsonify({"success": True, "updated_count": updated_count})
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/audiobooks/bulk-delete", methods=["POST"])
    def bulk_delete_audiobooks() -> FlaskResponse:
        """Delete multiple audiobooks"""
        data = request.get_json()

        if not data or "ids" not in data:
            return jsonify(
                {"success": False, "error": "Missing required field: ids"}
            ), 400

        ids = data["ids"]
        delete_files = data.get("delete_files", False)

        if not ids:
            return jsonify(
                {"success": False, "error": "No audiobook IDs provided"}
            ), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            # Get file paths if we need to delete files
            deleted_files = []
            if delete_files:
                placeholders = ",".join("?" * len(ids))
                cursor.execute(
                    f"SELECT id, file_path FROM audiobooks WHERE id IN ({placeholders})",
                    ids,
                )
                for row in cursor.fetchall():
                    file_path = Path(row["file_path"])
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            deleted_files.append(str(file_path))
                        except Exception as e:
                            print(f"Warning: Could not delete file {file_path}: {e}")

            # Delete related records
            placeholders = ",".join("?" * len(ids))
            cursor.execute(
                f"DELETE FROM audiobook_genres WHERE audiobook_id IN ({placeholders})",
                ids,
            )
            cursor.execute(
                f"DELETE FROM audiobook_topics WHERE audiobook_id IN ({placeholders})",
                ids,
            )
            cursor.execute(
                f"DELETE FROM audiobook_eras WHERE audiobook_id IN ({placeholders})",
                ids,
            )
            cursor.execute(
                f"DELETE FROM supplements WHERE audiobook_id IN ({placeholders})", ids
            )

            # Delete audiobooks
            cursor.execute(f"DELETE FROM audiobooks WHERE id IN ({placeholders})", ids)
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()

            return jsonify(
                {
                    "success": True,
                    "deleted_count": deleted_count,
                    "files_deleted": len(deleted_files) if delete_files else 0,
                }
            )
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/audiobooks/missing-narrator", methods=["GET"])
    def get_audiobooks_missing_narrator() -> Response:
        """Get audiobooks without narrator information"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, author, narrator, series, file_path
            FROM audiobooks
            WHERE narrator IS NULL OR narrator = '' OR narrator = 'Unknown Narrator'
            ORDER BY title
            LIMIT 200
        """)

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(audiobooks)

    @utilities_bp.route("/api/audiobooks/missing-hash", methods=["GET"])
    def get_audiobooks_missing_hash() -> Response:
        """Get audiobooks without SHA-256 hash"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, author, narrator, series, file_path
            FROM audiobooks
            WHERE sha256_hash IS NULL OR sha256_hash = ''
            ORDER BY title
            LIMIT 200
        """)

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(audiobooks)

    @utilities_bp.route("/api/utilities/rescan", methods=["POST"])
    def rescan_library() -> FlaskResponse:
        """Trigger a library rescan"""
        scanner_path = project_root / "scanner" / "scan_audiobooks.py"

        if not scanner_path.exists():
            return jsonify({"success": False, "error": "Scanner script not found"}), 500

        try:
            result = subprocess.run(
                ["python3", str(scanner_path)],
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minute timeout for large libraries
            )

            # Parse output to get file count
            output = result.stdout
            files_found = 0
            for line in output.split("\n"):
                if "Total audiobook files:" in line:
                    try:
                        files_found = int(line.split(":")[1].strip())
                    except (ValueError, IndexError):
                        pass

            return jsonify(
                {
                    "success": result.returncode == 0,
                    "files_found": files_found,
                    "output": output[-2000:] if len(output) > 2000 else output,
                    "error": result.stderr if result.returncode != 0 else None,
                }
            )
        except subprocess.TimeoutExpired:
            return jsonify(
                {"success": False, "error": "Scan timed out after 30 minutes"}
            ), 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/utilities/reimport", methods=["POST"])
    def reimport_database() -> FlaskResponse:
        """Reimport audiobooks to database"""
        import_path = project_root / "backend" / "import_to_db.py"

        if not import_path.exists():
            return jsonify({"success": False, "error": "Import script not found"}), 500

        try:
            result = subprocess.run(
                ["python3", str(import_path)],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            # Parse output to get import count
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

            return jsonify(
                {
                    "success": result.returncode == 0,
                    "imported_count": imported_count,
                    "output": output[-2000:] if len(output) > 2000 else output,
                    "error": result.stderr if result.returncode != 0 else None,
                }
            )
        except subprocess.TimeoutExpired:
            return jsonify(
                {"success": False, "error": "Import timed out after 5 minutes"}
            ), 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/utilities/generate-hashes", methods=["POST"])
    def generate_hashes() -> FlaskResponse:
        """Generate SHA-256 hashes for audiobooks"""
        import re as regex

        hash_script = project_root / "scripts" / "generate_hashes.py"

        if not hash_script.exists():
            return jsonify(
                {"success": False, "error": "Hash generation script not found"}
            ), 500

        try:
            result = subprocess.run(
                ["python3", str(hash_script), "--parallel"],
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minute timeout for large libraries
            )

            # Parse output to get hash count
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

            return jsonify(
                {
                    "success": result.returncode == 0,
                    "hashes_generated": hashes_generated,
                    "output": output[-2000:] if len(output) > 2000 else output,
                    "error": result.stderr if result.returncode != 0 else None,
                }
            )
        except subprocess.TimeoutExpired:
            return jsonify(
                {
                    "success": False,
                    "error": "Hash generation timed out after 30 minutes",
                }
            ), 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/utilities/vacuum", methods=["POST"])
    def vacuum_database() -> FlaskResponse:
        """Vacuum the SQLite database to reclaim space"""
        conn = get_db(db_path)

        try:
            # Get size before vacuum
            size_before = db_path.stat().st_size

            # Run VACUUM
            conn.execute("VACUUM")
            conn.close()

            # Get size after vacuum
            size_after = db_path.stat().st_size
            space_reclaimed = (size_before - size_after) / (
                1024 * 1024
            )  # Convert to MB

            return jsonify(
                {
                    "success": True,
                    "size_before_mb": size_before / (1024 * 1024),
                    "size_after_mb": size_after / (1024 * 1024),
                    "space_reclaimed_mb": max(0, space_reclaimed),
                }
            )
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_bp.route("/api/utilities/export-db", methods=["GET"])
    def export_database() -> FlaskResponse:
        """Download the SQLite database file"""
        if db_path.exists():
            return send_file(
                db_path,
                mimetype="application/x-sqlite3",
                as_attachment=True,
                download_name="audiobooks.db",
            )
        else:
            return jsonify({"error": "Database not found"}), 404

    @utilities_bp.route("/api/utilities/export-json", methods=["GET"])
    def export_json() -> Response:
        """Export library as JSON"""
        import json
        from datetime import datetime
        from flask import current_app

        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, author, narrator, publisher, series, series_sequence,
                   duration_hours, file_size_mb, file_path, published_year, asin, isbn
            FROM audiobooks
            ORDER BY title
        """)

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Create response with JSON file
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "total_count": len(audiobooks),
            "audiobooks": audiobooks,
        }

        response = current_app.response_class(
            response=json.dumps(export_data, indent=2),
            status=200,
            mimetype="application/json",
        )
        response.headers["Content-Disposition"] = (
            "attachment; filename=audiobooks_export.json"
        )
        return response

    @utilities_bp.route("/api/utilities/export-csv", methods=["GET"])
    def export_csv() -> Response:
        """Export library as CSV"""
        import csv
        import io
        from datetime import datetime
        from flask import current_app

        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, author, narrator, publisher, series, series_sequence,
                   duration_hours, duration_formatted, file_size_mb, published_year, asin, isbn, file_path
            FROM audiobooks
            ORDER BY title
        """)

        audiobooks = cursor.fetchall()
        conn.close()

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(
            [
                "ID",
                "Title",
                "Author",
                "Narrator",
                "Publisher",
                "Series",
                "Series #",
                "Duration (hours)",
                "Duration",
                "Size (MB)",
                "Year",
                "ASIN",
                "ISBN",
                "File Path",
            ]
        )

        # Write data
        for book in audiobooks:
            writer.writerow(list(book))

        # Create response
        response = current_app.response_class(
            response=output.getvalue(), status=200, mimetype="text/csv"
        )
        response.headers["Content-Disposition"] = (
            f"attachment; filename=audiobooks_export_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        return response

    # =========================================================================
    # Operation Status Endpoints
    # =========================================================================

    @utilities_bp.route("/api/operations/status/<operation_id>", methods=["GET"])
    def get_operation_status(operation_id: str) -> FlaskResponse:
        """Get status of a specific operation."""
        tracker = get_tracker()
        status = tracker.get_status(operation_id)

        if not status:
            return jsonify({"error": "Operation not found"}), 404

        return jsonify(status)

    @utilities_bp.route("/api/operations/active", methods=["GET"])
    def get_active_operations() -> FlaskResponse:
        """Get all active (running) operations."""
        tracker = get_tracker()
        operations = tracker.get_active_operations()
        return jsonify({"operations": operations, "count": len(operations)})

    @utilities_bp.route("/api/operations/all", methods=["GET"])
    def get_all_operations() -> FlaskResponse:
        """Get all tracked operations (including completed)."""
        tracker = get_tracker()
        operations = tracker.get_all_operations()
        return jsonify({"operations": operations, "count": len(operations)})

    @utilities_bp.route("/api/operations/cancel/<operation_id>", methods=["POST"])
    def cancel_operation(operation_id: str) -> FlaskResponse:
        """Cancel an operation (sets flag, actual cancellation depends on operation)."""
        tracker = get_tracker()
        if tracker.cancel_operation(operation_id):
            return jsonify({"success": True, "message": "Operation marked for cancellation"})
        return jsonify({"error": "Operation not found"}), 404

    # =========================================================================
    # Incremental Add Endpoint (Async with Progress)
    # =========================================================================

    @utilities_bp.route("/api/utilities/add-new", methods=["POST"])
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

    @utilities_bp.route("/api/utilities/rescan-async", methods=["POST"])
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

    @utilities_bp.route("/api/utilities/reimport-async", methods=["POST"])
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

    @utilities_bp.route("/api/utilities/generate-hashes-async", methods=["POST"])
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

    # =========================================================================
    # Conversion Monitor Status Endpoint
    # =========================================================================

    @utilities_bp.route("/api/conversion/status", methods=["GET"])
    def get_conversion_status() -> FlaskResponse:
        """
        Get current audiobook conversion status.
        Returns file counts, active processes, and statistics for the monitor.
        """
        # Import config paths
        sys.path.insert(0, str(project_root))
        from config import (
            AUDIOBOOKS_SOURCES,
            AUDIOBOOKS_LIBRARY,
            AUDIOBOOKS_DATA,
        )

        staging_dir = AUDIOBOOKS_DATA / "Staging"
        index_dir = AUDIOBOOKS_DATA / ".index"
        queue_file = index_dir / "queue.txt"

        try:
            # Count source AAXC files
            sources_dir = AUDIOBOOKS_SOURCES
            aaxc_count = len(list(sources_dir.glob("*.aaxc"))) if sources_dir.exists() else 0

            # Count staged opus files (excluding covers)
            staged_count = 0
            if staging_dir.exists():
                for f in staging_dir.glob("*.opus"):
                    if not f.name.endswith(".cover.opus"):
                        staged_count += 1

            # Count library opus files (excluding covers)
            library_count = 0
            if AUDIOBOOKS_LIBRARY.exists():
                for f in AUDIOBOOKS_LIBRARY.rglob("*.opus"):
                    if not f.name.endswith(".cover.opus"):
                        library_count += 1

            # Total converted
            total_converted = library_count + staged_count

            # Queue count (files pending conversion)
            queue_count = 0
            if queue_file.exists():
                with open(queue_file) as f:
                    queue_count = sum(1 for line in f if line.strip())

            # Remaining calculation
            remaining = max(0, aaxc_count - total_converted)

            # Get active ffmpeg opus conversion processes
            ffmpeg_count = 0
            ffmpeg_nice = None
            active_conversions = []
            try:
                result = subprocess.run(
                    ["pgrep", "-c", "-f", "ffmpeg.*libopus"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    ffmpeg_count = int(result.stdout.strip())

                # Get nice value
                ps_result = subprocess.run(
                    ["ps", "-eo", "ni,comm"],
                    capture_output=True,
                    text=True
                )
                for line in ps_result.stdout.split("\n"):
                    if "ffmpeg" in line:
                        parts = line.strip().split()
                        if parts:
                            ffmpeg_nice = parts[0]
                            break

                # Get active conversion file names
                ps_aux = subprocess.run(
                    ["ps", "aux"],
                    capture_output=True,
                    text=True
                )
                for line in ps_aux.stdout.split("\n"):
                    if "ffmpeg" in line and "libopus" in line:
                        # Extract output filename from -f ogg "filename"
                        import re
                        match = re.search(r'-f ogg "([^"]+)"', line)
                        if match:
                            filename = Path(match.group(1)).name
                            if len(filename) > 50:
                                filename = filename[:47] + "..."
                            active_conversions.append(filename)
            except Exception:
                pass

            # System stats
            load_avg = None
            tmpfs_usage = None
            tmpfs_avail = None

            try:
                # CPU idle from /proc/stat
                with open("/proc/loadavg") as f:
                    load_avg = f.read().strip().split()[0]

                # tmpfs usage
                df_result = subprocess.run(
                    ["df", "-h", "/tmp"],
                    capture_output=True,
                    text=True
                )
                if df_result.returncode == 0:
                    lines = df_result.stdout.strip().split("\n")
                    if len(lines) > 1:
                        parts = lines[1].split()
                        if len(parts) >= 5:
                            tmpfs_usage = parts[4]  # e.g., "15%"
                            tmpfs_avail = parts[3]  # e.g., "7.5G"
            except Exception:
                pass

            # Calculate completion percentage
            percent = int(total_converted * 100 / aaxc_count) if aaxc_count > 0 else 0

            return jsonify({
                "success": True,
                "status": {
                    "source_count": aaxc_count,
                    "library_count": library_count,
                    "staged_count": staged_count,
                    "total_converted": total_converted,
                    "queue_count": queue_count,
                    "remaining": remaining,
                    "percent_complete": percent,
                    "is_complete": remaining == 0 and aaxc_count > 0,
                },
                "processes": {
                    "ffmpeg_count": ffmpeg_count,
                    "ffmpeg_nice": ffmpeg_nice,
                    "active_conversions": active_conversions[:6],  # Limit to 6
                },
                "system": {
                    "load_avg": load_avg,
                    "tmpfs_usage": tmpfs_usage,
                    "tmpfs_avail": tmpfs_avail,
                }
            })

        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    return utilities_bp
