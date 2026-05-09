"""Tests for the v8.3.10.5 operator-alert email helper.

Validates :mod:`library.translation_monitor.notify` end-to-end against a
fresh SQLite DB loaded from the canonical schema, with all SMTP
interactions mocked (no network traffic, no real mail server).

Covers:
    * Recipient resolution (ADMIN_EMAIL → SMTP_FROM → empty)
    * Cooldown idempotency (a second call within the cooldown window is a
      no-op, no extra event row, no second SMTP send)
    * Recipient absent → no SMTP send, no event row written
    * Empty segment_ids → no-op (defensive against caller bugs)
    * SMTP failure does not crash the caller and is reported as False
    * Body assembly: subject contains the audiobook ID and title; body
      mentions the active_chapter, the stuck-chapter list, and the count
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from translation_monitor.events import recent_events
from translation_monitor.notify import (
    EMAIL_COOLDOWN_SEC,
    _format_email,
    _gather_alert_context,
    _recently_emailed,
    _resolve_recipient,
    send_chapter_starvation_alert,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "library" / "backend" / "schema.sql"


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path) -> Generator[sqlite3.Connection, None, None]:
    """Fresh DB with the canonical schema applied + one audiobook row."""
    conn = sqlite3.connect(str(tmp_path / "notify.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'Test Book', '/tmp/t')")
    conn.commit()
    yield conn
    conn.close()


def _insert_segment(
    conn: sqlite3.Connection,
    *,
    seg_id: int,
    chapter: int = 0,
    state: str = "pending",
    created_at_offset_sec: int = 180,
    locale: str = "zh-Hans",
) -> int:
    created = f"datetime('now','-{created_at_offset_sec} seconds')"
    sql = (
        f"INSERT INTO streaming_segments "  # nosec B608 - test fixture
        "(id, audiobook_id, chapter_index, segment_index, locale, "
        " state, priority, origin, worker_id, created_at) "
        f"VALUES (?, 1, ?, ?, ?, ?, 0, 'live', NULL, {created})"
    )
    conn.execute(sql, (seg_id, chapter, seg_id, locale, state))
    conn.commit()
    return seg_id


def _insert_session(
    conn: sqlite3.Connection, *, active_chapter: int = 6, locale: str = "zh-Hans"
) -> None:
    conn.execute(
        "INSERT INTO streaming_sessions (audiobook_id, locale, active_chapter, state) "
        "VALUES (1, ?, ?, 'streaming')",
        (locale, active_chapter),
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _scrub_smtp_env(monkeypatch):
    """Every test starts with a known SMTP env baseline, opts in as needed."""
    for key in ("ADMIN_EMAIL", "SMTP_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(key, raising=False)
    yield


# ─── _resolve_recipient ────────────────────────────────────────────────────


def test_resolve_recipient_prefers_admin_email(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setenv("SMTP_FROM", "library@example.com")
    assert _resolve_recipient() == "ops@example.com"


def test_resolve_recipient_falls_back_to_smtp_from(monkeypatch):
    monkeypatch.setenv("SMTP_FROM", "library@example.com")
    assert _resolve_recipient() == "library@example.com"


def test_resolve_recipient_empty_when_neither_configured():
    assert _resolve_recipient() == ""


# ─── _recently_emailed cooldown ───────────────────────────────────────────


def test_recently_emailed_false_on_fresh_db(db):
    assert _recently_emailed(db, audiobook_id=1) is False


def test_recently_emailed_true_after_event_logged(db):
    db.execute(
        "INSERT INTO translation_monitor_events (monitor, event_type, audiobook_id) "
        "VALUES ('live', 'live_age_alert_emailed', 1)"
    )
    db.commit()
    assert _recently_emailed(db, audiobook_id=1) is True


def test_recently_emailed_false_for_other_audiobook(db):
    db.execute(
        "INSERT INTO translation_monitor_events (monitor, event_type, audiobook_id) "
        "VALUES ('live', 'live_age_alert_emailed', 1)"
    )
    db.commit()
    assert _recently_emailed(db, audiobook_id=2) is False


def test_recently_emailed_respects_explicit_cooldown_arg(db):
    """A 0-second cooldown ignores any prior event."""
    db.execute(
        "INSERT INTO translation_monitor_events (monitor, event_type, audiobook_id) "
        "VALUES ('live', 'live_age_alert_emailed', 1)"
    )
    db.commit()
    assert _recently_emailed(db, audiobook_id=1, cooldown_sec=0) is False


# ─── _gather_alert_context ────────────────────────────────────────────────


def test_gather_context_includes_title_and_locale(db):
    _insert_segment(db, seg_id=1, chapter=6)
    ctx = _gather_alert_context(db, audiobook_id=1, segment_ids=[1])
    assert ctx["title"] == "Test Book"
    assert ctx["locale"] == "zh-Hans"
    assert ctx["stuck_chapters"] == [6]
    assert ctx["stale_count"] == 1
    assert ctx["oldest_age_sec"] >= 100  # we inserted at -180s


def test_gather_context_resolves_active_chapter_from_session(db):
    _insert_segment(db, seg_id=1, chapter=5)
    _insert_segment(db, seg_id=2, chapter=5)
    _insert_session(db, active_chapter=6)
    ctx = _gather_alert_context(db, audiobook_id=1, segment_ids=[1, 2])
    assert ctx["active_chapter"] == 6
    assert ctx["stuck_chapters"] == [5]


# ─── _format_email ────────────────────────────────────────────────────────


def test_format_email_subject_contains_book_id_and_title():
    ctx = {
        "title": "Test Book",
        "locale": "zh-Hans",
        "active_chapter": 6,
        "stuck_chapters": [5],
        "stale_count": 12,
        "oldest_age_sec": 240.0,
    }
    subject, body = _format_email(audiobook_id=1, ctx=ctx)
    assert "1" in subject
    assert "Test Book" in subject
    assert "12 stale" in body
    assert "chapter 6" in body
    assert "[5]" in body
    assert "240" in body


def test_format_email_chapter_advance_detected_when_active_ahead_of_stuck():
    ctx = {
        "title": "T",
        "locale": "zh-Hans",
        "active_chapter": 6,
        "stuck_chapters": [5],
        "stale_count": 1,
        "oldest_age_sec": 200.0,
    }
    _, body = _format_email(audiobook_id=1, ctx=ctx)
    assert "chapter-advance starvation" in body


def test_format_email_no_advance_text_when_stuck_matches_active():
    ctx = {
        "title": "T",
        "locale": "zh-Hans",
        "active_chapter": 5,
        "stuck_chapters": [5],
        "stale_count": 1,
        "oldest_age_sec": 200.0,
    }
    _, body = _format_email(audiobook_id=1, ctx=ctx)
    assert "chapter-advance starvation" not in body


# ─── send_chapter_starvation_alert end-to-end ─────────────────────────────


def test_send_returns_false_for_empty_segment_list(db):
    assert send_chapter_starvation_alert(db, audiobook_id=1, segment_ids=[]) is False


def test_send_returns_false_when_no_recipient_configured(db):
    """No ADMIN_EMAIL, no SMTP_FROM → suppressed (and no event row)."""
    _insert_segment(db, seg_id=1, chapter=6)
    result = send_chapter_starvation_alert(db, audiobook_id=1, segment_ids=[1])
    assert result is False
    assert recent_events(db, event_type="live_age_alert_emailed") == []


def test_send_succeeds_with_admin_email(db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "library@example.com")
    _insert_segment(db, seg_id=1, chapter=6)
    _insert_session(db, active_chapter=6)

    with patch("translation_monitor.notify.smtplib.SMTP") as mock_smtp:
        instance = mock_smtp.return_value.__enter__.return_value
        result = send_chapter_starvation_alert(db, audiobook_id=1, segment_ids=[1])

    assert result is True
    instance.sendmail.assert_called_once()
    sent_from, sent_to, _msg_str = instance.sendmail.call_args.args
    assert sent_from == "library@example.com"
    assert sent_to == "ops@example.com"

    events = recent_events(db, event_type="live_age_alert_emailed")
    assert len(events) == 1
    assert events[0]["audiobook_id"] == 1


def test_send_idempotent_within_cooldown(db, monkeypatch):
    """A second send within the cooldown is a no-op even though SMTP would
    succeed — proves the dedup event blocks the second SMTP call."""
    monkeypatch.setenv("ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "library@example.com")
    _insert_segment(db, seg_id=1, chapter=6)

    with patch("translation_monitor.notify.smtplib.SMTP") as mock_smtp:
        first = send_chapter_starvation_alert(db, audiobook_id=1, segment_ids=[1])
        second = send_chapter_starvation_alert(db, audiobook_id=1, segment_ids=[1])

    assert first is True
    assert second is False
    assert mock_smtp.return_value.__enter__.return_value.sendmail.call_count == 1
    assert len(recent_events(db, event_type="live_age_alert_emailed")) == 1


def test_send_returns_false_on_smtp_failure(db, monkeypatch):
    """SMTP exceptions must be swallowed — a transient mail outage
    cannot crash a monitor tick."""
    monkeypatch.setenv("ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "library@example.com")
    _insert_segment(db, seg_id=1, chapter=6)

    import smtplib

    with patch(
        "translation_monitor.notify.smtplib.SMTP", side_effect=smtplib.SMTPException("nope")
    ):
        result = send_chapter_starvation_alert(db, audiobook_id=1, segment_ids=[1])

    assert result is False
    # Event was logged eagerly (before SMTP) — that's intentional, prevents
    # a slow SMTP from allowing a second tick to also fire.
    assert len(recent_events(db, event_type="live_age_alert_emailed")) == 1


def test_email_cooldown_sec_constant():
    assert EMAIL_COOLDOWN_SEC == 3600


# Suppress an unused-import warning on `os`; reserved for future env probes.
_ = os
