"""
Periodicals API - Non-audiobook content from Audible library

Provides endpoints for browsing podcasts, news, shows, and other
non-audiobook content that appears in the user's Audible library.

Content types synced:
    - Podcast: podcast series
    - Newspaper / Magazine: NYT Digest, etc.
    - Show: meditation series, interview shows
    - Radio/TV Program: documentaries, radio dramas

Endpoints:
    GET  /api/v1/periodicals              - List all periodicals
    GET  /api/v1/periodicals/<asin>       - Item details
    POST /api/v1/periodicals/download     - Queue items for download
    DEL  /api/v1/periodicals/download/<a> - Cancel queued download
    GET  /api/v1/periodicals/queue        - Get download queue
    GET  /api/v1/periodicals/sync/status  - SSE stream for sync status
    POST /api/v1/periodicals/sync/trigger - Manually trigger sync
    GET  /api/v1/periodicals/categories   - List categories with counts
"""

import re
import subprocess
from flask import Blueprint, Response, jsonify, request, g, current_app

from .core import get_db

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
        """List all periodical items.

        Query params:
            category: Filter by category (podcast, news, meditation, documentary, show, other)
            sort: Sort by 'title', 'runtime', 'release' (default: title)
            page: Page number (default: 1)
            per_page: Items per page (default: 50, max: 200)
        """
        db = get_db(g.db_path)
        category = request.args.get("category")
        sort = request.args.get("sort", "title")
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
        offset = (page - 1) * per_page

        # Build query
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
                last_synced
            FROM periodicals
        """
        params = []

        if category:
            query += " WHERE category = ?"
            params.append(category)

        # Sort options
        sort_map = {
            "title": "title ASC",
            "runtime": "runtime_minutes DESC",
            "release": "release_date DESC",
            "category": "category ASC, title ASC",
        }
        query += f" ORDER BY {sort_map.get(sort, 'title ASC')}"

        # Get total count
        count_query = "SELECT COUNT(*) FROM periodicals"
        if category:
            count_query += " WHERE category = ?"
        total = db.execute(count_query, params[:1] if params else []).fetchone()[0]

        # Add pagination
        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, offset])

        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        periodicals = []
        for row in rows:
            periodicals.append({
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
            })

        return jsonify({
            "periodicals": periodicals,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
        })

    @periodicals_bp.route("/api/v1/periodicals/<asin>", methods=["GET"])
    def periodical_details(asin: str):
        """Get detailed info for a single periodical."""
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db(g.db_path)
        row = db.execute("""
            SELECT
                asin,
                title,
                author,
                narrator,
                category,
                content_type,
                runtime_minutes,
                release_date,
                description,
                cover_url,
                is_downloaded,
                download_requested,
                download_priority,
                last_synced,
                created_at,
                updated_at
            FROM periodicals
            WHERE asin = ?
        """, [asin]).fetchone()

        if not row:
            return jsonify({"error": "Periodical not found"}), 404

        return jsonify({
            "asin": row[0],
            "title": row[1],
            "author": row[2],
            "narrator": row[3],
            "category": row[4],
            "content_type": row[5],
            "runtime_minutes": row[6],
            "release_date": row[7],
            "description": row[8],
            "cover_url": row[9],
            "is_downloaded": bool(row[10]),
            "download_requested": bool(row[11]),
            "download_priority": row[12],
            "last_synced": row[13],
            "created_at": row[14],
            "updated_at": row[15],
        })

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
                [asin]
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
            db.execute("""
                UPDATE periodicals
                SET download_requested = 1, download_priority = ?
                WHERE asin = ?
            """, [priority, asin])
            queued += 1

        db.commit()

        return jsonify({
            "queued": queued,
            "already_downloaded": already_downloaded,
            "already_queued": already_queued,
            "total_requested": len(asins),
        })

    @periodicals_bp.route("/api/v1/periodicals/download/<asin>", methods=["DELETE"])
    def cancel_download(asin: str):
        """Cancel a queued download."""
        if not validate_asin(asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db(g.db_path)
        cursor = db.execute("""
            UPDATE periodicals
            SET download_requested = 0, download_priority = 0
            WHERE asin = ? AND download_requested = 1 AND is_downloaded = 0
        """, [asin])
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({"error": "Item not in queue"}), 404

        return jsonify({"cancelled": asin})

    @periodicals_bp.route("/api/v1/periodicals/queue", methods=["GET"])
    def get_queue():
        """Get current download queue."""
        db = get_db(g.db_path)
        cursor = db.execute("""
            SELECT
                asin,
                title,
                category,
                content_type,
                download_priority,
                queued_at
            FROM periodicals_download_queue
            LIMIT 100
        """)

        queue = []
        for row in cursor.fetchall():
            queue.append({
                "asin": row[0],
                "title": row[1],
                "category": row[2],
                "content_type": row[3],
                "priority": row[4],
                "queued_at": row[5],
            })

        return jsonify({
            "queue": queue,
            "total": len(queue),
        })

    @periodicals_bp.route("/api/v1/periodicals/sync/status", methods=["GET"])
    def sync_status_sse():
        """SSE endpoint for real-time sync status.

        Returns Server-Sent Events stream with sync progress updates.
        Connect via EventSource in browser.
        """
        def generate():
            db = get_db(g.db_path)

            # Send current status immediately
            row = db.execute("""
                SELECT sync_id, status, started_at, processed_parents,
                       total_parents, total_episodes, new_episodes
                FROM periodicals_sync_status
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()

            if row:
                yield f"data: {{\"sync_id\":\"{row[0]}\",\"status\":\"{row[1]}\",\"started\":\"{row[2]}\",\"processed\":{row[3]},\"total\":{row[4]},\"items\":{row[5]},\"new\":{row[6]}}}\n\n"
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
            }
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

        # Build command
        cmd = ["/opt/audiobooks/scripts/sync-periodicals-index"]
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
            current_app.logger.error(f"Failed to start periodicals sync for ASIN {asin}: {e}")
            return jsonify({"error": "Failed to start sync process"}), 500

        return jsonify({
            "status": "started",
            "asin": asin,
            "force": force,
        })

    @periodicals_bp.route("/api/v1/periodicals/categories", methods=["GET"])
    def list_categories():
        """Get list of categories with counts."""
        db = get_db(g.db_path)
        cursor = db.execute("""
            SELECT category, COUNT(*) as item_count
            FROM periodicals
            GROUP BY category
            ORDER BY item_count DESC
        """)

        categories = []
        for row in cursor.fetchall():
            categories.append({
                "category": row[0],
                "count": row[1],
            })

        return jsonify({"categories": categories})
