"""
Database maintenance operations.
Handles rescan, reimport, hash generation, vacuum, and export operations.
"""

import subprocess
from flask import Blueprint, Response, jsonify, send_file

from .core import get_db, FlaskResponse

utilities_db_bp = Blueprint("utilities_db", __name__)


def init_db_routes(db_path, project_root):
    """Initialize database operation routes with database path and project root."""

    @utilities_db_bp.route("/api/utilities/rescan", methods=["POST"])
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

    @utilities_db_bp.route("/api/utilities/reimport", methods=["POST"])
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

    @utilities_db_bp.route("/api/utilities/generate-hashes", methods=["POST"])
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

    @utilities_db_bp.route("/api/utilities/vacuum", methods=["POST"])
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

    @utilities_db_bp.route("/api/utilities/export-db", methods=["GET"])
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

    @utilities_db_bp.route("/api/utilities/export-json", methods=["GET"])
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

    @utilities_db_bp.route("/api/utilities/export-csv", methods=["GET"])
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

    return utilities_db_bp
