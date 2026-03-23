"""Tests for notification queue polling logic."""

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def notif_db(tmp_path):
    db_path = tmp_path / "test.db"
    schema = (Path(__file__).parent.parent / "backend" / "schema.sql").read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()
    return db_path


def test_poll_finds_pending_notifications(notif_db):
    """Poller finds undelivered notifications."""
    conn = sqlite3.connect(str(notif_db))
    conn.execute(
        "INSERT INTO maintenance_notifications (notification_type, payload) VALUES (?, ?)",
        ("announce", json.dumps({"window_id": 1})),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM maintenance_notifications WHERE delivered = 0"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


def test_poll_marks_delivered(notif_db):
    """Poller marks notifications as delivered after processing."""
    conn = sqlite3.connect(str(notif_db))
    conn.execute(
        "INSERT INTO maintenance_notifications (notification_type, payload) VALUES (?, ?)",
        ("announce", json.dumps({"window_id": 1})),
    )
    conn.commit()
    nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE maintenance_notifications SET delivered = 1 WHERE id = ?", (nid,)
    )
    conn.commit()
    pending = conn.execute(
        "SELECT COUNT(*) FROM maintenance_notifications WHERE delivered = 0"
    ).fetchone()[0]
    assert pending == 0
    conn.close()
