"""
Audiobook Translations API blueprint.

Manages per-locale metadata translations for book cards.
Translations can be created manually by admins or auto-generated
via DeepL machine translation on demand.

Endpoints:
    GET  /api/audiobooks/<id>/translations          — all translations for a book
    GET  /api/audiobooks/<id>/translations/<locale>  — single locale translation
    POST /api/audiobooks/<id>/translations           — create or update translation
    DELETE /api/audiobooks/<id>/translations/<locale> — delete a translation
    POST /api/translations/batch                     — batch translate multiple books
    POST /api/translations/on-demand                 — on-demand translate visible books
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


@translations_bp.route("/api/translations/on-demand", methods=["POST"])
@guest_allowed
def on_demand_translate():
    """Translate book metadata on demand for visible book cards.

    Called automatically by the frontend when a non-English locale is
    active and book cards are missing translations. Translates titles
    and author names via DeepL, caches results in the DB, and returns
    the translations keyed by audiobook_id.

    Request body:
        {
            "audiobook_ids": [1, 2, 3],
            "locale": "zh-Hans"
        }

    Returns translations in the same format as GET /translations/by-locale,
    so the frontend can apply them directly.
    """
    data = request.get_json()
    if not data or not data.get("locale") or not data.get("audiobook_ids"):
        return jsonify({"error": "locale and audiobook_ids are required"}), 400

    locale = data["locale"]
    if locale == "en":
        return jsonify({})

    try:
        requested_ids = [int(bid) for bid in data["audiobook_ids"]]
    except (ValueError, TypeError):
        return jsonify({"error": "audiobook_ids must contain integers"}), 400
    if not requested_ids:
        return jsonify({})

    # Cap per request to prevent abuse (a library page shows ~50 books max)
    MAX_PER_REQUEST = 60
    requested_ids = requested_ids[:MAX_PER_REQUEST]

    conn = _get_db()
    try:
        # Find which books already have translations cached
        all_translations = conn.execute(
            "SELECT audiobook_id, title, author_display, description "
            "FROM audiobook_translations WHERE locale = ?",
            (locale,),
        ).fetchall()
        cached = {}
        for r in all_translations:
            if r["audiobook_id"] in requested_ids:
                cached[str(r["audiobook_id"])] = {
                    "title": r["title"],
                    "author_display": r["author_display"],
                    "description": r["description"],
                }

        # Determine which IDs still need translation
        cached_ids = {int(k) for k in cached}
        missing_ids = [bid for bid in requested_ids if bid not in cached_ids]

        if not missing_ids:
            return jsonify(cached)

        # Load DeepL API key from localization config
        from localization.config import DEEPL_API_KEY
        if not DEEPL_API_KEY:
            logger.warning("On-demand translation requested but no DeepL API key configured")
            return jsonify(cached)

        # Fetch book metadata for untranslated books
        all_books = conn.execute(
            "SELECT id, title, author FROM audiobooks"
        ).fetchall()
        books_to_translate = [dict(r) for r in all_books if r["id"] in missing_ids]

        if not books_to_translate:
            return jsonify(cached)

        # Batch translate titles and authors via DeepL
        from localization.translation.deepl_translate import DeepLTranslator
        translator = DeepLTranslator(DEEPL_API_KEY)

        titles = [b["title"] for b in books_to_translate]
        authors = [b["author"] or "" for b in books_to_translate]

        # Single batch call for titles; separate for authors (different context)
        translated_titles = translator.translate(titles, locale)
        translated_authors = translator.translate(
            [a for a in authors if a], locale
        ) if any(authors) else []

        # Map author translations back (skipping empty originals)
        author_iter = iter(translated_authors)
        author_map = []
        for a in authors:
            if a:
                author_map.append(next(author_iter, a))
            else:
                author_map.append("")

        # Store in DB and build response
        new_translations = {}
        for i, book in enumerate(books_to_translate):
            t_title = translated_titles[i] if i < len(translated_titles) else book["title"]
            t_author = author_map[i] if i < len(author_map) else (book["author"] or "")

            conn.execute(
                """INSERT INTO audiobook_translations
                   (audiobook_id, locale, title, author_display, translator)
                   VALUES (?, ?, ?, ?, 'deepl')
                   ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                       title = excluded.title,
                       author_display = excluded.author_display,
                       translator = excluded.translator,
                       updated_at = CURRENT_TIMESTAMP
                """,
                (book["id"], locale, t_title, t_author),
            )

            new_translations[str(book["id"])] = {
                "title": t_title,
                "author_display": t_author,
                "description": None,
            }

        conn.commit()
        logger.info(
            "On-demand translated %d books to %s via DeepL",
            len(books_to_translate), locale,
        )

        # Merge cached + newly translated
        cached.update(new_translations)
        return jsonify(cached)

    except Exception:
        logger.exception("On-demand translation failed")
        # Return whatever we have cached — partial is better than nothing
        try:
            return jsonify(cached)
        except NameError:
            return jsonify({})
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
    """
    data = request.get_json()
    if not data or not data.get("locale"):
        return jsonify({"error": "locale is required"}), 400

    locale = data["locale"]
    provider = data.get("provider", "deepl")
    book_ids = data.get("audiobook_ids")

    if provider != "deepl":
        return jsonify({"error": "Only 'deepl' provider is supported"}), 400

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
        requested_ids = None

    conn = _get_db()
    try:
        all_rows = conn.execute(
            "SELECT id, title, author FROM audiobooks"
        ).fetchall()
        if requested_ids is not None:
            books = [dict(r) for r in all_rows if r["id"] in requested_ids]
        else:
            books = [dict(r) for r in all_rows]

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

        if not needs_translation:
            return jsonify({
                "total_books": len(books),
                "translated": len(existing),
                "needs_translation": 0,
                "translations": {},
            })

        from localization.config import DEEPL_API_KEY
        if not DEEPL_API_KEY:
            return jsonify({"error": "DeepL API key not configured"}), 503

        from localization.translation.deepl_translate import DeepLTranslator
        translator = DeepLTranslator(DEEPL_API_KEY)

        titles = [b["title"] for b in needs_translation]
        authors = [b["author"] or "" for b in needs_translation]

        translated_titles = translator.translate(titles, locale)
        translated_authors = translator.translate(
            [a for a in authors if a], locale
        ) if any(authors) else []

        author_iter = iter(translated_authors)
        author_map = []
        for a in authors:
            author_map.append(next(author_iter, a) if a else "")

        translations = {}
        for i, book in enumerate(needs_translation):
            t_title = translated_titles[i] if i < len(translated_titles) else book["title"]
            t_author = author_map[i] if i < len(author_map) else (book["author"] or "")

            conn.execute(
                """INSERT INTO audiobook_translations
                   (audiobook_id, locale, title, author_display, translator)
                   VALUES (?, ?, ?, ?, 'deepl')
                   ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                       title = excluded.title,
                       author_display = excluded.author_display,
                       translator = excluded.translator,
                       updated_at = CURRENT_TIMESTAMP
                """,
                (book["id"], locale, t_title, t_author),
            )
            translations[str(book["id"])] = {
                "title": t_title,
                "author_display": t_author,
            }

        conn.commit()
        logger.info("Batch translated %d books to %s", len(needs_translation), locale)

        return jsonify({
            "total_books": len(books),
            "already_translated": len(existing),
            "newly_translated": len(needs_translation),
            "translations": translations,
        })
    finally:
        conn.close()
