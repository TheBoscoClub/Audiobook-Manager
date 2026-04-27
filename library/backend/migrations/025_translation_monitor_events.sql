-- Migration 025: translation_monitor_events audit-trail table (v8.3.9).
--
-- Context:
--   v8.3.9 introduces a two-tier translation monitor:
--     * translation-monitor-live    — every 30s, watches origin='live' segments
--     * translation-monitor-sampler — every 5min, watches sampler_jobs +
--                                      origin in ('sampler','backlog')
--
--   The monitors detect stuck claims, exceeded retry budgets, and (future)
--   spend-cap pauses. Every action they take is recorded to this table for
--   operator audit and post-mortem analysis. Without an audit trail, a
--   reset claim is silent and indistinguishable from "the worker just got
--   to it" — which makes diagnosing repeat offenders impossible.
--
-- Schema:
--   id              — PK, autoincrement
--   monitor         — 'live' | 'sampler' — which tier emitted this event
--   event_type      — short string, see TYPE TAXONOMY below
--   audiobook_id    — nullable; populated when event is book-scoped
--   segment_id      — nullable; populated when event resets/touches a row
--   sampler_job_id  — nullable; populated when event acts on a sampler_job
--   worker_id       — nullable; the worker_id that was holding the claim,
--                     for tracing back to a misbehaving GPU worker
--   details         — JSON blob with event-specific context
--   created_at      — timestamp; ISO 8601 via CURRENT_TIMESTAMP
--
-- TYPE TAXONOMY (extend as needed; keep names short and predicate-style):
--   claim_reset           — stuck claim cleared, segment back to pending
--   retry_exceeded        — retry_count >= cap, segment marked failed
--   sampler_job_failed    — sampler_job marked failed (retry budget exhausted)
--   sampler_job_reset     — sampler_job stuck-running reset to pending
--   spend_pause_book      — per-book spend cap hit, sampler_job paused
--   spend_pause_global    — daily/global spend cap hit, sampler queue paused
--   live_age_alert        — live segment >2 min old, escalation logged
--   capacity_warning      — all instances saturated AND queue still growing
--   gpu_probe_failed      — GPU instance health probe returned non-OK
--
-- Local-only audit table. Not exported by transfer.py: events are diagnostic
-- ephemera tied to a specific environment's worker_id and timestamps; they
-- have no value moving between dev/qa/prod. Indexes target the two operator
-- queries we expect to run: "what happened recently?" and "find all events
-- of type X" for trend analysis.

CREATE TABLE IF NOT EXISTS translation_monitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor TEXT NOT NULL CHECK (monitor IN ('live','sampler')),
    event_type TEXT NOT NULL,
    audiobook_id INTEGER,
    segment_id INTEGER,
    sampler_job_id INTEGER,
    worker_id TEXT,
    details TEXT,                       -- JSON blob; NULL allowed for trivial events
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tm_events_created
    ON translation_monitor_events(created_at);
CREATE INDEX IF NOT EXISTS idx_tm_events_type
    ON translation_monitor_events(event_type);
CREATE INDEX IF NOT EXISTS idx_tm_events_monitor_created
    ON translation_monitor_events(monitor, created_at);
