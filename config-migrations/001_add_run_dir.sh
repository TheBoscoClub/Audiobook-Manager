#!/bin/bash
# Config migration 001: Add AUDIOBOOKS_RUN_DIR variable (v7.2.0)
#
# Required for the maintenance scheduler daemon's file lock and
# for Gunicorn/gevent WebSocket runtime state.
#
# Idempotent: skips if variable already exists in config.

# shellcheck disable=SC2154  # CONF_FILE, USE_SUDO, DRY_RUN set by caller

if grep -q '^AUDIOBOOKS_RUN_DIR=' "$CONF_FILE" 2>/dev/null; then
    return 0 2>/dev/null || exit 0
fi

echo "  Adding AUDIOBOOKS_RUN_DIR to config"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would add: AUDIOBOOKS_RUN_DIR to $CONF_FILE"
    return 0 2>/dev/null || exit 0
fi

# Derive default from existing VAR_DIR if set, otherwise use standard path
var_dir=$(grep -oP '^AUDIOBOOKS_VAR_DIR=\K.*' "$CONF_FILE" 2>/dev/null | tr -d '"' || true)
if [[ -z "$var_dir" ]]; then
    # Check for the older pattern without AUDIOBOOKS_ prefix
    var_dir=$(grep -oP '^VAR_DIR=\K.*' "$CONF_FILE" 2>/dev/null | tr -d '"' || true)
fi
run_dir="${var_dir:=/var/lib/audiobooks}/.run"

# Append to end of config file with explanatory comment
local_text="
# Runtime directory for locks and FIFOs (added by config migration 001)
AUDIOBOOKS_RUN_DIR=\"${run_dir}\""

if [[ -n "$USE_SUDO" ]]; then
    echo "$local_text" | sudo tee -a "$CONF_FILE" > /dev/null
    # Create the directory if it doesn't exist
    sudo mkdir -p "$run_dir"
    sudo chown audiobooks:audiobooks "$run_dir" 2>/dev/null || true
else
    echo "$local_text" >> "$CONF_FILE"
    mkdir -p "$run_dir" 2>/dev/null || true
fi

echo "  Added: AUDIOBOOKS_RUN_DIR=\"${run_dir}\""
