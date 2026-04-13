"""
Internationalization (i18n) module for the Audiobook Library API.

Provides server-side translation via shared JSON catalogs (library/locales/*.json).
The same JSON files are served to the frontend via /api/i18n/<locale>.

Usage:
    from backend.i18n import t, get_locale, supported_locales

    # In a Flask route:
    label = t("shell.account", locale=get_locale())
"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from flask import request

# Resolve locales directory relative to this file
_LOCALES_DIR = Path(__file__).parent.parent / "locales"

# Defaults — overridable via environment
DEFAULT_LOCALE = os.environ.get("AUDIOBOOKS_DEFAULT_LOCALE", "en")
SUPPORTED_LOCALES = set(
    os.environ.get("AUDIOBOOKS_SUPPORTED_LOCALES", "en,zh-Hans").split(",")
)


@lru_cache(maxsize=16)
def _load_catalog(locale: str) -> dict:
    """Load and cache a locale's JSON catalog."""
    # Prevent path traversal — locale must be alphanumeric with optional hyphens
    if not re.match(r"^[a-zA-Z0-9-]+$", locale):
        return {}
    catalog_path = _LOCALES_DIR / f"{locale}.json"
    if not catalog_path.exists():
        return {}
    with open(catalog_path, encoding="utf-8") as f:
        return json.load(f)


def reload_catalogs():
    """Clear the catalog cache (call after updating JSON files)."""
    _load_catalog.cache_clear()


def get_catalog(locale: str) -> dict:
    """Get the full catalog for a locale, falling back to default."""
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    catalog = _load_catalog(locale)
    if locale != DEFAULT_LOCALE:
        # Merge with default so missing keys fall back to English
        default = _load_catalog(DEFAULT_LOCALE)
        merged = {**default, **catalog}
        return merged
    return catalog


def t(key: str, locale: Optional[str] = None) -> str:
    """
    Translate a key to the given locale.

    Falls back to default locale, then returns the key itself
    if no translation exists.
    """
    if locale is None:
        locale = get_locale()
    catalog = get_catalog(locale)
    return catalog.get(key, key)


def get_locale() -> str:
    """
    Detect the current request's locale.

    Priority:
    1. ?locale= query parameter
    2. X-Locale header (set by frontend i18n.js)
    3. User's saved preference (from session/cookie)
    4. Accept-Language header
    5. Default locale
    """
    # 1. Query param
    locale = request.args.get("locale")
    if locale and locale in SUPPORTED_LOCALES:
        return locale

    # 2. X-Locale header
    locale = request.headers.get("X-Locale")
    if locale and locale in SUPPORTED_LOCALES:
        return locale

    # 3. Accept-Language header (parse first match)
    accept = request.headers.get("Accept-Language", "")
    for part in accept.split(","):
        tag = part.split(";")[0].strip()
        if tag in SUPPORTED_LOCALES:
            return tag
        # Try language-only (e.g., "zh" → "zh-Hans")
        lang = tag.split("-")[0]
        for supported in SUPPORTED_LOCALES:
            if supported.startswith(lang):
                return supported

    return DEFAULT_LOCALE
