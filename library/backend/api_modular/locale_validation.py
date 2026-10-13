"""Locale validation for request-facing code.

Every route taking a locale from a client MUST pass it through
:func:`locale_error_response` before any DB or translation work. Routes that
can ENQUEUE or TRIGGER translation work use :func:`enqueue_locale_error`,
which additionally requires membership in ``i18n.SUPPORTED_LOCALES`` so an
anonymous caller cannot queue work for arbitrary well-formed junk locales.
The pattern itself lives in ``common_utils.locale_safety`` (shared with the
queue worker and the vLLM prompt builder).
"""

import i18n
from common_utils.locale_safety import SAFE_LOCALE_RE, sanitize_locale
from flask import jsonify

__all__ = [
    "SAFE_LOCALE_RE",
    "enqueue_locale_error",
    "locale_error_response",
    "sanitize_locale",
]


def locale_error_response(locale):
    """Return a ``(response, 400)`` tuple for a malformed locale, else ``None``."""
    try:
        sanitize_locale(locale)
    except ValueError:
        return jsonify({"error": "Invalid locale"}), 400
    return None


def enqueue_locale_error(locale):
    """Like :func:`locale_error_response`, plus an ``i18n.SUPPORTED_LOCALES`` check.

    ``"en"`` is always accepted (callers short-circuit it). Use on every route
    that can enqueue or trigger translation work.
    """
    bad = locale_error_response(locale)
    if bad is not None:
        return bad
    if locale != "en" and locale not in i18n.SUPPORTED_LOCALES:
        return jsonify({"error": "Unsupported locale"}), 400
    return None
