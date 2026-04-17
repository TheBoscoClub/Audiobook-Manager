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

DB_PATH="/var/lib/audiobooks/db/audiobooks.db"
CONF_FILE="/etc/audiobooks/audiobooks.conf"
if [[ -f "$CONF_FILE" ]]; then
    conf_db=$(grep -oP '^AUDIOBOOKS_DATABASE=\K.*' "$CONF_FILE" 2>/dev/null)
    [[ -n "$conf_db" ]] && DB_PATH="$conf_db"
fi

FLEET_STALE_SEC="${FLEET_STALE_SEC:-1200}" # 20 min — STT for one chapter is ≤5 min on L40S

log() { echo "$(date +%H:%M:%S) [fleet-watchdog] $*"; }

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
