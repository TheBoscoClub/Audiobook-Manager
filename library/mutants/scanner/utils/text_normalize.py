"""Free-text normalization for fields ingested from external sources.

Audible API responses and Opus/M4B comment tags sometimes carry HTML-encoded
entities (``&quot;``, ``&nbsp;``, ``&amp;``) baked into otherwise plain-text
fields. When those entity-encoded strings hit the web UI's HTML-attribute
rendering path, the entities get HTML-decoded twice — once by the renderer
that wrapped the data, once by the browser parsing the attribute — and the
result is broken JS (see prod incident 2026-05-12, audiobook id 116208).

The single canonical entry point is ``normalize_freetext``. It is a thin,
defensive wrapper over :func:`html.unescape` with two guarantees:

1. ``None`` / non-string inputs round-trip unchanged (so callers don't have
   to special-case missing optional fields).
2. The result is idempotent — running ``normalize_freetext`` on an already
   normalized string returns the same string.
"""

from __future__ import annotations

import html
from typing import Any


def normalize_freetext(value: Any) -> Any:
    """Return ``value`` with HTML entities decoded if it is a string.

    Non-string inputs (``None``, ints, dicts, …) are returned as-is so the
    helper composes safely with optional-field dict accessors.
    """
    if not isinstance(value, str):
        return value
    return html.unescape(value)
