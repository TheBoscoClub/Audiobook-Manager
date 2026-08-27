"""Behavioural guard: every mail path must actually SEND when no credential is set.

Since Audiobook-Manager-9nu the project submits to the local relay at
127.0.0.1:25 with NO credential, so SMTP_USER is empty BY DESIGN. Two separate
bugs shipped from assuming the opposite, and both failed silently:

  1. `starttls()` called unconditionally. The relay does not advertise STARTTLS,
     so it raised SMTPNotSupportedError, which the surrounding broad
     `except Exception` swallowed — logging only the exception CLASS name.
  2. `if not smtp_user: return False` — a precondition written for the old
     credentialed transport. It returned before attempting anything at all;
     `_send_admin_alert` did not even log.

Affected: admin contact alerts, admin replies, and `audiobook-inbox reply`.
All three reported success to the caller (`auth.py` discards the False) while
sending nothing.

test_source_guards.py section 4 catches shape (1) statically. These tests catch
BOTH by behaviour: with a credential-less environment and a fake SMTP, sendmail
MUST be reached.
"""

from __future__ import annotations

import smtplib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "library"))

pytestmark = pytest.mark.requires_repo_source


class _FakeSMTP:
    """Stand-in for a credential-less relay: STARTTLS is NOT offered."""

    instances: list[_FakeSMTP] = []

    def __init__(self, host, port, *a, **k):
        self.host, self.port = host, port
        self.sendmail_calls: list[tuple] = []
        self.started_tls = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, *a, **k):
        self.started_tls = True
        raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")

    def login(self, *a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("login() attempted with no credential configured")

    def sendmail(self, from_addr, to_addr, msg):
        self.sendmail_calls.append((from_addr, to_addr, msg))


@pytest.fixture
def relay(monkeypatch):
    """Credential-less relay environment plus a fake SMTP that refuses STARTTLS."""
    _FakeSMTP.instances.clear()
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_FROM", "library@thebosco.club")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@thebosco.club")
    monkeypatch.delenv("SMTP_PASS", raising=False)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP


def test_inbox_cli_reply_actually_sends(relay, monkeypatch):
    from auth import inbox_cli

    monkeypatch.setattr(inbox_cli, "resolve_secret", lambda _n: "")
    ok = inbox_cli.send_email_reply("reader@example.com", "reader", "thanks for writing")

    assert ok is True, "send_email_reply must succeed against a credential-less relay"
    assert relay.instances, "no SMTP connection was ever opened"
    assert relay.instances[0].sendmail_calls, "sendmail() was never reached"
    assert not relay.instances[0].started_tls, "starttls() must not be attempted"


def test_inbox_cli_reply_addresses_the_recipient(relay, monkeypatch):
    from auth import inbox_cli

    monkeypatch.setattr(inbox_cli, "resolve_secret", lambda _n: "")
    inbox_cli.send_email_reply("reader@example.com", "reader", "body text")
    frm, to, _ = relay.instances[0].sendmail_calls[0]
    assert to == "reader@example.com"
    assert frm == "library@thebosco.club"
