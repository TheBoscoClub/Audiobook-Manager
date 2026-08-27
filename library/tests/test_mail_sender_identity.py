"""Sender-identity guard: refuse to send rather than send under a bad identity.

Audiobook-Manager-9nu. The relay picks its upstream credential by envelope
sender, so a sender it cannot map is rejected upstream at MAIL FROM (530)
*after* local submission already returned 250 OK. The application never sees
the bounce. These tests pin the refusal, and pin that no send is attempted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from common_utils.mail_identity import SenderIdentityError, resolve_sender  # noqa: E402


class TestResolveSender:
    @pytest.mark.parametrize(
        "value",
        ["library@thebosco.club", "  library@thebosco.club  ", "a.b+c@example.co.uk"],
    )
    def test_accepts_deliverable_addresses(self, value):
        assert resolve_sender(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "noreply@localhost",
            "audiobooks@localhost",
            "NoReply@LOCALHOST",
            "x@localhost.localdomain",
            "nobody",
        ],
    )
    def test_refuses_undeliverable_identities(self, value):
        with pytest.raises(SenderIdentityError):
            resolve_sender(value)

    def test_refusal_names_the_variable_so_the_operator_can_act(self):
        with pytest.raises(SenderIdentityError, match="SMTP_FROM"):
            resolve_sender(None)


class TestNoSendUnderBadIdentity:
    """The guard must prevent the SMTP conversation, not merely annotate it."""

    def test_translation_monitor_does_not_open_a_connection(self, monkeypatch):
        from translation_monitor import notify

        monkeypatch.delenv("SMTP_FROM", raising=False)

        opened: list[tuple] = []

        class Boom:
            def __init__(self, *a, **k):
                opened.append(a)
                raise AssertionError("SMTP connection opened despite bad sender")

        monkeypatch.setattr(notify.smtplib, "SMTP", Boom)
        assert notify._send_email("to@example.com", "subject", "body") is False
        assert opened == []
