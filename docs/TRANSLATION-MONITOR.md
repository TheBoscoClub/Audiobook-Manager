# Translation Monitor — Two-Tier Stuck-Claim / Retry-Budget Watchdog

**Introduced**: v8.3.9
**Audience**: operators debugging stalled translations or auditing worker behaviour

The translation monitor automatically resets translation jobs that are stuck in claimed/running states beyond their expected duration. Without it, a crashed or disconnected worker can leave a `streaming_segments` row claimed indefinitely, blocking subsequent workers from picking it up and stalling the queue.

## Architecture

Two systemd-timer-driven oneshot scripts, each handling one priority class:

| Tier | Cadence | Scope |
|------|---------|-------|
| `audiobook-translation-monitor-live.timer` | every 30s | `streaming_segments` rows with `origin='live'` (priority 0/1) |
| `audiobook-translation-monitor-sampler.timer` | every 5min | `sampler_jobs` + `streaming_segments` with `origin in ('sampler','backlog')` |

The cadences are tuned to the cost of a missed detection on each tier. Live work has a human waiting; a stuck claim that survives one 30s tick costs at most one segment's worth of inference time. Sampler/backlog work has no human deadline — the cost of a 5-minute detection lag is negligible compared to a 2-hour stuck claim.

Why oneshot, not a daemon? A timer-driven oneshot has the simplest possible failure model: a crashed run on tick N is auto-recovered by the next timer fire. No restart-storm logic, no PID files, no signal handlers. The 30s cadence costs roughly 1 second of CPU per minute on the host — negligible.

## What each tier detects + fixes

### Live tier (every 30s)

1. **Stuck claim reset** — `state IN ('processing','claimed') AND started_at >60s ago` → clear `worker_id`, `started_at`, set `state='pending'` so the next worker poll picks it up.
2. **Retry budget sweep** — `retry_count >= 3 AND state NOT IN ('failed','completed')` → mark `state='failed'` with `error='retry budget exhausted (monitor)'`. This is a second-line defence; the worker is supposed to do this on its 4th attempt, but a crash mid-handler can leave the row in `processing` with the budget already exhausted.
3. **Live age alert** — `origin='live' AND state IN ('pending','processing','claimed') AND age >LIVE_AGE_ALERT_SEC` → emit `live_age_alert` event (informational only, no state mutation). Distinct from #1: that catches *worker-stuck* claims; this catches *queue-depth* problems where a healthy worker is just busy with prior work. One alert per segment per `cooldown_sec` (default 1h) to prevent log spam.
4. **Capacity warning** — pending live segments exceed `LIVE_PENDING_PRESSURE_THRESHOLD` AND at least one worker is currently active → emit one `capacity_warning` event. Two-condition gate prevents false-fire when the system is fully idle (a large pending count with no active workers is a different failure mode). Idempotent over `CAPACITY_WARNING_COOLDOWN_SEC` (default 5min).
5. **Backend instance health probe** *(stub in v8.3.9, planned for a future release)* — query the configured inference backend for instance health. Currently returns `any_healthy=True` so call sites are stable.

### Sampler tier (every 5 min)

1. **Stuck segment-claim reset** — same as live but for `origin IN ('sampler','backlog')` and a 2-hour ceiling.
2. **Stuck `sampler_jobs` reset** — `status='running' AND updated_at >2h ago` → flip back to `pending`. `segments_done` is preserved so the next sampler-daemon sweep continues where the dead worker left off.
3. **Retry budget sweep** — same as live but for sampler/backlog origins.
4. *(Planned for a future release)* per-book `spent_cents` aggregation + per-book auto-pause on cap breach. Useful for installations that pay per-inference; a no-op for installations using local hardware.
5. *(Planned for a future release)* daily total spend cap + global queue pause.

## Audit trail — `translation_monitor_events`

Every action either tier takes is logged to the `translation_monitor_events` table. Without an audit trail, a reset is silent and indistinguishable from "the worker just got to it" — which makes diagnosing repeat offenders impossible.

Schema (see `library/backend/migrations/025_translation_monitor_events.sql`):

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | autoincrement |
| `monitor` | TEXT | `'live'` or `'sampler'` |
| `event_type` | TEXT | predicate-style; see taxonomy below |
| `audiobook_id` | INTEGER | nullable; book-scoped events |
| `segment_id` | INTEGER | nullable; segment-scoped events |
| `sampler_job_id` | INTEGER | nullable; job-scoped events |
| `worker_id` | TEXT | nullable; the worker that was holding the claim |
| `details` | TEXT | JSON blob with event-specific context |
| `created_at` | TIMESTAMP | ISO 8601 via `CURRENT_TIMESTAMP` |

### Event type taxonomy

Keep names short and predicate-style. Extend the `ALLOWED_EVENT_TYPES` set in `library/translation_monitor/events.py` when adding a new type — the writer rejects unknown types with `ValueError` to surface typos at test time.

| Event type | Tier | Meaning |
|------------|------|---------|
| `claim_reset` | live, sampler | A stuck claim was cleared |
| `retry_exceeded` | live, sampler | A segment past `retry_count >= 3` was marked failed |
| `sampler_job_reset` | sampler | A `sampler_jobs` row stuck in `running` was reset to `pending` |
| `sampler_job_failed` | sampler | A `sampler_jobs` row was marked failed (retry budget exhausted) |
| `live_age_alert` | live | A live segment past `LIVE_AGE_ALERT_SEC` (default 120s) was logged for escalation |
| `capacity_warning` | live | Pending live queue exceeds `LIVE_PENDING_PRESSURE_THRESHOLD` while workers are active |
| `spend_pause_book` *(future)* | sampler | A book's sampler job auto-paused on per-book cap |
| `spend_pause_global` *(future)* | sampler | The whole sampler queue was auto-paused on daily cap |
| `backend_probe_failed` *(future)* | live, sampler | Inference-backend health probe returned non-OK |

## Reading the audit trail

```sql
-- Last 50 events (any tier, any type)
SELECT created_at, monitor, event_type, audiobook_id, segment_id, worker_id, details
  FROM translation_monitor_events
  ORDER BY id DESC LIMIT 50;

-- All retry-exhausted events in the last 24h
SELECT * FROM translation_monitor_events
  WHERE event_type = 'retry_exceeded'
    AND created_at >= datetime('now', '-1 day')
  ORDER BY id DESC;

-- Per-worker reset frequency (find a misbehaving worker)
SELECT worker_id, COUNT(*) AS resets
  FROM translation_monitor_events
  WHERE event_type = 'claim_reset'
    AND created_at >= datetime('now', '-1 day')
  GROUP BY worker_id
  ORDER BY resets DESC;
```

The Python helper `library.translation_monitor.events.recent_events(conn, limit=50, monitor=None, event_type=None)` returns the same data as `sqlite3.Row` objects.

## Tuning thresholds

Thresholds live as module constants in `library/translation_monitor/probe.py`. Edit and redeploy via `upgrade.sh`:

| Constant | Default | Purpose |
|----------|---------|---------|
| `LIVE_CLAIM_TIMEOUT_SEC` | 60 | Maximum age of a live segment claim before reset |
| `LIVE_AGE_ALERT_SEC` | 120 | Maximum live-segment queue age before logging a `live_age_alert` |
| `LIVE_PENDING_PRESSURE_THRESHOLD` | 50 | Pending live segments above this floor trigger `capacity_warning` (when workers are also active) |
| `CAPACITY_WARNING_COOLDOWN_SEC` | 300 (5min) | Idempotency window for `capacity_warning` events |
| `SAMPLER_CLAIM_TIMEOUT_SEC` | 7200 (2h) | Maximum age of a sampler/backlog claim before reset |
| `SAMPLER_JOB_RUNNING_TIMEOUT_SEC` | 7200 (2h) | Maximum age of a `sampler_jobs.status='running'` row before reset |
| `RETRY_CAP` | 3 | Retry budget — segments past this are marked failed |

The defaults are deliberately conservative on the live tier — the cost of a false negative (stuck claim survives one tick) is at most one segment's worth of inference time, while the cost of a false positive (a healthy slow worker has its claim reset) is at most one redundant inference. The asymmetry favours aggressive resets.

## Operator commands

```bash
# Check timer status
sudo systemctl status audiobook-translation-monitor-live.timer
sudo systemctl status audiobook-translation-monitor-sampler.timer

# Force an immediate run (bypassing the timer)
sudo systemctl start audiobook-translation-monitor-live.service
sudo systemctl start audiobook-translation-monitor-sampler.service

# Tail the journal for either tier
sudo journalctl -u audiobook-translation-monitor-live.service -f
sudo journalctl -u audiobook-translation-monitor-sampler.service -f

# Disable temporarily (e.g. to rule out the monitor as a cause of weirdness)
sudo systemctl stop audiobook-translation-monitor-live.timer
sudo systemctl stop audiobook-translation-monitor-sampler.timer
```

## Idempotency contract

Every reset in this system is idempotent: running the monitor twice in a row, with no intervening worker activity, produces zero events on the second run. This is what makes the 30s/5min cadence safe — no risk of double-counting, no risk of cascading resets, no race conditions with the worker.

The idempotency is enforced by the SQL predicates: each `UPDATE` is guarded by `WHERE state IN ('processing','claimed')`, so once a row is back to `pending` the next pass leaves it alone.

## Files

| Path | Role |
|------|------|
| `library/translation_monitor/__init__.py` | Public package surface |
| `library/translation_monitor/db.py` | Connection helper, path resolution, schema gate |
| `library/translation_monitor/events.py` | Audit-trail writer + reader |
| `library/translation_monitor/probe.py` | Detector + reset primitives |
| `scripts/translation-monitor-live.py` | Live tier oneshot |
| `scripts/translation-monitor-sampler.py` | Sampler tier oneshot |
| `systemd/audiobook-translation-monitor-live.service` | Live oneshot unit |
| `systemd/audiobook-translation-monitor-live.timer` | Live timer (every 30s) |
| `systemd/audiobook-translation-monitor-sampler.service` | Sampler oneshot unit |
| `systemd/audiobook-translation-monitor-sampler.timer` | Sampler timer (every 5min) |
| `library/backend/migrations/025_translation_monitor_events.sql` | Schema migration |
| `data-migrations/009_translation_monitor_events.sh` | Upgrade-time data migration |
| `library/tests/test_translation_monitor.py` | Test suite (33 tests) |

## Forthcoming

Items below are anticipated but not yet implemented. Order is suggestive, not committed.

* **Per-book `spent_cents` aggregation** joining `streaming_segments` to a (yet-to-be-added) `translation_costs` table; auto-pause via `spend_pause_book` event when a per-book cap is exceeded. Useful for installations paying per-inference; a no-op for installations using local hardware.
* **Daily/weekly global spend cap** with `spend_pause_global` event and a scheduler flag for global queue pause. Provider-agnostic — operators configure the cap; the cost source is whatever telemetry their inference backend exposes.
* **Real backend health probe** replacing the v8.3.9 stub. Provider-agnostic interface so operators can plug in whatever inference backend they have configured. Emits `backend_probe_failed` events.
* **Operator API endpoint** exposing `recent_events()` to the admin UI so reading the audit trail doesn't require SQL.
