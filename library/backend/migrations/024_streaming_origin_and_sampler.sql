-- Migration 024: Add `origin` column to streaming_segments and create
-- sampler_jobs table for the 6-minute pretranslation sampler (v8.3.8).
--
-- Context:
--   v8.3.8 introduces a per-book 6-minute sampler: at book ingest and on
--   locale addition, the first 12 segments of chapter 0 are pretranslated
--   per enabled non-EN locale. This serves three purposes:
--     1. Cost control — translate only books users actually commit to
--     2. Library-wide discovery — instant preview in any listener's locale
--     3. GPU cold-start runway — covers the 3-4 min before live buffer can
--        catch up once a user commits to listening
--
-- Priority model:
--   p0 = live cursor buffer (current book only)
--   p1 = live forward chase (current book only)
--   p2 = sampler work (pretranslation of ch0, all books)
--   p3 = backlog / all other bulk work
--   Sampler MUST NEVER enqueue at p0/p1 — enforced by BEFORE INSERT/UPDATE
--   triggers that ABORT on `origin = 'sampler' AND priority < 2`.
--
-- Paired with data-migration 008_streaming_sampler.sh for upgrades.

-- ─── streaming_segments.origin ───────────────────────────────────────────
-- Column-level CHECK constrains values to the known origin set. The
-- cross-column "sampler requires priority >= 2" invariant is enforced by
-- the triggers below (SQLite ALTER TABLE cannot add multi-column CHECK).
ALTER TABLE streaming_segments
    ADD COLUMN origin TEXT NOT NULL DEFAULT 'live'
        CHECK (origin IN ('live','sampler','backlog'));

-- Priority-invariant triggers. Create on fresh schemas AND on migrated
-- DBs — idempotent via CREATE TRIGGER IF NOT EXISTS.
CREATE TRIGGER IF NOT EXISTS streaming_segments_sampler_priority_ins
BEFORE INSERT ON streaming_segments
WHEN NEW.origin = 'sampler' AND NEW.priority < 2
BEGIN
    SELECT RAISE(ABORT, 'sampler rows must have priority >= 2 (p0/p1 reserved for live playback)');
END;

CREATE TRIGGER IF NOT EXISTS streaming_segments_sampler_priority_upd
BEFORE UPDATE ON streaming_segments
WHEN NEW.origin = 'sampler' AND NEW.priority < 2
BEGIN
    SELECT RAISE(ABORT, 'sampler rows must have priority >= 2 (p0/p1 reserved for live playback)');
END;

-- ─── sampler_jobs table ──────────────────────────────────────────────────
-- One row per (audiobook_id, locale). Status transitions:
--   pending → running → complete
--   pending → running → failed
-- The library-browse UI shows the "Play sample" affordance iff a row
-- exists AND status='complete'.
CREATE TABLE IF NOT EXISTS sampler_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    segments_target INTEGER NOT NULL,
    segments_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audiobook_id, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sampler_jobs_status ON sampler_jobs(status);
CREATE INDEX IF NOT EXISTS idx_sampler_jobs_locale ON sampler_jobs(locale, status);
