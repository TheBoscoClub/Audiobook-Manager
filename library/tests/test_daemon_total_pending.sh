#!/bin/bash
# test_daemon_total_pending.sh — Integration test for get_total_pending()
#
# Verifies that get_total_pending() in translation-daemon.sh correctly counts
# across all three pending-work sources:
#   - translation_queue WHERE state='pending'       (batch queue)
#   - streaming_segments WHERE state='pending'      (streaming pipeline)
#   - streaming_sessions WHERE gpu_warm=1 AND state='buffering'
#       AND datetime(created_at, '+15 minutes') > datetime('now')  (warmup)
#
# Expected result: 6
#   2 batch pending + 3 streaming pending + 1 recent warmup = 6
#   (1 expired warmup + 1 streaming-state session + 1 completed session excluded)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SCRIPT="$(realpath "${SCRIPT_DIR}/../../scripts/translation-daemon.sh")"

# Abort clearly if daemon script is missing
if [[ ! -f "$DAEMON_SCRIPT" ]]; then
    echo "FAIL: daemon script not found at $DAEMON_SCRIPT" >&2
    exit 1
fi

# ── Temp DB setup ──────────────────────────────────────────────────────────────
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT
DB_PATH="${TMPDIR_TEST}/test.db"

sqlite3 "$DB_PATH" <<'SQL'
-- Minimal translation_queue schema (state column is all we need)
CREATE TABLE translation_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    state TEXT DEFAULT 'pending',
    step TEXT DEFAULT 'stt',
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    last_progress_at TIMESTAMP,
    total_chapters INTEGER,
    UNIQUE(audiobook_id, locale)
);

-- Minimal streaming_segments schema
CREATE TABLE streaming_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    locale TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 2,
    worker_id TEXT,
    vtt_content TEXT,
    audio_path TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale)
);

-- Minimal streaming_sessions schema (uses created_at, state='buffering')
CREATE TABLE streaming_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,
    active_chapter INTEGER NOT NULL DEFAULT 0,
    buffer_threshold INTEGER NOT NULL DEFAULT 6,
    state TEXT NOT NULL DEFAULT 'buffering',
    gpu_warm INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Fixture rows ─────────────────────────────────────────────────────────────
-- translation_queue: 2 pending (counted), 1 processing (excluded)
INSERT INTO translation_queue (audiobook_id, locale, state) VALUES (1, 'zh-Hans', 'pending');
INSERT INTO translation_queue (audiobook_id, locale, state) VALUES (2, 'zh-Hans', 'pending');
INSERT INTO translation_queue (audiobook_id, locale, state) VALUES (3, 'zh-Hans', 'processing');

-- streaming_segments: 3 pending (counted), 1 processing (excluded), 1 completed (excluded)
INSERT INTO streaming_segments (audiobook_id, chapter_index, segment_index, locale, state)
    VALUES (1, 0, 0, 'zh-Hans', 'pending');
INSERT INTO streaming_segments (audiobook_id, chapter_index, segment_index, locale, state)
    VALUES (1, 0, 1, 'zh-Hans', 'pending');
INSERT INTO streaming_segments (audiobook_id, chapter_index, segment_index, locale, state)
    VALUES (1, 0, 2, 'zh-Hans', 'pending');
INSERT INTO streaming_segments (audiobook_id, chapter_index, segment_index, locale, state)
    VALUES (1, 0, 3, 'zh-Hans', 'processing');
INSERT INTO streaming_segments (audiobook_id, chapter_index, segment_index, locale, state)
    VALUES (1, 0, 4, 'zh-Hans', 'completed');

-- streaming_sessions:
--   1 recent warmup: gpu_warm=1, state='buffering', created 1 min ago   → COUNTED
--   1 expired warmup: gpu_warm=1, state='buffering', created 30 min ago  → EXCLUDED (past 15-min window)
--   1 streaming-state session: state='streaming'                          → EXCLUDED (wrong state)
--   1 completed session: state='completed'                                → EXCLUDED

-- recent warmup (within 15-min window)
INSERT INTO streaming_sessions (audiobook_id, locale, state, gpu_warm, created_at)
    VALUES (1, 'zh-Hans', 'buffering', 1, datetime('now', '-1 minutes'));

-- expired warmup (outside 15-min window)
INSERT INTO streaming_sessions (audiobook_id, locale, state, gpu_warm, created_at)
    VALUES (2, 'zh-Hans', 'buffering', 1, datetime('now', '-30 minutes'));

-- streaming-state session (wrong state for warmup inclusion)
INSERT INTO streaming_sessions (audiobook_id, locale, state, gpu_warm, created_at)
    VALUES (3, 'zh-Hans', 'streaming', 1, datetime('now', '-2 minutes'));

-- completed session
INSERT INTO streaming_sessions (audiobook_id, locale, state, gpu_warm, created_at)
    VALUES (4, 'zh-Hans', 'completed', 0, datetime('now', '-60 minutes'));
SQL

# ── Extract get_total_pending from the daemon and call it ─────────────────────
# translation-daemon.sh has top-level side-effect code (sources translation-env.sh,
# validates GPU config) that exits non-zero without a real GPU config file. We
# cannot source the daemon directly. Instead, we extract only the get_total_pending
# function block via sed and eval it, giving us the real function body from the
# daemon script under test.
#
# This approach verifies: (a) the function exists in the daemon, (b) its SQL
# produces the correct result against the fixture DB.

# Extract the function body from the daemon script.
# The function spans from "get_total_pending() {" to its closing "}".
FUNC_BODY="$(sed -n '/^get_total_pending()/,/^}/p' "$DAEMON_SCRIPT")"

if [[ -z "$FUNC_BODY" ]]; then
    echo "FAIL: get_total_pending() not found in $DAEMON_SCRIPT" >&2
    echo "      The function must be defined before this test can pass." >&2
    exit 1
fi

# Eval the extracted function definition into the current shell
eval "$FUNC_BODY"

# Call the function against the fixture DB
ACTUAL="$(get_total_pending)"

# ── Assert ────────────────────────────────────────────────────────────────────
EXPECTED=6

if [[ "$ACTUAL" == "$EXPECTED" ]]; then
    echo "PASS: get_total_pending = $ACTUAL (expected $EXPECTED)"
    exit 0
else
    echo "FAIL: get_total_pending = ${ACTUAL:-<empty>} (expected $EXPECTED)" >&2
    echo "" >&2
    echo "Debug — raw DB counts:" >&2
    sqlite3 "$DB_PATH" "SELECT 'batch_pending', COUNT(*) FROM translation_queue WHERE state='pending';" >&2
    sqlite3 "$DB_PATH" "SELECT 'streaming_pending', COUNT(*) FROM streaming_segments WHERE state='pending';" >&2
    sqlite3 "$DB_PATH" "SELECT 'warmup_sessions', COUNT(*) FROM streaming_sessions WHERE gpu_warm=1 AND state='buffering' AND datetime(created_at, '+15 minutes') > datetime('now');" >&2
    exit 1
fi
