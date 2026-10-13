"""Shared locale-tag validation for request-facing code.

A locale reaches filesystem paths, SQL keys, log lines, the translation
memory and the vLLM system prompt. Every route taking a locale from a
client MUST pass it through :func:`sanitize_locale` (or
:func:`locale_error_response`) before any DB or translation work.
"""

import re

from flask import jsonify

# Two-letter primary language plus an optional single subtag (zh-Hans, pt-BR)
SAFE_LOCALE_RE = re.compile(r"^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$")


def sanitize_locale(locale: str) -> str:
    """Return ``locale`` unchanged or raise ``ValueError``.

    Uses ``fullmatch`` rather than ``match``: a ``$``-anchored ``match``
    accepts a single trailing newline (``"en\\n"``), which would let a
    CR/LF-bearing value past validation.
    """
    if not isinstance(locale, str) or not SAFE_LOCALE_RE.fullmatch(locale):
        raise ValueError(f"invalid locale: {locale!r}")
    return locale


def locale_error_response(locale):
    """Return a ``(response, 400)`` tuple for an invalid locale, else ``None``."""
    try:
        sanitize_locale(locale)
    except ValueError:
        return jsonify({"error": "Invalid locale"}), 400
    return None
