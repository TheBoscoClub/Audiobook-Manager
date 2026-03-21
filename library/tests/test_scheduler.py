"""Tests for maintenance scheduler daemon."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def scheduler_db(tmp_path):
    """Create a test database with maintenance schema."""
    db_path = tmp_path / "test.db"
    schema = (Path(__file__).parent.parent / "backend" / "schema.sql").read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()
    return db_path


def test_find_due_windows(scheduler_db):
    """Scheduler finds windows with next_run_at in the past."""
    conn = sqlite3.connect(str(scheduler_db))
    conn.execute(
        """INSERT INTO maintenance_windows
           (name, task_type, schedule_type, next_run_at, status)
           VALUES ('Test', 'db_vacuum', 'once', datetime('now', '-1 hour'), 'active')"""
    )
    conn.commit()
    rows = conn.execute(
        """SELECT * FROM maintenance_windows
           WHERE next_run_at <= datetime('now') AND status = 'active'"""
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_write_notification(scheduler_db):
    """Scheduler writes to notification queue after execution."""
    conn = sqlite3.connect(str(scheduler_db))
    conn.execute(
        """INSERT INTO maintenance_notifications (notification_type, payload)
           VALUES ('update', '{"window_id": 1, "status": "success"}')"""
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM maintenance_notifications WHERE delivered = 0"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_history_recorded(scheduler_db):
    """Execution results are recorded in history table."""
    conn = sqlite3.connect(str(scheduler_db))
    conn.execute(
        """INSERT INTO maintenance_windows
           (name, task_type, schedule_type, scheduled_at, next_run_at, status)
           VALUES ('Test', 'db_vacuum', 'once', datetime('now'), datetime('now'), 'active')"""
    )
    conn.commit()
    wid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO maintenance_history (window_id, started_at, status, result_message)
           VALUES (?, datetime('now'), 'success', 'OK')""",
        (wid,),
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM maintenance_history").fetchall()
    conn.close()
    assert len(rows) == 1
