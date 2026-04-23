"""
Database maintenance operations.
Handles rescan, reimport, hash generation, vacuum, and export operations.
"""

import logging
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import sys
from pathlib import Path

from flask import Blueprint, jsonify, send_file

from .auth import admin_if_enabled
from .core import FlaskResponse, get_db

utilities_db_bp = Blueprint("utilities_db", __name__)
logger = logging.getLogger(__name__)

# Module-level state, set by init_db_routes()
_db_path: Path | None = None
_project_root: Path | None = None


def _truncate_output(output: str, max_len: int = 2000) -> str:
    """Truncate output to max_len, keeping the tail."""
    return output[-max_len:] if len(output) > max_len else output


def _subprocess_result_payload(result, count_key: str, count_value: int) -> dict:
    """Build a standard JSON payload from a subprocess result."""
    return {
        "success": result.returncode == 0,
        count_key: count_value,
        "output": _truncate_output(result.stdout),
        "error": result.stderr if result.returncode != 0 else None,
    }


def _run_script(script_path, timeout: int):
    """Run a Python script as a subprocess with the given timeout."""
    return subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
        [sys.executable, str(script_path)], capture_output=True, text=True, timeout=timeout
    )


def _run_script_with_args(script_path, args: list, timeout: int):
    """Run a Python script with extra arguments."""
    return subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
        [sys.executable, str(script_path)] + args, capture_output=True, text=True, timeout=timeout
    )


def _parse_files_found(output: str) -> int:
    """Parse 'Total audiobook files: N' from scanner output."""
    for line in output.split("\n"):
        if "Total audiobook files:" in line:
            try:
                return int(line.split(":")[1].strip())
            except (ValueError, IndexError):  # fmt: skip
                pass
    return 0


def _parse_imported_count(output: str) -> int:
    """Parse 'Imported N audiobooks' from import output."""
    for line in output.split("\n"):
        if "Imported" in line and "audiobooks" in line:
            try:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "Imported" and i + 1 < len(parts):
                        return int(parts[i + 1])
            except (ValueError, IndexError):  # fmt: skip
                pass
    return 0


def _parse_hashes_generated(output: str) -> int:
    """Parse hash count from generate_hashes output."""
    import re as regex

    for line in output.split("\n"):
        if "Generated" in line or "hashes" in line.lower():
            try:
                numbers = regex.findall(r"\d+", line)
                if numbers:
                    return int(numbers[0])
            except ValueError:
                pass
    return 0


def _error_response(message: str, status: int = 500) -> FlaskResponse:
    """Return a standard error JSON response."""
    return jsonify({"success": False, "error": message}), status


@utilities_db_bp.route("/api/utilities/rescan", methods=["POST"])
@admin_if_enabled
def rescan_library() -> FlaskResponse:
    """Trigger a library rescan."""
    if _project_root is None:
        return _error_response("Project root not configured")
    scanner_path = _project_root / "scanner" / "scan_audiobooks.py"
    if not scanner_path.exists():
        return _error_response("Scanner script not found")

    try:
        result = _run_script(scanner_path, timeout=1800)
        files_found = _parse_files_found(result.stdout)
        return jsonify(_subprocess_result_payload(result, "files_found", files_found))
    except subprocess.TimeoutExpired:
        return _error_response("Scan timed out after 30 minutes")
    except Exception as e:
        logger.exception("Error during library rescan: %s", e)
        return _error_response("Library rescan failed")


@utilities_db_bp.route("/api/utilities/reimport", methods=["POST"])
@admin_if_enabled
def reimport_database() -> FlaskResponse:
    """Reimport audiobooks to database."""
    if _project_root is None:
        return _error_response("Project root not configured")
    import_path = _project_root / "backend" / "import_to_db.py"
    if not import_path.exists():
        return _error_response("Import script not found")

    try:
        result = _run_script(import_path, timeout=300)
        imported_count = _parse_imported_count(result.stdout)
        return jsonify(_subprocess_result_payload(result, "imported_count", imported_count))
    except subprocess.TimeoutExpired:
        return _error_response("Import timed out after 5 minutes")
    except Exception as e:
        logger.exception("Error during database reimport: %s", e)
        return _error_response("Database reimport failed")


@utilities_db_bp.route("/api/utilities/generate-hashes", methods=["POST"])
@admin_if_enabled
def generate_hashes() -> FlaskResponse:
    """Generate SHA-256 hashes for audiobooks."""
    if _project_root is None:
        return _error_response("Project root not configured")
    hash_script = _project_root / "scripts" / "generate_hashes.py"
    if not hash_script.exists():
        return _error_response("Hash generation script not found")

    try:
        result = _run_script_with_args(hash_script, ["--parallel"], timeout=1800)
        hashes_generated = _parse_hashes_generated(result.stdout)
        return jsonify(_subprocess_result_payload(result, "hashes_generated", hashes_generated))
    except subprocess.TimeoutExpired:
        return _error_response("Hash generation timed out after 30 minutes")
    except Exception as e:
        logger.exception("Error during hash generation: %s", e)
        return _error_response("Hash generation failed")


@utilities_db_bp.route("/api/utilities/vacuum", methods=["POST"])
@admin_if_enabled
def vacuum_database() -> FlaskResponse:
    """Vacuum the SQLite database to reclaim space."""
    if _db_path is None:
        return _error_response("Database not configured")
    conn = get_db(_db_path)
    try:
        size_before = _db_path.stat().st_size
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("VACUUM")
        conn.close()
        size_after = _db_path.stat().st_size
        space_reclaimed = (size_before - size_after) / (1024 * 1024)
        return jsonify(
            {
                "success": True,
                "size_before_mb": size_before / (1024 * 1024),
                "size_after_mb": size_after / (1024 * 1024),
                "space_reclaimed_mb": max(0, space_reclaimed),
            }
        )
    except Exception as e:
        logger.exception("Error during database vacuum: %s", e)
        return _error_response("Database vacuum failed")


@utilities_db_bp.route("/api/utilities/export-db", methods=["GET"])
@admin_if_enabled
def export_database() -> FlaskResponse:
    """Download the SQLite database file."""
    if _db_path is None:
        return jsonify({"error": "Database not found"}), 404
    if _db_path.exists():
        return send_file(
            _db_path,
            mimetype="application/x-sqlite3",
            as_attachment=True,
            download_name="audiobooks.db",
        )
    return jsonify({"error": "Database not found"}), 404


@utilities_db_bp.route("/api/utilities/export-json", methods=["GET"])
@admin_if_enabled
def export_json() -> FlaskResponse:
    """Export library as JSON."""
    import json
    from datetime import datetime

    from flask import current_app

    if _db_path is None:
        return jsonify({"error": "Database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, author, narrator, publisher, series, series_sequence,
               duration_hours, file_size_mb, file_path, published_year, asin, isbn
        FROM audiobooks
        ORDER BY title COLLATE NOCASE
    """)
    audiobooks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    export_data = {
        "exported_at": datetime.now().isoformat(),
        "total_count": len(audiobooks),
        "audiobooks": audiobooks,
    }

    response = current_app.response_class(
        response=json.dumps(export_data, indent=2), status=200, mimetype="application/json"
    )
    response.headers["Content-Disposition"] = "attachment; filename=audiobooks_export.json"
    return response


@utilities_db_bp.route("/api/utilities/export-csv", methods=["GET"])
@admin_if_enabled
def export_csv() -> FlaskResponse:
    """Export library as CSV."""
    import csv
    import io
    from datetime import datetime

    from flask import current_app

    if _db_path is None:
        return jsonify({"error": "Database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, author, narrator, publisher, series, series_sequence,
               duration_hours, duration_formatted, file_size_mb,
               published_year, asin, isbn, file_path
        FROM audiobooks
        ORDER BY title COLLATE NOCASE
    """)
    audiobooks = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
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
    for book in audiobooks:
        writer.writerow(list(book))

    response = current_app.response_class(
        response=output.getvalue(), status=200, mimetype="text/csv"
    )
    export_filename = f"audiobooks_export_{datetime.now().strftime('%Y%m%d')}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={export_filename}"
    return response


def init_db_routes(db_path, project_root):
    """Initialize database operation routes with database path and project root."""
    global _db_path, _project_root
    _db_path = db_path
    _project_root = project_root
    return utilities_db_bp
