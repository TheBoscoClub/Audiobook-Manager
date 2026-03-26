-- Migration 013: Fix FTS5 external content triggers
--
-- FTS5 external content tables (content=audiobooks) do NOT support UPDATE.
-- The old trigger used UPDATE ... SET ... WHERE rowid, which silently corrupts
-- the FTS index. The correct pattern is delete-then-insert.
--
-- This migration:
-- 1. Drops the broken triggers
-- 2. Drops and recreates the FTS table (clears corrupted index)
-- 3. Creates correct triggers using delete+insert pattern
-- 4. Rebuilds the FTS index from the content table

-- Step 1: Drop broken triggers
DROP TRIGGER IF EXISTS audiobooks_ai;
DROP TRIGGER IF EXISTS audiobooks_ad;
DROP TRIGGER IF EXISTS audiobooks_au;

-- Step 2: Drop and recreate the FTS table
DROP TABLE IF EXISTS audiobooks_fts;

CREATE VIRTUAL TABLE IF NOT EXISTS audiobooks_fts USING fts5(
    title,
    author,
    narrator,
    publisher,
    series,
    description,
    content=audiobooks,
    content_rowid=id
);

-- Step 3: Create correct triggers

-- Insert trigger (unchanged — INSERT INTO fts is correct)
CREATE TRIGGER IF NOT EXISTS audiobooks_ai AFTER INSERT ON audiobooks BEGIN
    INSERT INTO audiobooks_fts(rowid, title, author, narrator, publisher, series, description)
    VALUES (new.id, new.title, new.author, new.narrator, new.publisher, new.series, new.description);
END;

-- Delete trigger (use FTS5 'delete' command, not DELETE FROM)
CREATE TRIGGER IF NOT EXISTS audiobooks_ad AFTER DELETE ON audiobooks BEGIN
    INSERT INTO audiobooks_fts(audiobooks_fts, rowid, title, author, narrator, publisher, series, description)
    VALUES ('delete', old.id, old.title, old.author, old.narrator, old.publisher, old.series, old.description);
END;

-- Update trigger (delete old row + insert new row — NOT UPDATE SET)
CREATE TRIGGER IF NOT EXISTS audiobooks_au AFTER UPDATE ON audiobooks BEGIN
    INSERT INTO audiobooks_fts(audiobooks_fts, rowid, title, author, narrator, publisher, series, description)
    VALUES ('delete', old.id, old.title, old.author, old.narrator, old.publisher, old.series, old.description);
    INSERT INTO audiobooks_fts(rowid, title, author, narrator, publisher, series, description)
    VALUES (new.id, new.title, new.author, new.narrator, new.publisher, new.series, new.description);
END;

-- Step 4: Rebuild FTS index from content table
INSERT INTO audiobooks_fts(audiobooks_fts) VALUES('rebuild');
