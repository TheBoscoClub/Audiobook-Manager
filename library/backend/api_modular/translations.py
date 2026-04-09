"""
Audiobook Translations API blueprint.

Manages per-locale metadata translations for book cards.
Translations can be created manually by admins or auto-generated
via DeepL machine translation.

Endpoints:
    GET  /api/audiobooks/<id>/translations          — all translations for a book
    GET  /api/audiobooks/<id>/translations/<locale>  — single locale translation
    POST /api/audiobooks/<id>/translations           — create or update translation
    DELETE /api/audiobooks/<id>/translations/<locale> — delete a translation
    POST /api/translations/batch                     — batch translate multiple books
"""

import logging
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request

from .auth import admin_if_enabled, guest_allowed

translations_bp = Blueprint("translations", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None


def init_translations_routes(database_path):
    """Initialize with database path."""
    global _db_path
    _db_path = database_path


def _get_db():
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@translations_bp.route(
    "/api/audiobooks/<int:book_id>/translations", methods=["GET"]
)
@guest_allowed
def get_book_translations(book_id):
    """Get all translations for a book."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM audiobook_translations "
            "WHERE audiobook_id = ? ORDER BY locale",
            (book_id,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@translations_bp.route(
    "/api/audiobooks/<int:book_id>/translations/<locale>", methods=["GET"]
)
@guest_allowed
def get_translation(book_id, locale):
    """Get translation for a specific locale."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM audiobook_translations "
            "WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if not row:
            return jsonify({"error": "Translation not found"}), 404
        return jsonify(dict(row))
    finally:
        conn.close()


@translations_bp.route(
    "/api/audiobooks/<int:book_id>/translations", methods=["POST"]
)
@admin_if_enabled
def upsert_translation(book_id):
    """Create or update a translation (upsert)."""
    data = request.get_json()
    if not data or not data.get("locale"):
        return jsonify({"error": "locale is required"}), 400

    locale = data["locale"]
    title = data.get("title")
    author_display = data.get("author_display")
    description = data.get("description")
    translator = data.get("translator", "manual")

    conn = _get_db()
    try:
        # Verify the audiobook exists
        book = conn.execute(
            "SELECT id FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        conn.execute(
            """INSERT INTO audiobook_translations
               (audiobook_id, locale, title, author_display, description, translator)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                   title = excluded.title,
                   author_display = excluded.author_display,
                   description = excluded.description,
                   translator = excluded.translator,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, locale, title, author_display, description, translator),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM audiobook_translations "
            "WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        ).fetchone()
        return jsonify(dict(row)), 201
    finally:
        conn.close()


@translations_bp.route(
    "/api/audiobooks/<int:book_id>/translations/<locale>", methods=["DELETE"]
)
@admin_if_enabled
def delete_translation(book_id, locale):
    """Delete a translation for a specific locale."""
    conn = _get_db()
    try:
        result = conn.execute(
            "DELETE FROM audiobook_translations "
            "WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        )
        conn.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Translation not found"}), 404
        return jsonify({"message": "Translation deleted"})
    finally:
        conn.close()


@translations_bp.route("/api/translations/by-locale/<locale>", methods=["GET"])
@guest_allowed
def get_translations_by_locale(locale):
    """Get all translations for a given locale.

    Returns a dict keyed by audiobook_id for easy client-side lookup.
    Used by the frontend to overlay translated metadata on book cards.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT audiobook_id, title, author_display, description "
            "FROM audiobook_translations WHERE locale = ?",
            (locale,),
        ).fetchall()
        result = {}
        for r in rows:
            result[str(r["audiobook_id"])] = {
                "title": r["title"],
                "author_display": r["author_display"],
                "description": r["description"],
            }
        return jsonify(result)
    finally:
        conn.close()


@translations_bp.route("/api/translations/batch", methods=["POST"])
@admin_if_enabled
def batch_translate():
    """Batch translate multiple audiobooks for a given locale.

    Request body:
        {
            "audiobook_ids": [1, 2, 3],  -- or "all" for entire library
            "locale": "zh-Hans",
            "provider": "deepl"          -- only "deepl" supported for now
        }

    This is a synchronous endpoint for small batches. For large-scale
    translation, a background job system will be added later.
    """
    data = request.get_json()
    if not data or not data.get("locale"):
        return jsonify({"error": "locale is required"}), 400

    locale = data["locale"]
    provider = data.get("provider", "deepl")
    book_ids = data.get("audiobook_ids")

    if provider != "deepl":
        return jsonify({"error": "Only 'deepl' provider is supported"}), 400

    # Validate IDs upfront if a list was provided
    if isinstance(book_ids, list):
        try:
            requested_ids = {int(bid) for bid in book_ids}
        except (ValueError, TypeError):
            return jsonify({"error": "audiobook_ids must contain integers"}), 400
        if not requested_ids:
            return jsonify({"error": "audiobook_ids must not be empty"}), 400
    elif book_ids != "all":
        return jsonify({"error": "audiobook_ids must be a list or 'all'"}), 400
    else:
        requested_ids = None  # means "all"

    conn = _get_db()
    try:
        # Fetch all books and filter in Python (avoids dynamic IN clause)
        all_rows = conn.execute(
            "SELECT id, title, author FROM audiobooks"
        ).fetchall()
        if requested_ids is not None:
            books = [dict(r) for r in all_rows if r["id"] in requested_ids]
        else:
            books = [dict(r) for r in all_rows]

        # Fetch all existing translations for this locale and filter in Python
        existing = set()
        if books:
            all_translations = conn.execute(
                "SELECT audiobook_id FROM audiobook_translations WHERE locale = ?",
                (locale,),
            ).fetchall()
            all_translated_ids = {r["audiobook_id"] for r in all_translations}
            book_ids_set = {b["id"] for b in books}
            existing = all_translated_ids & book_ids_set

        needs_translation = [b for b in books if b["id"] not in existing]

        return jsonify({
            "total_books": len(books),
            "already_translated": len(existing),
            "needs_translation": len(needs_translation),
            "books": needs_translation,
            "message": "DeepL batch translation not yet implemented. "
                       "Use POST /api/audiobooks/<id>/translations for manual entries.",
        })
    finally:
        conn.close()
