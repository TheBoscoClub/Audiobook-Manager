"""
i18n API routes — serve locale catalogs and manage locale preferences.

GET /api/i18n/<locale>       → full JSON catalog for the locale
GET /api/i18n/supported      → list of supported locales
"""

from flask import Blueprint, jsonify

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
