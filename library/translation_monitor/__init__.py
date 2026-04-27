"""library.translation_monitor — two-tier translation monitor framework (v8.3.9).

Detects and resets translation jobs that are stuck in claimed/running states
beyond their expected duration so the queue keeps progressing instead of
stalling on a worker that crashed or lost connectivity. Also flips
retry-budget-exhausted segments to ``failed`` so they exit the work queue
cleanly instead of being retried indefinitely. Implemented as two
systemd-timer-driven oneshot scripts with different cadences:

    translation-monitor-live    — every 30s   — origin='live', priority 0/1
    translation-monitor-sampler — every 5min  — sampler_jobs + origin in ('sampler','backlog')

Both share the primitives in this package:

    db        — sqlite connection helper, schema-aware path resolution
    probe     — pure-function detectors (stuck claims, retry budget, etc.)
                and idempotent reset operations
    events    — append-only audit-trail writer to translation_monitor_events

The scripts in scripts/translation-monitor-{live,sampler}.py wire these
primitives into a single one-pass-per-tick loop. They never run as daemons —
the systemd timer fires once per cadence and the script exits cleanly. This
keeps the failure-mode trivial: a crashed monitor on tick N is auto-recovered
by the next timer fire.

The package itself contains no I/O — all DB access goes through
:func:`library.translation_monitor.db.connect` so tests can swap in a
tmp_path SQLite DB without touching the real one.
"""

from translation_monitor.db import connect  # noqa: F401
from translation_monitor.events import log_event  # noqa: F401
from translation_monitor.probe import (  # noqa: F401
    CAPACITY_WARNING_COOLDOWN_SEC,
    LIVE_AGE_ALERT_SEC,
    LIVE_CLAIM_TIMEOUT_SEC,
    LIVE_PENDING_PRESSURE_THRESHOLD,
    RETRY_CAP,
    SAMPLER_CLAIM_TIMEOUT_SEC,
    SAMPLER_JOB_RUNNING_TIMEOUT_SEC,
    alert_capacity_pressure,
    alert_old_live_segments,
    probe_gpu_instance_health,
    reset_stuck_live_claims,
    reset_stuck_sampler_claims,
    reset_stuck_sampler_jobs,
    sweep_retry_exhausted_segments,
)

__all__ = [
    "CAPACITY_WARNING_COOLDOWN_SEC",
    "LIVE_AGE_ALERT_SEC",
    "LIVE_CLAIM_TIMEOUT_SEC",
    "LIVE_PENDING_PRESSURE_THRESHOLD",
    "RETRY_CAP",
    "SAMPLER_CLAIM_TIMEOUT_SEC",
    "SAMPLER_JOB_RUNNING_TIMEOUT_SEC",
    "alert_capacity_pressure",
    "alert_old_live_segments",
    "connect",
    "log_event",
    "probe_gpu_instance_health",
    "reset_stuck_live_claims",
    "reset_stuck_sampler_claims",
    "reset_stuck_sampler_jobs",
    "sweep_retry_exhausted_segments",
]
