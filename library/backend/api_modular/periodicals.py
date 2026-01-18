"""
Periodicals API - Non-audiobook content from Audible library

Provides endpoints for browsing podcasts, news, shows, and other
non-audiobook content that appears in the user's Audible library.

Content types synced:
    - Podcast: podcast series and episodes
    - Newspaper / Magazine: NYT Digest, etc.
    - Show: meditation series, interview shows
    - Radio/TV Program: documentaries, radio dramas

Parent items (series) have parent_asin=NULL; episodes have parent_asin set.

Endpoints:
    GET  /api/v1/periodicals              - List periodicals (with filtering)
    GET  /api/v1/periodicals/<asin>       - Item details
    GET  /api/v1/periodicals/<asin>/episodes - List episodes for a parent
    POST /api/v1/periodicals/download     - Queue items for download
    DEL  /api/v1/periodicals/download/<a> - Cancel queued download
    GET  /api/v1/periodicals/queue        - Get download queue
    GET  /api/v1/periodicals/sync/status  - SSE stream for sync status
    POST /api/v1/periodicals/sync/trigger - Manually trigger sync
    GET  /api/v1/periodicals/categories   - List categories with counts
    GET  /api/v1/periodicals/parents      - List parent items with episode counts

Position Sync (Whispersync):
    GET  /api/v1/periodicals/<asin>/position     - Get position for item
    PUT  /api/v1/periodicals/<asin>/position     - Update local position
    POST /api/v1/periodicals/<asin>/position/sync - Sync with Audible cloud
    GET  /api/v1/periodicals/position/test/<asin> - Test if Audible supports position for ASIN
"""

import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, current_app, g, jsonify, request

from .core import get_db

# Audible client for position sync (optional - graceful degradation)
RND_PATH = Path(__file__).parent.parent.parent.parent / "rnd"
sys.path.insert(0, str(RND_PATH))

try:
    import audible
    from credential_manager import has_stored_credential, retrieve_credential

    AUDIBLE_AVAILABLE = True
except ImportError as e:
    AUDIBLE_AVAILABLE = False
    AUDIBLE_IMPORT_ERROR = str(e)

# Audible config paths
AUDIBLE_CONFIG_DIR = Path.home() / ".audible"
AUTH_FILE = AUDIBLE_CONFIG_DIR / "audible.json"
COUNTRY_CODE = "us"

# Script paths - use environment variable with fallback
_audiobooks_home = os.environ.get("AUDIOBOOKS_HOME", "/opt/audiobooks")

periodicals_bp = Blueprint("periodicals", __name__)

# ASIN validation regex
ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")


def validate_asin(asin: str) -> bool:
    """Validate ASIN format for security."""
    return bool(ASIN_PATTERN.match(asin))


def init_periodicals_routes(db_path: str) -> None:
    """Initialize periodicals routes with database path."""

    @periodicals_bp.before_request
    def before_request():
        g.db_path = db_path

    @periodicals_bp.route("/api/v1/periodicals", methods=["GET"])
    def list_periodicals():
        """List periodical items.

        Query params:
            category: Filter by category (podcast, news, meditation, documentary, show, other)
            type: Filter by 'parents' (series only) or 'episodes' (episodes only)
            parent_asin: Filter episodes by parent ASIN
            sort: Sort by 'title', 'runtime', 'release' (default: title)
            page: Page number (default: 1)
            per_page: Items per page (default: 50, max: 200)
        """
        db = get_db(g.db_path)
        category = request.args.get("category")
        item_type = request.args.get("type")
        parent_asin = request.args.get("parent_asin")
        sort = request.args.get("sort", "title")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        # Validate parent_asin if provided
        if parent_asin and not validate_asin(parent_asin):
            return jsonify({"error": "Invalid parent_asin format"}), 400

        # Build query
        query = """
            SELECT
                p.asin,
                p.title,
                p.author,
                p.category,
                p.content_type,
                p.content_delivery_type,
                p.runtime_minutes,
                p.release_date,
                p.description,
                p.cover_url,
                p.is_downloaded,
                p.download_requested,
                p.last_synced,
                p.parent_asin
            FROM periodicals p
        """
        conditions = []
        params = []

        if category:
            conditions.append("p.category = ?")
            params.append(category)

        if item_type == "parents":
            conditions.append("p.parent_asin IS NULL")
        elif item_type == "episodes":
            conditions.append("p.parent_asin IS NOT NULL")

        if parent_asin:
            conditions.append("p.parent_asin = ?")
            params.append(parent_asin)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Sort options
        sort_map = {
            "title": "p.title ASC",
            "runtime": "p.runtime_minutes DESC",
            "release": "p.release_date DESC",
            "category": "p.category ASC, p.title ASC",
        }
        query += f" ORDER BY {sort_map.get(sort, 'p.title ASC')}"

        # Get total count
        count_query = "SELECT COUNT(*) FROM periodicals p"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        total = db.execute(count_query, params).fetchone()[0]

        # Add pagination
        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, offset])

        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        periodicals = []
        for row in rows:
            periodicals.append(
                {
                    "asin": row[0],
                    "title": row[1],
                    "author": row[2],
                    "category": row[3],
                    "content_type": row[4],
                    "content_delivery_type": row[5],
                    "runtime_minutes": row[6],
                    "release_date": row[7],
                    "description": row[8],
                    "cover_url": row[9],
                    "is_downloaded": bool(row[10]),
                    "download_requested": bool(row[11]),
                    "last_synced": row[12],
                    "parent_asin": row[13],
                }
            )

        return jsonify(
            {
                "periodicals": periodicals,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/parents", methods=["GET"])
    def list_parents():
        """List parent periodicals (series) with episode counts.

        Query params:
            category: Filter by category
            sort: Sort by 'title', 'episode_count', 'release' (default: title)
            page: Page number (default: 1)
            per_page: Items per page (default: 50, max: 200)
        """
        db = get_db(g.db_path)
        category = request.args.get("category")
        sort = request.args.get("sort", "title")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        # Build query using the view
        query = """
            SELECT
                asin,
                title,
                author,
                category,
                content_type,
                runtime_minutes,
                release_date,
                description,
                cover_url,
                is_downloaded,
                download_requested,
                last_synced,
                episode_count
            FROM periodicals_parents
        """
        conditions = []
        params = []

        if category:
            conditions.append("category = ?")
            params.append(category)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Sort options
        sort_map = {
            "title": "title ASC",
            "episode_count": "episode_count DESC, title ASC",
            "release": "release_date DESC",
        }
        query += f" ORDER BY {sort_map.get(sort, 'title ASC')}"

        # Get total count
        count_query = "SELECT COUNT(*) FROM periodicals_parents"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        total = db.execute(count_query, params).fetchone()[0]

        # Add pagination
        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, offset])

        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        parents = []
        for row in rows:
            parents.append(
                {
                    "asin": row[0],
                    "title": row[1],
                    "author": row[2],
                    "category": row[3],
                    "content_type": row[4],
                    "runtime_minutes": row[5],
                    "release_date": row[6],
                    "description": row[7],
                    "cover_url": row[8],
                    "is_downloaded": bool(row[9]),
                    "download_requested": bool(row[10]),
                    "last_synced": row[11],
                    "episode_count": row[12],
                }
            )

        return jsonify(
            {
                "parents": parents,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/<asin>", methods=["GET"])
    def periodical_details(asin: str):
        """Get detailed info for a single periodical."""
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db(g.db_path)
        row = db.execute(
            """
            SELECT
                asin,
                title,
                author,
                narrator,
                category,
                content_type,
                content_delivery_type,
                runtime_minutes,
                release_date,
                description,
                cover_url,
                is_downloaded,
                download_requested,
                download_priority,
                last_synced,
                created_at,
                updated_at,
                parent_asin
            FROM periodicals
            WHERE asin = ?
        """,
            [asin],
        ).fetchone()

        if not row:
            return jsonify({"error": "Periodical not found"}), 404

        result = {
            "asin": row[0],
            "title": row[1],
            "author": row[2],
            "narrator": row[3],
            "category": row[4],
            "content_type": row[5],
            "content_delivery_type": row[6],
            "runtime_minutes": row[7],
            "release_date": row[8],
            "description": row[9],
            "cover_url": row[10],
            "is_downloaded": bool(row[11]),
            "download_requested": bool(row[12]),
            "download_priority": row[13],
            "last_synced": row[14],
            "created_at": row[15],
            "updated_at": row[16],
            "parent_asin": row[17],
        }

        # If this is a parent, include episode count
        if row[17] is None:  # parent_asin is NULL = this is a parent
            episode_count = db.execute(
                "SELECT COUNT(*) FROM periodicals WHERE parent_asin = ?", [asin]
            ).fetchone()[0]
            result["episode_count"] = episode_count

        return jsonify(result)

    @periodicals_bp.route("/api/v1/periodicals/<asin>/episodes", methods=["GET"])
    def list_episodes(asin: str):
        """List episodes for a parent periodical.

        Query params:
            sort: Sort by 'title', 'runtime', 'release' (default: release DESC)
            page: Page number (default: 1)
            per_page: Items per page (default: 50, max: 200)
        """
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db(g.db_path)

        # Verify parent exists
        parent = db.execute(
            "SELECT title FROM periodicals WHERE asin = ? AND parent_asin IS NULL",
            [asin],
        ).fetchone()

        if not parent:
            return jsonify({"error": "Parent periodical not found"}), 404

        sort = request.args.get("sort", "release")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        # Sort options
        sort_map = {
            "title": "title ASC",
            "runtime": "runtime_minutes DESC",
            "release": "release_date DESC",
        }
        order_by = sort_map.get(sort, "release_date DESC")

        # Get total count
        total = db.execute(
            "SELECT COUNT(*) FROM periodicals WHERE parent_asin = ?", [asin]
        ).fetchone()[0]

        # Get episodes
        cursor = db.execute(
            f"""
            SELECT
                asin,
                title,
                author,
                category,
                content_type,
                content_delivery_type,
                runtime_minutes,
                release_date,
                description,
                cover_url,
                is_downloaded,
                download_requested,
                last_synced
            FROM periodicals
            WHERE parent_asin = ?
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """,
            [asin, per_page, offset],
        )

        episodes = []
        for row in cursor.fetchall():
            episodes.append(
                {
                    "asin": row[0],
                    "title": row[1],
                    "author": row[2],
                    "category": row[3],
                    "content_type": row[4],
                    "content_delivery_type": row[5],
                    "runtime_minutes": row[6],
                    "release_date": row[7],
                    "description": row[8],
                    "cover_url": row[9],
                    "is_downloaded": bool(row[10]),
                    "download_requested": bool(row[11]),
                    "last_synced": row[12],
                }
            )

        return jsonify(
            {
                "parent_asin": asin,
                "parent_title": parent[0],
                "episodes": episodes,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/download", methods=["POST"])
    def queue_downloads():
        """Queue periodicals for download.

        Request body:
            asins: List of ASINs to download
            priority: 'high', 'normal', 'low' (default: normal)
        """
        data = request.get_json()
        if not data or "asins" not in data:
            return jsonify({"error": "Missing 'asins' in request body"}), 400

        asins = data.get("asins", [])
        priority_map = {"high": 10, "normal": 0, "low": -10}
        priority = priority_map.get(data.get("priority", "normal"), 0)

        # Validate all ASINs
        invalid = [a for a in asins if not validate_asin(a)]
        if invalid:
            return jsonify({"error": f"Invalid ASINs: {invalid}"}), 400

        if not asins:
            return jsonify({"error": "Empty ASIN list"}), 400

        db = get_db(g.db_path)
        queued = 0
        already_downloaded = 0
        already_queued = 0

        for asin in asins:
            # Check current status
            row = db.execute(
                "SELECT is_downloaded, download_requested FROM periodicals WHERE asin = ?",
                [asin],
            ).fetchone()

            if not row:
                continue
            if row[0]:  # Already downloaded
                already_downloaded += 1
                continue
            if row[1]:  # Already queued
                already_queued += 1
                continue

            # Queue the download
            db.execute(
                """
                UPDATE periodicals
                SET download_requested = 1, download_priority = ?
                WHERE asin = ?
            """,
                [priority, asin],
            )
            queued += 1

        db.commit()

        return jsonify(
            {
                "queued": queued,
                "already_downloaded": already_downloaded,
                "already_queued": already_queued,
                "total_requested": len(asins),
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/download/<asin>", methods=["DELETE"])
    def cancel_download(asin: str):
        """Cancel a queued download."""
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db(g.db_path)
        cursor = db.execute(
            """
            UPDATE periodicals
            SET download_requested = 0, download_priority = 0
            WHERE asin = ? AND download_requested = 1 AND is_downloaded = 0
        """,
            [asin],
        )
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({"error": "Item not in queue"}), 404

        return jsonify({"cancelled": asin})

    @periodicals_bp.route("/api/v1/periodicals/<asin>/expunge", methods=["DELETE"])
    def expunge_periodical(asin: str):
        """Completely expunge a periodical - delete from database AND filesystem.

        SAFETY: Only periodical content (Podcast, Show, News, etc.) can be expunged.
        Regular audiobooks (content_type='Product') are PROTECTED and cannot be
        deleted through this endpoint to preserve paid purchases.

        If ASIN is a parent series, expunges all episodes of that series.
        Deletes: audio files, covers, chapters.json, database entries.

        Query params:
            include_children: If true and ASIN is a parent, also expunge all episodes
        """
        import shutil
        from pathlib import Path

        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        include_children = (
            request.args.get("include_children", "true").lower() == "true"
        )
        db = get_db(g.db_path)

        # Check if this is a parent (series) or episode
        row = db.execute(
            "SELECT asin, title, parent_asin, content_type FROM periodicals WHERE asin = ?",
            [asin],
        ).fetchone()

        if not row:
            return jsonify({"error": "Periodical not found"}), 404

        # SAFETY CHECK: Never expunge paid audiobooks
        content_type = row[3]
        if content_type == "Product":
            return jsonify(
                {
                    "error": "Cannot expunge paid audiobooks",
                    "message": "This is a purchased audiobook (content_type='Product'). "
                    "Expungement is only allowed for periodical content like podcasts.",
                }
            ), 403

        is_parent = row[2] is None
        asins_to_expunge = [asin]

        # If parent and include_children, get all episode ASINs
        if is_parent and include_children:
            episodes = db.execute(
                "SELECT asin, content_type FROM periodicals WHERE parent_asin = ?",
                [asin],
            ).fetchall()
            # Filter out any Product types (extra safety)
            safe_episodes = [ep[0] for ep in episodes if ep[1] != "Product"]
            asins_to_expunge.extend(safe_episodes)

        expunged = {"database": 0, "files": 0, "errors": [], "protected": 0}

        for target_asin in asins_to_expunge:
            # Find file path from audiobooks table (if downloaded/converted)
            audiobook_row = db.execute(
                "SELECT id, file_path FROM audiobooks WHERE asin = ?", [target_asin]
            ).fetchone()

            if audiobook_row and audiobook_row[1]:
                file_path = Path(audiobook_row[1])

                # Delete the directory containing the audiobook
                # (includes audio file, cover.jpg, chapters.json)
                if file_path.exists():
                    try:
                        audiobook_dir = file_path.parent
                        if audiobook_dir.is_dir():
                            shutil.rmtree(audiobook_dir)
                            expunged["files"] += 1
                    except Exception as e:
                        expunged["errors"].append(
                            f"Failed to delete {file_path}: {str(e)}"
                        )

                # Delete from audiobooks table
                db.execute("DELETE FROM audiobooks WHERE id = ?", [audiobook_row[0]])

            # Delete from periodicals table
            cursor = db.execute("DELETE FROM periodicals WHERE asin = ?", [target_asin])
            if cursor.rowcount > 0:
                expunged["database"] += 1

        db.commit()

        return jsonify(
            {
                "expunged": asin,
                "is_parent": is_parent,
                "database_deleted": expunged["database"],
                "files_deleted": expunged["files"],
                "errors": expunged["errors"] if expunged["errors"] else None,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/orphans", methods=["GET"])
    def list_orphan_episodes():
        """Find episodes whose parent_asin doesn't exist in the periodicals table.

        Orphans occur when a parent series is deleted but its episodes remain,
        or when sync data becomes inconsistent.

        Returns list of orphan episodes with their details.
        """
        db = get_db(g.db_path)

        # Find episodes whose parent_asin references a non-existent parent
        orphans = db.execute(
            """
            SELECT e.asin, e.title, e.parent_asin, e.content_type,
                   e.is_downloaded, e.runtime_minutes, e.release_date,
                   a.file_path
            FROM periodicals e
            LEFT JOIN audiobooks a ON e.asin = a.asin
            WHERE e.parent_asin IS NOT NULL
              AND e.parent_asin NOT IN (
                  SELECT asin FROM periodicals WHERE parent_asin IS NULL
              )
            ORDER BY e.parent_asin, e.release_date DESC
        """
        ).fetchall()

        result = []
        for row in orphans:
            result.append(
                {
                    "asin": row[0],
                    "title": row[1],
                    "parent_asin": row[2],
                    "content_type": row[3],
                    "is_downloaded": bool(row[4]),
                    "runtime_minutes": row[5],
                    "release_date": row[6],
                    "file_path": row[7],
                }
            )

        return jsonify({"orphans": result, "count": len(result)})

    @periodicals_bp.route("/api/v1/periodicals/orphans", methods=["DELETE"])
    def expunge_all_orphans():
        """Delete all orphan episodes (files + database entries).

        Uses the same safe deletion logic as expunge_periodical.
        Only deletes episodes (not parents) to avoid accidental data loss.
        """
        import shutil
        from pathlib import Path

        db = get_db(g.db_path)

        # Find orphan episodes
        orphans = db.execute(
            """
            SELECT e.asin, e.title, e.content_type
            FROM periodicals e
            WHERE e.parent_asin IS NOT NULL
              AND e.parent_asin NOT IN (
                  SELECT asin FROM periodicals WHERE parent_asin IS NULL
              )
        """
        ).fetchall()

        if not orphans:
            return jsonify(
                {
                    "orphans_found": 0,
                    "database_deleted": 0,
                    "files_deleted": 0,
                    "errors": None,
                }
            )

        expunged = {"database": 0, "files": 0, "errors": []}

        for orphan in orphans:
            target_asin = orphan[0]
            content_type = orphan[2]

            # SAFETY: Skip Product types (paid audiobooks)
            if content_type == "Product":
                continue

            # Find and delete file
            audiobook_row = db.execute(
                "SELECT id, file_path FROM audiobooks WHERE asin = ?", [target_asin]
            ).fetchone()

            if audiobook_row and audiobook_row[1]:
                file_path = Path(audiobook_row[1])
                if file_path.exists():
                    try:
                        audiobook_dir = file_path.parent
                        if audiobook_dir.is_dir():
                            shutil.rmtree(audiobook_dir)
                            expunged["files"] += 1
                    except Exception as e:
                        expunged["errors"].append(
                            f"Failed to delete {file_path}: {str(e)}"
                        )

                # Delete from audiobooks table
                db.execute("DELETE FROM audiobooks WHERE id = ?", [audiobook_row[0]])

            # Delete from periodicals table
            cursor = db.execute("DELETE FROM periodicals WHERE asin = ?", [target_asin])
            if cursor.rowcount > 0:
                expunged["database"] += 1

        db.commit()

        return jsonify(
            {
                "orphans_found": len(orphans),
                "database_deleted": expunged["database"],
                "files_deleted": expunged["files"],
                "errors": expunged["errors"] if expunged["errors"] else None,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/stale", methods=["GET"])
    def list_stale_periodicals():
        """List periodicals that haven't been synced recently.

        These may be unsubscribed from Audible. Sync runs every 10 minutes,
        so items not synced in 24+ hours are likely no longer in Audible library.

        Query params:
            hours: Number of hours since last sync to consider stale (default: 24)
        """
        hours = int(request.args.get("hours", "24"))
        db = get_db(g.db_path)

        cursor = db.execute(
            """
            SELECT
                p.asin,
                p.title,
                p.category,
                p.is_downloaded,
                p.last_synced,
                parent.title as parent_title
            FROM periodicals p
            LEFT JOIN periodicals parent ON p.parent_asin = parent.asin
            WHERE p.last_synced < datetime('now', '-' || ? || ' hours')
            ORDER BY p.last_synced ASC
            LIMIT 100
        """,
            [hours],
        )

        stale = []
        for row in cursor.fetchall():
            stale.append(
                {
                    "asin": row[0],
                    "title": row[1],
                    "category": row[2],
                    "is_downloaded": bool(row[3]),
                    "last_synced": row[4],
                    "parent_title": row[5],
                }
            )

        return jsonify(
            {
                "stale": stale,
                "total": len(stale),
                "threshold_hours": hours,
                "message": f"Items not synced in {hours}+ hours (may be unsubscribed)",
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/queue", methods=["GET"])
    def get_queue():
        """Get current download queue."""
        db = get_db(g.db_path)
        cursor = db.execute(
            """
            SELECT
                asin,
                title,
                category,
                content_type,
                download_priority,
                queued_at
            FROM periodicals_download_queue
            LIMIT 100
        """
        )

        queue = []
        for row in cursor.fetchall():
            queue.append(
                {
                    "asin": row[0],
                    "title": row[1],
                    "category": row[2],
                    "content_type": row[3],
                    "priority": row[4],
                    "queued_at": row[5],
                }
            )

        return jsonify(
            {
                "queue": queue,
                "total": len(queue),
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/sync/status", methods=["GET"])
    def sync_status_sse():
        """SSE endpoint for real-time sync status.

        Returns Server-Sent Events stream with sync progress updates.
        Connect via EventSource in browser.
        """
        # Capture db_path before generator - g is only available in request context
        db_path = g.db_path

        def generate():
            db = get_db(db_path)

            # Send current status immediately
            row = db.execute(
                """
                SELECT sync_id, status, started_at, processed_parents,
                       total_parents, total_episodes, new_episodes
                FROM periodicals_sync_status
                ORDER BY created_at DESC LIMIT 1
            """
            ).fetchone()

            if row:
                yield f'data: {{"sync_id":"{row[0]}","status":"{row[1]}","started":"{row[2]}","processed":{row[3]},"total":{row[4]},"items":{row[5]},"new":{row[6]}}}\n\n'
            else:
                yield 'data: {"status":"no_sync_history"}\n\n'

            # Keep connection open for future updates
            yield 'data: {"event":"connected"}\n\n'

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    @periodicals_bp.route("/api/v1/periodicals/sync/trigger", methods=["POST"])
    def trigger_sync():
        """Manually trigger a periodicals sync.

        Query params:
            asin: Sync only this ASIN (optional)
            force: Re-sync all even if recently synced (optional)
        """
        asin = request.args.get("asin")
        force = request.args.get("force", "").lower() == "true"

        # Validate ASIN if provided
        if asin and not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN"}), 400

        # Build command - use configurable path
        cmd = [f"{_audiobooks_home}/scripts/sync-periodicals-index"]
        if asin:
            cmd.extend(["--asin", asin])
        if force:
            cmd.append("--force")

        # Start sync in background
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            current_app.logger.error(
                f"Failed to start periodicals sync for ASIN {asin}: {e}"
            )
            return jsonify({"error": "Failed to start sync process"}), 500

        return jsonify(
            {
                "status": "started",
                "asin": asin,
                "force": force,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/categories", methods=["GET"])
    def list_categories():
        """Get list of categories with counts."""
        db = get_db(g.db_path)
        cursor = db.execute(
            """
            SELECT category, COUNT(*) as item_count
            FROM periodicals
            GROUP BY category
            ORDER BY item_count DESC
        """
        )

        categories = []
        for row in cursor.fetchall():
            categories.append(
                {
                    "category": row[0],
                    "count": row[1],
                }
            )

        return jsonify({"categories": categories})

    # ========================================
    # Position Sync (Whispersync) Endpoints
    # ========================================

    def ms_to_human(ms: int) -> str:
        """Convert milliseconds to human-readable format."""
        if ms is None or ms == 0:
            return "0s"
        seconds = ms // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    async def get_audible_client():
        """Create authenticated Audible client."""
        if not AUDIBLE_AVAILABLE:
            raise RuntimeError(f"Audible library not available: {AUDIBLE_IMPORT_ERROR}")

        if not AUTH_FILE.exists():
            raise RuntimeError(f"Audible auth file not found: {AUTH_FILE}")

        # First try loading without password (unencrypted auth file)
        try:
            auth = audible.Authenticator.from_file(AUTH_FILE)
            return audible.AsyncClient(auth=auth, country_code=COUNTRY_CODE)
        except Exception:
            pass  # Auth file might be password-protected, try with credential

        # Fall back to stored credential for password-protected auth files
        if not has_stored_credential():
            raise RuntimeError(
                "No stored Audible credential. Run position_sync_test.py first to set up."
            )

        password = retrieve_credential()
        if not password:
            raise RuntimeError("Could not retrieve stored Audible credential")

        auth = audible.Authenticator.from_file(AUTH_FILE, password=password)
        return audible.AsyncClient(auth=auth, country_code=COUNTRY_CODE)

    async def fetch_audible_position(client, asin: str) -> dict:
        """Fetch position from Audible for a single ASIN."""
        try:
            response = await client.get(
                "1.0/annotations/lastpositions", params={"asins": asin}
            )

            annotations = response.get("asin_last_position_heard_annots", [])
            for annot in annotations:
                if annot.get("asin") == asin:
                    pos_data = annot.get("last_position_heard", {})
                    return {
                        "asin": asin,
                        "position_ms": pos_data.get("position_ms"),
                        "last_updated": pos_data.get("last_updated"),
                        "status": pos_data.get("status"),
                        "supported": True,  # Audible returned data
                    }

            return {
                "asin": asin,
                "position_ms": None,
                "status": "NotFound",
                "supported": False,
            }

        except Exception as e:
            return {"asin": asin, "error": str(e), "supported": None}

    async def push_audible_position(client, asin: str, position_ms: int) -> dict:
        """Push position to Audible for a single ASIN."""
        try:
            # First get ACR from license request
            license_response = await client.post(
                f"1.0/content/{asin}/licenserequest",
                body={
                    "drm_type": "Adrm",
                    "consumption_type": "Download",
                    "quality": "High",
                },
            )

            content_license = license_response.get("content_license", {})
            acr = content_license.get("acr")

            if not acr:
                return {"asin": asin, "success": False, "error": "Could not obtain ACR"}

            # Push position
            await client.put(
                f"1.0/lastpositions/{asin}",
                body={"acr": acr, "asin": asin, "position_ms": position_ms},
            )

            return {"asin": asin, "success": True, "position_ms": position_ms}

        except Exception as e:
            return {"asin": asin, "success": False, "error": str(e)}

    def run_async(coro):
        """Run async coroutine in sync context."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @periodicals_bp.route("/api/v1/periodicals/<asin>/position", methods=["GET"])
    def get_periodical_position(asin: str):
        """Get playback position for a periodical item."""
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db(g.db_path)
        row = db.execute(
            """
            SELECT asin, title, runtime_minutes,
                   playback_position_ms, playback_position_updated,
                   audible_position_ms, audible_position_updated,
                   position_synced_at
            FROM periodicals WHERE asin = ?
        """,
            [asin],
        ).fetchone()

        if not row:
            return jsonify({"error": "Periodical not found"}), 404

        duration_ms = (row[2] or 0) * 60000  # runtime_minutes to ms
        local_pos = row[3] or 0
        percent = round(local_pos / duration_ms * 100, 1) if duration_ms > 0 else 0

        return jsonify(
            {
                "asin": row[0],
                "title": row[1],
                "duration_ms": duration_ms,
                "duration_human": ms_to_human(duration_ms),
                "local_position_ms": local_pos,
                "local_position_human": ms_to_human(local_pos),
                "local_position_updated": row[4],
                "audible_position_ms": row[5],
                "audible_position_human": ms_to_human(row[5]),
                "audible_position_updated": row[6],
                "position_synced_at": row[7],
                "percent_complete": percent,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/<asin>/position", methods=["PUT"])
    def update_periodical_position(asin: str):
        """Update local playback position for a periodical."""
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        data = request.get_json()
        position_ms = data.get("position_ms")

        if position_ms is None:
            return jsonify({"error": "position_ms required"}), 400

        db = get_db(g.db_path)

        now = datetime.now().isoformat()
        cursor = db.execute(
            """
            UPDATE periodicals
            SET playback_position_ms = ?,
                playback_position_updated = ?,
                updated_at = ?
            WHERE asin = ?
        """,
            [position_ms, now, now, asin],
        )

        if cursor.rowcount == 0:
            return jsonify({"error": "Periodical not found"}), 404

        # Record in history
        db.execute(
            """
            INSERT INTO periodicals_playback_history (periodical_asin, position_ms, source)
            VALUES (?, ?, 'local')
        """,
            [asin, position_ms],
        )

        db.commit()

        return jsonify(
            {
                "success": True,
                "asin": asin,
                "position_ms": position_ms,
                "position_human": ms_to_human(position_ms),
                "updated_at": now,
            }
        )

    @periodicals_bp.route("/api/v1/periodicals/position/test/<asin>", methods=["GET"])
    def test_position_support(asin: str):
        """Test if Audible supports position sync for this ASIN.

        Use this to check whether periodical content (podcasts, shows)
        actually has Whispersync support in Audible.
        """
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        if not AUDIBLE_AVAILABLE:
            return jsonify(
                {"error": "Audible library not available", "audible_available": False}
            ), 503

        async def do_test():
            async with await get_audible_client() as client:
                return await fetch_audible_position(client, asin)

        try:
            result = run_async(do_test())
            return jsonify(
                {
                    "asin": asin,
                    "audible_response": result,
                    "supports_position_sync": result.get("supported", False),
                    "message": (
                        "Audible supports position sync for this content"
                        if result.get("supported")
                        else "Audible may not track position for this content type"
                    ),
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @periodicals_bp.route("/api/v1/periodicals/<asin>/position/sync", methods=["POST"])
    def sync_periodical_position(asin: str):
        """Sync position for a periodical with Audible (Whispersync).

        Logic: "Furthest ahead wins"
        - If Audible > local: update local from Audible
        - If local > Audible: push local to Audible
        - If equal: no action needed

        NOTE: This may not work for all periodical content types. Audible's
        Whispersync might only support certain content. Use the test endpoint
        first to verify support.
        """
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        if not AUDIBLE_AVAILABLE:
            return jsonify({"error": "Audible library not available"}), 503

        db = get_db(g.db_path)

        # Get periodical info
        row = db.execute(
            """
            SELECT asin, title, playback_position_ms, runtime_minutes
            FROM periodicals WHERE asin = ?
        """,
            [asin],
        ).fetchone()

        if not row:
            return jsonify({"error": "Periodical not found"}), 404

        local_pos = row[2] or 0

        async def do_sync():
            async with await get_audible_client() as client:
                # Fetch Audible position
                audible_data = await fetch_audible_position(client, asin)

                if "error" in audible_data:
                    return {"error": audible_data["error"]}

                # Check if Audible actually supports position for this content
                if not audible_data.get("supported"):
                    return {
                        "error": "Audible does not appear to track position for this content",
                        "audible_response": audible_data,
                    }

                audible_pos = audible_data.get("position_ms") or 0

                result = {
                    "asin": asin,
                    "title": row[1],
                    "local_position_ms": local_pos,
                    "local_position_human": ms_to_human(local_pos),
                    "audible_position_ms": audible_pos,
                    "audible_position_human": ms_to_human(audible_pos),
                }

                now = datetime.now().isoformat()

                if audible_pos > local_pos:
                    # Audible is ahead - update local
                    result["action"] = "pulled_from_audible"
                    result["final_position_ms"] = audible_pos
                    result["final_position_human"] = ms_to_human(audible_pos)

                elif local_pos > audible_pos:
                    # Local is ahead - push to Audible
                    push_result = await push_audible_position(client, asin, local_pos)
                    result["action"] = "pushed_to_audible"
                    result["push_result"] = push_result
                    result["final_position_ms"] = local_pos
                    result["final_position_human"] = ms_to_human(local_pos)

                else:
                    result["action"] = "already_synced"
                    result["final_position_ms"] = local_pos
                    result["final_position_human"] = ms_to_human(local_pos)

                return result, audible_pos, now

        try:
            sync_result = run_async(do_sync())

            # Handle error case (single dict returned instead of tuple)
            if isinstance(sync_result, dict) and "error" in sync_result:
                return jsonify(sync_result), 500

            result, audible_pos, now = sync_result

            # Update database with sync results
            final_pos = result["final_position_ms"]
            db.execute(
                """
                UPDATE periodicals
                SET playback_position_ms = ?,
                    playback_position_updated = ?,
                    audible_position_ms = ?,
                    audible_position_updated = ?,
                    position_synced_at = ?,
                    updated_at = ?
                WHERE asin = ?
            """,
                [final_pos, now, audible_pos, now, now, now, asin],
            )

            # Record in history
            db.execute(
                """
                INSERT INTO periodicals_playback_history (periodical_asin, position_ms, source)
                VALUES (?, ?, 'sync')
            """,
                [asin, final_pos],
            )

            db.commit()

            return jsonify(result)

        except Exception as e:
            # Use %s formatting to prevent log injection via exception messages
            import logging

            logging.error("Periodical position sync error: %s", e)
            return jsonify({"error": "Internal server error during position sync"}), 500
