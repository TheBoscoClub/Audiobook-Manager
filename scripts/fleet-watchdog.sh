#!/bin/bash
# fleet-watchdog.sh — Detect dead GPU fleets even when local daemon looks healthy
#
# Watchdog #1 (translation-check.sh) catches wedged local daemons via the
# translation_queue.last_progress_at heartbeat. This watchdog catches the
# inverse failure: the local daemon is happily retrying — its heartbeat is
# fresh — but the remote GPU instances died and zero chapters are actually
# completing. Signal: chapter_subtitles insert rate.
#
# Triggers: daemon active AND processing rows > 0 AND zero new
# chapter_subtitles in FLEET_STALE_SEC.
# Action: restart audiobook-translate.service (which re-provisions the fleet).

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

FLEET_STALE_SEC="${FLEET_STALE_SEC:-1200}" # 20 min — STT for one chapter is ≤5 min on L40S

log() { echo "$(date +%H:%M:%S) [fleet-watchdog] $*"; }

# Reclaim streaming_segments stuck in 'processing' for more than 10 minutes.
#
# Streaming workers are independent of the batch translation fleet — their rows
# must be reclaimed regardless of whether audiobook-translate.service is active.
# This function therefore runs unconditionally, before any batch-watchdog gates.
#
# Reclaimed rows re-enter at priority=1 (forward chase tier in the 3-tier model).
#
# The UPDATE and SELECT changes() MUST share a single sqlite3 invocation —
# changes() is per-connection, so splitting them into two sqlite3 calls would
# always return 0 (the second connection has made no modifications).
reclaim_stuck_streaming_segments() {
    local reclaimed
    reclaimed="$(
        sqlite3 "$DB_PATH" <<'SQL'
UPDATE streaming_segments
  SET state='pending', worker_id=NULL, started_at=NULL, priority=1
  WHERE state='processing'
    AND datetime(started_at, '+10 minutes') < datetime('now');
SELECT changes();
SQL
    )"
    if [[ "${reclaimed:-0}" -gt 0 ]]; then
        log "Reclaimed $reclaimed streaming_segment(s) stuck in 'processing' > 10 min"
    fi
}

# Run streaming reclaim unconditionally — streaming workers are independent of
# the batch daemon; stuck segments must be freed even when the batch fleet is idle.
reclaim_stuck_streaming_segments

# Only act when the daemon is supposedly running.
if ! systemctl is-active --quiet audiobook-translate.service; then
    exit 0
fi

processing=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM translation_queue WHERE state='processing';" 2>/dev/null)
if [ "${processing:-0}" -eq 0 ]; then
    exit 0
fi

# Newest chapter_subtitles row across any locale — proves the fleet is producing.
recent=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM chapter_subtitles \
     WHERE strftime('%s', created_at) > strftime('%s','now') - $FLEET_STALE_SEC;" \
    2>/dev/null)

if [ "${recent:-0}" -gt 0 ]; then
    exit 0 # fleet is producing; nothing to do
fi

log "Fleet appears dead: $processing processing rows but 0 chapter_subtitles in ${FLEET_STALE_SEC}s — restarting daemon"
systemctl restart audiobook-translate.service

# Reset processing rows so the new daemon re-picks them up cleanly.
sqlite3 "$DB_PATH" \
    "UPDATE translation_queue SET state='pending', started_at=NULL \
     WHERE state='processing';" 2>/dev/null
