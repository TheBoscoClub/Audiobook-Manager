"""Reconcile must see a sampler job that was scheduled but never started.

Found 2026-09-13 while backfilling samples. Two books carried a ``pending``
sampler_jobs row with zero ``streaming_segments``, annotated by an August
backfill with "requeue if audio translation is re-enabled" — and no mechanism
existed that could ever requeue them:

* the worker claims ``streaming_segments`` rows, and there were none;
* the admin reset path acts on ``failed`` rows, and these read ``pending``;
* the reconciler skipped them because a job row existed at all.

The job row records that work was SCHEDULED and was being read as proof work
HAPPENED. Those agree until scheduling itself fails, which is exactly when the
answer matters.

The predicate stays narrow on purpose. ``complete`` is done and ``failed``
belongs to the admin by this script's documented contract; only a non-terminal
row with nothing anywhere to show for it is stranded.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sampler-reconcile.py"
_SPEC = importlib.util.spec_from_file_location("sampler_reconcile", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
reconcile = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(reconcile)

TARGETS = ["zh-Hans"]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE sampler_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "audiobook_id INTEGER NOT NULL, locale TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', segments_target INTEGER NOT NULL DEFAULT 0, "
        "segments_done INTEGER NOT NULL DEFAULT 0, error TEXT, "
        "UNIQUE(audiobook_id, locale))"
    )
    c.execute(
        "CREATE TABLE streaming_segments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "audiobook_id INTEGER, chapter_index INT, segment_index INT, locale TEXT, "
        "state TEXT, origin TEXT)"
    )
    yield c
    c.close()


def _job(conn, *, status, book=1, locale="zh-Hans"):
    conn.execute(
        "INSERT INTO sampler_jobs (audiobook_id, locale, status, segments_target) "
        "VALUES (?, ?, ?, 5)",
        (book, locale, status),
    )
    conn.commit()


def _segments(conn, *, n, state="pending", book=1, locale="zh-Hans", origin="sampler"):
    for i in range(n):
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, origin) "
            "VALUES (?, 0, ?, ?, ?, ?)",
            (book, i, locale, state, origin),
        )
    conn.commit()


class TestStrandedJobsAreRequeued:
    def test_pending_job_with_no_segments_is_stranded(self, conn):
        """The live case: scheduled in April, never enqueued, invisible since."""
        _job(conn, status="pending")
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]

    def test_running_job_with_no_segments_is_stranded(self, conn):
        """A worker died between marking `running` and inserting segments."""
        _job(conn, status="running")
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]

    def test_no_job_row_at_all_still_needs_sampling(self, conn):
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]


class TestWorkInFlightIsLeftAlone:
    def test_pending_job_with_queued_segments_is_not_touched(self, conn):
        """Segments exist, so a worker can reach it. Re-enqueueing would
        duplicate live work — the opposite failure."""
        _job(conn, status="pending")
        _segments(conn, n=5)
        assert reconcile._pending_locales(conn, 1, TARGETS) == []

    def test_completed_segments_count_as_work_to_show(self, conn):
        _job(conn, status="running")
        _segments(conn, n=5, state="completed")
        assert reconcile._pending_locales(conn, 1, TARGETS) == []


class TestTerminalJobsKeepTheirDocumentedContract:
    def test_complete_is_never_requeued(self, conn):
        """Samples land in chapter_subtitles and the segments are pruned, so a
        finished book legitimately has zero segments. Requeueing on emptiness
        alone would re-translate the entire done corpus."""
        _job(conn, status="complete")
        assert reconcile._pending_locales(conn, 1, TARGETS) == []

    def test_failed_stays_with_the_admin(self, conn):
        """This script documents failures as admin-reset-only; a reconciler
        that silently retried them would spend GPU time on books a human
        already looked at and set aside."""
        _job(conn, status="failed")
        assert reconcile._pending_locales(conn, 1, TARGETS) == []


class TestScoping:
    def test_another_book_s_segments_do_not_count(self, conn):
        _job(conn, status="pending", book=1)
        _segments(conn, n=5, book=2)
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]

    def test_another_locale_s_segments_do_not_count(self, conn):
        _job(conn, status="pending", locale="zh-Hans")
        _segments(conn, n=5, locale="ja")
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]

    def test_live_playback_segments_do_not_count_as_sampler_work(self, conn):
        """A listener streaming the book creates origin='live' rows. Those are
        somebody else's queue and say nothing about the sampler job."""
        _job(conn, status="pending")
        _segments(conn, n=5, origin="live")
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]

    def test_only_requested_targets_are_returned(self, conn):
        _job(conn, status="pending", locale="ja")
        assert reconcile._pending_locales(conn, 1, TARGETS) == ["zh-Hans"]


class TestOutcomeClassification:
    """`complete` became a success outcome when enqueue_sampler learned to
    credit existing translations. The classifier's fall-through counts any
    unrecognised status as a failure — a safe default that turned two real
    successes into `failed=2` in the operator's summary."""

    @staticmethod
    def _classify(status, **extra):
        def fake_enqueue(*_a, **_k):
            return {"status": status, **extra}

        return reconcile._enqueue_one_locale(None, fake_enqueue, 1, "zh-Hans", [600.0])

    def test_already_satisfied_is_not_reported_as_a_failure(self):
        assert self._classify("complete", segments_done=12, segments_target=12) == "satisfied"

    def test_a_real_enqueue_is_still_enqueued(self):
        assert self._classify("running", segments_target=12) == "enqueued"

    def test_an_unknown_status_still_fails_loudly(self):
        """The fall-through must stay strict — a status nobody anticipated is
        a fault, not something to wave through as success."""
        assert self._classify("wedged") == "failed"

    def test_an_error_result_still_fails(self):
        assert self._classify("error", reason="empty sampler scope") == "failed"
