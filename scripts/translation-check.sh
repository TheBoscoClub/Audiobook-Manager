#!/bin/bash
# translation-check.sh — Check for pending translations and start daemon
#
# Called by audiobook-translate.timer. If there are pending translations
# and the daemon isn't already running, starts the translation service.
#
# The daemon will provision GPU instances on first run if they're not
# already available, process all pending books, then tear them down.

set -uo pipefail

# Source canonical config — sets AUDIOBOOKS_DATABASE etc. from
# /etc/audiobooks/audiobooks.conf (or built-in defaults).
# shellcheck source=/dev/null
if [[ -f /usr/local/lib/audiobooks/audiobook-config.sh ]]; then
    source /usr/local/lib/audiobooks/audiobook-config.sh
elif [[ -f "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/audiobook-config.sh" ]]; then
    source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/audiobook-config.sh"
fi

DB_PATH="${AUDIOBOOKS_DATABASE}"

log() { echo "$(date +%H:%M:%S) [translate-check] $*"; }

# Liveness check: if daemon is active AND there are processing rows
# AND the heartbeat is stale (>60min), restart the daemon. Wedged event
# loops show as "active" to systemd but stop touching last_progress_at.
# Workers only update last_progress_at after each chapter finishes STT.
# Long chapters (60+ min audio) can take 20-40 min on an L40S, so a
# 15-min threshold causes false restarts during normal processing.
STALE_THRESHOLD_SEC=3600 # 60 minutes
if systemctl is-active --quiet audiobook-translate.service; then
    stale=$(sqlite3 "$DB_PATH" \
        "SELECT COUNT(*) FROM translation_queue \
         WHERE state='processing' \
           AND (last_progress_at IS NULL \
                OR strftime('%s','now') - strftime('%s', last_progress_at) > $STALE_THRESHOLD_SEC);" \
        2>/dev/null)
    if [ "${stale:-0}" -gt 0 ]; then
        log "Daemon wedged: $stale processing rows have no heartbeat in ${STALE_THRESHOLD_SEC}s — restarting"
        systemctl restart audiobook-translate.service
        # Reset the wedged rows so the new daemon picks them up.
        sqlite3 "$DB_PATH" \
            "UPDATE translation_queue SET state='pending', started_at=NULL \
             WHERE state='processing' \
               AND (last_progress_at IS NULL \
                    OR strftime('%s','now') - strftime('%s', last_progress_at) > $STALE_THRESHOLD_SEC);" \
            2>/dev/null
        exit 0
    fi
    log "Translation daemon already running — nothing to do"
    exit 0
fi

# Count pending work across all three sources:
#   - translation_queue (batch pipeline)
#   - streaming_segments (on-demand streaming pipeline)
#   - streaming_sessions in warmup phase (gpu_warm=1 AND state='buffering' AND within 15-min window)
#
# NOTE: Keep this query in sync with get_total_pending() in translation-daemon.sh.
# Schema deviation from original plan (Task 11): streaming_sessions uses
# created_at (not requested_at) and state='buffering' (not 'warmup').
pending=$(
    sqlite3 "$DB_PATH" <<'SQL' 2>/dev/null
SELECT
  (SELECT COUNT(*) FROM translation_queue WHERE state='pending') +
  (SELECT COUNT(*) FROM streaming_segments WHERE state='pending') +
  (SELECT COUNT(*) FROM streaming_sessions
     WHERE gpu_warm=1 AND state='buffering'
       AND datetime(created_at, '+15 minutes') > datetime('now'));
SQL
)

if [ "${pending:-0}" -eq 0 ]; then
    log "No pending translations — nothing to do"
    exit 0
fi

# Verify translation config exists before starting
TRANSLATION_ENV="${AUDIOBOOKS_TRANSLATION_ENV:-/etc/audiobooks/scripts/translation-env.sh}"
if [[ ! -f "$TRANSLATION_ENV" ]]; then
    log "No translation config at $TRANSLATION_ENV — cannot start daemon"
    log "Copy etc/translation-env.sh.example to $TRANSLATION_ENV and configure GPU instances"
    exit 0
fi

log "Found $pending pending translations/streaming segments — starting translation daemon"
systemctl start audiobook-translate.service

# Verify it started
if systemctl is-active --quiet audiobook-translate.service; then
    log "Translation daemon started successfully"
else
    log "WARNING: Translation daemon failed to start"
    exit 1
fi
