"""Regression tests for Audiobook-Manager-od0.

The sampler completed nothing for ~11 weeks while reporting healthy. The cause
was not one swallowed exception — it was that FAILURE COULD NOT BE RECORDED:

  * migration 024 documents the transition ``pending -> running -> failed``
  * migration 024 defines ``sampler_jobs.error TEXT``
  * ``ALLOWED_EVENT_TYPES`` already reserved ``sampler_job_failed``
  * ...and NO code ever wrote any of the three.

All 1884 production rows had ``error IS NULL`` — not because nothing failed,
but because the failed leg was never implemented. Meanwhile the reset path
recycled jobs ``running -> pending`` with no budget, so a job whose backend was
gone could cycle forever while its ``updated_at`` kept moving.

These tests pin the missing leg, the retry budget, and the staleness signal.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from translation_monitor import (
    SAMPLER_JOB_RESET_CAP,
    mark_sampler_job_failed,
    reset_stuck_sampler_jobs,
    sampler_completion_age_days,
)
from translation_monitor.events import recent_events

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "library" / "backend" / "schema.sql"

pytestmark = pytest.mark.requires_repo_source


@pytest.fixture
def db(tmp_path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(tmp_path / "monitor.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (1, 't', '/tmp/t')")
    conn.commit()
    yield conn
    conn.close()


def _job(conn, job_id: int, status: str, *, age_sec: int = 0, done: int = 0) -> int:
    updated = f"datetime('now','-{age_sec} seconds')" if age_sec else "CURRENT_TIMESTAMP"
    conn.execute(
        "INSERT INTO sampler_jobs "  # nosec B608 - test fixture, int-validated literal
        "(id, audiobook_id, locale, status, segments_target, segments_done, updated_at) "
        f"VALUES (?, 1, 'zh-Hans', ?, 12, ?, {updated})",
        (job_id, status, done),
    )
    conn.commit()
    return job_id


class TestFailedTransitionExists:
    """The leg migration 024 documented and nobody implemented."""

    def test_marking_failed_records_status_and_error(self, db):
        job = _job(db, 1, "running")
        mark_sampler_job_failed(db, job, "backend gone", audiobook_id=1)
        row = db.execute("SELECT status, error FROM sampler_jobs WHERE id=?", (job,)).fetchone()
        assert row["status"] == "failed"
        assert row["error"] == "backend gone"

    def test_marking_failed_emits_an_event(self, db):
        job = _job(db, 1, "running")
        mark_sampler_job_failed(db, job, "backend gone", audiobook_id=1)
        events = recent_events(db, limit=10, monitor="sampler", event_type="sampler_job_failed")
        assert len(events) == 1
        assert events[0]["sampler_job_id"] == job

    def test_error_column_is_no_longer_write_only_null(self, db):
        """The exact production symptom: every row's error was NULL."""
        job = _job(db, 1, "running")
        mark_sampler_job_failed(db, job, "why it died", audiobook_id=1)
        nulls = db.execute("SELECT COUNT(*) n FROM sampler_jobs WHERE error IS NULL").fetchone()
        assert nulls["n"] == 0


class TestResetBudget:
    """Without a cap the reset path is an unbounded, invisible retry loop."""

    def _bounce(self, db, job, times):
        for _ in range(times):
            db.execute(
                "UPDATE sampler_jobs SET status='running', "
                "updated_at=datetime('now','-3 hours') WHERE id=?",
                (job,),
            )
            db.commit()
            reset_stuck_sampler_jobs(db)

    def test_job_fails_once_it_exhausts_its_reset_budget(self, db):
        job = _job(db, 1, "running", age_sec=3 * 3600, done=7)
        self._bounce(db, job, SAMPLER_JOB_RESET_CAP + 1)
        row = db.execute("SELECT status, error FROM sampler_jobs WHERE id=?", (job,)).fetchone()
        assert row["status"] == "failed"
        assert "resets without completing" in row["error"]
        assert "7/12" in row["error"], "the error must say how far it got"

    def test_under_budget_still_recycles(self, db):
        job = _job(db, 1, "running", age_sec=3 * 3600)
        self._bounce(db, job, 1)
        row = db.execute("SELECT status FROM sampler_jobs WHERE id=?", (job,)).fetchone()
        assert row["status"] == "pending", "a first stall must still get retried"

    def test_healthy_running_job_untouched(self, db):
        job = _job(db, 1, "running", age_sec=60)
        assert reset_stuck_sampler_jobs(db) == []
        row = db.execute("SELECT status FROM sampler_jobs WHERE id=?", (job,)).fetchone()
        assert row["status"] == "running"


class TestStalenessSignal:
    """`systemctl is-active` said healthy for 11 weeks. Ask the data instead."""

    def test_none_when_nothing_ever_completed(self, db):
        _job(db, 1, "pending")
        assert sampler_completion_age_days(db) is None

    def test_zero_ish_for_a_fresh_completion(self, db):
        _job(db, 1, "complete")
        age = sampler_completion_age_days(db)
        assert age is not None and age < 1

    def test_detects_a_long_gap(self, db):
        db.execute(
            "INSERT INTO sampler_jobs "
            "(id, audiobook_id, locale, status, segments_target, segments_done, updated_at) "
            "VALUES (1, 1, 'zh-Hans', 'complete', 12, 12, datetime('now','-77 days'))"
        )
        db.commit()
        age = sampler_completion_age_days(db)
        assert age is not None and age > 70, "an 11-week gap must be visible as a number"
