#!/bin/bash
# test_fleet_watchdog_streaming_reclaim.sh — Integration test for
# reclaim_stuck_streaming_segments() in fleet-watchdog.sh
#
# Verifies that the function resets streaming_segments rows that have been
# stuck in 'processing' for more than 10 minutes, while leaving all other
# rows (recently-processing, pending, completed, failed) untouched.
#
# Fixtures:
#   Row A: state='processing', started 15 min ago, priority=0 → MUST be reclaimed → priority=1
#   Row B: state='processing', started  2 min ago, priority=0 → NOT stuck yet, unchanged
#   Row C: state='pending',    started_at=NULL,    priority=2 → untouched
#   Row D: state='completed',  started 30 min ago, priority=0 → untouched (terminal)
#   Row E: state='failed',     started 30 min ago, priority=0 → untouched (terminal)
#
# Expected: only Row A is reset (state→'pending', worker_id→NULL, started_at→NULL, priority→1)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SCRIPT="$(realpath "${SCRIPT_DIR}/../../scripts/fleet-watchdog.sh")"

if [[ ! -f "$WATCHDOG_SCRIPT" ]]; then
    echo "FAIL: fleet-watchdog.sh not found at $WATCHDOG_SCRIPT" >&2
    exit 1
fi

# ── Temp DB setup ──────────────────────────────────────────────────────────────
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT
DB_PATH="${TMPDIR_TEST}/test.db"
export DB_PATH

sqlite3 "$DB_PATH" <<'SQL'
-- Minimal streaming_segments schema (matches production schema)
CREATE TABLE streaming_segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id  INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    locale        TEXT    NOT NULL,
    state         TEXT    NOT NULL DEFAULT 'pending',
    priority      INTEGER NOT NULL DEFAULT 2,
    worker_id     TEXT,
    vtt_content   TEXT,
    audio_path    TEXT,
    error         TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale)
);

-- ── Fixture rows ─────────────────────────────────────────────────────────────
-- Row A: stuck processing (15 min ago) — MUST be reclaimed, priority reset to 1
INSERT INTO streaming_segments
    (audiobook_id, chapter_index, segment_index, locale, state, priority, worker_id, started_at)
    VALUES (1, 0, 0, 'zh-Hans', 'processing', 0, 'gpu-a', datetime('now', '-15 minutes'));

-- Row B: recently processing (2 min ago) — NOT stuck, must remain unchanged
INSERT INTO streaming_segments
    (audiobook_id, chapter_index, segment_index, locale, state, priority, worker_id, started_at)
    VALUES (1, 0, 1, 'zh-Hans', 'processing', 0, 'gpu-b', datetime('now', '-2 minutes'));

-- Row C: pending, no started_at — untouched
INSERT INTO streaming_segments
    (audiobook_id, chapter_index, segment_index, locale, state, priority)
    VALUES (1, 0, 2, 'zh-Hans', 'pending', 2);

-- Row D: completed 30 min ago — terminal state, untouched
INSERT INTO streaming_segments
    (audiobook_id, chapter_index, segment_index, locale, state, priority, started_at, completed_at)
    VALUES (1, 0, 3, 'zh-Hans', 'completed', 0,
            datetime('now', '-30 minutes'), datetime('now', '-25 minutes'));

-- Row E: failed 30 min ago — terminal state, untouched
INSERT INTO streaming_segments
    (audiobook_id, chapter_index, segment_index, locale, state, priority, started_at)
    VALUES (1, 0, 4, 'zh-Hans', 'failed', 0, datetime('now', '-30 minutes'));
SQL

# ── Extract reclaim_stuck_streaming_segments from fleet-watchdog.sh ───────────
# fleet-watchdog.sh has top-level side-effect code (sources audiobook-config.sh,
# calls systemctl) that cannot be sourced directly in a test context. We extract
# only the target function block via sed and eval it, exactly as Task 11's test
# extracts get_total_pending from translation-daemon.sh.
#
# The sed range relies on the function's closing brace being a lone "}" at
# column 0 (the "/^}/" terminator). If a future refactor introduces any other
# line starting with "}" inside the function (e.g., brace-expansion blocks),
# the extraction will truncate silently — adjust the terminator pattern then.
FUNC_BODY="$(sed -n '/^reclaim_stuck_streaming_segments()/,/^}/p' "$WATCHDOG_SCRIPT")"

if [[ -z "$FUNC_BODY" ]]; then
    echo "FAIL: reclaim_stuck_streaming_segments() not found in $WATCHDOG_SCRIPT" >&2
    echo "      The function must be defined in fleet-watchdog.sh before this test can pass." >&2
    exit 1
fi

# Provide a stub log() so the function can call it without the real watchdog env.
# shellcheck disable=SC2329  # invoked indirectly by eval'd reclaim function
log() { echo "$(date +%H:%M:%S) [fleet-watchdog-test] $*"; }

# Eval the extracted function definition into the current shell
eval "$FUNC_BODY"

# Invoke the function against the fixture DB and capture log output.
# The contract is: log fires exactly when reclaimed > 0, silent otherwise.
# Capturing stdout+stderr lets us assert both sides of that contract.
RECLAIM_LOG_1="$(reclaim_stuck_streaming_segments 2>&1)"

# Second invocation: with Row A already reclaimed, nothing is stuck anymore.
# The function must run cleanly with zero log output (silent-on-zero contract).
RECLAIM_LOG_2="$(reclaim_stuck_streaming_segments 2>&1)"

# ── Assertions ────────────────────────────────────────────────────────────────
PASS=0
FAIL=0

assert_eq() {
    local label="$1" actual="$2" expected="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "PASS: $label = '$actual'"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $label = '${actual:-<NULL>}' (expected '$expected')" >&2
        FAIL=$((FAIL + 1))
    fi
}

# Row A assertions — must be reclaimed
ROW_A_STATE="$(sqlite3 "$DB_PATH" \
    "SELECT state FROM streaming_segments WHERE audiobook_id=1 AND segment_index=0;")"
ROW_A_WORKER="$(sqlite3 "$DB_PATH" \
    "SELECT COALESCE(worker_id,'NULL') FROM streaming_segments WHERE audiobook_id=1 AND segment_index=0;")"
ROW_A_STARTED="$(sqlite3 "$DB_PATH" \
    "SELECT COALESCE(started_at,'NULL') FROM streaming_segments WHERE audiobook_id=1 AND segment_index=0;")"
ROW_A_PRIORITY="$(sqlite3 "$DB_PATH" \
    "SELECT priority FROM streaming_segments WHERE audiobook_id=1 AND segment_index=0;")"

assert_eq "Row A state" "$ROW_A_STATE" "pending"
assert_eq "Row A worker_id" "$ROW_A_WORKER" "NULL"
assert_eq "Row A started_at" "$ROW_A_STARTED" "NULL"
assert_eq "Row A priority" "$ROW_A_PRIORITY" "1"

# Row B assertions — must remain 'processing' (not yet stuck)
ROW_B_STATE="$(sqlite3 "$DB_PATH" \
    "SELECT state FROM streaming_segments WHERE audiobook_id=1 AND segment_index=1;")"
ROW_B_WORKER="$(sqlite3 "$DB_PATH" \
    "SELECT COALESCE(worker_id,'NOT_NULL') FROM streaming_segments WHERE audiobook_id=1 AND segment_index=1;")"

assert_eq "Row B state" "$ROW_B_STATE" "processing"
assert_eq "Row B worker_id" "$ROW_B_WORKER" "gpu-b"

# Row C — pending, unchanged
ROW_C_STATE="$(sqlite3 "$DB_PATH" \
    "SELECT state FROM streaming_segments WHERE audiobook_id=1 AND segment_index=2;")"
ROW_C_PRIORITY="$(sqlite3 "$DB_PATH" \
    "SELECT priority FROM streaming_segments WHERE audiobook_id=1 AND segment_index=2;")"

assert_eq "Row C state" "$ROW_C_STATE" "pending"
assert_eq "Row C priority" "$ROW_C_PRIORITY" "2"

# Row D — completed, unchanged
ROW_D_STATE="$(sqlite3 "$DB_PATH" \
    "SELECT state FROM streaming_segments WHERE audiobook_id=1 AND segment_index=3;")"

assert_eq "Row D state" "$ROW_D_STATE" "completed"

# Row E — failed, unchanged
ROW_E_STATE="$(sqlite3 "$DB_PATH" \
    "SELECT state FROM streaming_segments WHERE audiobook_id=1 AND segment_index=4;")"

assert_eq "Row E state" "$ROW_E_STATE" "failed"

# ── Log-output contract ───────────────────────────────────────────────────────
# First invocation reclaimed Row A — must log "Reclaimed 1 streaming_segment(s)"
LOG_1_HITS="$(echo "$RECLAIM_LOG_1" | grep -c "Reclaimed 1 streaming_segment" || true)"
assert_eq "First call logs reclaim count" "$LOG_1_HITS" "1"

# Second invocation had no stuck rows — must be completely silent
assert_eq "Silent on zero reclaimed" "$RECLAIM_LOG_2" ""

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ "$FAIL" -gt 0 ]]; then
    echo "" >&2
    echo "Debug — full streaming_segments state:" >&2
    sqlite3 "$DB_PATH" \
        "SELECT id, segment_index, state, priority, COALESCE(worker_id,'NULL'), COALESCE(started_at,'NULL') FROM streaming_segments;" >&2
    exit 1
fi

exit 0
