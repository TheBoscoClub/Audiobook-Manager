"""Flask-free locale-tag validation shared by the API and the translation pipeline.

A locale reaches filesystem paths, SQL keys, log lines, the translation
memory and the vLLM system prompt. Request handlers (``backend.api_modular``)
and the sinks behind them (``localization``) all use this one definition.
"""

import re

# Two-letter primary language plus an optional single subtag (zh-Hans, pt-BR)
SAFE_LOCALE_RE = re.compile(r"^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$")


def is_safe_locale(locale: object) -> bool:
    """True when ``locale`` is a string that fully matches the allowlist."""
    return isinstance(locale, str) and SAFE_LOCALE_RE.fullmatch(locale) is not None


def sanitize_locale(locale: str) -> str:
    """Return ``locale`` unchanged or raise ``ValueError``.

    Uses ``fullmatch`` rather than ``match``: a ``$``-anchored ``match``
    accepts a single trailing newline (``"en\\n"``), which would let a
    CR/LF-bearing value past validation.
    """
    if not is_safe_locale(locale):
        raise ValueError(f"invalid locale: {locale!r}")
    return locale  # type: ignore[return-value]
