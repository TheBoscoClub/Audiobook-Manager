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

from .auth import admin_if_enabled, admin_required, guest_allowed
from .search_cjk import pinyin_sort_key

translations_bp = Blueprint("translations", __name__)
logger = logging.getLogger(__name__)


def _sanitize_log(value) -> str:
    """Sanitize user-controlled values for safe logging (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


_db_path: Path | None = None


def _run_migration(label: str, body):
    """Open a connection, invoke body(conn), commit, close. Log + swallow sqlite3.Error."""
    conn = None
    try:
        conn = sqlite3.connect(str(_db_path))
        body(conn)
        conn.commit()
    except sqlite3.Error:
        logger.exception(label)
    finally:
        if conn is not None:
            conn.close()


def _migrate_audiobook_translations(conn):
    """Migration 016: ensure the base audiobook_translations table exists.

    New installs get this from schema.sql; older installs that predate
    schema.sql migration 016 (or whose DB wasn't reinitialized) need this
    idempotent CREATE to avoid downstream "no such table" failures from
    subsequent column migrations like _migrate_series_display.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS audiobook_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            title TEXT,
            author_display TEXT,
            series_display TEXT,
            description TEXT,
            translator TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(audiobook_id, locale),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audiobook_translations_locale "
        "ON audiobook_translations(locale)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audiobook_translations_book "
        "ON audiobook_translations(audiobook_id)"
    )


def _migrate_series_display(conn):
    """Older installs lack series_display column. SQLite has no
    ADD COLUMN IF NOT EXISTS, so we check pragma first."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audiobook_translations)")}
    if "series_display" not in cols:
        conn.execute("ALTER TABLE audiobook_translations ADD COLUMN series_display TEXT")
        logger.info("Added series_display column to audiobook_translations")


def _migrate_pinyin_sort(conn):
    """Migration 017b: ensure pinyin_sort column and its composite index exist.

    INSERTs at lines 239/387/765 already reference pinyin_sort; upgrades from
    DBs predating canonical schema.sql migration 017 must get the column via
    ALTER TABLE or those zh-* INSERTs raise 'no such column' at first use."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audiobook_translations)")}
    if "pinyin_sort" not in cols:
        conn.execute("ALTER TABLE audiobook_translations ADD COLUMN pinyin_sort TEXT")
        logger.info("Added pinyin_sort column to audiobook_translations")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audiobook_translations_pinyin_sort "
        "ON audiobook_translations(locale, pinyin_sort)"
    )


def _migrate_collection_translations(conn):
    """Migration 018: collection_translations cache table."""
    conn.execute("""CREATE TABLE IF NOT EXISTS collection_translations (
            collection_id TEXT NOT NULL,
            locale TEXT NOT NULL,
            name TEXT NOT NULL,
            translator TEXT DEFAULT 'deepl',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (collection_id, locale)
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_translations_locale "
        "ON collection_translations(locale)"
    )


def _migrate_string_translations(conn):
    """Migration 019: string_translations generic cache table."""
    conn.execute("""CREATE TABLE IF NOT EXISTS string_translations (
            source_hash TEXT NOT NULL,
            locale TEXT NOT NULL,
            source TEXT NOT NULL,
            translation TEXT NOT NULL,
            translator TEXT DEFAULT 'deepl',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_hash, locale)
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_string_translations_locale ON string_translations(locale)"
    )


def _migrate_deepl_quota(conn):
    """Migration 020: deepl_quota single-row bookkeeping for quota/glossary."""
    conn.execute("""CREATE TABLE IF NOT EXISTS deepl_quota (
            id TEXT PRIMARY KEY DEFAULT 'default',
            chars_used INTEGER NOT NULL DEFAULT 0,
            char_limit INTEGER NOT NULL DEFAULT 500000,
            period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_api_check TIMESTAMP,
            glossary_id TEXT,
            glossary_source_hash TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
    conn.execute("INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default')")


# Each entry: (error log label, migration callable taking a live conn).
# Order matters — the base table must be ensured before column/index migrations
# against it run.
_MIGRATIONS: tuple[tuple[str, object], ...] = (
    ("Failed to ensure audiobook_translations table", _migrate_audiobook_translations),
    ("Failed to ensure audiobook_translations.series_display column", _migrate_series_display),
    ("Failed to ensure audiobook_translations.pinyin_sort column", _migrate_pinyin_sort),
    ("Failed to ensure collection_translations table", _migrate_collection_translations),
    ("Failed to ensure string_translations table", _migrate_string_translations),
    ("Failed to ensure deepl_quota table", _migrate_deepl_quota),
)


def init_translations_routes(database_path):
    """Initialize with database path and ensure schema is current."""
    global _db_path
    _db_path = database_path

    for label, migration in _MIGRATIONS:
        _run_migration(label, migration)


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


@translations_bp.route("/api/audiobooks/<int:book_id>/translations", methods=["GET"])
@guest_allowed
def get_book_translations(book_id):
    """Get all translations for a book."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM audiobook_translations WHERE audiobook_id = ? ORDER BY locale",
            (book_id,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@translations_bp.route("/api/audiobooks/<int:book_id>/translations/<locale>", methods=["GET"])
@guest_allowed
def get_translation(book_id, locale):
    """Get translation for a specific locale."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM audiobook_translations WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if not row:
            return jsonify({"error": "Translation not found"}), 404
        return jsonify(dict(row))
    finally:
        conn.close()


@translations_bp.route("/api/audiobooks/<int:book_id>/translations", methods=["POST"])
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
        book = conn.execute("SELECT id FROM audiobooks WHERE id = ?", (book_id,)).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        pinyin = pinyin_sort_key(title) if locale.startswith("zh") else None
        conn.execute(
            """INSERT INTO audiobook_translations
               (audiobook_id, locale, title, author_display, description,
                translator, pinyin_sort)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                   title = excluded.title,
                   author_display = excluded.author_display,
                   description = excluded.description,
                   translator = excluded.translator,
                   pinyin_sort = excluded.pinyin_sort,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, locale, title, author_display, description, translator, pinyin),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM audiobook_translations WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        ).fetchone()
        return jsonify(dict(row)), 201
    finally:
        conn.close()


@translations_bp.route("/api/audiobooks/<int:book_id>/translations/<locale>", methods=["DELETE"])
@admin_if_enabled
def delete_translation(book_id, locale):
    """Delete a translation for a specific locale."""
    conn = _get_db()
    try:
        result = conn.execute(
            "DELETE FROM audiobook_translations WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        )
        conn.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Translation not found"}), 404
        return jsonify({"message": "Translation deleted"})
    finally:
        conn.close()


def _load_locale_translations(conn, locale):
    """Fetch cached translations for a locale, keyed by audiobook_id as str."""
    rows = conn.execute(
        "SELECT audiobook_id, title, author_display, series_display, description "
        "FROM audiobook_translations WHERE locale = ?",
        (locale,),
    ).fetchall()
    return {
        str(r["audiobook_id"]): {
            "title": r["title"],
            "author_display": r["author_display"],
            "series_display": r["series_display"],
            "description": r["description"],
        }
        for r in rows
    }


def _parse_ids_param(ids_param):
    """Parse ?ids=1,2,3 into a list of ints, capped at 60. Ignores garbage."""
    if not ids_param:
        return []
    try:
        requested_ids = [int(x) for x in ids_param.split(",") if x.strip()]
    except ValueError, TypeError:
        return []
    return requested_ids[:60]


def _compute_missing_ids(requested_ids, result):
    """A book is missing if not cached OR cached row lacks series_display."""
    return [
        bid
        for bid in requested_ids
        if str(bid) not in result or result[str(bid)].get("series_display") is None
    ]


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
        result = _load_locale_translations(conn, locale)
        requested_ids = _parse_ids_param(request.args.get("ids", ""))
        if requested_ids:
            missing_ids = _compute_missing_ids(requested_ids, result)
            if missing_ids:
                _translate_missing(conn, missing_ids, locale, result)
        return jsonify(result)
    finally:
        conn.close()


def _load_books_for_missing(conn, missing_ids):
    """Fetch audiobook rows whose id is in missing_ids."""
    all_books = conn.execute("SELECT id, title, author, series FROM audiobooks").fetchall()
    return [dict(r) for r in all_books if r["id"] in missing_ids]


def _translate_title_author_batch(translator, needs_title, locale):
    """Translate titles + authors for books not yet cached.

    Returns (translated_titles, author_map_new) aligned to needs_title order.
    """
    if not needs_title:
        return [], []
    titles = [b["title"] for b in needs_title]
    authors = [b["author"] or "" for b in needs_title]
    translated_titles = translator.translate(titles, locale)
    translated_authors = (
        translator.translate([a for a in authors if a], locale) if any(authors) else []
    )
    author_iter = iter(translated_authors)
    author_map_new = [next(author_iter, a) if a else "" for a in authors]
    return translated_titles, author_map_new


def _translate_unique_series(translator, books, locale):
    """Dedupe + translate series strings. Returns {source: translation}."""
    unique_series = sorted(
        {b["series"].strip() for b in books if b.get("series") and b["series"].strip()}
    )
    if not unique_series:
        return {}, unique_series
    translated_series = translator.translate(unique_series, locale)
    return dict(zip(unique_series, translated_series)), unique_series


def _insert_fresh_translation(conn, book, locale, t_title, t_author, t_series, result_dict):
    """INSERT a fresh translation row and update result_dict."""
    pinyin = pinyin_sort_key(t_title) if locale.startswith("zh") else None
    conn.execute(
        """INSERT INTO audiobook_translations
           (audiobook_id, locale, title, author_display,
            series_display, translator, pinyin_sort)
           VALUES (?, ?, ?, ?, ?, 'deepl', ?)
           ON CONFLICT(audiobook_id, locale) DO UPDATE SET
               title = excluded.title,
               author_display = excluded.author_display,
               series_display = excluded.series_display,
               translator = excluded.translator,
               pinyin_sort = excluded.pinyin_sort,
               updated_at = CURRENT_TIMESTAMP
        """,
        (book["id"], locale, t_title, t_author, t_series, pinyin),
    )
    result_dict[str(book["id"])] = {
        "title": t_title,
        "author_display": t_author,
        "series_display": t_series,
        "description": None,
    }


def _update_series_only(conn, book, locale, t_series, result_dict):
    """Update series_display on an existing translation row."""
    conn.execute(
        "UPDATE audiobook_translations SET series_display = ?, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND locale = ?",
        (t_series, book["id"], locale),
    )
    result_dict[str(book["id"])]["series_display"] = t_series


def _apply_translations(
    conn, books, locale, translated_titles, author_map_new, series_translation, result_dict
):
    """Apply title/author/series translations to DB and result_dict."""
    title_iter = iter(translated_titles)
    author_iter2 = iter(author_map_new)
    for book in books:
        book_id_str = str(book["id"])
        src_series = (book.get("series") or "").strip()
        # Empty string (not NULL) marks "source had no series" so the
        # row no longer re-qualifies as missing on subsequent requests.
        t_series = series_translation.get(src_series, "") if src_series else ""

        if book_id_str not in result_dict:
            t_title = next(title_iter, book["title"])
            t_author = next(author_iter2, book["author"] or "")
            _insert_fresh_translation(conn, book, locale, t_title, t_author, t_series, result_dict)
        else:
            _update_series_only(conn, book, locale, t_series, result_dict)


def _do_translate_missing(conn, missing_ids, locale, result_dict):
    """Inner body for _translate_missing (exception-wrapped by caller)."""
    from localization.config import DEEPL_API_KEY

    if not DEEPL_API_KEY:
        logger.warning("On-demand translation: no DeepL API key configured")
        return

    books = _load_books_for_missing(conn, missing_ids)
    if not books:
        return

    from localization.translation.deepl_translate import DeepLTranslator

    translator = DeepLTranslator(DEEPL_API_KEY)

    needs_title = [b for b in books if str(b["id"]) not in result_dict]
    translated_titles, author_map_new = _translate_title_author_batch(
        translator, needs_title, locale
    )
    series_translation, unique_series = _translate_unique_series(translator, books, locale)
    _apply_translations(
        conn, books, locale, translated_titles, author_map_new, series_translation, result_dict
    )

    conn.commit()
    logger.info(
        "On-demand translated %d books (%d unique series) to %s via DeepL",
        len(books),
        len(unique_series),
        _sanitize_log(locale),
    )


def _translate_missing(conn, missing_ids, locale, result_dict):
    """Translate missing book metadata via DeepL and store in DB.

    Translates titles and authors per-book, and series names de-duplicated
    (many books share the same series — translating once keeps API usage
    low and output consistent across the series).

    Updates result_dict in place with newly translated entries.
    """
    try:
        _do_translate_missing(conn, missing_ids, locale, result_dict)
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
            "SELECT collection_id, name FROM collection_translations WHERE locale = ?", (locale,)
        ).fetchall()
        result: dict[str, str] = {
            r["collection_id"]: r["name"] for r in cached_rows if r["collection_id"] in id_to_name
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

        unique_names = sorted({id_to_name[cid] for cid in missing_ids if id_to_name.get(cid)})
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
            len(missing_ids),
            len(unique_names),
            _sanitize_log(locale),
        )
    except Exception:
        logger.exception("Collection translation failed")


def _normalize_strings_payload(raw_strings):
    """Normalize input: strip, dedupe, cap at 200 entries / 1000 chars each.

    Returns dict of {source_hash: source_text}.
    """
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
    return seen


def _fetch_cached_string_translations(conn, locale, seen):
    """Fetch cached translations for given hashes. Returns {hash: translation}."""
    placeholders = ",".join("?" * len(seen))
    rows = conn.execute(
        f"SELECT source_hash, translation FROM string_translations "  # nosec B608  # noqa: S608
        f"WHERE locale = ? AND source_hash IN ({placeholders})",
        (locale, *seen.keys()),
    ).fetchall()
    return {r["source_hash"]: r["translation"] for r in rows}


def _translate_and_cache_strings(conn, missing, locale, result):
    """Translate missing strings via DeepL and cache them. Updates result in place."""
    try:
        from localization.config import DEEPL_API_KEY

        if not DEEPL_API_KEY:
            logger.warning("String translation: no DeepL API key configured")
            return

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
            len(missing),
            _sanitize_log(locale),
        )
    except Exception:
        logger.exception("String translation failed")


def _validate_translate_strings_request(data):
    """Validate /strings payload. Returns (locale, raw_strings, err_or_None).

    err, when truthy, is a fully-formed Flask return value.
    """
    if not data or not data.get("locale"):
        return None, None, (jsonify({"error": "locale is required"}), 400)

    locale = data["locale"]
    if locale == "en":
        return None, None, jsonify({})

    raw_strings = data.get("strings") or []
    if not isinstance(raw_strings, list):
        return None, None, (jsonify({"error": "strings must be a list"}), 400)

    return locale, raw_strings, None


def _run_translate_strings(conn, locale, seen):
    """Fetch cached + translate missing. Returns result dict."""
    result = _fetch_cached_string_translations(conn, locale, seen)
    missing = {h: src for h, src in seen.items() if h not in result}
    if missing:
        _translate_and_cache_strings(conn, missing, locale, result)
    return result


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
    locale, raw_strings, err = _validate_translate_strings_request(request.get_json())
    if err:
        return err

    seen = _normalize_strings_payload(raw_strings)
    if not seen:
        return jsonify({})

    conn = _get_db()
    try:
        return jsonify(_run_translate_strings(conn, locale, seen))
    finally:
        conn.close()


def _parse_on_demand_ids(data):
    """Parse + validate audiobook_ids. Returns (ids, error_response_or_None)."""
    try:
        requested_ids = [int(bid) for bid in data["audiobook_ids"]]
    except ValueError, TypeError:
        return None, (jsonify({"error": "audiobook_ids must contain integers"}), 400)
    return requested_ids, None


def _load_cached_on_demand(conn, locale, requested_ids):
    """Return {id_str: {title, author_display, description}} for cached rows."""
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
    return cached


def _translate_on_demand_titles_authors(translator, books_to_translate, locale):
    """Translate titles + authors. Returns (translated_titles, author_map)."""
    titles = [b["title"] for b in books_to_translate]
    authors = [b["author"] or "" for b in books_to_translate]
    translated_titles = translator.translate(titles, locale)
    translated_authors = (
        translator.translate([a for a in authors if a], locale) if any(authors) else []
    )
    author_iter = iter(translated_authors)
    author_map = []
    for a in authors:
        if a:
            author_map.append(next(author_iter, a))
        else:
            author_map.append("")
    return translated_titles, author_map


def _persist_on_demand_translations(
    conn, books_to_translate, translated_titles, author_map, locale
):
    """Store on-demand translations in DB; return new_translations dict."""
    new_translations = {}
    for i, book in enumerate(books_to_translate):
        t_title = translated_titles[i] if i < len(translated_titles) else book["title"]
        t_author = author_map[i] if i < len(author_map) else (book["author"] or "")

        pinyin = pinyin_sort_key(t_title) if locale.startswith("zh") else None
        conn.execute(
            """INSERT INTO audiobook_translations
               (audiobook_id, locale, title, author_display, translator, pinyin_sort)
               VALUES (?, ?, ?, ?, 'deepl', ?)
               ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                   title = excluded.title,
                   author_display = excluded.author_display,
                   translator = excluded.translator,
                   pinyin_sort = excluded.pinyin_sort,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (book["id"], locale, t_title, t_author, pinyin),
        )
        new_translations[str(book["id"])] = {
            "title": t_title,
            "author_display": t_author,
            "description": None,
        }
    return new_translations


def _validate_on_demand_request(data):
    """Validate /on-demand payload. Returns (locale, requested_ids, err_or_None).

    err, when truthy, is a fully-formed Flask return value
    (either a plain response or a (response, status) tuple).
    """
    if not data or not data.get("locale") or not data.get("audiobook_ids"):
        return (None, None, (jsonify({"error": "locale and audiobook_ids are required"}), 400))

    locale = data["locale"]
    if locale == "en":
        return None, None, jsonify({})

    requested_ids, err = _parse_on_demand_ids(data)
    if err:
        return None, None, err
    if not requested_ids:
        return None, None, jsonify({})

    return locale, requested_ids, None


def _do_on_demand_translation(conn, locale, missing_ids, cached):
    """Perform DeepL translation for missing ids and merge into cached.

    Returns True if any translation happened (for logging alignment);
    callers treat None / no-op equivalently via the cached response.
    """
    from localization.config import DEEPL_API_KEY

    if not DEEPL_API_KEY:
        logger.warning("On-demand translation requested but no DeepL API key configured")
        return

    all_books = conn.execute("SELECT id, title, author FROM audiobooks").fetchall()
    books_to_translate = [dict(r) for r in all_books if r["id"] in missing_ids]

    if not books_to_translate:
        return

    from localization.translation.deepl_translate import DeepLTranslator

    translator = DeepLTranslator(DEEPL_API_KEY)
    translated_titles, author_map = _translate_on_demand_titles_authors(
        translator, books_to_translate, locale
    )
    new_translations = _persist_on_demand_translations(
        conn, books_to_translate, translated_titles, author_map, locale
    )

    conn.commit()
    logger.info(
        "On-demand translated %d books to %s via DeepL",
        len(books_to_translate),
        _sanitize_log(locale),
    )
    cached.update(new_translations)


def _run_on_demand(locale, requested_ids):
    """Execute on-demand translation lifecycle. Always returns a response."""
    conn = _get_db()
    cached: dict = {}
    try:
        cached = _load_cached_on_demand(conn, locale, requested_ids)
        cached_ids = {int(k) for k in cached}
        missing_ids = [bid for bid in requested_ids if bid not in cached_ids]
        if missing_ids:
            _do_on_demand_translation(conn, locale, missing_ids, cached)
        return jsonify(cached)
    except Exception:
        logger.exception("On-demand translation failed")
        return jsonify(cached)
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
    locale, requested_ids, err = _validate_on_demand_request(request.get_json())
    if err:
        return err

    # Cap per request to prevent abuse (a library page shows ~50 books max)
    return _run_on_demand(locale, requested_ids[:60])


def _validate_batch_request(data):
    """Validate /batch payload. Returns (locale, requested_ids|None, err_response|None).

    requested_ids is a set of ints, or None for "all".
    """
    if not data or not data.get("locale"):
        return None, None, (jsonify({"error": "locale is required"}), 400)

    locale = data["locale"]
    provider = data.get("provider", "deepl")
    book_ids = data.get("audiobook_ids")

    if provider != "deepl":
        return (None, None, (jsonify({"error": "Only 'deepl' provider is supported"}), 400))

    if isinstance(book_ids, list):
        try:
            requested_ids = {int(bid) for bid in book_ids}
        except ValueError, TypeError:
            return (None, None, (jsonify({"error": "audiobook_ids must contain integers"}), 400))
        if not requested_ids:
            return (None, None, (jsonify({"error": "audiobook_ids must not be empty"}), 400))
        return locale, requested_ids, None
    if book_ids != "all":
        return (None, None, (jsonify({"error": "audiobook_ids must be a list or 'all'"}), 400))
    return locale, None, None


def _load_batch_books(conn, requested_ids):
    """Load candidate book rows; optionally filter by requested_ids."""
    all_rows = conn.execute(
        "SELECT id, title, author, series, description, publisher_summary FROM audiobooks"
    ).fetchall()
    if requested_ids is not None:
        return [dict(r) for r in all_rows if r["id"] in requested_ids]
    return [dict(r) for r in all_rows]


def _find_existing_translations(conn, locale, books):
    """Return set of book ids already translated for this locale."""
    if not books:
        return set()
    all_translations = conn.execute(
        "SELECT audiobook_id FROM audiobook_translations WHERE locale = ?", (locale,)
    ).fetchall()
    all_translated_ids = {r["audiobook_id"] for r in all_translations}
    book_ids_set = {b["id"] for b in books}
    return all_translated_ids & book_ids_set


def _translate_batch_field_with_map(translator, values, locale):
    """Translate non-empty strings in `values`, produce a map aligned to values.

    Empty / whitespace entries become "" in the output.
    """
    non_empty = [v for v in values if v.strip()]
    translated = translator.translate(non_empty, locale) if non_empty else []
    t_iter = iter(translated)
    return [next(t_iter, v) if v.strip() else "" for v in values]


def _translate_batch_descriptions(translator, descriptions, locale):
    """Translate descriptions in sub-batches of 10. Returns list aligned to input."""
    desc_map = [""] * len(descriptions)
    desc_indices = [(j, d) for j, d in enumerate(descriptions) if d.strip()]
    for di in range(0, len(desc_indices), 10):
        sub = desc_indices[di : di + 10]
        t_descs = translator.translate([d for _, d in sub], locale)
        for k, (orig_idx, _) in enumerate(sub):
            if k < len(t_descs):
                desc_map[orig_idx] = t_descs[k]
    return desc_map


def _persist_batch_translations(
    conn, needs_translation, translated_titles, author_map, series_map, desc_map, locale
):
    """Insert/update rows in DB; return translations dict keyed by id string."""
    translations = {}
    for i, book in enumerate(needs_translation):
        t_title = translated_titles[i] if i < len(translated_titles) else book["title"]
        t_author = author_map[i] if i < len(author_map) else (book["author"] or "")
        t_series = series_map[i] if i < len(series_map) else ""
        t_desc = desc_map[i]

        pinyin = pinyin_sort_key(t_title) if locale.startswith("zh") else None
        conn.execute(
            """INSERT INTO audiobook_translations
               (audiobook_id, locale, title, author_display, series_display,
                description, translator, pinyin_sort)
               VALUES (?, ?, ?, ?, ?, ?, 'deepl', ?)
               ON CONFLICT(audiobook_id, locale) DO UPDATE SET
                   title = excluded.title,
                   author_display = excluded.author_display,
                   series_display = excluded.series_display,
                   description = excluded.description,
                   translator = excluded.translator,
                   pinyin_sort = excluded.pinyin_sort,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (book["id"], locale, t_title, t_author, t_series, t_desc, pinyin),
        )
        translations[str(book["id"])] = {
            "title": t_title,
            "author_display": t_author,
            "series_display": t_series,
            "description": t_desc,
        }
    return translations


def _translate_batch_all_fields(translator, needs_translation, locale):
    """Translate titles/authors/series/descriptions for a batch.

    Returns (translated_titles, author_map, series_map, desc_map) aligned
    to needs_translation.
    """
    titles = [b["title"] for b in needs_translation]
    authors = [b["author"] or "" for b in needs_translation]
    series_list = [b["series"] or "" for b in needs_translation]
    descriptions = [b["description"] or b["publisher_summary"] or "" for b in needs_translation]

    translated_titles = translator.translate(titles, locale)
    author_map = _translate_batch_field_with_map(translator, authors, locale)
    series_map = _translate_batch_field_with_map(translator, series_list, locale)
    desc_map = _translate_batch_descriptions(translator, descriptions, locale)
    return translated_titles, author_map, series_map, desc_map


def _run_batch_translation(conn, locale, needs_translation):
    """Run DeepL translations + persist. Returns (translations, err_or_None)."""
    from localization.config import DEEPL_API_KEY

    if not DEEPL_API_KEY:
        return None, (jsonify({"error": "DeepL API key not configured"}), 503)

    from localization.translation.deepl_translate import DeepLTranslator

    translator = DeepLTranslator(DEEPL_API_KEY, db_path=str(_db_path))
    translated_titles, author_map, series_map, desc_map = _translate_batch_all_fields(
        translator, needs_translation, locale
    )
    translations = _persist_batch_translations(
        conn, needs_translation, translated_titles, author_map, series_map, desc_map, locale
    )
    conn.commit()
    logger.info("Batch translated %d books to %s", len(needs_translation), _sanitize_log(locale))
    return translations, None


def _batch_nothing_to_do_response(books, existing):
    """Build the response when no books need translation."""
    return jsonify(
        {
            "total_books": len(books),
            "translated": len(existing),
            "needs_translation": 0,
            "translations": {},
        }
    )


def _batch_execute(conn, locale, requested_ids):
    """Full batch pipeline under an open DB connection."""
    books = _load_batch_books(conn, requested_ids)
    existing = _find_existing_translations(conn, locale, books)
    needs_translation = [b for b in books if b["id"] not in existing]

    if not needs_translation:
        return _batch_nothing_to_do_response(books, existing)

    translations, tr_err = _run_batch_translation(conn, locale, needs_translation)
    if tr_err:
        return tr_err

    return jsonify(
        {
            "total_books": len(books),
            "already_translated": len(existing),
            "newly_translated": len(needs_translation),
            "translations": translations,
        }
    )


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
    locale, requested_ids, err = _validate_batch_request(request.get_json())
    if err:
        return err

    conn = _get_db()
    try:
        return _batch_execute(conn, locale, requested_ids)
    finally:
        conn.close()


@translations_bp.route("/api/admin/localization/quota", methods=["GET"])
@admin_required
def admin_localization_quota():
    """Return DeepL quota + glossary status for the backoffice.

    Admin-only. The backoffice utilities page will eventually surface
    this — until then, admins can read it directly via:
        curl -b session.cookie https://host/api/admin/localization/quota

    Response shape:
        {
          "used": int,        # characters billed this period
          "limit": int,       # character cap (DeepL free tier = 500000)
          "percent": float,   # used / limit * 100
          "remaining": int,
          "reset_date": str,  # YYYY-MM-DD of next monthly reset
          "glossary_id": str | null,
          "note": str
        }
    """
    if _db_path is None:
        return jsonify({"error": "quota unavailable (db not initialized)"}), 500
    try:
        from localization.translation.quota import QuotaTracker

        tracker = QuotaTracker(db_path=_db_path)
        snap = tracker.snapshot()
    except Exception:
        logger.exception("Failed to read DeepL quota snapshot")
        return jsonify({"error": "quota unavailable"}), 500

    snap["note"] = (
        "DeepL quota + glossary status. Hard limit at 99% triggers "
        "pass-through English fallback. Refresh glossary by restarting "
        "the backend or editing library/localization/glossary/en-zh.yaml."
    )
    return jsonify(snap)
