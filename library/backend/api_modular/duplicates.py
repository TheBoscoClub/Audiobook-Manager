"""
Duplicate detection endpoints - hash-based and title-based duplicate finding.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

from .auth import admin_if_enabled, auth_if_enabled
from .core import FlaskResponse, get_db

duplicates_bp = Blueprint("duplicates", __name__)
logger = logging.getLogger(__name__)

# Module-level db_path, set by init_duplicates_routes()
_db_path: Path = Path()


def _sanitize_for_log(value: str) -> str:
    """Sanitize a string for safe logging by removing control characters."""
    # Remove newlines, carriage returns, tabs, and other control characters
    # that could be used for log injection attacks
    return "".join(c if c.isprintable() and c not in "\r\n\t" else "_" for c in value)


def _is_safe_path(filepath: Path, allowed_bases: list[Path]) -> bool:
    """
    Validate that a path is safely contained within allowed directories.

    Prevents path traversal attacks by:
    1. Resolving the path to its canonical form (following symlinks)
    2. Checking that the resolved path starts with one of the allowed bases

    Args:
        filepath: The path to validate
        allowed_bases: List of allowed base directories

    Returns:
        True if the path is safely within an allowed directory, False otherwise
    """
    try:
        # Resolve to canonical path (follows symlinks, resolves ..)
        # CodeQL: This IS the path validation function - resolve() is intentional
        resolved = filepath.resolve()
        # Check if it's under any of the allowed base directories
        for base in allowed_bases:
            try:
                base_resolved = base.resolve()
                # Check if resolved path is under the base directory
                resolved.relative_to(base_resolved)
                return True
            except ValueError:
                # Not under this base, try next
                continue
        return False
    except (OSError, RuntimeError):
        # Path resolution failed (broken symlink, permission error, etc.)
        return False


def remove_from_indexes(filepath: Path) -> dict:
    """
    Remove a file path from all checksum index files.
    Called after a file is deleted to keep indexes clean.

    Returns dict with counts of entries removed from each index.
    """
    index_dir = Path(os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks")) / ".index"
    filepath_str = str(filepath)

    removed = {}
    index_files = [
        "source_checksums.idx",
        "library_checksums.idx",
        "source_asins.idx",
        "sources.idx",
    ]

    for idx_name in index_files:
        idx_path = index_dir / idx_name
        if not idx_path.exists():
            continue

        try:
            # Read all lines, filter out ones matching this filepath
            lines = idx_path.read_text().splitlines()
            original_count = len(lines)
            filtered = [line for line in lines if filepath_str not in line]
            new_count = len(filtered)

            if new_count < original_count:
                idx_path.write_text("\n".join(filtered) + "\n" if filtered else "")
                removed[idx_name] = original_count - new_count
        except Exception as e:
            # Use %s formatting to prevent log injection via exception messages
            logger.warning("Failed to update index %s: %s", idx_name, e)

    return removed


def _delete_audiobook_cascade(cursor, audiobook_id: int) -> None:
    """Delete an audiobook and all related records from the database."""
    cursor.execute(
        "DELETE FROM audiobook_topics WHERE audiobook_id = ?",
        (audiobook_id,),
    )
    cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (audiobook_id,))
    cursor.execute(
        "DELETE FROM audiobook_genres WHERE audiobook_id = ?",
        (audiobook_id,),
    )
    cursor.execute("DELETE FROM audiobooks WHERE id = ?", (audiobook_id,))


def _delete_file_and_index(filepath: Path) -> None:
    """Delete a physical file and remove it from checksum indexes."""
    if filepath.exists():
        filepath.unlink()
        remove_from_indexes(filepath)


def _extract_asin_from_basename(basename: str) -> str | None:
    """Extract ASIN if present (first 10 alphanumeric chars before _)."""
    if "_" in basename and len(basename) > 10:
        potential_asin = basename.split("_")[0]
        if len(potential_asin) == 10 and potential_asin.isalnum():
            return potential_asin
    return None


def _build_file_info(fpath: str) -> dict[str, Any]:
    """Build file info dict for a single path in a checksum index."""
    basename = os.path.basename(fpath)
    try:
        size = os.path.getsize(fpath) if os.path.exists(fpath) else 0
        return {
            "path": fpath,
            "basename": basename,
            "asin": _extract_asin_from_basename(basename),
            "size_bytes": size,
            "size_mb": round(size / 1048576, 2),
            "exists": os.path.exists(fpath),
        }
    except Exception as e:
        logger.debug("Error building file info for %s: %s", fpath, e)
        return {
            "path": fpath,
            "basename": basename,
            "asin": None,
            "size_bytes": 0,
            "size_mb": 0,
            "exists": False,
        }


def _parse_checksum_index(index_file: str) -> dict[str, list[str]] | None:
    """Parse a checksum index file into a dict of checksum -> list of paths.

    Returns None on parse failure.
    """
    checksums: dict[str, list[str]] = {}
    try:
        with open(index_file, "r") as f:
            for line in f:
                line = line.strip()
                if "|" in line:
                    checksum, filepath = line.split("|", 1)
                    if checksum not in checksums:
                        checksums[checksum] = []
                    checksums[checksum].append(filepath)
    except Exception as e:
        logger.warning("Failed to parse checksum index %s: %s", index_file, e)
        return None
    return checksums


def _build_duplicate_group(checksum: str, files: list[str]) -> dict[str, Any]:
    """Build a duplicate group dict for files sharing a checksum."""
    file_infos = [_build_file_info(fpath) for fpath in files]

    # Sort by size descending (keep largest)
    file_infos.sort(key=lambda x: int(x.get("size_bytes", 0)), reverse=True)

    # Mark keeper and duplicates
    for i, file_info in enumerate(file_infos):
        file_info["is_keeper"] = i == 0
        file_info["is_duplicate"] = i > 0

    wasted = sum(int(f.get("size_bytes", 0)) for f in file_infos[1:])

    return {
        "checksum": checksum,
        "count": len(files),
        "wasted_mb": round(wasted / 1048576, 2),
        "wasted_bytes": wasted,
        "files": file_infos,
    }


def _find_duplicates_from_index(index_file: str) -> dict:
    """Parse checksum index and find duplicates."""
    if not os.path.exists(index_file):
        return {
            "exists": False,
            "error": f"Index file not found: {index_file}",
            "duplicate_groups": [],
        }

    checksums = _parse_checksum_index(index_file)
    if checksums is None:
        return {
            "exists": True,
            "error": "Failed to parse checksum index",
            "duplicate_groups": [],
        }

    # Find groups with >1 file (duplicates)
    duplicate_groups = []
    total_duplicate_files = 0
    total_wasted_bytes = 0

    for checksum, files in checksums.items():
        if len(files) > 1:
            group = _build_duplicate_group(checksum, files)
            total_wasted_bytes += group["wasted_bytes"]
            total_duplicate_files += len(files) - 1
            # Remove internal-only field before appending
            del group["wasted_bytes"]
            duplicate_groups.append(group)

    # Sort by count descending
    duplicate_groups.sort(key=lambda x: int(x.get("count", 0)), reverse=True)

    return {
        "exists": True,
        "total_files": sum(len(files) for files in checksums.values()),
        "unique_checksums": len(checksums),
        "duplicate_groups": duplicate_groups,
        "total_duplicate_groups": len(duplicate_groups),
        "total_duplicate_files": total_duplicate_files,
        "total_wasted_mb": round(total_wasted_bytes / 1048576, 2),
    }


def _generate_checksums(scan_dir: str, output_file: str, pattern: str) -> dict:
    """Generate checksums for files matching pattern."""
    try:
        # Use find + head + md5sum for efficiency
        cmd = (
            f'find "{scan_dir}" -name "{pattern}" -type f'
            f" 2>/dev/null | sort | while read -r f; do"
            f' checksum=$(head -c 1048576 "$f" 2>/dev/null'
            f' | md5sum | cut -d" " -f1);'
            f' echo "${{checksum}}|${{f}}";'
            f' done > "{output_file}"'
        )
        subprocess.run(["bash", "-c", cmd], check=True, timeout=600)

        # Count results
        with open(output_file, "r") as f:
            count = sum(1 for _ in f)

        return {"success": True, "count": count, "file": output_file}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout after 10 minutes"}
    except Exception as e:
        logger.error("Checksum generation failed: %s", e)
        return {"success": False, "error": "Checksum generation failed"}


def _title_mode_sort_key(x: dict) -> tuple:
    """Sort key for title-mode deletion: prefer real author, then format, then ID."""
    author_priority = 1 if x["norm_author"] == "audiobook" else 0
    fmt_order = {"opus": 1, "m4b": 2, "m4a": 3, "mp3": 4}
    ext = Path(x["file_path"]).suffix.lower().lstrip(".")
    return (author_priority, fmt_order.get(ext, 5), x["id"])


def _check_title_groups(cursor, to_delete: list[dict]) -> tuple[list, list]:
    """Check title-mode groups and return (blocked_ids, safe_to_delete)."""
    title_groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for item in to_delete:
        key = (item["norm_title"], item["duration_group"])
        if key not in title_groups:
            title_groups[key] = []
        title_groups[key].append(item)

    blocked_ids: list[int] = []
    safe_to_delete: list[int] = []

    for (norm_title, duration_group), items in title_groups.items():
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM audiobooks
            WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''),
            '-', ''), '  ', ' '))) = ?
              AND ROUND(duration_hours, 1) = ?
        """,
            (norm_title, duration_group),
        )
        total_copies = cursor.fetchone()["count"]

        if len(items) >= total_copies:
            items_sorted = sorted(items, key=_title_mode_sort_key)
            blocked_ids.append(items_sorted[0]["id"])
            safe_to_delete.extend([i["id"] for i in items_sorted[1:]])
        else:
            safe_to_delete.extend([i["id"] for i in items])

    return blocked_ids, safe_to_delete


def _check_hash_groups(cursor, to_delete: list[dict]) -> tuple[list, list]:
    """Check hash-mode groups and return (blocked_ids, safe_to_delete)."""
    hash_groups: dict[str | None, list[dict[str, Any]]] = {}
    for item in to_delete:
        h = item["sha256_hash"]
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(item)

    blocked_ids: list[int] = []
    safe_to_delete: list[int] = []

    for hash_val, items in hash_groups.items():
        if hash_val is None:
            blocked_ids.extend([i["id"] for i in items])
            continue

        cursor.execute(
            """
            SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?
        """,
            (hash_val,),
        )
        total_copies = cursor.fetchone()["count"]

        if len(items) >= total_copies:
            items_sorted = sorted(items, key=lambda x: x["id"])
            blocked_ids.append(items_sorted[0]["id"])
            safe_to_delete.extend([i["id"] for i in items_sorted[1:]])
        else:
            safe_to_delete.extend([i["id"] for i in items])

    return blocked_ids, safe_to_delete


def _perform_deletions(cursor, safe_to_delete: list[int]) -> tuple[list, list]:
    """Perform actual file and DB deletions. Returns (deleted_files, errors)."""
    deleted_files = []
    errors = []

    for audiobook_id in safe_to_delete:
        cursor.execute(
            "SELECT file_path, title FROM audiobooks WHERE id = ?", (audiobook_id,)
        )
        row = cursor.fetchone()

        if not row:
            continue

        file_path = Path(row["file_path"])
        title = row["title"]

        try:
            _delete_file_and_index(file_path)
            _delete_audiobook_cascade(cursor, audiobook_id)
            deleted_files.append(
                {"id": audiobook_id, "title": title, "path": str(file_path)}
            )
        except Exception as e:
            logger.exception("Error deleting audiobook %d: %s", audiobook_id, e)
            errors.append(
                {"id": audiobook_id, "title": title, "error": "Deletion failed"}
            )

    return deleted_files, errors


def _delete_library_file_with_db(
    cursor, filepath: Path, filepath_str: str
) -> tuple[list, list, list]:
    """Delete a library file with its DB record. Returns (deleted, errors, not_found)."""
    deleted_files: list[dict] = []
    errors: list[dict] = []
    skipped_not_found: list[str] = []

    cursor.execute(
        "SELECT id, title, file_path FROM audiobooks WHERE file_path = ?",
        (filepath_str,),
    )
    row = cursor.fetchone()

    if row:
        audiobook_id = row["id"]
        title = row["title"]
        try:
            _delete_file_and_index(filepath)
            _delete_audiobook_cascade(cursor, audiobook_id)
            deleted_files.append(
                {"path": filepath_str, "title": title, "id": audiobook_id}
            )
        except Exception as e:
            # CodeQL: _sanitize_for_log removes control chars (log injection safe)
            logger.exception(
                "Error deleting library file %s: %s",
                _sanitize_for_log(filepath_str),
                e,
            )
            errors.append({"path": filepath_str, "error": "Deletion failed"})
    elif filepath.exists():
        try:
            _delete_file_and_index(filepath)
            deleted_files.append(
                {"path": filepath_str, "title": filepath.name, "id": None}
            )
        except Exception as e:
            # CodeQL: _sanitize_for_log removes control chars (log injection safe)
            logger.exception(
                "Error deleting file %s: %s",
                _sanitize_for_log(filepath_str),
                e,
            )
            errors.append({"path": filepath_str, "error": "Deletion failed"})
    else:
        skipped_not_found.append(filepath_str)

    return deleted_files, errors, skipped_not_found


def _delete_source_file(filepath: Path, filepath_str: str) -> tuple[list, list, list]:
    """Delete a source file (no DB record). Returns (deleted, errors, not_found)."""
    deleted_files: list[dict] = []
    errors: list[dict] = []
    skipped_not_found: list[str] = []

    # CodeQL: filepath validated by _is_safe_path() before calling this function
    if filepath.exists():
        try:
            _delete_file_and_index(filepath)
            deleted_files.append(
                {"path": filepath_str, "title": filepath.name, "id": None}
            )
        except Exception as e:
            # CodeQL: _sanitize_for_log removes control chars (log injection safe)
            logger.exception(
                "Error deleting source file %s: %s",
                _sanitize_for_log(filepath_str),
                e,
            )
            errors.append({"path": filepath_str, "error": "Deletion failed"})
    else:
        skipped_not_found.append(filepath_str)

    return deleted_files, errors, skipped_not_found


# --- Route handlers (module-level, registered by init_duplicates_routes) ---


@duplicates_bp.route("/api/hash-stats", methods=["GET"])
@auth_if_enabled
def get_hash_stats() -> Response:
    """Get hash generation statistics"""
    conn = get_db(_db_path)
    try:
        cursor = conn.cursor()

        # Check if sha256_hash column exists
        cursor.execute("PRAGMA table_info(audiobooks)")
        columns = [row["name"] for row in cursor.fetchall()]

        if "sha256_hash" not in columns:
            return jsonify(
                {
                    "hash_column_exists": False,
                    "total_audiobooks": 0,
                    "hashed_count": 0,
                    "unhashed_count": 0,
                    "duplicate_groups": 0,
                }
            )

        cursor.execute("SELECT COUNT(*) as total FROM audiobooks")
        total = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash IS NOT NULL"
        )
        hashed = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT COUNT(*) as count FROM (
                SELECT sha256_hash FROM audiobooks
                WHERE sha256_hash IS NOT NULL
                GROUP BY sha256_hash
                HAVING COUNT(*) > 1
            )
        """)
        duplicate_groups = cursor.fetchone()["count"]

        return jsonify(
            {
                "hash_column_exists": True,
                "total_audiobooks": total,
                "hashed_count": hashed,
                "unhashed_count": total - hashed,
                "hashed_percentage": (
                    round(hashed * 100 / total, 1) if total > 0 else 0
                ),
                "duplicate_groups": duplicate_groups,
            }
        )
    finally:
        conn.close()


@duplicates_bp.route("/api/duplicates", methods=["GET"])
@auth_if_enabled
def get_duplicates() -> FlaskResponse:
    """Get all duplicate audiobook groups"""
    conn = get_db(_db_path)
    try:
        cursor = conn.cursor()

        # Check if sha256_hash column exists
        cursor.execute("PRAGMA table_info(audiobooks)")
        columns = [row["name"] for row in cursor.fetchall()]

        if "sha256_hash" not in columns:
            return (
                jsonify({"error": "Hash column not found. Run hash generation first."}),
                400,
            )

        # Get all duplicate groups
        cursor.execute("""
            SELECT sha256_hash, COUNT(*) as count
            FROM audiobooks
            WHERE sha256_hash IS NOT NULL
            GROUP BY sha256_hash
            HAVING count > 1
            ORDER BY count DESC
        """)
        groups = cursor.fetchall()

        duplicate_groups = []
        total_wasted_space = 0

        for group in groups:
            hash_val = group["sha256_hash"]
            count = group["count"]

            # Get all files in this group
            cursor.execute(
                """
                SELECT id, title, author, narrator, file_path, file_size_mb,
                       format, duration_formatted, cover_path
                FROM audiobooks
                WHERE sha256_hash = ?
                ORDER BY id ASC
            """,
                (hash_val,),
            )

            files = [dict(row) for row in cursor.fetchall()]

            # First file (by ID) is the "keeper"
            for i, f in enumerate(files):
                f["is_keeper"] = i == 0
                f["is_duplicate"] = i > 0

            file_size = files[0]["file_size_mb"] if files else 0
            wasted = file_size * (count - 1)
            total_wasted_space += wasted

            duplicate_groups.append(
                {
                    "hash": hash_val,
                    "count": count,
                    "file_size_mb": file_size,
                    "wasted_mb": round(wasted, 2),
                    "files": files,
                }
            )

        return jsonify(
            {
                "duplicate_groups": duplicate_groups,
                "total_groups": len(duplicate_groups),
                "total_wasted_mb": round(total_wasted_space, 2),
                "total_duplicate_files": sum(g["count"] - 1 for g in duplicate_groups),
            }
        )
    finally:
        conn.close()


@duplicates_bp.route("/api/duplicates/by-title", methods=["GET"])
@auth_if_enabled
def get_duplicates_by_title() -> Response:
    """
    Get duplicate audiobooks based on normalized title and REAL author.
    This finds "same book, different version/format" entries.

    IMPROVED LOGIC:
    - Excludes "Audiobook" as a valid author for grouping
    - Groups by title + real author + similar duration (within 10%)
    - Prevents flagging different books with same title as duplicates
    """
    conn = get_db(_db_path)
    cursor = conn.cursor()

    # Find duplicates by normalized title + real author (excluding "Audiobook")
    # Also require similar duration to avoid grouping different books
    cursor.execute("""
        SELECT
            LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''),
            '  ', ' '))) as norm_title,
            LOWER(TRIM(author)) as norm_author,
            ROUND(duration_hours, 1) as duration_group,
            COUNT(*) as count
        FROM audiobooks
        WHERE title IS NOT NULL
          AND author IS NOT NULL
          AND LOWER(TRIM(author)) != 'audiobook'
          AND LOWER(TRIM(author)) != 'unknown author'
        GROUP BY norm_title, norm_author, duration_group
        HAVING count > 1
        ORDER BY count DESC, norm_title COLLATE NOCASE
    """)
    groups = cursor.fetchall()

    duplicate_groups = []
    total_potential_savings = 0

    for group in groups:
        norm_title = group["norm_title"]
        norm_author = group["norm_author"]
        duration_group = group["duration_group"]

        # Get all files in this group (including any with "Audiobook" author
        # that match)
        cursor.execute(
            """
            SELECT id, title, author, narrator, file_path, file_size_mb,
                   format, duration_formatted, duration_hours,
                   cover_path, sha256_hash
            FROM audiobooks
            WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''),
            '-', ''), '  ', ' '))) = ?
              AND (LOWER(TRIM(author)) = ? OR LOWER(TRIM(author)) = 'audiobook')
              AND ROUND(duration_hours, 1) = ?
            ORDER BY
                -- Prefer entries with real author over "Audiobook"
                CASE WHEN LOWER(TRIM(author)) = 'audiobook' THEN 1 ELSE 0 END,
                CASE format
                    WHEN 'opus' THEN 1
                    WHEN 'm4b' THEN 2
                    WHEN 'm4a' THEN 3
                    WHEN 'mp3' THEN 4
                    ELSE 5
                END,
                file_size_mb DESC,
                id ASC
        """,
            (norm_title, norm_author, duration_group),
        )

        files = [dict(row) for row in cursor.fetchall()]

        if len(files) < 2:
            continue

        # First file (with real author, preferred format) is the "keeper"
        for i, f in enumerate(files):
            f["is_keeper"] = i == 0
            f["is_duplicate"] = i > 0

        # Calculate potential savings (sum of all but the largest file)
        sizes = sorted([f["file_size_mb"] for f in files], reverse=True)
        potential_savings = sum(sizes[1:])  # All except the largest
        total_potential_savings += potential_savings

        # Use the real author (first file has real author due to ORDER BY)
        display_author = _resolve_display_author(files)

        duplicate_groups.append(
            {
                "title": files[0]["title"],
                "author": display_author,
                "count": len(files),
                "potential_savings_mb": round(potential_savings, 2),
                "files": files,
            }
        )

    conn.close()

    return jsonify(
        {
            "duplicate_groups": duplicate_groups,
            "total_groups": len(duplicate_groups),
            "total_potential_savings_mb": round(total_potential_savings, 2),
            "total_duplicate_files": sum(g["count"] - 1 for g in duplicate_groups),
        }
    )


def _resolve_display_author(files: list[dict]) -> str:
    """Find the best display author from a list of files, preferring non-'Audiobook'."""
    display_author = files[0]["author"]
    if display_author.lower() == "audiobook":
        for f in files:
            if f["author"].lower() != "audiobook":
                display_author = f["author"]
                break
    return display_author


@duplicates_bp.route("/api/duplicates/delete", methods=["POST"])
@admin_if_enabled
def delete_duplicates() -> FlaskResponse:
    """
    Delete selected duplicate audiobooks.
    SAFETY: Will NEVER delete the last remaining copy of any audiobook.

    Request body:
    {
        "audiobook_ids": [1, 2, 3],  // IDs to delete
        "mode": "title" or "hash"    // Optional, defaults to "title"
    }

    IMPROVED SAFETY:
    - Groups by title + duration (not author, since author may be "Audiobook")
    - Ensures at least one copy with REAL author is kept
    - Prefers keeping entries with real author over "Audiobook" entries
    """
    data = request.get_json()
    if not data or "audiobook_ids" not in data:
        return jsonify({"error": "Missing audiobook_ids"}), 400

    ids_to_delete = data["audiobook_ids"]
    if not ids_to_delete:
        return jsonify({"error": "No audiobook IDs provided"}), 400

    mode = data.get("mode", "title")  # Default to title mode

    conn = get_db(_db_path)
    cursor = conn.cursor()

    # Get all audiobooks to be deleted with their grouping keys
    placeholders = ",".join("?" * len(ids_to_delete))
    query = (
        "SELECT id, sha256_hash, title, author, file_path,"  # nosec B608
        " duration_hours, file_size_mb,"
        " LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''),"
        " '-', ''), '  ', ' '))) as norm_title,"
        " LOWER(TRIM(author)) as norm_author,"
        " ROUND(duration_hours, 1) as duration_group"
        " FROM audiobooks"
        f" WHERE id IN ({placeholders})"
    )
    cursor.execute(query, ids_to_delete)

    to_delete = [dict(row) for row in cursor.fetchall()]

    if mode == "title":
        blocked_ids, safe_to_delete = _check_title_groups(cursor, to_delete)
    else:
        blocked_ids, safe_to_delete = _check_hash_groups(cursor, to_delete)

    deleted_files, errors = _perform_deletions(cursor, safe_to_delete)

    conn.commit()
    conn.close()

    return jsonify(
        {
            "success": True,
            "deleted_count": len(deleted_files),
            "deleted_files": deleted_files,
            "blocked_count": len(blocked_ids),
            "blocked_ids": blocked_ids,
            "blocked_reason": (
                "These IDs were blocked to prevent deleting the last copy"
            ),
            "errors": errors,
        }
    )


@duplicates_bp.route("/api/duplicates/by-checksum", methods=["GET"])
@auth_if_enabled
def get_duplicates_by_checksum() -> Response:
    """
    Get duplicate files based on filesystem checksum indexes.

    These checksums are generated from the actual file content (first 1MB),
    making them authoritative for detecting true duplicates regardless of
    filename, title, or ASIN differences.

    Query params:
        type: "sources" | "library" | "both" (default: "both")
    """
    check_type = request.args.get("type", "both")
    index_dir = os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks") + "/.index"

    result: dict[str, Any] = {
        "sources": None,
        "library": None,
    }

    if check_type in ("sources", "both"):
        result["sources"] = _find_duplicates_from_index(
            os.path.join(index_dir, "source_checksums.idx")
        )

    if check_type in ("library", "both"):
        result["library"] = _find_duplicates_from_index(
            os.path.join(index_dir, "library_checksums.idx")
        )

    return jsonify(result)


@duplicates_bp.route("/api/duplicates/regenerate-checksums", methods=["POST"])
@admin_if_enabled
def regenerate_checksums() -> Response:
    """
    Regenerate checksum indexes for sources and/or library.

    Request body:
        type: "sources" | "library" | "both" (default: "both")

    Note: This runs synchronously and may take several minutes for large
    collections.
    """
    data = request.get_json() or {}
    check_type = data.get("type", "both")

    index_dir = os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks") + "/.index"
    sources_dir = os.environ.get("AUDIOBOOKS_SOURCES", "/srv/audiobooks/Sources")
    library_dir = os.environ.get("AUDIOBOOKS_LIBRARY", "/srv/audiobooks/Library")

    results = {}

    if check_type in ("sources", "both"):
        results["sources"] = _generate_checksums(
            sources_dir, os.path.join(index_dir, "source_checksums.idx"), "*.aaxc"
        )

    if check_type in ("library", "both"):
        results["library"] = _generate_checksums(
            library_dir, os.path.join(index_dir, "library_checksums.idx"), "*.opus"
        )

    return jsonify(results)


@duplicates_bp.route("/api/duplicates/delete-by-path", methods=["POST"])
@admin_if_enabled
def delete_duplicates_by_path() -> FlaskResponse:
    """
    Delete duplicate files by file path (for checksum-based duplicates).

    For library files: looks up DB record by path, deletes both file and DB entry.
    For source files: deletes file only (sources aren't in DB).

    SAFETY: Will NEVER delete the keeper (first/largest file in each group).

    Request body:
    {
        "paths": ["/path/to/file1.opus", "/path/to/file2.opus"],
        "type": "library" | "sources"
    }
    """
    data = request.get_json()
    if not data or "paths" not in data:
        return jsonify({"error": "Missing paths"}), 400

    paths_to_delete = data["paths"]
    file_type = data.get("type", "library")

    if not paths_to_delete:
        return jsonify({"error": "No paths provided"}), 400

    # Get allowed directories from environment for path safety validation
    library_dir = Path(os.environ.get("AUDIOBOOKS_LIBRARY", "/srv/audiobooks/Library"))
    sources_dir = Path(os.environ.get("AUDIOBOOKS_SOURCES", "/srv/audiobooks/Sources"))

    # Determine which directories are allowed based on file type
    if file_type == "library":
        allowed_bases = [library_dir]
    else:  # sources
        allowed_bases = [sources_dir]

    conn = get_db(_db_path)
    cursor = conn.cursor()

    deleted_files: list[dict] = []
    errors: list[dict] = []
    skipped_not_found: list[str] = []
    skipped_unsafe: list[str] = []

    for filepath_str in paths_to_delete:
        filepath = Path(filepath_str)

        # SECURITY: Validate path is within allowed directories
        if not _is_safe_path(filepath, allowed_bases):
            skipped_unsafe.append(filepath_str)
            continue

        if file_type == "library":
            d, e, nf = _delete_library_file_with_db(cursor, filepath, filepath_str)
        else:
            d, e, nf = _delete_source_file(filepath, filepath_str)

        deleted_files.extend(d)
        errors.extend(e)
        skipped_not_found.extend(nf)

    conn.commit()
    conn.close()

    return jsonify(
        {
            "success": True,
            "deleted_count": len(deleted_files),
            "deleted_files": deleted_files,
            "skipped_not_found": skipped_not_found,
            "skipped_unsafe": skipped_unsafe,
            "errors": errors,
        }
    )


@duplicates_bp.route("/api/duplicates/verify", methods=["POST"])
@auth_if_enabled
def verify_deletion_safe() -> FlaskResponse:
    """
    Verify that a list of IDs can be safely deleted.
    Returns which IDs are safe and which would delete the last copy.
    """
    data = request.get_json()
    if not data or "audiobook_ids" not in data:
        return jsonify({"error": "Missing audiobook_ids"}), 400

    ids_to_check = data["audiobook_ids"]

    conn = get_db(_db_path)
    cursor = conn.cursor()

    placeholders = ",".join("?" * len(ids_to_check))
    # nosec B608 — placeholders is `?,?,?...` literal; ids_to_check bound via params
    _dup_sql = f"SELECT id, sha256_hash, title FROM audiobooks WHERE id IN ({placeholders})"  # nosec B608  # nosemgrep: python.django.security.injection.tainted-sql-string.tainted-sql-string,python.flask.security.injection.tainted-sql-string.tainted-sql-string,python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    cursor.execute(_dup_sql, ids_to_check)  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query

    items = [dict(row) for row in cursor.fetchall()]

    safe_ids, unsafe_ids = _verify_hash_groups(cursor, items)

    conn.close()

    return jsonify(
        {
            "safe_ids": safe_ids,
            "unsafe_ids": unsafe_ids,
            "safe_count": len(safe_ids),
            "unsafe_count": len(unsafe_ids),
        }
    )


def _verify_hash_groups(cursor, items: list[dict]) -> tuple[list[int], list[dict]]:
    """Verify which items can be safely deleted by hash grouping."""
    hash_groups: dict[str | None, list[dict[str, Any]]] = {}
    for item in items:
        h = item["sha256_hash"]
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(item)

    safe_ids: list[int] = []
    unsafe_ids: list[dict] = []

    for hash_val, group_items in hash_groups.items():
        if hash_val is None:
            unsafe_ids.extend(
                [
                    {
                        "id": i["id"],
                        "title": i["title"],
                        "reason": "No hash - cannot verify duplicates",
                    }
                    for i in group_items
                ]
            )
            continue

        cursor.execute(
            "SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?",
            (hash_val,),
        )
        total_copies = cursor.fetchone()["count"]

        if len(group_items) >= total_copies:
            sorted_items = sorted(group_items, key=lambda x: x["id"])
            unsafe_ids.append(
                {
                    "id": sorted_items[0]["id"],
                    "title": sorted_items[0]["title"],
                    "reason": "Last remaining copy - protected from deletion",
                }
            )
            safe_ids.extend([i["id"] for i in sorted_items[1:]])
        else:
            safe_ids.extend([i["id"] for i in group_items])

    return safe_ids, unsafe_ids


def init_duplicates_routes(db_path):
    """Initialize routes with database path."""
    global _db_path
    _db_path = db_path
    return duplicates_bp
