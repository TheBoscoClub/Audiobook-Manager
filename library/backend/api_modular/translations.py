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
    """Initialize with database path and ensure schema is current."""
    global _db_path
    _db_path = database_path

    # Idempotent migration: older installs lack series_display column.
    # SQLite has no ADD COLUMN IF NOT EXISTS, so we check pragma first.
    try:
        conn = sqlite3.connect(str(_db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(audiobook_translations)")}
        if "series_display" not in cols:
            conn.execute("ALTER TABLE audiobook_translations ADD COLUMN series_display TEXT")
            conn.commit()
            logger.info("Added series_display column to audiobook_translations")
        conn.close()
    except sqlite3.Error:
        logger.exception("Failed to ensure audiobook_translations.series_display column")

    # Migration 018: collection_translations cache table.
    try:
        conn = sqlite3.connect(str(_db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS collection_translations (
                collection_id TEXT NOT NULL,
                locale TEXT NOT NULL,
                name TEXT NOT NULL,
                translator TEXT DEFAULT 'deepl',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collection_id, locale)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection_translations_locale "
            "ON collection_translations(locale)"
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        logger.exception("Failed to ensure collection_translations table")

    # Migration 019: string_translations generic cache table.
    try:
        conn = sqlite3.connect(str(_db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS string_translations (
                source_hash TEXT NOT NULL,
                locale TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                translator TEXT DEFAULT 'deepl',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_hash, locale)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_string_translations_locale "
            "ON string_translations(locale)"
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        logger.exception("Failed to ensure string_translations table")


def _hash_source(text: str) -> str:
    """Short SHA-256 hex digest used as cache key for a source string.

    Not used as a signature — only a stable lookup key for the
    string_translations cache. SHA-256 satisfies the security linter.
    """
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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

    Optional query param:
        ?ids=123,456,789  — visible book IDs; triggers on-demand DeepL
                            translation for any IDs missing from the cache.
    """
    if locale == "en":
        return jsonify({})

    conn = _get_db()
    try:
        # Fetch all cached translations for this locale
        rows = conn.execute(
            "SELECT audiobook_id, title, author_display, series_display, description "
            "FROM audiobook_translations WHERE locale = ?",
            (locale,),
        ).fetchall()
        result = {}
        for r in rows:
            result[str(r["audiobook_id"])] = {
                "title": r["title"],
                "author_display": r["author_display"],
                "series_display": r["series_display"],
                "description": r["description"],
            }

        # On-demand translation: if ?ids= provided, translate missing ones
        ids_param = request.args.get("ids", "")
        if ids_param:
            try:
                requested_ids = [int(x) for x in ids_param.split(",") if x.strip()]
            except (ValueError, TypeError):
                requested_ids = []

            # Cap per request
            requested_ids = requested_ids[:60]
            # A book needs (re-)translation if it is not cached OR the cached
            # row predates the series_display column and has no series yet.
            missing_ids = [
                bid for bid in requested_ids
                if str(bid) not in result
                or result[str(bid)].get("series_display") is None
            ]

            if missing_ids:
                _translate_missing(conn, missing_ids, locale, result)

        return jsonify(result)
    finally:
        conn.close()


def _translate_missing(conn, missing_ids, locale, result_dict):
    """Translate missing book metadata via DeepL and store in DB.

    Translates titles and authors per-book, and series names de-duplicated
    (many books share the same series — translating once keeps API usage
    low and output consistent across the series).

    Updates result_dict in place with newly translated entries.
    """
    try:
        from localization.config import DEEPL_API_KEY
        if not DEEPL_API_KEY:
            logger.warning("On-demand translation: no DeepL API key configured")
            return

        all_books = conn.execute(
            "SELECT id, title, author, series FROM audiobooks"
        ).fetchall()
        books = [dict(r) for r in all_books if r["id"] in missing_ids]
        if not books:
            return

        from localization.translation.deepl_translate import DeepLTranslator
        translator = DeepLTranslator(DEEPL_API_KEY)

        # Determine which books need title+author translation vs. series-only.
        # A book already in result_dict (from cached row) only needs series.
        needs_title = [b for b in books if str(b["id"]) not in result_dict]

        translated_titles = []
        author_map_new = []
        if needs_title:
            titles = [b["title"] for b in needs_title]
            authors = [b["author"] or "" for b in needs_title]
            translated_titles = translator.translate(titles, locale)
            translated_authors = translator.translate(
                [a for a in authors if a], locale
            ) if any(authors) else []
            author_iter = iter(translated_authors)
            for a in authors:
                author_map_new.append(next(author_iter, a) if a else "")

        # Dedupe series — many books share the same series string.
        unique_series = sorted({
            b["series"].strip() for b in books
            if b.get("series") and b["series"].strip()
        })
        series_translation = {}
        if unique_series:
            translated_series = translator.translate(unique_series, locale)
            for src, tgt in zip(unique_series, translated_series):
                series_translation[src] = tgt

        # Apply translations: either insert fresh or update existing row's series.
        title_iter = iter(translated_titles)
        author_iter2 = iter(author_map_new)
        for book in books:
            book_id_str = str(book["id"])
            src_series = (book.get("series") or "").strip()
            # Empty string (not NULL) marks "source had no series" so the
            # row no longer re-qualifies as missing on subsequent requests.
            t_series = series_translation.get(src_series, "") if src_series else ""

            if book_id_str not in result_dict:
                # Fresh insert: need title + author too.
                t_title = next(title_iter, book["title"])
                t_author = next(author_iter2, book["author"] or "")
                conn.execute(
                    """INSERT INTO audiobook_translations
                       (audiobook_id, locale, title, author_display,
                        series_display, translator)
                       VALUES (?, ?, ?, ?, ?, 'deepl')
                       ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                           title = excluded.title,
                           author_display = excluded.author_display,
                           series_display = excluded.series_display,
                           translator = excluded.translator,
                           updated_at = CURRENT_TIMESTAMP
                    """,
                    (book["id"], locale, t_title, t_author, t_series),
                )
                result_dict[book_id_str] = {
                    "title": t_title,
                    "author_display": t_author,
                    "series_display": t_series,
                    "description": None,
                }
            else:
                # Existing row with NULL series_display — update that field only.
                conn.execute(
                    "UPDATE audiobook_translations SET series_display = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE audiobook_id = ? AND locale = ?",
                    (t_series, book["id"], locale),
                )
                result_dict[book_id_str]["series_display"] = t_series

        conn.commit()
        logger.info(
            "On-demand translated %d books (%d unique series) to %s via DeepL",
            len(books), len(unique_series), locale,
        )

    except Exception:
        logger.exception("On-demand translation failed")


@translations_bp.route("/api/translations/collections/<locale>", methods=["GET"])
@guest_allowed
def get_collection_translations(locale):
    """Return {collection_id: translated_name} for all sidebar collections.

    Walks the live collection tree (same source as /api/collections),
    returns cached translations, and on-demand translates any missing
    names via DeepL. English short-circuits to an empty dict.
    """
    if locale == "en":
        return jsonify({})

    from .collections import _build_dynamic_collections

    conn = _get_db()
    try:
        cursor = conn.cursor()
        tree, _flat = _build_dynamic_collections(cursor)

        id_to_name: dict[str, str] = {}
        for node in tree:
            id_to_name[node["id"]] = node["name"]
            for child in node.get("children", []):
                id_to_name[child["id"]] = child["name"]

        cached_rows = conn.execute(
            "SELECT collection_id, name FROM collection_translations "
            "WHERE locale = ?",
            (locale,),
        ).fetchall()
        result: dict[str, str] = {
            r["collection_id"]: r["name"]
            for r in cached_rows
            if r["collection_id"] in id_to_name
        }

        missing = [cid for cid in id_to_name if cid not in result]
        if missing:
            _translate_missing_collections(conn, missing, id_to_name, locale, result)

        return jsonify(result)
    finally:
        conn.close()


def _translate_missing_collections(conn, missing_ids, id_to_name, locale, result_dict):
    """Translate missing collection names via DeepL and cache them.

    Deduplicates by source name so DeepL is called once per unique label.
    Updates result_dict in place.
    """
    try:
        from localization.config import DEEPL_API_KEY
        if not DEEPL_API_KEY:
            logger.warning("Collection translation: no DeepL API key configured")
            return

        unique_names = sorted({
            id_to_name[cid] for cid in missing_ids
            if id_to_name.get(cid)
        })
        if not unique_names:
            return

        from localization.translation.deepl_translate import DeepLTranslator
        translator = DeepLTranslator(DEEPL_API_KEY)
        translated = translator.translate(unique_names, locale)
        name_map = dict(zip(unique_names, translated))

        for cid in missing_ids:
            src = id_to_name.get(cid)
            if not src:
                continue
            t_name = name_map.get(src, src)
            conn.execute(
                """INSERT INTO collection_translations
                   (collection_id, locale, name, translator)
                   VALUES (?, ?, ?, 'deepl')
                   ON CONFLICT(collection_id, locale) DO UPDATE SET
                       name = excluded.name,
                       translator = excluded.translator,
                       updated_at = CURRENT_TIMESTAMP
                """,
                (cid, locale, t_name),
            )
            result_dict[cid] = t_name

        conn.commit()
        logger.info(
            "On-demand translated %d collections (%d unique names) to %s",
            len(missing_ids), len(unique_names), locale,
        )
    except Exception:
        logger.exception("Collection translation failed")


@translations_bp.route("/api/translations/strings", methods=["POST"])
@guest_allowed
def translate_strings():
    """Generic string translation cache.

    Frontend collects visible text (section headings, tour titles,
    notification bodies, etc.), posts the batch, receives a
    {source_hash: translation} map, and overlays locally.

    Request body:
        { "locale": "zh-Hans", "strings": ["Welcome", "Getting Started", ...] }

    Response:
        { "<hash>": "欢迎", ... }  -- keyed by _hash_source(source)
    """
    data = request.get_json()
    if not data or not data.get("locale"):
        return jsonify({"error": "locale is required"}), 400

    locale = data["locale"]
    if locale == "en":
        return jsonify({})

    raw_strings = data.get("strings") or []
    if not isinstance(raw_strings, list):
        return jsonify({"error": "strings must be a list"}), 400

    # Normalize: strip, dedupe, cap.
    seen: dict[str, str] = {}
    for s in raw_strings:
        if not isinstance(s, str):
            continue
        text = s.strip()
        if not text or len(text) > 1000:
            continue
        h = _hash_source(text)
        if h not in seen:
            seen[h] = text
        if len(seen) >= 200:
            break

    if not seen:
        return jsonify({})

    conn = _get_db()
    try:
        placeholders = ",".join("?" * len(seen))
        rows = conn.execute(
            f"SELECT source_hash, translation FROM string_translations "
            f"WHERE locale = ? AND source_hash IN ({placeholders})",
            (locale, *seen.keys()),
        ).fetchall()
        result: dict[str, str] = {r["source_hash"]: r["translation"] for r in rows}

        missing = {h: src for h, src in seen.items() if h not in result}
        if missing:
            try:
                from localization.config import DEEPL_API_KEY
                if DEEPL_API_KEY:
                    from localization.translation.deepl_translate import DeepLTranslator
                    translator = DeepLTranslator(DEEPL_API_KEY)
                    hashes = list(missing.keys())
                    sources = [missing[h] for h in hashes]
                    translated = translator.translate(sources, locale)
                    for h, src, tgt in zip(hashes, sources, translated):
                        conn.execute(
                            """INSERT INTO string_translations
                               (source_hash, locale, source, translation, translator)
                               VALUES (?, ?, ?, ?, 'deepl')
                               ON CONFLICT(source_hash, locale) DO UPDATE SET
                                   translation = excluded.translation,
                                   translator = excluded.translator,
                                   updated_at = CURRENT_TIMESTAMP
                            """,
                            (h, locale, src, tgt),
                        )
                        result[h] = tgt
                    conn.commit()
                    logger.info(
                        "String-translated %d unique strings to %s via DeepL",
                        len(missing), locale,
                    )
                else:
                    logger.warning("String translation: no DeepL API key configured")
            except Exception:
                logger.exception("String translation failed")

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
