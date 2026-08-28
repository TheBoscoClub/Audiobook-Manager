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


class TestRefusalMessagesAreActionable:
    """The refusal message is the operator's only signal — pin its content.

    Mutation testing showed ~20 mutants living inside these strings: mutmut
    wraps them in XX…XX and flips their case, and every existing test passed
    regardless, because none looked at what the exception actually said. A
    refusal nobody can act on is barely better than a silent failure, so the
    distinguishing content is asserted here.
    """

    def test_unset_sender_says_which_variable_and_why(self):
        with pytest.raises(SenderIdentityError) as exc:
            resolve_sender(None)
        msg = str(exc.value)
        assert "SMTP_FROM is not set" in msg
        assert "envelope sender" in msg  # names the mechanism
        assert "250 OK" in msg  # names the trap: local success

    def test_empty_string_is_reported_as_unset_not_as_malformed(self):
        """Distinguishes the `(value or "")` default from a substituted one.

        With `(value or "XXXX")` the empty case falls through to the
        "not an email address" branch instead, which is a different — and
        misleading — diagnosis for the operator.
        """
        with pytest.raises(SenderIdentityError) as exc:
            resolve_sender("")
        assert "SMTP_FROM is not set" in str(exc.value)

    def test_non_address_says_it_is_not_an_address(self):
        with pytest.raises(SenderIdentityError) as exc:
            resolve_sender("nobody")
        assert "is not an email address" in str(exc.value)

    def test_localhost_refusal_explains_the_relay_mapping(self):
        with pytest.raises(SenderIdentityError) as exc:
            resolve_sender("noreply@localhost")
        msg = str(exc.value)
        assert "localhost" in msg
        assert "sender mapping" in msg
        assert "authorised to send as" in msg  # tells the operator the fix


class TestDomainIsTakenFromTheLastAtSign:
    """`rsplit("@", 1)` is load-bearing, not stylistic."""

    def test_multiple_at_signs_still_resolve_to_the_real_domain(self):
        """`split` instead of `rsplit` would ACCEPT this as deliverable.

        With split("@", 1)[-1] the domain reads as "b@localhost", which is not
        in the refusal tuple, so a localhost sender sails through.
        """
        with pytest.raises(SenderIdentityError):
            resolve_sender("a@b@localhost")

    def test_multiple_at_signs_with_a_real_domain_are_accepted(self):
        assert resolve_sender("a@b@thebosco.club") == "a@b@thebosco.club"
