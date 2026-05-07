"""Operator-alert email helper for the translation monitor.

When :func:`alert_old_live_segments` flags one or more segments as stale
beyond the latency threshold a human listener would tolerate, the
translation monitor escalates by emailing the operator. Detection alone
is insufficient — the 2026-05-04 prod incident demonstrated that the
``live_age_alert`` events were being written but no human ever looked at
the table, so chapter-6 segments backed up for 10+ minutes while the
worker ground through chapter 5.

Idempotency: each ``(audiobook_id, locale)`` tuple gets at most one
``live_age_alert_emailed`` event per cooldown window (default 3600s).
The cooldown is tracked via the same ``translation_monitor_events``
audit table that backs every other monitor signal, not via in-memory
state, so it survives the timer-driven oneshot's process exit.

SMTP credentials come from the same env-var contract as
:func:`library.auth.audit._send_notification_email` (SMTP_HOST,
SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM). The recipient is taken
from ``ADMIN_EMAIL`` (already an existing convention used by
:mod:`library.backend.api_modular.auth_email`); when unset, falls back
to ``SMTP_FROM`` so a fresh install with only the SMTP block configured
still gets alerts.
"""

from __future__ import annotations

import logging
import os
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from translation_monitor.events import log_event

logger = logging.getLogger(__name__)

EMAIL_COOLDOWN_SEC = 3600


def _resolve_recipient() -> str:
    """Recipient address for operator alerts. Env-driven; never hardcoded."""
    return os.environ.get("ADMIN_EMAIL") or os.environ.get("SMTP_FROM", "")


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """Send a plain-text email via the configured SMTP server.

    Mirrors the pattern in :func:`library.auth.audit._send_notification_email`.
    Returns True on success, False (with logger.error) on any SMTP failure —
    callers are expected to swallow the failure so a transient SMTP outage
    does not crash the monitor timer.
    """
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "noreply@localhost")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.error("Failed to send operator alert to %s: %s", to_email, e)
        return False


def _recently_emailed(
    conn: sqlite3.Connection, audiobook_id: int, cooldown_sec: int = EMAIL_COOLDOWN_SEC
) -> bool:
    """True iff an alert email was already sent for this audiobook within
    the cooldown window. Survives timer-oneshot restarts via the
    audit-trail table."""
    row = conn.execute(
        "SELECT 1 FROM translation_monitor_events "
        "WHERE event_type = 'live_age_alert_emailed' "
        "  AND audiobook_id = ? "
        "  AND (julianday('now') - julianday(created_at)) * 86400 < ? "
        "LIMIT 1",
        (audiobook_id, cooldown_sec),
    ).fetchone()
    return row is not None


def _gather_alert_context(
    conn: sqlite3.Connection, audiobook_id: int, segment_ids: list[int]
) -> dict:
    """Pull the data needed for the email body in a single round-trip per
    table. Falls back gracefully when ``audiobooks`` or
    ``streaming_sessions`` rows are absent (test fixtures, fresh DB)."""
    title: str | None = None
    locale: str | None = None
    active_chapter: int | None = None
    stuck_chapters: list[int] = []
    oldest_age_sec = 0.0

    try:
        row = conn.execute("SELECT title FROM audiobooks WHERE id = ?", (audiobook_id,)).fetchone()
        if row:
            title = row["title"]
    except sqlite3.OperationalError:
        # audiobooks table absent (translation-monitor unit tests use a
        # minimal schema). The email is still useful with just the ID.
        pass

    placeholders = ",".join("?" for _ in segment_ids)
    if segment_ids:
        rows = conn.execute(
            "SELECT chapter_index, locale, "
            "       (julianday('now') - julianday(created_at)) * 86400 AS age_sec "
            f"FROM streaming_segments WHERE id IN ({placeholders})",  # nosec B608  # noqa: S608
            tuple(segment_ids),
        ).fetchall()
        for r in rows:
            if r["chapter_index"] is not None:
                stuck_chapters.append(int(r["chapter_index"]))
            if locale is None and r["locale"] is not None:
                locale = r["locale"]
            if r["age_sec"] is not None and r["age_sec"] > oldest_age_sec:
                oldest_age_sec = float(r["age_sec"])

    if locale is not None:
        try:
            row = conn.execute(
                "SELECT active_chapter FROM streaming_sessions "
                "WHERE audiobook_id = ? AND locale = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (audiobook_id, locale),
            ).fetchone()
            if row and row["active_chapter"] is not None:
                active_chapter = int(row["active_chapter"])
        except sqlite3.OperationalError:
            pass

    return {
        "title": title,
        "locale": locale,
        "active_chapter": active_chapter,
        "stuck_chapters": sorted(set(stuck_chapters)),
        "stale_count": len(segment_ids),
        "oldest_age_sec": round(oldest_age_sec, 1),
    }


def _format_email(audiobook_id: int, ctx: dict) -> tuple[str, str]:
    """Build (subject, body) for an operator alert.

    Body intentionally plain text (no HTML) — operator inbox triage
    works fine with a few lines, and HTML+SMTP failure modes are an
    extra surface we don't need.
    """
    title_part = f' "{ctx["title"]}"' if ctx["title"] else ""
    subject = f"[Audiobook Library] Translation queue starved — book {audiobook_id}{title_part}"
    chapter_advance = ""
    if (
        ctx["active_chapter"] is not None
        and ctx["stuck_chapters"]
        and any(c < ctx["active_chapter"] for c in ctx["stuck_chapters"])
    ):
        chapter_advance = (
            f"  • Player advanced to chapter {ctx['active_chapter']} but stuck "
            f"segments are in chapters {ctx['stuck_chapters']} "
            "(worker stuck on previous chapter — chapter-advance starvation).\n"
        )
    body = (
        f"Translation monitor detected {ctx['stale_count']} stale streaming "
        f"segment(s) for audiobook {audiobook_id}"
        f"{title_part}.\n\n"
        f"Locale: {ctx['locale'] or 'unknown'}\n"
        f"Stuck chapters: {ctx['stuck_chapters'] or 'unknown'}\n"
        f"Player active chapter: {ctx['active_chapter'] if ctx['active_chapter'] is not None else 'unknown'}\n"
        f"Oldest stale segment age: {ctx['oldest_age_sec']:.0f}s\n"
        f"{chapter_advance}\n"
        "Investigate: check journalctl -u audiobook-translation-monitor-live "
        "and the streaming_segments table for "
        f"audiobook_id={audiobook_id} with state in pending/processing/claimed.\n\n"
        "Cooldown: this alert will not re-send for the same book within "
        f"{EMAIL_COOLDOWN_SEC // 60} minutes.\n"
    )
    return subject, body


def send_chapter_starvation_alert(
    conn: sqlite3.Connection,
    audiobook_id: int,
    segment_ids: list[int],
    *,
    cooldown_sec: int = EMAIL_COOLDOWN_SEC,
) -> bool:
    """Email the operator that ``audiobook_id`` has stale streaming segments.

    Returns True iff an email was actually dispatched (recipient resolved,
    SMTP succeeded, no cooldown active). Returns False — without logging
    an event — when the cooldown blocks the send, when no recipient is
    configured, or when SMTP fails. The latter two cases are logged via
    the standard logger so an operator running ``journalctl -u
    audiobook-translation-monitor-live`` can see why no email arrived.

    Idempotent within the cooldown window via a ``live_age_alert_emailed``
    event written before the SMTP send. The event is written eagerly so
    a slow SMTP server does not allow a second tick to also fire — the
    monitor's 30s cadence is faster than typical SMTP round-trips.
    """
    if not segment_ids:
        return False
    if _recently_emailed(conn, audiobook_id, cooldown_sec=cooldown_sec):
        return False

    recipient = _resolve_recipient()
    if not recipient:
        logger.warning(
            "operator alert suppressed for audiobook %d: no ADMIN_EMAIL or SMTP_FROM configured",
            audiobook_id,
        )
        return False

    ctx = _gather_alert_context(conn, audiobook_id, segment_ids)
    subject, body = _format_email(audiobook_id, ctx)

    log_event(
        conn,
        monitor="live",
        event_type="live_age_alert_emailed",
        audiobook_id=audiobook_id,
        details={
            "recipient": recipient,
            "stale_count": ctx["stale_count"],
            "stuck_chapters": ctx["stuck_chapters"],
            "active_chapter": ctx["active_chapter"],
            "oldest_age_sec": ctx["oldest_age_sec"],
        },
    )
    conn.commit()

    return _send_email(recipient, subject, body)
