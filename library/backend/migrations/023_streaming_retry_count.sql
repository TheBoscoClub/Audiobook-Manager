-- Migration 023: Add retry_count to streaming_segments.
--
-- Enables a bounded-retry policy for failed GPU work: the worker
-- increments retry_count on exception and requeues (state='pending')
-- until retry_count >= 3, after which the row is marked state='failed'
-- permanently. Prevents both silent loss (pre-023: failed rows never
-- retried) and infinite loops (no cap on requeues).
--
-- Paired with data-migration 007_streaming_retry_count.sh for upgrades.

ALTER TABLE streaming_segments ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
