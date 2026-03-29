"""
User Suggestions API blueprint.

Authenticated users can submit suggestions from the Help page.
Admins can view, mark read/unread, and delete suggestions.
"""

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from .auth import admin_if_enabled, login_required

suggestions_bp = Blueprint("suggestions", __name__)

_db_path = None
MAX_MESSAGE_LENGTH = 2048


def init_suggestions_routes(database_path):
    """Initialize with database path."""
    global _db_path
    _db_path = database_path


def _get_db():
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def sanitize_message(text):
    """Sanitize user input: strip invisible chars, HTML, control codes.

    Allows normal printable text including accented characters, punctuation,
    and standard whitespace (space, newline, tab). Blocks:
    - HTML tags (all stripped)
    - Control characters (U+0000-U+001F except \\n \\r \\t, U+007F-U+009F)
    - Unicode invisibles (zero-width chars, soft hyphens, direction overrides)
    - Category Cf (format chars), Cc (control), Cs (surrogates), Co (private use)
    - Excessive whitespace (collapsed)
    """
    if not text:
        return ""

    # Strip HTML tags (use lazy quantifier to avoid ReDoS on pathological input)
    text = re.sub(r"<[^>]*?>", "", text)

    # Strip HTML entities
    text = re.sub(r"&[#\w]+;", "", text)

    # Remove invisible and dangerous Unicode characters
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        # Allow normal letters, numbers, punctuation, symbols, spaces
        if cat.startswith(("L", "N", "P", "S", "Z")):
            # But block specific invisible space chars
            cp = ord(ch)
            if cp in (
                0x00AD,  # Soft hyphen
                0x034F,  # Combining grapheme joiner
                0x061C,  # Arabic letter mark
                0x115F,
                0x1160,  # Hangul fillers
                0x17B4,
                0x17B5,  # Khmer invisible chars
                0x180E,  # Mongolian vowel separator
                0x200B,  # Zero-width space
                0x200C,
                0x200D,  # Zero-width non-joiner/joiner
                0x200E,
                0x200F,  # LTR/RTL marks
                0x202A,
                0x202B,
                0x202C,
                0x202D,
                0x202E,  # Bidi overrides
                0x2060,  # Word joiner
                0x2061,
                0x2062,
                0x2063,
                0x2064,  # Invisible operators
                0x2066,
                0x2067,
                0x2068,
                0x2069,  # Bidi isolates
                0x206A,
                0x206B,
                0x206C,
                0x206D,
                0x206E,
                0x206F,  # Deprecated
                0xFE00,
                0xFE01,
                0xFE02,
                0xFE03,  # Variation selectors (first 4)
                0xFEFF,  # BOM / zero-width no-break space
                0xFFF9,
                0xFFFA,
                0xFFFB,  # Interlinear annotations
                0xFFFC,
                0xFFFD,  # Object replacement, replacement char
            ):
                continue
            # Block variation selectors range
            if 0xFE00 <= cp <= 0xFE0F:
                continue
            # Block tags block
            if 0xE0001 <= cp <= 0xE007F:
                continue
            cleaned.append(ch)
        elif ch in ("\n", "\r", "\t"):
            cleaned.append(ch)
        # Everything else (Cf, Cc, Cs, Co, Mn combining marks used for zalgo) dropped

    text = "".join(cleaned)

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Collapse runs of 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Collapse runs of whitespace on each line (preserve newlines)
    lines = text.split("\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    text = "\n".join(lines)

    # Final trim
    text = text.strip()

    return text[:MAX_MESSAGE_LENGTH]


@suggestions_bp.route("/api/suggestions", methods=["POST"])
@login_required
def submit_suggestion():
    """Authenticated user submits a suggestion."""
    from .auth import get_current_user

    user = get_current_user()
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "message is required"}), 400

    raw = data["message"]
    if not isinstance(raw, str):
        return jsonify({"error": "message must be a string"}), 400

    message = sanitize_message(raw)
    if not message:
        return jsonify({"error": "message is empty after sanitization"}), 400

    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH]

    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    conn.execute(
        "INSERT INTO user_suggestions (username, message, created_at) VALUES (?, ?, ?)",
        (user.username, message, now),
    )
    conn.commit()
    conn.close()

    # Broadcast to admins via WebSocket
    try:
        from .websocket import connection_manager

        connection_manager.broadcast(
            {
                "type": "suggestion_new",
                "username": user.username,
            }
        )
    except Exception:
        pass  # WebSocket is optional

    return jsonify({"message": "Thank you for your suggestion"}), 201


@suggestions_bp.route("/api/admin/suggestions", methods=["GET"])
@admin_if_enabled
def admin_get_suggestions():
    """Admin: list suggestions. ?filter=unread|read|all (default: all)"""
    filt = request.args.get("filter", "all")
    conn = _get_db()

    if filt == "unread":
        rows = conn.execute(
            "SELECT * FROM user_suggestions WHERE is_read = 0 ORDER BY created_at ASC"
        ).fetchall()
    elif filt == "read":
        rows = conn.execute(
            "SELECT * FROM user_suggestions WHERE is_read = 1 ORDER BY created_at ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM user_suggestions ORDER BY created_at ASC"
        ).fetchall()

    conn.close()
    return jsonify([dict(r) for r in rows])


@suggestions_bp.route("/api/admin/suggestions/unread-count", methods=["GET"])
@admin_if_enabled
def admin_unread_count():
    """Admin: get count of unread suggestions (for badge)."""
    conn = _get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM user_suggestions WHERE is_read = 0"
    ).fetchone()[0]
    conn.close()
    return jsonify({"count": count})


@suggestions_bp.route("/api/admin/suggestions/<int:item_id>", methods=["PATCH"])
@admin_if_enabled
def admin_update_suggestion(item_id):
    """Admin: mark suggestion read/unread."""
    data = request.get_json()
    if not data or "is_read" not in data:
        return jsonify({"error": "is_read field required"}), 400

    is_read = 1 if data["is_read"] else 0
    conn = _get_db()
    result = conn.execute(
        "UPDATE user_suggestions SET is_read = ? WHERE id = ?", (is_read, item_id)
    )
    conn.commit()
    conn.close()
    if result.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"message": "Updated"})


@suggestions_bp.route("/api/admin/suggestions/<int:item_id>", methods=["DELETE"])
@admin_if_enabled
def admin_delete_suggestion(item_id):
    """Admin: delete a suggestion."""
    conn = _get_db()
    result = conn.execute("DELETE FROM user_suggestions WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    if result.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"message": "Deleted"})
