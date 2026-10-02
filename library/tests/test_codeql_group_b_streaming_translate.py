"""CodeQL triage, group B — streaming_translate.py path/log-injection sinks.

Every flagged sink in ``backend/api_modular/streaming_translate.py`` sits
behind two layers: ``_sanitize_locale`` (allowlist regex at the request
boundary) and either ``_safe_log_value`` (log sinks) or a
``resolve()``/``is_relative_to`` containment check (path sinks).

Triage found one real hole in the first layer: the locale regex was applied
with ``re.match`` and anchored on ``$``, which in Python also matches just
before a single trailing newline — so ``"zh-Hans\\n"`` passed validation.
The second layer still neutralised it, but these tests pin both layers
independently so neither can silently regress into the other.
"""

from __future__ import annotations

import pytest
from backend.api_modular.streaming_translate import (
    _safe_log_value,
    _safe_subtitles_path,
    _sanitize_locale,
)

# Values that the ``$``-anchored ``match`` used to accept.
_TRAILING_NEWLINE_LOCALES = ["en\n", "zh-Hans\n", "pt-BR\n"]


class TestSanitizeLocaleTrailingNewline:
    """First layer: the request-boundary allowlist must reject a trailing LF."""

    @pytest.mark.parametrize("locale", _TRAILING_NEWLINE_LOCALES)
    def test_trailing_newline_rejected(self, locale):
        with pytest.raises(ValueError):
            _sanitize_locale(locale)

    @pytest.mark.parametrize("locale", _TRAILING_NEWLINE_LOCALES)
    def test_subtitles_path_rejects_trailing_newline(self, tmp_path, locale):
        """Path sink (ch<NNN>.<locale>.vtt) must not get a newline into a filename."""
        with pytest.raises(ValueError):
            _safe_subtitles_path(tmp_path, 1, 0, locale)

    @pytest.mark.parametrize("endpoint", ["/api/translate/seek", "/api/translate/stream"])
    def test_endpoint_refuses_trailing_newline_locale(self, app_client, endpoint):
        """The HTTP boundary returns 400 before any DB or filesystem work."""
        resp = app_client.post(endpoint, json={"audiobook_id": 1, "locale": "zh-Hans\n"})
        assert resp.status_code == 400
        assert resp.get_json() == {"error": "invalid parameters"}


class TestSafeLogValueSink:
    """Second layer: the log sink must emit exactly one line whatever it is fed."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "zh-Hans\n",
            "zh-Hans\r\nINFO forged line",
            "en\rzh",
            "a b",  # LINE SEPARATOR — not matched by the control-char scrub
            "a\x85b",  # NEL — likewise
            "a\x00\x1b[31mb",
        ],
    )
    def test_output_is_single_line_without_control_bytes(self, hostile):
        out = _safe_log_value(hostile)
        assert len(("locale=" + out).splitlines()) == 1
        assert not any(ch in out for ch in "\r\n \x85")
        assert all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in out)
