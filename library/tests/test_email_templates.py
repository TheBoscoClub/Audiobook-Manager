"""
Tests for ``backend.api_modular.email_templates``.

These tests verify the localized email rendering infrastructure that backs
the guest-facing email flows (magic link, approval, denial, reply,
invitation, activation).

Scenarios covered:

1. Every template renders in both ``en`` and ``zh-Hans`` without raising.
2. User-supplied ``username`` is HTML-escaped in the HTML body so
   ``<script>`` payloads cannot survive rendering.
3. Missing-key fallback to English works when a Chinese slot is absent.
4. Unknown locale (``de``) falls back to English.

Pure unit tests — no SQLCipher, no SMTP, no Flask app required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ``backend.api_modular.__init__`` imports ``i18n_routes`` which does an
# absolute ``from i18n import ...``. The root conftest only adds
# ``library/`` to sys.path; we also need ``library/backend/`` so that the
# sibling ``i18n`` package resolves when this test module is collected
# standalone (without the heavier ``auth_app`` fixture pulling it in).
_LIBRARY_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_LIBRARY_BACKEND) not in sys.path:
    sys.path.insert(0, str(_LIBRARY_BACKEND))

from backend.api_modular import email_templates  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures providing realistic variables for each template
# ---------------------------------------------------------------------------


_TEMPLATE_VARS = {
    "magic_link": {
        "username": "alice",
        "link": "https://library.example.com/verify.html?token=abc123",
        "expires_minutes": 15,
    },
    "approval": {
        "username": "alice",
        "claim_url": "https://library.example.com/claim.html",
    },
    "denial": {
        "username": "alice",
        "reason": "Username already in use.",
    },
    "reply": {
        "username": "alice",
        "reply_text": "Thanks for your message!\nRegards, The Library.",
    },
    "invitation": {
        "username": "alice",
        "claim_url": "https://library.example.com/claim.html",
        "claim_token": "TOKEN-1234-5678",
        "expires_hours": 24,
    },
    "activation": {
        "username": "alice",
        "activation_url": "https://library.example.com/verify.html?token=xyz",
        "expires_hours": 24,
    },
}


# ---------------------------------------------------------------------------
# 1. Every template renders in both locales without raising
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_VARS))
@pytest.mark.parametrize("locale", ["en", "zh-Hans"])
def test_template_renders_in_locale(template_name: str, locale: str) -> None:
    """Each supported template must render a full (subject, text, html)."""
    subject, text, html_body = email_templates.render_email(
        template_name, locale, **_TEMPLATE_VARS[template_name]
    )

    assert subject and isinstance(subject, str)
    assert text and isinstance(text, str)
    assert html_body and isinstance(html_body, str)

    # Raw placeholder tokens must never survive rendering.
    for placeholder in _TEMPLATE_VARS[template_name]:
        assert "{" + placeholder + "}" not in subject
        assert "{" + placeholder + "}" not in text
        assert "{" + placeholder + "}" not in html_body

    # Missing-key sentinel (the dot-notation key itself) must not appear.
    assert "email." + template_name not in html_body


def test_chinese_copy_contains_chinese_characters() -> None:
    """Sanity check: zh-Hans renders should contain CJK characters."""
    _subject, _text, html_body = email_templates.render_email(
        "magic_link", "zh-Hans", **_TEMPLATE_VARS["magic_link"]
    )
    # Look for at least one Han character — guards against accidentally
    # falling back to English for every key.
    assert any("\u4e00" <= ch <= "\u9fff" for ch in html_body)


# ---------------------------------------------------------------------------
# 2. HTML escaping prevents <script> injection in user-supplied values
# ---------------------------------------------------------------------------


def test_username_is_html_escaped_in_html_body() -> None:
    """A username with HTML must never render as live markup."""
    payload = "<script>alert('xss')</script>"
    variables = dict(_TEMPLATE_VARS["magic_link"], username=payload)

    _subject, text, html_body = email_templates.render_email(
        "magic_link", "en", **variables
    )

    # Plain-text body keeps the raw value — that's by design (text/plain
    # renders it literally, no injection risk).
    assert payload in text

    # HTML body must contain the escaped form and must NOT contain the
    # raw tag.
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_reply_text_is_html_escaped() -> None:
    """Reply bodies often contain user prose — must also be escaped."""
    payload = "<img src=x onerror=alert(1)>"
    variables = dict(_TEMPLATE_VARS["reply"], reply_text=payload)

    _subject, _text, html_body = email_templates.render_email(
        "reply", "en", **variables
    )
    assert "<img src=x" not in html_body
    assert "&lt;img src=x" in html_body


# ---------------------------------------------------------------------------
# 3. Missing-key fallback to English
# ---------------------------------------------------------------------------


def test_missing_zh_key_falls_back_to_english() -> None:
    """If a zh-Hans slot is missing the renderer must pull from English."""
    removed = email_templates._CATALOGS["zh-Hans"].pop("email.magic_link.intro")
    try:
        _subject, _text, html_body = email_templates.render_email(
            "magic_link", "zh-Hans", **_TEMPLATE_VARS["magic_link"]
        )
    finally:
        email_templates._CATALOGS["zh-Hans"]["email.magic_link.intro"] = removed

    # The English intro text must appear in the otherwise-Chinese body.
    english_intro_fragment = "Click the big gold button"
    assert english_intro_fragment in html_body


# ---------------------------------------------------------------------------
# 4. Unknown locale falls back to English
# ---------------------------------------------------------------------------


def test_unknown_locale_falls_back_to_english() -> None:
    """``render_email`` must treat unknown locales as English."""
    subject, _text, html_body = email_templates.render_email(
        "approval", "de", **_TEMPLATE_VARS["approval"]
    )
    assert subject == "You're Approved! Welcome to The Library"
    assert "Welcome to The Library!" in html_body


def test_unknown_template_raises() -> None:
    """Typos in template names must surface as ValueError, not KeyError."""
    with pytest.raises(ValueError, match="Unknown email template"):
        email_templates.render_email("nope", "en", username="alice")
