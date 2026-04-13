"""
i18n API routes — serve locale catalogs and manage locale preferences.

GET  /api/i18n/<locale>       → full JSON catalog for the locale
GET  /api/i18n/supported      → list of supported locales
POST /api/i18n/activate        → notify backend of locale change (triggers translation queue)
GET  /api/translation/queue    → queue status summary
GET  /api/translation/status/<id>/<locale> → per-book translation status
"""

from flask import Blueprint, jsonify, request

from i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, get_catalog, reload_catalogs

i18n_bp = Blueprint("i18n", __name__)


@i18n_bp.route("/api/i18n/supported")
def get_supported_locales():
    """Return supported locales and the default."""
    return jsonify(
        {
            "default": DEFAULT_LOCALE,
            "supported": sorted(SUPPORTED_LOCALES),
        }
    )


@i18n_bp.route("/api/i18n/<locale>")
def get_locale_catalog(locale: str):
    """Return the full translation catalog for a locale."""
    if locale not in SUPPORTED_LOCALES:
        return jsonify({"error": f"Unsupported locale: {locale}"}), 404
    catalog = get_catalog(locale)
    response = jsonify(catalog)
    # Cache aggressively — catalogs change only on deploy
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@i18n_bp.route("/api/i18n/reload", methods=["POST"])
def reload_locale_catalogs():
    """Admin endpoint to reload catalogs without restart."""
    reload_catalogs()
    return jsonify({"status": "ok"})


@i18n_bp.route("/api/i18n/activate", methods=["POST"])
def activate_locale():
    """Notify backend that a user switched locale.

    Queues all books missing translations for the new locale. The
    frontend calls this on locale change so the translation queue
    starts processing before the user opens a book.
    """
    data = request.get_json(silent=True) or {}
    locale = data.get("locale", "")
    if not locale or locale == "en":
        return jsonify({"status": "ok", "queued": 0})

    try:
        from localization.queue import enqueue_all_books_for_locale, get_queue_status
        enqueue_all_books_for_locale(locale)
        status = get_queue_status()
        return jsonify({"status": "ok", "queued": status.get("pending", 0)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@i18n_bp.route("/api/translation/queue")
def translation_queue_status():
    """Return translation queue summary."""
    try:
        from localization.queue import get_queue_status
        return jsonify(get_queue_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@i18n_bp.route("/api/translation/bump", methods=["POST"])
def translation_bump_priority():
    """Bump a book's translation priority (e.g., user just opened it).

    If the book isn't queued yet, enqueue it at high priority.
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "")
    if not audiobook_id or not locale or locale == "en":
        return jsonify({"status": "ok"})

    try:
        from localization.queue import bump_priority, enqueue
        bump_priority(audiobook_id, locale, priority=100)
        enqueue(audiobook_id, locale, priority=100, start_worker=True)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@i18n_bp.route("/api/translation/status/<int:book_id>/<locale>")
def translation_book_status(book_id, locale):
    """Return translation status for a specific book+locale."""
    try:
        from localization.queue import get_book_translation_status
        status = get_book_translation_status(book_id, locale)
        if not status:
            return jsonify({"state": "not_queued"})
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
