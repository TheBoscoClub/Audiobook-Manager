"""
Periodicals API - Episodic content management

Provides endpoints for browsing and downloading periodical content
(podcasts, news, meditation series) that is skipped by default.

Endpoints:
    GET  /api/v1/periodicals              - List all periodical parents
    GET  /api/v1/periodicals/<asin>       - List episodes for parent
    GET  /api/v1/periodicals/<asin>/<ep>  - Episode details
    POST /api/v1/periodicals/download     - Queue episodes for download
    DEL  /api/v1/periodicals/download/<a> - Cancel queued download
    GET  /api/v1/periodicals/sync/status  - SSE stream for sync status
    POST /api/v1/periodicals/sync/trigger - Manually trigger sync
"""

import re
import subprocess
from flask import Blueprint, Response, jsonify, request, g

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
        """List all periodical parents with episode counts.

        Query params:
            category: Filter by category (podcast, news, meditation, other)
            sort: Sort by 'title', 'episodes', 'latest' (default: title)
        """
        db = get_db()
        category = request.args.get("category")
        sort = request.args.get("sort", "title")

        # Build query using the summary view
        query = """
            SELECT
                parent_asin,
                title,
                category,
                total_episodes,
                downloaded_count,
                queued_count,
                latest_episode_date,
                last_synced,
                cover_url
            FROM periodicals_summary
        """
        params = []

        if category:
            query += " WHERE category = ?"
            params.append(category)

        # Sort options
        sort_map = {
            "title": "title ASC",
            "episodes": "total_episodes DESC",
            "latest": "latest_episode_date DESC",
        }
        query += f" ORDER BY {sort_map.get(sort, 'title ASC')}"

        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        periodicals = []
        for row in rows:
            periodicals.append({
                "parent_asin": row[0],
                "title": row[1],
                "category": row[2],
                "episode_count": row[3],
                "downloaded_count": row[4],
                "queued_count": row[5],
                "latest_episode_date": row[6],
                "last_synced": row[7],
                "cover_url": row[8],
            })

        return jsonify({
            "periodicals": periodicals,
            "total": len(periodicals),
        })

    @periodicals_bp.route("/api/v1/periodicals/<parent_asin>", methods=["GET"])
    def list_episodes(parent_asin: str):
        """List episodes for a parent periodical.

        Query params:
            page: Page number (default: 1)
            per_page: Items per page (default: 50, max: 200)
            status: Filter by 'available', 'downloaded', 'queued'
        """
        if not validate_asin(parent_asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db()
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
        status = request.args.get("status")
        offset = (page - 1) * per_page

        # Base query for episodes (child_asin IS NOT NULL)
        query = """
            SELECT
                child_asin,
                episode_title,
                episode_number,
                runtime_minutes,
                release_date,
                description,
                is_downloaded,
                download_requested
            FROM periodicals
            WHERE parent_asin = ? AND child_asin IS NOT NULL
        """
        params = [parent_asin]

        # Status filter
        if status == "available":
            query += " AND is_downloaded = 0 AND download_requested = 0"
        elif status == "downloaded":
            query += " AND is_downloaded = 1"
        elif status == "queued":
            query += " AND download_requested = 1 AND is_downloaded = 0"

        # Get total count
        count_query = f"SELECT COUNT(*) FROM ({query})"
        total = db.execute(count_query, params).fetchone()[0]

        # Add pagination
        query += " ORDER BY release_date DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])

        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        # Get parent info
        parent = db.execute(
            "SELECT title, category, cover_url FROM periodicals WHERE parent_asin = ? AND child_asin IS NULL",
            [parent_asin]
        ).fetchone()

        episodes = []
        for row in rows:
            episodes.append({
                "child_asin": row[0],
                "episode_title": row[1],
                "episode_number": row[2],
                "runtime_minutes": row[3],
                "release_date": row[4],
                "description": row[5],
                "is_downloaded": bool(row[6]),
                "download_requested": bool(row[7]),
            })

        return jsonify({
            "parent_asin": parent_asin,
            "title": parent[0] if parent else "Unknown",
            "category": parent[1] if parent else "unknown",
            "cover_url": parent[2] if parent else None,
            "episodes": episodes,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        })

    @periodicals_bp.route("/api/v1/periodicals/<parent_asin>/<child_asin>", methods=["GET"])
    def episode_details(parent_asin: str, child_asin: str):
        """Get detailed info for a single episode."""
        if not validate_asin(parent_asin) or not validate_asin(child_asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db()
        row = db.execute("""
            SELECT
                p.parent_asin,
                p.child_asin,
                p.title,
                p.episode_title,
                p.episode_number,
                p.author,
                p.narrator,
                p.runtime_minutes,
                p.release_date,
                p.description,
                p.category,
                p.is_downloaded,
                p.download_requested,
                p.cover_url,
                p.last_synced
            FROM periodicals p
            WHERE p.parent_asin = ? AND p.child_asin = ?
        """, [parent_asin, child_asin]).fetchone()

        if not row:
            return jsonify({"error": "Episode not found"}), 404

        return jsonify({
            "parent_asin": row[0],
            "child_asin": row[1],
            "title": row[2],
            "episode_title": row[3],
            "episode_number": row[4],
            "author": row[5],
            "narrator": row[6],
            "runtime_minutes": row[7],
            "release_date": row[8],
            "description": row[9],
            "category": row[10],
            "is_downloaded": bool(row[11]),
            "download_requested": bool(row[12]),
            "cover_url": row[13],
            "last_synced": row[14],
        })

    @periodicals_bp.route("/api/v1/periodicals/download", methods=["POST"])
    def queue_downloads():
        """Queue episodes for download.

        Request body:
            asins: List of child ASINs to download
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

        db = get_db()
        queued = 0
        already_downloaded = 0
        already_queued = 0

        for asin in asins:
            # Check current status
            row = db.execute(
                "SELECT is_downloaded, download_requested FROM periodicals WHERE child_asin = ?",
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
                WHERE child_asin = ?
            """, [priority, asin])
            queued += 1

        db.commit()

        return jsonify({
            "queued": queued,
            "already_downloaded": already_downloaded,
            "already_queued": already_queued,
            "total_requested": len(asins),
        })

    @periodicals_bp.route("/api/v1/periodicals/download/<child_asin>", methods=["DELETE"])
    def cancel_download(child_asin: str):
        """Cancel a queued download."""
        if not validate_asin(child_asin):
            return jsonify({"error": "Invalid ASIN format"}), 400

        db = get_db()
        cursor = db.execute("""
            UPDATE periodicals
            SET download_requested = 0, download_priority = 0
            WHERE child_asin = ? AND download_requested = 1 AND is_downloaded = 0
        """, [child_asin])
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({"error": "Episode not in queue"}), 404

        return jsonify({"cancelled": child_asin})

    @periodicals_bp.route("/api/v1/periodicals/queue", methods=["GET"])
    def get_queue():
        """Get current download queue."""
        db = get_db()
        cursor = db.execute("""
            SELECT
                child_asin,
                parent_asin,
                title,
                episode_title,
                episode_number,
                category,
                download_priority
            FROM periodicals_download_queue
            LIMIT 100
        """)

        queue = []
        for row in cursor.fetchall():
            queue.append({
                "child_asin": row[0],
                "parent_asin": row[1],
                "title": row[2],
                "episode_title": row[3],
                "episode_number": row[4],
                "category": row[5],
                "priority": row[6],
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
            db = get_db()

            # Send current status immediately
            row = db.execute("""
                SELECT sync_id, status, started_at, processed_parents,
                       total_parents, total_episodes, new_episodes
                FROM periodicals_sync_status
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()

            if row:
                yield f"data: {{\"sync_id\":\"{row[0]}\",\"status\":\"{row[1]}\",\"started\":\"{row[2]}\",\"processed\":{row[3]},\"total\":{row[4]},\"episodes\":{row[5]},\"new\":{row[6]}}}\n\n"
            else:
                yield 'data: {"status":"no_sync_history"}\n\n'

            # Keep connection open for future updates
            # In production, this would watch the FIFO or use polling
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
            parent: Sync only this parent ASIN (optional)
            force: Re-sync all even if recently synced (optional)
        """
        parent = request.args.get("parent")
        force = request.args.get("force", "").lower() == "true"

        # Validate parent if provided
        if parent and not validate_asin(parent):
            return jsonify({"error": "Invalid parent ASIN"}), 400

        # Build command
        cmd = ["/opt/audiobooks/scripts/sync-periodicals-index"]
        if parent:
            cmd.extend(["--parent", parent])
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
            return jsonify({"error": f"Failed to start sync: {e}"}), 500

        return jsonify({
            "status": "started",
            "parent": parent,
            "force": force,
        })

    @periodicals_bp.route("/api/v1/periodicals/categories", methods=["GET"])
    def list_categories():
        """Get list of categories with counts."""
        db = get_db()
        cursor = db.execute("""
            SELECT category, COUNT(DISTINCT parent_asin) as parent_count
            FROM periodicals
            WHERE child_asin IS NULL
            GROUP BY category
            ORDER BY parent_count DESC
        """)

        categories = []
        for row in cursor.fetchall():
            categories.append({
                "category": row[0],
                "count": row[1],
            })

        return jsonify({"categories": categories})
