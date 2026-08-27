"""Resolve the SMTP envelope sender, or refuse to send.

Every outbound path in this project submits to the local relay on
``127.0.0.1:25`` and holds no credential. The relay chooses its upstream
credential by *envelope sender* (``smtp_sender_dependent_authentication``), so
the sender is not cosmetic — it selects the account that will be used.

A sender the relay has no mapping for (anything ``@localhost``, or an empty
value) is therefore not a harmless default. Local submission still returns
``250 OK``, so the application sees success; the rejection happens upstream at
``MAIL FROM`` with ``530`` — no credential row matched — and the message hard
bounces with ``dsn=5.0.0``: no queue, no retry, and the bounce notification is
itself undeliverable because ``localhost`` is not in ``mydestination``.

That is exactly how 14 messages were lost on 2026-08-26, one path being
login/OTP mail (Audiobook-Manager-9nu). A missing environment variable became a
*wrong identity* rather than an error.

So this module refuses. A caller that cannot name a deliverable sender does not
send, and says so loudly.
"""

from __future__ import annotations


class SenderIdentityError(RuntimeError):
    """Raised when no deliverable envelope sender is configured."""


def resolve_sender(value: str | None) -> str:
    """Return a usable envelope sender, or raise :class:`SenderIdentityError`.

    Args:
        value: the configured ``SMTP_FROM``, from the environment or a config
            mapping. ``None`` and empty are treated identically.

    Returns:
        The stripped sender address.

    Raises:
        SenderIdentityError: when the value is missing, empty, not an address,
            or points at ``localhost`` — none of which the relay can map to an
            upstream credential.
    """
    sender = (value or "").strip()
    if not sender:
        raise SenderIdentityError(
            "SMTP_FROM is not set. Refusing to send: the relay selects its "
            "upstream credential by envelope sender, so an unset sender is "
            "rejected upstream at MAIL FROM after local submission already "
            "returned 250 OK (a silent hard bounce)."
        )
    if "@" not in sender:
        raise SenderIdentityError(
            f"SMTP_FROM={sender!r} is not an email address. Refusing to send."
        )
    if sender.lower().rsplit("@", 1)[-1] in ("localhost", "localhost.localdomain"):
        raise SenderIdentityError(
            f"SMTP_FROM={sender!r} points at localhost, which the relay has no "
            "sender mapping for. Refusing to send rather than hard-bouncing "
            "under an unmapped identity. Set SMTP_FROM to an address the relay "
            "is authorised to send as."
        )
    return sender
