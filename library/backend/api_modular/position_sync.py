"""
Position Sync API Module

Provides bidirectional playback position synchronization between
the local audiobook library and Audible cloud.

Endpoints:
    GET  /api/position/<id>           - Get position for a single book
    PUT  /api/position/<id>           - Update local position
    POST /api/position/sync/<id>      - Sync single book with Audible
    POST /api/position/sync-all       - Sync all books with ASINs
    GET  /api/position/syncable       - List all syncable books
    GET  /api/position/history/<id>   - Get position history for a book
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

# Add rnd directory to path for credential_manager and audible imports
RND_PATH = Path(__file__).parent.parent.parent.parent / "rnd"
sys.path.insert(0, str(RND_PATH))

try:
    import audible
    from credential_manager import retrieve_credential, has_stored_credential
    AUDIBLE_AVAILABLE = True
except ImportError as e:
    AUDIBLE_AVAILABLE = False
    AUDIBLE_IMPORT_ERROR = str(e)


# Blueprint for position sync routes
position_bp = Blueprint('position', __name__, url_prefix='/api/position')

# Configuration
AUDIBLE_CONFIG_DIR = Path.home() / ".audible"
AUTH_FILE = AUDIBLE_CONFIG_DIR / "audible.json"
COUNTRY_CODE = "us"

# Module-level database path (set by init function)
_db_path = None


def init_position_routes(database_path: Path):
    """Initialize position routes with database path."""
    global _db_path
    _db_path = database_path


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


def get_db():
    """Get database connection using module's database path."""
    import sqlite3
    if _db_path is None:
        raise RuntimeError("Position routes not initialized. Call init_position_routes first.")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


async def get_audible_client():
    """Create authenticated Audible client."""
    if not AUDIBLE_AVAILABLE:
        raise RuntimeError(f"Audible library not available: {AUDIBLE_IMPORT_ERROR}")

    if not has_stored_credential():
        raise RuntimeError("No stored Audible credential. Run position_sync_test.py first to set up.")

    password = retrieve_credential()
    if not password:
        raise RuntimeError("Could not retrieve stored Audible credential")

    if not AUTH_FILE.exists():
        raise RuntimeError(f"Audible auth file not found: {AUTH_FILE}")

    auth = audible.Authenticator.from_file(AUTH_FILE, password=password)
    return audible.AsyncClient(auth=auth, country_code=COUNTRY_CODE)


async def fetch_audible_position(client, asin: str) -> dict:
    """Fetch position from Audible for a single ASIN."""
    try:
        response = await client.get(
            "1.0/annotations/lastpositions",
            params={"asins": asin}
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
                }

        return {"asin": asin, "position_ms": None, "status": "NotFound"}

    except Exception as e:
        return {"asin": asin, "error": str(e)}


async def fetch_audible_positions_batch(client, asins: list[str]) -> dict:
    """Fetch positions from Audible for multiple ASINs."""
    try:
        # API accepts comma-separated ASINs
        response = await client.get(
            "1.0/annotations/lastpositions",
            params={"asins": ",".join(asins)}
        )

        results = {}
        annotations = response.get("asin_last_position_heard_annots", [])
        for annot in annotations:
            asin = annot.get("asin")
            pos_data = annot.get("last_position_heard", {})
            results[asin] = {
                "position_ms": pos_data.get("position_ms"),
                "last_updated": pos_data.get("last_updated"),
                "status": pos_data.get("status"),
            }

        # Mark missing ASINs
        for asin in asins:
            if asin not in results:
                results[asin] = {"position_ms": None, "status": "NotFound"}

        return results

    except Exception as e:
        return {"error": str(e)}


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
            }
        )

        content_license = license_response.get("content_license", {})
        acr = content_license.get("acr")

        if not acr:
            return {"asin": asin, "success": False, "error": "Could not obtain ACR"}

        # Push position
        await client.put(
            f"1.0/lastpositions/{asin}",
            body={
                "acr": acr,
                "asin": asin,
                "position_ms": position_ms
            }
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


# ============================================================
# API Endpoints
# ============================================================

@position_bp.route('/status', methods=['GET'])
def position_status():
    """Check if position sync is available and configured."""
    status = {
        "audible_available": AUDIBLE_AVAILABLE,
        "credential_stored": has_stored_credential() if AUDIBLE_AVAILABLE else False,
        "auth_file_exists": AUTH_FILE.exists(),
    }

    if not AUDIBLE_AVAILABLE:
        status["error"] = AUDIBLE_IMPORT_ERROR

    return jsonify(status)


@position_bp.route('/<int:audiobook_id>', methods=['GET'])
def get_position(audiobook_id: int):
    """Get playback position for a single audiobook."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, asin, duration_hours,
               playback_position_ms, playback_position_updated,
               audible_position_ms, audible_position_updated,
               position_synced_at
        FROM audiobooks WHERE id = ?
    """, (audiobook_id,))

    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Audiobook not found"}), 404

    duration_ms = int((row['duration_hours'] or 0) * 3600000)
    local_pos = row['playback_position_ms'] or 0
    percent = round(local_pos / duration_ms * 100, 1) if duration_ms > 0 else 0

    return jsonify({
        "id": row['id'],
        "title": row['title'],
        "asin": row['asin'],
        "duration_ms": duration_ms,
        "duration_human": ms_to_human(duration_ms),
        "local_position_ms": local_pos,
        "local_position_human": ms_to_human(local_pos),
        "local_position_updated": row['playback_position_updated'],
        "audible_position_ms": row['audible_position_ms'],
        "audible_position_human": ms_to_human(row['audible_position_ms']),
        "audible_position_updated": row['audible_position_updated'],
        "position_synced_at": row['position_synced_at'],
        "percent_complete": percent,
        "syncable": bool(row['asin']),
    })


@position_bp.route('/<int:audiobook_id>', methods=['PUT'])
def update_position(audiobook_id: int):
    """Update local playback position for an audiobook."""
    data = request.get_json()
    position_ms = data.get('position_ms')

    if position_ms is None:
        return jsonify({"error": "position_ms required"}), 400

    conn = get_db()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE audiobooks
        SET playback_position_ms = ?,
            playback_position_updated = ?,
            updated_at = ?
        WHERE id = ?
    """, (position_ms, now, now, audiobook_id))

    if cursor.rowcount == 0:
        return jsonify({"error": "Audiobook not found"}), 404

    # Record in history
    cursor.execute("""
        INSERT INTO playback_history (audiobook_id, position_ms, source)
        VALUES (?, ?, 'local')
    """, (audiobook_id, position_ms))

    conn.commit()

    return jsonify({
        "success": True,
        "audiobook_id": audiobook_id,
        "position_ms": position_ms,
        "position_human": ms_to_human(position_ms),
        "updated_at": now,
    })


@position_bp.route('/sync/<int:audiobook_id>', methods=['POST'])
def sync_position(audiobook_id: int):
    """
    Sync position for a single audiobook with Audible.

    Logic: "Furthest ahead wins"
    - If Audible > local: update local from Audible
    - If local > Audible: push local to Audible
    - If equal: no action needed
    """
    if not AUDIBLE_AVAILABLE:
        return jsonify({"error": "Audible library not available"}), 503

    conn = get_db()
    cursor = conn.cursor()

    # Get audiobook info
    cursor.execute("""
        SELECT id, title, asin, playback_position_ms, duration_hours
        FROM audiobooks WHERE id = ?
    """, (audiobook_id,))

    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Audiobook not found"}), 404

    if not row['asin']:
        return jsonify({"error": "Audiobook has no ASIN, cannot sync with Audible"}), 400

    asin = row['asin']
    local_pos = row['playback_position_ms'] or 0

    async def do_sync():
        async with await get_audible_client() as client:
            # Fetch Audible position
            audible_data = await fetch_audible_position(client, asin)

            if "error" in audible_data:
                return {"error": audible_data["error"]}

            audible_pos = audible_data.get("position_ms") or 0

            result = {
                "audiobook_id": audiobook_id,
                "title": row['title'],
                "asin": asin,
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
        result, audible_pos, now = run_async(do_sync())

        if "error" in result:
            return jsonify(result), 500

        # Update database with sync results
        final_pos = result["final_position_ms"]
        cursor.execute("""
            UPDATE audiobooks
            SET playback_position_ms = ?,
                playback_position_updated = ?,
                audible_position_ms = ?,
                audible_position_updated = ?,
                position_synced_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (final_pos, now, audible_pos, now, now, now, audiobook_id))

        # Record in history
        cursor.execute("""
            INSERT INTO playback_history (audiobook_id, position_ms, source)
            VALUES (?, ?, 'sync')
        """, (audiobook_id, final_pos))

        conn.commit()

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@position_bp.route('/sync-all', methods=['POST'])
def sync_all_positions():
    """
    Sync positions for all audiobooks with ASINs.

    This performs batch operations for efficiency.
    """
    if not AUDIBLE_AVAILABLE:
        return jsonify({"error": "Audible library not available"}), 503

    conn = get_db()
    cursor = conn.cursor()

    # Get all syncable audiobooks
    cursor.execute("""
        SELECT id, title, asin, playback_position_ms, duration_hours
        FROM audiobooks
        WHERE asin IS NOT NULL AND asin != ''
    """)

    books = cursor.fetchall()
    if not books:
        return jsonify({"message": "No syncable audiobooks found", "synced": 0})

    asins = [b['asin'] for b in books]
    asin_to_book = {b['asin']: dict(b) for b in books}

    async def do_batch_sync():
        async with await get_audible_client() as client:
            # Batch fetch all positions
            audible_positions = await fetch_audible_positions_batch(client, asins)

            if "error" in audible_positions:
                return {"error": audible_positions["error"]}

            results = []
            push_tasks = []

            for asin, audible_data in audible_positions.items():
                book = asin_to_book.get(asin)
                if not book:
                    continue

                local_pos = book['playback_position_ms'] or 0
                audible_pos = audible_data.get("position_ms") or 0

                result = {
                    "audiobook_id": book['id'],
                    "title": book['title'],
                    "asin": asin,
                    "local_position_ms": local_pos,
                    "audible_position_ms": audible_pos,
                }

                if audible_pos > local_pos:
                    result["action"] = "pulled_from_audible"
                    result["final_position_ms"] = audible_pos
                elif local_pos > audible_pos:
                    result["action"] = "push_to_audible"
                    result["final_position_ms"] = local_pos
                    push_tasks.append((asin, local_pos))
                else:
                    result["action"] = "already_synced"
                    result["final_position_ms"] = local_pos

                results.append(result)

            # Batch push updates to Audible
            for asin, position_ms in push_tasks:
                push_result = await push_audible_position(client, asin, position_ms)
                # Update result with push status
                for r in results:
                    if r["asin"] == asin:
                        r["push_result"] = push_result
                        if push_result.get("success"):
                            r["action"] = "pushed_to_audible"
                        else:
                            r["action"] = "push_failed"
                        break

            return results, audible_positions

    try:
        results, audible_positions = run_async(do_batch_sync())

        if isinstance(results, dict) and "error" in results:
            return jsonify(results), 500

        # Update database
        now = datetime.now().isoformat()
        for result in results:
            book_id = result["audiobook_id"]
            final_pos = result["final_position_ms"]
            audible_pos = result["audible_position_ms"]

            cursor.execute("""
                UPDATE audiobooks
                SET playback_position_ms = ?,
                    playback_position_updated = ?,
                    audible_position_ms = ?,
                    audible_position_updated = ?,
                    position_synced_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (final_pos, now, audible_pos, now, now, now, book_id))

        conn.commit()

        # Summary stats
        pulled = sum(1 for r in results if r["action"] == "pulled_from_audible")
        pushed = sum(1 for r in results if r["action"] == "pushed_to_audible")
        synced = sum(1 for r in results if r["action"] == "already_synced")
        failed = sum(1 for r in results if r["action"] == "push_failed")

        return jsonify({
            "total": len(results),
            "pulled_from_audible": pulled,
            "pushed_to_audible": pushed,
            "already_synced": synced,
            "failed": failed,
            "results": results,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@position_bp.route('/syncable', methods=['GET'])
def list_syncable():
    """List all audiobooks that can be synced with Audible."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, asin, duration_hours,
               playback_position_ms, audible_position_ms, position_synced_at
        FROM audiobooks
        WHERE asin IS NOT NULL AND asin != ''
        ORDER BY title
    """)

    books = []
    for row in cursor.fetchall():
        duration_ms = int((row['duration_hours'] or 0) * 3600000)
        local_pos = row['playback_position_ms'] or 0
        percent = round(local_pos / duration_ms * 100, 1) if duration_ms > 0 else 0

        books.append({
            "id": row['id'],
            "title": row['title'],
            "author": row['author'],
            "asin": row['asin'],
            "duration_human": ms_to_human(duration_ms),
            "local_position_human": ms_to_human(local_pos),
            "audible_position_human": ms_to_human(row['audible_position_ms']),
            "percent_complete": percent,
            "last_synced": row['position_synced_at'],
        })

    return jsonify({
        "total": len(books),
        "books": books,
    })


@position_bp.route('/history/<int:audiobook_id>', methods=['GET'])
def get_position_history(audiobook_id: int):
    """Get position history for an audiobook."""
    conn = get_db()
    cursor = conn.cursor()

    limit = request.args.get('limit', 50, type=int)

    cursor.execute("""
        SELECT position_ms, source, recorded_at
        FROM playback_history
        WHERE audiobook_id = ?
        ORDER BY recorded_at DESC
        LIMIT ?
    """, (audiobook_id, limit))

    history = [{
        "position_ms": row['position_ms'],
        "position_human": ms_to_human(row['position_ms']),
        "source": row['source'],
        "recorded_at": row['recorded_at'],
    } for row in cursor.fetchall()]

    return jsonify({
        "audiobook_id": audiobook_id,
        "history": history,
    })
