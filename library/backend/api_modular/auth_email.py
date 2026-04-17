"""
Email senders for the auth blueprint.

All SMTP delivery is centralized here. Each `_send_*_email` helper:
- Pulls SMTP/base-URL config from the environment and the current Flask request
- Renders localized templates via `backend.api_modular.email_templates.render_email`
- Returns `True` on success, `False` on any SMTP / rendering error (error type
  is logged, never the full exception body, to avoid leaking email addresses).

External modules and tests access these helpers through
`backend.api_modular.auth` (re-exported at the end of `auth.py`). Keeping that
stable import path is what lets existing `@patch("...auth._send_admin_alert")`
mocks continue to work after this extraction.
"""

import os
import smtplib
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from flask import current_app, request


def _get_base_url() -> str:
    """Get the base URL for email links, auto-detecting from request if not
    configured."""
    configured = os.environ.get("BASE_URL", "")
    if configured:
        return configured.rstrip("/")
    return request.host_url.rstrip("/")


def _get_email_config() -> tuple:
    """Get SMTP configuration from environment."""
    return (
        os.environ.get("SMTP_HOST", "localhost"),
        int(os.environ.get("SMTP_PORT", "25")),
        os.environ.get("SMTP_USER", ""),
        os.environ.get("SMTP_PASS", ""),
        os.environ.get("SMTP_FROM", "noreply@localhost"),
    )


def _send_magic_link_email(
    to_email: str, username: str, magic_link: str, expires_minutes: int, locale: str = "en"
) -> bool:
    """Send a magic link email for login recovery."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    full_link = f"{base_url}{magic_link}"

    subject, text_content, html_content = render_email(
        "magic_link", locale, username=username, link=full_link, expires_minutes=expires_minutes
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to send magic link email: {type(e).__name__}")
        return False


def _send_approval_email(to_email: str, username: str, locale: str = "en") -> bool:
    """Send approval email with setup instructions and claim URL."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    claim_url = f"{base_url}/claim.html?username={urllib.parse.quote(username)}"

    subject, text_content, html_content = render_email(
        "approval", locale, username=username, claim_url=claim_url
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to send approval email: {type(e).__name__}")
        return False


def _send_denial_email(
    to_email: str, username: str, reason: Optional[str] = None, locale: str = "en"
) -> bool:
    """Send denial notification for access request."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()

    reason_text = reason if reason else "No specific reason was provided."

    subject, text_content, html_content = render_email(
        "denial", locale, username=username, reason=reason_text
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to send denial email: {type(e).__name__}")
        return False


def _send_admin_alert(username: str, message_preview: str) -> bool:
    """Alert admin about new contact message (plain text, no template)."""
    smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from = _get_email_config()
    admin_email = os.environ.get("ADMIN_EMAIL", smtp_from)

    if not smtp_user:
        return False

    subject = f"New message from {username} - The Library"
    body = f"""You have a new message from {username} in The Library inbox.

Preview: {message_preview}{"..." if len(message_preview) >= 100 else ""}

View all messages:
  audiobook-inbox list
  audiobook-inbox read <id>
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = admin_email

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, admin_email, msg.as_string())

        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send admin alert: {type(e).__name__}")
        return False


def _send_reply_email(to_email: str, username: str, reply_text: str, locale: str = "en") -> bool:
    """Send admin reply to user inbox message."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from = _get_email_config()

    subject, body, html_content = render_email(
        "reply", locale, username=username, reply_text=reply_text
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to_email

        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, to_email, msg.as_string())

        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send reply email: {type(e).__name__}")
        return False


def _send_invitation_email(
    to_email: str,
    username: str,
    claim_token: str,
    locale: str = "en",
    expires_hours: int = 48,
) -> bool:
    """Send invitation email with claim URL + token (TOTP flow)."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    claim_url = (
        f"{base_url}/claim.html"
        f"?username={urllib.parse.quote(username)}"
        f"&token={urllib.parse.quote(claim_token)}"
    )

    subject, text_content, html_content = render_email(
        "invitation",
        locale,
        username=username,
        claim_url=claim_url,
        claim_token=claim_token,
        expires_hours=expires_hours,
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to send invitation email: {type(e).__name__}")
        return False


def _send_activation_email(
    to_email: str,
    username: str,
    activation_token: str,
    locale: str = "en",
    expires_hours: int = 48,
) -> bool:
    """Send activation email (magic-link flow — single click, no TOTP)."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    activation_url = f"{base_url}/verify.html?token={activation_token}&activate=1"

    subject, text_content, html_content = render_email(
        "activation",
        locale,
        username=username,
        activation_url=activation_url,
        expires_hours=expires_hours,
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to send activation email: {type(e).__name__}")
        return False
