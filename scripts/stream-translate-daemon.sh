#!/bin/bash
# stream-translate-daemon.sh — Wrapper for audiobook-stream-translate.service
#
# Sources lib/audiobook-config.sh (the single canonical source of bash
# defaults), then execs scripts/stream-translate-worker.py with resolved
# --db / --library / --api-base arguments.
#
# Why a wrapper and not a direct ExecStart=python ...:
#   systemd's EnvironmentFile parses KEY=VALUE only and does not expand
#   ${X:-default} shell syntax. Putting config defaults in audiobooks.conf
#   hardcodes them and reintroduces the 2026-04 drift vector (user conf
#   diverging from library/config.py + lib/audiobook-config.sh). A wrapper
#   sidesteps that: the config loader handles "commented defaults + user
#   overrides" correctly and stays in sync with the canonical sources.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source canonical config — sets AUDIOBOOKS_DATABASE, AUDIOBOOKS_LIBRARY,
# AUDIOBOOKS_VENV, etc. Prefer the installed copy, fall back to the in-tree
# lib/ when running from the project working tree.
# shellcheck source=/dev/null
if [[ -f /usr/local/lib/audiobooks/audiobook-config.sh ]]; then
    source /usr/local/lib/audiobooks/audiobook-config.sh
elif [[ -f "${SCRIPT_DIR}/../lib/audiobook-config.sh" ]]; then
    source "${SCRIPT_DIR}/../lib/audiobook-config.sh"
else
    echo "ERROR: audiobook-config.sh not found in canonical locations" >&2
    exit 1
fi

# Defensive: also source /etc/audiobooks/audiobooks.conf so operator
# overrides (path remappings + STT/DeepL/TTS credentials) are present when
# this script is invoked outside systemd (manual debug, cron, burst
# wrappers). Under systemd, EnvironmentFile= already populated these, so
# re-sourcing is an idempotent no-op. set -a exports every assignment so
# the Python worker subprocess inherits them.
CONFIG_ENV="${AUDIOBOOKS_CONFIG:-/etc/audiobooks/audiobooks.conf}"
if [[ -f "$CONFIG_ENV" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$CONFIG_ENV"
    set +a
fi

DB_PATH="${AUDIOBOOKS_DATABASE}"
LIBRARY_PATH="${AUDIOBOOKS_LIBRARY}"
VENV_PYTHON="${AUDIOBOOKS_VENV}/bin/python"
WORKER="${SCRIPT_DIR}/stream-translate-worker.py"
API_BASE="${AUDIOBOOKS_STREAM_API_BASE:-http://127.0.0.1:5001}"

# Validate all required paths before exec — clearer failure than a Python
# traceback when the venv is missing or the worker wasn't copied.
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "ERROR: venv python not executable: $VENV_PYTHON" >&2
    exit 1
fi
if [[ ! -f "$WORKER" ]]; then
    echo "ERROR: worker script not found: $WORKER" >&2
    exit 1
fi
if [[ ! -f "$DB_PATH" ]]; then
    echo "ERROR: database not found: $DB_PATH" >&2
    exit 1
fi
if [[ ! -d "$LIBRARY_PATH" ]]; then
    echo "ERROR: library directory not found: $LIBRARY_PATH" >&2
    exit 1
fi

exec "$VENV_PYTHON" "$WORKER" \
    --db "$DB_PATH" \
    --library "$LIBRARY_PATH" \
    --api-base "$API_BASE"
