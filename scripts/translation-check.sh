#!/bin/bash
# translation-check.sh — Check for pending translations and start daemon
#
# Called by audiobook-translate.timer. If there are pending translations
# and the daemon isn't already running, starts the translation service.
#
# The daemon will provision GPU instances on first run if they're not
# already available, process all pending books, then tear them down.

set -uo pipefail

DB_PATH="/var/lib/audiobooks/db/audiobooks.db"

log() { echo "$(date +%H:%M:%S) [translate-check] $*"; }

# Check if daemon is already running
if systemctl is-active --quiet audiobook-translate.service; then
    log "Translation daemon already running — nothing to do"
    exit 0
fi

# Count pending translations
pending=$(sudo -u audiobooks sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM translation_queue WHERE state='pending';" 2>/dev/null)

if [ "${pending:-0}" -eq 0 ]; then
    log "No pending translations — nothing to do"
    exit 0
fi

log "Found $pending pending translations — starting translation daemon"
sudo systemctl start audiobook-translate.service

# Verify it started
if systemctl is-active --quiet audiobook-translate.service; then
    log "Translation daemon started successfully"
else
    log "WARNING: Translation daemon failed to start"
    exit 1
fi
