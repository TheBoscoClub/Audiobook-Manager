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
"""

import os
import re
import subprocess

from flask import Blueprint, Response, current_app, g, jsonify, request

from .core import get_db

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

        If ASIN is a parent series, expunges all episodes of that series.
        Deletes: audio files, covers, chapters.json, database entries.

        Query params:
            include_children: If true and ASIN is a parent, also expunge all episodes
        """
        import shutil
        from pathlib import Path

        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        include_children = request.args.get("include_children", "true").lower() == "true"
        db = get_db(g.db_path)

        # Check if this is a parent (series) or episode
        row = db.execute(
            "SELECT asin, title, parent_asin FROM periodicals WHERE asin = ?",
            [asin]
        ).fetchone()

        if not row:
            return jsonify({"error": "Periodical not found"}), 404

        is_parent = row[2] is None
        asins_to_expunge = [asin]

        # If parent and include_children, get all episode ASINs
        if is_parent and include_children:
            episodes = db.execute(
                "SELECT asin FROM periodicals WHERE parent_asin = ?",
                [asin]
            ).fetchall()
            asins_to_expunge.extend([ep[0] for ep in episodes])

        expunged = {"database": 0, "files": 0, "errors": []}

        for target_asin in asins_to_expunge:
            # Find file path from audiobooks table (if downloaded/converted)
            audiobook_row = db.execute(
                "SELECT id, file_path FROM audiobooks WHERE asin = ?",
                [target_asin]
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
                        expunged["errors"].append(f"Failed to delete {file_path}: {str(e)}")

                # Delete from audiobooks table
                db.execute("DELETE FROM audiobooks WHERE id = ?", [audiobook_row[0]])

            # Delete from periodicals table
            cursor = db.execute("DELETE FROM periodicals WHERE asin = ?", [target_asin])
            if cursor.rowcount > 0:
                expunged["database"] += 1

        db.commit()

        return jsonify({
            "expunged": asin,
            "is_parent": is_parent,
            "database_deleted": expunged["database"],
            "files_deleted": expunged["files"],
            "errors": expunged["errors"] if expunged["errors"] else None
        })

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
            stale.append({
                "asin": row[0],
                "title": row[1],
                "category": row[2],
                "is_downloaded": bool(row[3]),
                "last_synced": row[4],
                "parent_title": row[5],
            })

        return jsonify({
            "stale": stale,
            "total": len(stale),
            "threshold_hours": hours,
            "message": f"Items not synced in {hours}+ hours (may be unsubscribed)"
        })

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

        def generate():
            db = get_db(g.db_path)

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
                "Connection": "keep-alive",
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
