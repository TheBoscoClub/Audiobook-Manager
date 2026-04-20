-- Migration 022: Add source_vtt_content column to streaming_segments
--
-- Purpose: Persist the English source VTT alongside the translated VTT for
-- every streaming segment. v8.3.2 and earlier discarded the source VTT at
-- worker dispatch time (stream-translate-worker.py:330), which left the
-- bilingual transcript panel (双语文字记录) with nothing to render once a
-- chapter consolidated — chapter_subtitles only had the translated locale.
--
-- The chapter-complete (prefetch) path already handled both locales
-- (streaming_translate.py:1207-1219); this migration extends the same
-- model to per-segment workers and the streaming consolidator.
--
-- Idempotent guard: ALTER TABLE ADD COLUMN fails on re-run, so the data
-- migration wrapper (data-migrations/006_streaming_source_vtt.sh) catches
-- "duplicate column name" before applying.

ALTER TABLE streaming_segments ADD COLUMN source_vtt_content TEXT;
