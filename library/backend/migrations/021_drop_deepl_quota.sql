-- Migration 021: drop the deepl_quota table.
--
-- The hosted-MT integration was removed outright (Audiobook-Manager-4uj,
-- 2026-09-11); quota tracking was a property of that vendor's billing model
-- and nothing reads this table any more. Existing translation rows are
-- untouched — provenance lives in the translator columns, not here.

DROP TABLE IF EXISTS deepl_quota;
