"""Phase 6c security test: worker callback endpoints require admin_or_localhost.

Verifies that segment-complete and chapter-complete reject unauthenticated
requests arriving from non-localhost IPs when AUTH_ENABLED=False (standalone
mode). In standalone mode, admin_or_localhost falls back to a localhost-only
check — this is the attack surface we're guarding.

AUTH_ENABLED=True behavior (admin session required) is covered by the
existing auth integration tests in test_auth_lifecycle_integration.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from backend.api_modular import streaming_translate as st


def _init_translation_queue(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            state TEXT DEFAULT 'pending',
            total_chapters INTEGER,
            UNIQUE(audiobook_id, locale)
        )
        """)
    conn.commit()
    conn.close()


@pytest.fixture
def streaming_db(flask_app, session_temp_dir):
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    st._db_path = db_path
    _init_translation_queue(db_path)
    yield db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM streaming_segments")
    conn.execute("DELETE FROM streaming_sessions")
    conn.execute("DELETE FROM chapter_subtitles")
    conn.execute("DELETE FROM chapter_translations_audio")
    conn.execute("DELETE FROM translation_queue")
    conn.commit()
    conn.close()


def test_segment_complete_rejects_non_localhost(app_client, streaming_db, flask_app):
    """segment-complete must return 404 for non-localhost callers in standalone mode.

    The admin_or_localhost decorator in AUTH_ENABLED=False mode restricts
    access to 127.0.0.1/::1/localhost. A request spoofing a remote IP
    (via ENVIRON_BASE) must be denied before any DB mutation occurs.
    """
    # Confirm standalone mode for this assertion to be meaningful
    assert not flask_app.config.get("AUTH_ENABLED", False), (
        "This test targets AUTH_ENABLED=False (standalone) mode; "
        "enable auth and use an admin session for AUTH_ENABLED=True coverage."
    )

    # Insert a pending segment so there is something for the endpoint to mutate
    conn = sqlite3.connect(str(streaming_db))
    conn.execute(
        "INSERT OR IGNORE INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
        "VALUES (42, 0, 0, 'zh-Hans', 'pending', 0)"
    )
    conn.commit()
    conn.close()

    # Simulate a request arriving from a remote (non-localhost) IP
    resp = app_client.post(
        "/api/translate/segment-complete",
        json={"audiobook_id": 42, "chapter_index": 0, "segment_index": 0, "locale": "zh-Hans"},
        environ_base={"REMOTE_ADDR": "203.0.113.1"},  # TEST-NET-3, RFC 5737 — not localhost
    )
    # admin_or_localhost in AUTH_ENABLED=False mode returns 404 for non-localhost
    assert (
        resp.status_code == 404
    ), f"Expected 404 (non-localhost blocked), got {resp.status_code}: {resp.get_data(as_text=True)}"

    # Confirm the segment row was NOT mutated (state must still be 'pending')
    conn = sqlite3.connect(str(streaming_db))
    row = conn.execute(
        "SELECT state FROM streaming_segments "
        "WHERE audiobook_id = 42 AND chapter_index = 0 AND segment_index = 0"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "pending", f"DB was mutated despite 404 response — state is {row[0]!r}"


def test_chapter_complete_rejects_non_localhost(app_client, streaming_db, flask_app):
    """chapter-complete must return 404 for non-localhost callers in standalone mode."""
    assert not flask_app.config.get("AUTH_ENABLED", False)

    resp = app_client.post(
        "/api/translate/chapter-complete",
        json={"audiobook_id": 43, "chapter_index": 0, "locale": "zh-Hans"},
        environ_base={"REMOTE_ADDR": "198.51.100.1"},  # TEST-NET-2, RFC 5737 — not localhost
    )
    assert (
        resp.status_code == 404
    ), f"Expected 404 (non-localhost blocked), got {resp.status_code}: {resp.get_data(as_text=True)}"


def test_segment_complete_allows_localhost(app_client, streaming_db):
    """segment-complete allows localhost requests in standalone mode (control case)."""
    conn = sqlite3.connect(str(streaming_db))
    conn.execute(
        "INSERT OR IGNORE INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
        "VALUES (44, 0, 0, 'zh-Hans', 'pending', 0)"
    )
    conn.commit()
    conn.close()

    # Default test client uses REMOTE_ADDR=127.0.0.1 — should be allowed
    resp = app_client.post(
        "/api/translate/segment-complete",
        json={"audiobook_id": 44, "chapter_index": 0, "segment_index": 0, "locale": "zh-Hans"},
    )
    assert (
        resp.status_code == 200
    ), f"Localhost request should be allowed, got {resp.status_code}: {resp.get_data(as_text=True)}"
    assert resp.get_json()["status"] == "ok"
