"""Tests for the v8.3.9 two-tier translation monitor.

Covers:
    * Live tier: stuck-claim reset (>60s)
    * Sampler tier: stuck-claim reset (>2h)
    * Sampler tier: stuck sampler_jobs reset (running >2h)
    * Both tiers: retry-cap sweep (retry_count >= 3)
    * Idempotency — running a monitor twice produces zero events on the
      second pass
    * Audit-trail correctness — every reset writes a typed event row

The monitor primitives are exercised against a fresh tmp-path SQLite DB
loaded from the canonical schema.sql, never against a real audiobooks DB.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from translation_monitor import (
    CAPACITY_WARNING_COOLDOWN_SEC,
    LIVE_AGE_ALERT_SEC,
    LIVE_CLAIM_TIMEOUT_SEC,
    LIVE_PENDING_PRESSURE_THRESHOLD,
    RETRY_CAP,
    SAMPLER_CLAIM_TIMEOUT_SEC,
    SAMPLER_JOB_RUNNING_TIMEOUT_SEC,
    alert_capacity_pressure,
    alert_old_live_segments,
    log_event,
    reset_stuck_live_claims,
    reset_stuck_sampler_claims,
    reset_stuck_sampler_jobs,
    sweep_retry_exhausted_segments,
)
from translation_monitor.events import ALLOWED_EVENT_TYPES, ALLOWED_MONITORS, recent_events

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "library" / "backend" / "schema.sql"


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    """Fresh DB with the canonical schema applied."""
    conn = sqlite3.connect(str(tmp_path / "monitor.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    # Insert a minimal audiobook so FK CASCADE doesn't reject test rows.
    conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (1, 't', '/tmp/t')")
    conn.commit()
    yield conn
    conn.close()


def _insert_segment(
    conn: sqlite3.Connection,
    *,
    seg_id: int,
    state: str = "processing",
    priority: int = 0,
    origin: str = "live",
    worker_id: str | None = "worker-A",
    retry_count: int = 0,
    started_at_offset_sec: int = 0,
    created_at_offset_sec: int | None = None,
) -> int:
    """Insert a streaming_segments row.

    started_at_offset_sec=0 means started right now; positive values are
    older — used to simulate stuck claims (claim age vs created age).

    created_at_offset_sec=None defaults to CURRENT_TIMESTAMP. Positive
    values are older — used to simulate queue-depth backup (segment has
    been pending in the queue for that many seconds).
    """
    started = (
        f"datetime('now','-{started_at_offset_sec} seconds')"
        if started_at_offset_sec
        else "CURRENT_TIMESTAMP"
    )
    created = (
        f"datetime('now','-{created_at_offset_sec} seconds')"
        if created_at_offset_sec
        else "CURRENT_TIMESTAMP"
    )
    sql_insert_seg = (
        f"INSERT INTO streaming_segments "  # nosec B608 - test fixture; offsets are int-validated literals
        f"(id, audiobook_id, chapter_index, segment_index, locale, "
        f" state, priority, origin, worker_id, retry_count, "
        f" started_at, created_at) "
        f"VALUES (?, 1, 0, ?, 'zh-Hans', ?, ?, ?, ?, ?, {started}, {created})"
    )
    conn.execute(
        sql_insert_seg,
        (seg_id, seg_id, state, priority, origin, worker_id, retry_count),
    )
    conn.commit()
    return seg_id


def _insert_sampler_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    status: str = "running",
    updated_at_offset_sec: int = 0,
) -> int:
    updated = (
        f"datetime('now','-{updated_at_offset_sec} seconds')"
        if updated_at_offset_sec
        else "CURRENT_TIMESTAMP"
    )
    sql_insert_job = (
        f"INSERT INTO sampler_jobs "  # nosec B608 - test fixture; offsets are int-validated literals
        f"(id, audiobook_id, locale, status, segments_target, segments_done, updated_at) "
        f"VALUES (?, 1, 'zh-Hans', ?, 12, 0, {updated})"
    )
    conn.execute(
        sql_insert_job,
        (job_id, status),
    )
    conn.commit()
    return job_id


# ─── Events module ─────────────────────────────────────────────────────────


def test_event_taxonomy_includes_required_types():
    required = {"claim_reset", "retry_exceeded", "sampler_job_failed", "sampler_job_reset"}
    assert required.issubset(ALLOWED_EVENT_TYPES)
    assert ALLOWED_MONITORS == {"live", "sampler"}


def test_log_event_writes_row(db):
    rid = log_event(
        db,
        monitor="live",
        event_type="claim_reset",
        audiobook_id=1,
        segment_id=42,
        worker_id="worker-X",
        details={"timeout_sec": 60},
    )
    assert rid is not None
    rows = recent_events(db, limit=10)
    assert len(rows) == 1
    assert rows[0]["monitor"] == "live"
    assert rows[0]["event_type"] == "claim_reset"
    assert rows[0]["segment_id"] == 42
    assert rows[0]["worker_id"] == "worker-X"
    assert '"timeout_sec": 60' in rows[0]["details"]


def test_log_event_rejects_unknown_monitor(db):
    with pytest.raises(ValueError, match="unknown monitor"):
        log_event(db, monitor="bogus", event_type="claim_reset")


def test_log_event_rejects_unknown_event_type(db):
    with pytest.raises(ValueError, match="unknown event_type"):
        log_event(db, monitor="live", event_type="not_in_taxonomy")


# ─── Live tier: stuck-claim reset ──────────────────────────────────────────


def test_live_claim_reset_clears_stuck_claim(db):
    # 90s old > 60s threshold
    seg = _insert_segment(db, seg_id=1, started_at_offset_sec=90)
    affected = reset_stuck_live_claims(db)
    assert affected == [seg]

    row = db.execute(
        "SELECT state, worker_id, started_at FROM streaming_segments WHERE id=?", (seg,)
    ).fetchone()
    assert row["state"] == "pending"
    assert row["worker_id"] is None
    assert row["started_at"] is None

    events = recent_events(db, limit=10, monitor="live")
    assert len(events) == 1
    assert events[0]["event_type"] == "claim_reset"
    assert events[0]["segment_id"] == seg


def test_live_claim_reset_leaves_fresh_claim_alone(db):
    # 10s old << 60s threshold
    _insert_segment(db, seg_id=1, started_at_offset_sec=10)
    affected = reset_stuck_live_claims(db)
    assert affected == []

    row = db.execute("SELECT state, worker_id FROM streaming_segments WHERE id=1").fetchone()
    assert row["state"] == "processing"
    assert row["worker_id"] == "worker-A"


def test_live_claim_reset_ignores_sampler_origin(db):
    # 90s old but origin=sampler → live monitor must skip
    # Sampler rows must have priority>=2 per the trigger.
    _insert_segment(db, seg_id=1, started_at_offset_sec=90, origin="sampler", priority=2)
    affected = reset_stuck_live_claims(db)
    assert affected == []


def test_live_claim_reset_idempotent(db):
    _insert_segment(db, seg_id=1, started_at_offset_sec=90)
    first = reset_stuck_live_claims(db)
    second = reset_stuck_live_claims(db)
    assert first == [1]
    assert second == []  # already pending — nothing more to do
    # Only one event written across both passes
    events = recent_events(db, limit=10, monitor="live")
    assert len(events) == 1


def test_live_claim_reset_uses_default_threshold():
    # Sanity check the constant is in the right ballpark
    assert 30 <= LIVE_CLAIM_TIMEOUT_SEC <= 300


# ─── Sampler tier: stuck-claim reset ───────────────────────────────────────


def test_sampler_claim_reset_clears_stuck_claim(db):
    # 3h old > 2h threshold
    seg = _insert_segment(
        db, seg_id=1, started_at_offset_sec=3 * 3600, origin="sampler", priority=2
    )
    affected = reset_stuck_sampler_claims(db)
    assert affected == [seg]

    row = db.execute(
        "SELECT state, worker_id FROM streaming_segments WHERE id=?", (seg,)
    ).fetchone()
    assert row["state"] == "pending"
    assert row["worker_id"] is None


def test_sampler_claim_reset_leaves_fresh_claim_alone(db):
    _insert_segment(
        db, seg_id=1, started_at_offset_sec=600, origin="sampler", priority=2  # 10min, way under 2h
    )
    affected = reset_stuck_sampler_claims(db)
    assert affected == []


def test_sampler_claim_reset_skips_live_origin(db):
    # 3h old but origin=live → sampler monitor must skip; live tier owns it
    _insert_segment(db, seg_id=1, started_at_offset_sec=3 * 3600, origin="live")
    affected = reset_stuck_sampler_claims(db)
    assert affected == []


def test_sampler_claim_threshold_constant():
    assert SAMPLER_CLAIM_TIMEOUT_SEC == 2 * 3600


# ─── Sampler tier: stuck sampler_jobs reset ────────────────────────────────


def test_sampler_job_reset_running_orphan(db):
    job = _insert_sampler_job(db, job_id=1, status="running", updated_at_offset_sec=3 * 3600)
    affected = reset_stuck_sampler_jobs(db)
    assert affected == [job]
    row = db.execute("SELECT status FROM sampler_jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "pending"

    events = recent_events(db, limit=10, monitor="sampler", event_type="sampler_job_reset")
    assert len(events) == 1
    assert events[0]["sampler_job_id"] == job


def test_sampler_job_reset_leaves_fresh_running_alone(db):
    _insert_sampler_job(db, job_id=1, status="running", updated_at_offset_sec=600)
    affected = reset_stuck_sampler_jobs(db)
    assert affected == []


def test_sampler_job_reset_skips_complete_status(db):
    _insert_sampler_job(db, job_id=1, status="complete", updated_at_offset_sec=3 * 3600)
    affected = reset_stuck_sampler_jobs(db)
    assert affected == []


def test_sampler_job_threshold_constant():
    assert SAMPLER_JOB_RUNNING_TIMEOUT_SEC == 2 * 3600


# ─── Retry-budget sweep (both tiers) ───────────────────────────────────────


def test_retry_sweep_marks_exhausted_live_segments_failed(db):
    seg = _insert_segment(
        db, seg_id=1, state="processing", retry_count=RETRY_CAP, started_at_offset_sec=10
    )
    affected = sweep_retry_exhausted_segments(db, monitor="live", origins=("live",))
    assert affected == [seg]
    row = db.execute("SELECT state, error FROM streaming_segments WHERE id=?", (seg,)).fetchone()
    assert row["state"] == "failed"
    assert "retry budget exhausted" in row["error"]

    events = recent_events(db, limit=10, monitor="live", event_type="retry_exceeded")
    assert len(events) == 1


def test_retry_sweep_only_targets_specified_origins(db):
    # Live row at retry_cap, but we sweep with origins=('sampler',) — must skip.
    _insert_segment(db, seg_id=1, state="processing", retry_count=RETRY_CAP)
    affected = sweep_retry_exhausted_segments(db, monitor="sampler", origins=("sampler", "backlog"))
    assert affected == []


def test_retry_sweep_skips_already_completed(db):
    _insert_segment(db, seg_id=1, state="completed", retry_count=RETRY_CAP)
    affected = sweep_retry_exhausted_segments(db, monitor="live", origins=("live",))
    assert affected == []


def test_retry_sweep_skips_already_failed(db):
    _insert_segment(db, seg_id=1, state="failed", retry_count=RETRY_CAP)
    affected = sweep_retry_exhausted_segments(db, monitor="live", origins=("live",))
    assert affected == []


def test_retry_sweep_idempotent(db):
    _insert_segment(db, seg_id=1, state="processing", retry_count=RETRY_CAP)
    first = sweep_retry_exhausted_segments(db, monitor="live", origins=("live",))
    second = sweep_retry_exhausted_segments(db, monitor="live", origins=("live",))
    assert first == [1]
    assert second == []
    events = recent_events(db, limit=10, event_type="retry_exceeded")
    assert len(events) == 1  # only first pass wrote


# ─── End-to-end: full live pass produces zero events on no-op DB ──────────


def test_full_live_pass_no_events_on_clean_db(db):
    """A monitor pass against a clean DB must NOT write any events."""
    reset_stuck_live_claims(db)
    sweep_retry_exhausted_segments(db, monitor="live", origins=("live",))
    assert recent_events(db, limit=10) == []


def test_full_sampler_pass_no_events_on_clean_db(db):
    reset_stuck_sampler_claims(db)
    reset_stuck_sampler_jobs(db)
    sweep_retry_exhausted_segments(db, monitor="sampler", origins=("sampler", "backlog"))
    assert recent_events(db, limit=10) == []


# ─── Audit-trail event details preserve forensic context ──────────────────


def test_claim_reset_event_records_worker_and_retry(db):
    _insert_segment(
        db, seg_id=7, worker_id="worker-zorblax", retry_count=2, started_at_offset_sec=120
    )
    reset_stuck_live_claims(db)
    events = recent_events(db, limit=1)
    assert events[0]["worker_id"] == "worker-zorblax"
    assert '"retry_count": 2' in events[0]["details"]
    assert '"timeout_sec":' in events[0]["details"]


# ─── Sanity: monitor scripts import cleanly ────────────────────────────────


def test_live_script_imports_cleanly():
    """The live-tier script must import without side effects (no I/O at import)."""
    import importlib.util

    path = PROJECT_ROOT / "scripts" / "translation-monitor-live.py"
    spec = importlib.util.spec_from_file_location("translation_monitor_live_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.main)


def test_sampler_script_imports_cleanly():
    import importlib.util

    path = PROJECT_ROOT / "scripts" / "translation-monitor-sampler.py"
    spec = importlib.util.spec_from_file_location("translation_monitor_sampler_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.main)


# ─── systemd unit + manifest wiring guard ─────────────────────────────────


def test_systemd_units_are_present():
    """Guard against the 8.3.1 orphan-script regression class."""
    sysd = PROJECT_ROOT / "systemd"
    for unit in (
        "audiobook-translation-monitor-live.service",
        "audiobook-translation-monitor-live.timer",
        "audiobook-translation-monitor-sampler.service",
        "audiobook-translation-monitor-sampler.timer",
    ):
        assert (sysd / unit).is_file(), f"missing systemd unit: {unit}"


def test_install_manifest_lists_monitor_units():
    """install-manifest.sh must include the monitor units in CANONICAL_UNITS."""
    manifest = (PROJECT_ROOT / "scripts" / "install-manifest.sh").read_text()
    for unit in (
        "audiobook-translation-monitor-live.service",
        "audiobook-translation-monitor-live.timer",
        "audiobook-translation-monitor-sampler.service",
        "audiobook-translation-monitor-sampler.timer",
    ):
        assert unit in manifest, f"install-manifest.sh missing: {unit}"


def test_install_manifest_lists_monitor_workers():
    """install-manifest.sh must include the monitor scripts in CANONICAL_WORKERS."""
    manifest = (PROJECT_ROOT / "scripts" / "install-manifest.sh").read_text()
    for script in ("translation-monitor-live.py", "translation-monitor-sampler.py"):
        assert script in manifest, f"install-manifest.sh missing worker: {script}"


def test_audiobook_target_wants_monitor_timers():
    """audiobook.target must Wants= the new timers for boot-up activation."""
    target = (PROJECT_ROOT / "systemd" / "audiobook.target").read_text()
    assert "audiobook-translation-monitor-live.timer" in target
    assert "audiobook-translation-monitor-sampler.timer" in target


def test_release_requirements_lists_monitor_table():
    """release-requirements.sh must require translation_monitor_events."""
    req = (PROJECT_ROOT / "scripts" / "release-requirements.sh").read_text()
    assert "translation_monitor_events" in req


def test_data_migration_009_present():
    mig = PROJECT_ROOT / "data-migrations" / "009_translation_monitor_events.sh"
    assert mig.is_file()
    body = mig.read_text()
    assert 'MIN_VERSION="8.3.9"' in body
    assert "translation_monitor_events" in body


# Suppress an unused-import warning on `time` from the helper era; kept for
# future tests that need wall-clock manipulation without sqlite "now".
_ = time


# ─── Live age alert (live_age_alert) ──────────────────────────────────────


def test_live_age_alert_fires_on_old_pending_segment(db):
    _insert_segment(db, seg_id=1, state="pending", worker_id=None, created_at_offset_sec=180)
    alerted = alert_old_live_segments(db)
    assert alerted == [1]
    events = recent_events(db, event_type="live_age_alert")
    assert len(events) == 1
    assert events[0]["segment_id"] == 1
    assert events[0]["monitor"] == "live"


def test_live_age_alert_skips_fresh_pending_segment(db):
    _insert_segment(db, seg_id=2, state="pending", worker_id=None, created_at_offset_sec=30)
    alerted = alert_old_live_segments(db)
    assert alerted == []


def test_live_age_alert_skips_completed_segment(db):
    _insert_segment(db, seg_id=3, state="completed", worker_id="w", created_at_offset_sec=300)
    alerted = alert_old_live_segments(db)
    assert alerted == []


def test_live_age_alert_skips_sampler_origin(db):
    _insert_segment(
        db,
        seg_id=4,
        state="pending",
        origin="sampler",
        priority=2,
        worker_id=None,
        created_at_offset_sec=300,
    )
    alerted = alert_old_live_segments(db)
    assert alerted == []


def test_live_age_alert_idempotent_within_cooldown(db):
    _insert_segment(db, seg_id=5, state="pending", worker_id=None, created_at_offset_sec=180)
    first = alert_old_live_segments(db)
    second = alert_old_live_segments(db)
    assert first == [5]
    assert second == []  # cooldown skipped the duplicate
    events = recent_events(db, event_type="live_age_alert")
    assert len(events) == 1


def test_live_age_alert_fires_for_processing_segment_too(db):
    _insert_segment(db, seg_id=6, state="processing", worker_id="w", created_at_offset_sec=200)
    alerted = alert_old_live_segments(db)
    assert alerted == [6]


def test_live_age_alert_threshold_constant():
    assert LIVE_AGE_ALERT_SEC >= 60  # at least longer than claim timeout


# ─── Capacity warning (capacity_warning) ──────────────────────────────────


def test_capacity_warning_fires_when_threshold_exceeded_and_workers_active(db):
    # Two active workers
    _insert_segment(db, seg_id=100, state="processing", worker_id="w1")
    _insert_segment(db, seg_id=101, state="processing", worker_id="w2")
    # 51 pending (above default threshold of 50)
    for i in range(200, 251):
        _insert_segment(db, seg_id=i, state="pending", worker_id=None)
    rid = alert_capacity_pressure(db)
    assert rid is not None
    events = recent_events(db, event_type="capacity_warning")
    assert len(events) == 1
    assert events[0]["monitor"] == "live"


def test_capacity_warning_skipped_below_threshold(db):
    _insert_segment(db, seg_id=300, state="processing", worker_id="w1")
    for i in range(400, 405):  # only 5 pending
        _insert_segment(db, seg_id=i, state="pending", worker_id=None)
    assert alert_capacity_pressure(db) is None


def test_capacity_warning_skipped_when_no_active_workers(db):
    # 51 pending but no worker doing anything → don't fire (different problem)
    for i in range(500, 551):
        _insert_segment(db, seg_id=i, state="pending", worker_id=None)
    assert alert_capacity_pressure(db) is None


def test_capacity_warning_idempotent_within_cooldown(db):
    _insert_segment(db, seg_id=700, state="processing", worker_id="w1")
    for i in range(800, 851):
        _insert_segment(db, seg_id=i, state="pending", worker_id=None)
    first = alert_capacity_pressure(db)
    second = alert_capacity_pressure(db)
    assert first is not None
    assert second is None  # cooldown blocked duplicate
    events = recent_events(db, event_type="capacity_warning")
    assert len(events) == 1


def test_capacity_warning_records_pending_and_worker_counts(db):
    _insert_segment(db, seg_id=900, state="processing", worker_id="w1")
    _insert_segment(db, seg_id=901, state="processing", worker_id="w2")
    for i in range(1000, 1052):
        _insert_segment(db, seg_id=i, state="pending", worker_id=None)
    alert_capacity_pressure(db)
    events = recent_events(db, event_type="capacity_warning")
    import json as _json

    details = _json.loads(events[0]["details"])
    assert details["pending_count"] == 52
    assert details["active_workers"] == 2
    assert details["pending_threshold"] == LIVE_PENDING_PRESSURE_THRESHOLD


def test_capacity_warning_constants():
    assert LIVE_PENDING_PRESSURE_THRESHOLD > 0
    assert CAPACITY_WARNING_COOLDOWN_SEC >= 60
