"""
Localized email template rendering for guest-facing messages.

This module loads the project locale catalogs (``library/locales/*.json``)
at import time and exposes :func:`render_email`, which assembles the
``(subject, text_body, html_body)`` triple for one of the named templates
defined below.

Design
------

The legacy :mod:`backend.api_modular.auth` module embedded each email body
as a large Python f-string mixing HTML structure, inline CSS, and
English-only copy. That made translation impractical. This module keeps
the HTML *structure* in Python (so locale catalogs stay free of markup)
and pulls every localizable string from the catalog using the existing
dot-notation key convention (``email.<template>.<slot>``).

Each template therefore owns a small, fixed set of catalog keys. Missing
keys fall back to the English (``en``) catalog — English is the source of
truth and must always be complete. Unknown locales also fall back to
English.

Variable substitution uses :meth:`str.format_map` with an HTML-escaping
mapping for the HTML body, and a plain mapping for the text body and the
subject. User-supplied values (usernames, reply text, denial reasons) are
therefore always escaped before they reach the HTML output.

Supported templates and their placeholders
------------------------------------------

``magic_link``
    ``username``, ``link``, ``expires_minutes``

``approval``
    ``username``, ``claim_url``

``denial``
    ``username``, ``reason``

``reply``
    ``username``, ``reply_text``

``invitation``
    ``username``, ``claim_url``, ``claim_token``, ``expires_hours``

``activation``
    ``username``, ``activation_url``, ``expires_hours``

Placeholders are referenced from the catalog strings via ``{name}``
tokens, e.g. ``"Hello {username},"``. Surrounding HTML/CSS lives in the
Python templates below, not in the catalogs.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

# Locale catalogs live at <repo>/library/locales/*.json.
# This file is library/backend/api_modular/email_templates.py, so walk up
# two parents to reach library/.
_LIBRARY_DIR = Path(__file__).resolve().parents[2]
_LOCALES_DIR = _LIBRARY_DIR / "locales"

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "zh-Hans")


def _load_catalogs() -> Dict[str, Dict[str, str]]:
    """Read every supported locale JSON file once at import time."""
    catalogs: Dict[str, Dict[str, str]] = {}
    for locale in SUPPORTED_LOCALES:
        path = _LOCALES_DIR / f"{locale}.json"
        try:
            with path.open("r", encoding="utf-8") as fh:
                catalogs[locale] = json.load(fh)
        except FileNotFoundError:
            logger.warning("Locale catalog missing: %s", path)
            catalogs[locale] = {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load locale catalog %s: %s", path, exc)
            catalogs[locale] = {}
    return catalogs


_CATALOGS: Dict[str, Dict[str, str]] = _load_catalogs()


def _t(key: str, locale: str) -> str:
    """Look up ``key`` in ``locale`` with fallback to English.

    Returns the key itself if the key is missing from both the requested
    locale and the English catalog — that way callers see the missing key
    in the rendered output instead of raising.
    """
    if locale not in _CATALOGS:
        locale = DEFAULT_LOCALE
    value = _CATALOGS.get(locale, {}).get(key)
    if value is None:
        value = _CATALOGS.get(DEFAULT_LOCALE, {}).get(key)
    if value is None:
        logger.warning("Missing email locale key: %s", key)
        return key
    return value


class _EscapedMapping(dict):
    """Mapping wrapper that HTML-escapes every value on lookup.

    Used as the argument to :meth:`str.format_map` when rendering HTML
    bodies so that user-supplied values cannot inject markup.
    """

    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive
        return "{" + key + "}"

    def __getitem__(self, key: str) -> str:
        value = super().__getitem__(key)
        return html.escape(str(value), quote=True)


def _format_plain(template: str, variables: Dict[str, Any]) -> str:
    """Format a plain-text (or subject) string without HTML escaping."""
    try:
        return template.format_map(variables)
    except KeyError as exc:
        logger.warning("Missing email placeholder %s in template", exc)
        return template


def _format_html(template: str, variables: Dict[str, Any]) -> str:
    """Format an HTML string with user-supplied values HTML-escaped."""
    try:
        return template.format_map(_EscapedMapping(variables))
    except KeyError as exc:
        logger.warning("Missing HTML email placeholder %s", exc)
        return template


# ---------------------------------------------------------------------------
# HTML wrappers
# ---------------------------------------------------------------------------
#
# Each template has a small HTML scaffold that references catalog-provided
# text via named slots: ``{heading}``, ``{greeting}``, ``{body}`` etc.
# The scaffold is intentionally plain — the goal is to get body copy into
# the catalog, not to preserve the legacy inline-styled layouts.

_BASE_HTML = (
    "<!DOCTYPE html>\n"
    "<html>\n"
    "<head><meta charset=\"UTF-8\"></head>\n"
    "<body style=\"font-family: Georgia, serif; background-color: #1a1a1a;"
    " color: #f5f5dc; padding: 20px;\">\n"
    "  <div style=\"max-width: 600px; margin: 0 auto;"
    " background-color: #2a2a2a; padding: 30px; border: 1px solid #8b7355;\">\n"
    "    <h1 style=\"color: #daa520; text-align: center;"
    " margin-bottom: 20px;\">{heading}</h1>\n"
    "{inner}"
    "  </div>\n"
    "</body>\n"
    "</html>\n"
)


def _wrap_html(heading: str, inner: str) -> str:
    return _BASE_HTML.format(heading=heading, inner=inner)


# ---------------------------------------------------------------------------
# Per-template renderers
# ---------------------------------------------------------------------------
#
# Each renderer pulls its slots from the catalog via ``_t``, formats them
# with the caller-supplied variables, and returns a tuple suitable for
# attaching to an email message.


def _render_magic_link(locale: str, v: Dict[str, Any]) -> Tuple[str, str, str]:
    subject = _format_plain(_t("email.magic_link.subject", locale), v)
    text = _format_plain(_t("email.magic_link.text", locale), v)

    greeting = _format_html(_t("email.common.greeting", locale), v)
    intro = _format_html(_t("email.magic_link.intro", locale), v)
    button_label = _format_html(_t("email.magic_link.button", locale), v)
    expiry_note = _format_html(_t("email.magic_link.expiry", locale), v)
    fallback = _format_html(_t("email.magic_link.fallback", locale), v)

    safe_link = html.escape(str(v.get("link", "")), quote=True)
    button_html = (
        f"<div style=\"text-align: center; margin: 30px 0;\">"
        f"<a href=\"{safe_link}\""
        f" style=\"background: linear-gradient(to bottom, #ffd700, #daa520, #8b7355);"
        f" color: #1a1a1a; padding: 18px 40px; text-decoration: none;"
        f" font-weight: bold; font-size: 1.1em; letter-spacing: 2px;\">"
        f"{button_label}</a></div>"
    )

    inner = (
        f"    <p style=\"line-height: 1.8;\">{greeting}</p>\n"
        f"    <p style=\"line-height: 1.8;\">{intro}</p>\n"
        f"    {button_html}\n"
        f"    <p style=\"line-height: 1.8;\">{expiry_note}</p>\n"
        f"    <p style=\"color: #888; font-size: 0.9em; text-align: center;\">"
        f"{fallback}<br>"
        f"<a href=\"{safe_link}\" style=\"color: #daa520; word-break: break-all;\">"
        f"{safe_link}</a></p>\n"
    )
    heading = _format_html(_t("email.common.heading", locale), v)
    return subject, text, _wrap_html(heading, inner)


def _render_approval(locale: str, v: Dict[str, Any]) -> Tuple[str, str, str]:
    subject = _format_plain(_t("email.approval.subject", locale), v)
    text = _format_plain(_t("email.approval.text", locale), v)

    greeting = _format_html(_t("email.common.greeting", locale), v)
    intro = _format_html(_t("email.approval.intro", locale), v)
    instructions = _format_html(_t("email.approval.instructions", locale), v)
    button_label = _format_html(_t("email.approval.button", locale), v)
    fallback = _format_html(_t("email.approval.fallback", locale), v)

    safe_url = html.escape(str(v.get("claim_url", "")), quote=True)
    button_html = (
        f"<div style=\"text-align: center; margin: 25px 0;\">"
        f"<a href=\"{safe_url}\""
        f" style=\"background: linear-gradient(to bottom, #ffd700, #daa520, #8b7355);"
        f" color: #1a1a1a; padding: 14px 30px; text-decoration: none;"
        f" font-weight: bold; font-size: 1.05em; letter-spacing: 1px;\">"
        f"{button_label}</a></div>"
    )

    inner = (
        f"    <p style=\"line-height: 1.8;\">{greeting}</p>\n"
        f"    <p style=\"line-height: 1.8;\">{intro}</p>\n"
        f"    <p style=\"line-height: 1.8;\">{instructions}</p>\n"
        f"    {button_html}\n"
        f"    <p style=\"color: #888; font-size: 0.9em; text-align: center;\">"
        f"{fallback}<br>"
        f"<a href=\"{safe_url}\" style=\"color: #daa520; word-break: break-all;\">"
        f"{safe_url}</a></p>\n"
    )
    heading = _format_html(_t("email.approval.heading", locale), v)
    return subject, text, _wrap_html(heading, inner)


def _render_denial(locale: str, v: Dict[str, Any]) -> Tuple[str, str, str]:
    subject = _format_plain(_t("email.denial.subject", locale), v)
    text = _format_plain(_t("email.denial.text", locale), v)

    greeting = _format_html(_t("email.common.greeting", locale), v)
    body = _format_html(_t("email.denial.body", locale), v)
    reason_label = _format_html(_t("email.denial.reason_label", locale), v)
    reason_value = html.escape(str(v.get("reason", "")), quote=True)
    retry_note = _format_html(_t("email.denial.retry", locale), v)

    inner = (
        f"    <p style=\"line-height: 1.6;\">{greeting}</p>\n"
        f"    <p style=\"line-height: 1.6;\">{body}</p>\n"
        f"    <div style=\"background-color: #3a3a3a; padding: 15px;"
        f" margin: 15px 0; border-left: 3px solid #8b7355;\">"
        f"<p style=\"margin: 0;\"><strong>{reason_label}</strong> {reason_value}</p>"
        f"</div>\n"
        f"    <p style=\"line-height: 1.6;\">{retry_note}</p>\n"
    )
    heading = _format_html(_t("email.common.heading", locale), v)
    return subject, text, _wrap_html(heading, inner)


def _render_reply(locale: str, v: Dict[str, Any]) -> Tuple[str, str, str]:
    subject = _format_plain(_t("email.reply.subject", locale), v)
    text = _format_plain(_t("email.reply.text", locale), v)

    greeting = _format_html(_t("email.common.greeting", locale), v)
    footer = _format_html(_t("email.reply.footer", locale), v)
    escaped_reply = html.escape(str(v.get("reply_text", "")), quote=True).replace(
        "\n", "<br>"
    )

    inner = (
        f"    <p style=\"line-height: 1.8;\">{greeting}</p>\n"
        f"    <div style=\"line-height: 1.8;\">{escaped_reply}</div>\n"
        f"    <hr style=\"border: none; border-top: 1px solid #8b7355;"
        f" margin: 20px 0;\">\n"
        f"    <p style=\"color: #888; font-size: 0.9em;\">{footer}</p>\n"
    )
    heading = _format_html(_t("email.common.heading", locale), v)
    return subject, text, _wrap_html(heading, inner)


def _render_invitation(locale: str, v: Dict[str, Any]) -> Tuple[str, str, str]:
    subject = _format_plain(_t("email.invitation.subject", locale), v)
    text = _format_plain(_t("email.invitation.text", locale), v)

    greeting = _format_html(_t("email.common.greeting", locale), v)
    intro = _format_html(_t("email.invitation.intro", locale), v)
    token_label = _format_html(_t("email.invitation.token_label", locale), v)
    warning = _format_html(_t("email.invitation.warning", locale), v)
    instructions = _format_html(_t("email.invitation.instructions", locale), v)
    button_label = _format_html(_t("email.invitation.button", locale), v)
    fallback = _format_html(_t("email.invitation.fallback", locale), v)

    safe_url = html.escape(str(v.get("claim_url", "")), quote=True)
    safe_token = html.escape(str(v.get("claim_token", "")), quote=True)
    token_box = (
        f"<div style=\"background-color: #3a3a3a; padding: 20px; margin: 20px 0;"
        f" border: 3px solid #daa520; text-align: center;\">"
        f"<p style=\"margin: 0 0 10px 0; font-weight: bold;\">{token_label}</p>"
        f"<p style=\"color: #daa520; font-family: 'Courier New', monospace;"
        f" font-size: 1.6em; letter-spacing: 0.15em; margin: 0; font-weight: bold;\">"
        f"{safe_token}</p></div>"
    )
    button_html = (
        f"<div style=\"text-align: center; margin: 25px 0;\">"
        f"<a href=\"{safe_url}\""
        f" style=\"background: linear-gradient(to bottom, #ffd700, #daa520, #8b7355);"
        f" color: #1a1a1a; padding: 14px 30px; text-decoration: none;"
        f" font-weight: bold; font-size: 1.05em; letter-spacing: 1px;\">"
        f"{button_label}</a></div>"
    )

    inner = (
        f"    <p style=\"line-height: 1.8;\">{greeting}</p>\n"
        f"    <p style=\"line-height: 1.8;\">{intro}</p>\n"
        f"    {token_box}\n"
        f"    <div style=\"background-color: #4a2a2a; padding: 15px;"
        f" margin: 0 0 20px 0; border: 2px solid #ff9999;\">"
        f"<p style=\"color: #ff9999; font-weight: bold; margin: 0;\">{warning}</p>"
        f"</div>\n"
        f"    <p style=\"line-height: 1.8;\">{instructions}</p>\n"
        f"    {button_html}\n"
        f"    <p style=\"color: #888; font-size: 0.9em; text-align: center;\">"
        f"{fallback}<br>"
        f"<a href=\"{safe_url}\" style=\"color: #daa520; word-break: break-all;\">"
        f"{safe_url}</a></p>\n"
    )
    heading = _format_html(_t("email.invitation.heading", locale), v)
    return subject, text, _wrap_html(heading, inner)


def _render_activation(locale: str, v: Dict[str, Any]) -> Tuple[str, str, str]:
    subject = _format_plain(_t("email.activation.subject", locale), v)
    text = _format_plain(_t("email.activation.text", locale), v)

    greeting = _format_html(_t("email.common.greeting", locale), v)
    intro = _format_html(_t("email.activation.intro", locale), v)
    button_label = _format_html(_t("email.activation.button", locale), v)
    expiry_note = _format_html(_t("email.activation.expiry", locale), v)
    how_it_works = _format_html(_t("email.activation.how", locale), v)
    fallback = _format_html(_t("email.activation.fallback", locale), v)

    safe_url = html.escape(str(v.get("activation_url", "")), quote=True)
    button_html = (
        f"<div style=\"text-align: center; margin: 30px 0;\">"
        f"<a href=\"{safe_url}\""
        f" style=\"background: linear-gradient(to bottom, #ffd700, #daa520, #8b7355);"
        f" color: #1a1a1a; padding: 18px 40px; text-decoration: none;"
        f" font-weight: bold; font-size: 1.1em; letter-spacing: 2px;\">"
        f"{button_label}</a></div>"
    )

    inner = (
        f"    <p style=\"line-height: 1.8;\">{greeting}</p>\n"
        f"    <p style=\"line-height: 1.8;\">{intro}</p>\n"
        f"    {button_html}\n"
        f"    <p style=\"line-height: 1.8;\">{expiry_note}</p>\n"
        f"    <p style=\"line-height: 1.8;\">{how_it_works}</p>\n"
        f"    <p style=\"color: #888; font-size: 0.9em; text-align: center;\">"
        f"{fallback}<br>"
        f"<a href=\"{safe_url}\" style=\"color: #daa520; word-break: break-all;\">"
        f"{safe_url}</a></p>\n"
    )
    heading = _format_html(_t("email.activation.heading", locale), v)
    return subject, text, _wrap_html(heading, inner)


_RENDERERS = {
    "magic_link": _render_magic_link,
    "approval": _render_approval,
    "denial": _render_denial,
    "reply": _render_reply,
    "invitation": _render_invitation,
    "activation": _render_activation,
}


def render_email(
    template_name: str, locale: str, **variables: Any
) -> Tuple[str, str, str]:
    """Render a localized email and return ``(subject, text, html)``.

    ``template_name`` must be one of the keys in :data:`_RENDERERS`.
    ``locale`` may be any supported locale code; unknown locales and
    missing keys fall back to English. ``variables`` are the per-template
    placeholders documented in the module docstring.
    """
    if template_name not in _RENDERERS:
        raise ValueError(f"Unknown email template: {template_name}")
    if locale not in _CATALOGS:
        locale = DEFAULT_LOCALE
    return _RENDERERS[template_name](locale, dict(variables))


__all__ = ["render_email", "DEFAULT_LOCALE", "SUPPORTED_LOCALES"]
