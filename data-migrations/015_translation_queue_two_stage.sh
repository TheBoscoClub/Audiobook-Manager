#!/bin/bash
# Data migration 015: translation_queue two-stage accounting (Audiobook-Manager-4j4)
#
# Adds subtitles_done_at, narration_done_at and claimed_by to translation_queue
# and maps existing rows by the evidence on disk. The schema and the mapping
# have ONE definition — library/localization/queue_schema.py
# (ensure_queue_schema), which the API, batch-translate.py and the drain also
# call — so this script only invokes it. Mapping (see that module's docstring):
#   - 'completed' with narration rows          -> stays 'completed'
#   - 'completed' with complete translated subtitles but no narration
#                                              -> 'pending', subtitles done
#                                                 (host-side edge-tts only, no GPU)
#   - 'completed' without complete translated subtitles
#                                              -> 'pending', both stages open
#   - every other state keeps its value; evidence timestamps are filled in.
#
# Idempotent: ensure_queue_schema remaps only in the run that adds the columns.
# Skips cleanly when the DB, the audiobooks table or the venv is absent (the
# API creates translation_queue with the new columns on its next start).
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   VENV_PYTHON   — installed venv python
#   APP_DIR       — installed application root (library/ lives under it)
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.7.2"

_dm015_run_py() {
    local code="$1"
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks env PYTHONPATH="${APP_DIR}/library" "$VENV_PYTHON" -c "$code" "$DB_PATH"
    else
        PYTHONPATH="${APP_DIR}/library" "$VENV_PYTHON" -c "$code" "$DB_PATH"
    fi
}

if [[ ! -f "$DB_PATH" ]] || [[ ! -x "${VENV_PYTHON:-}" ]] \
    || [[ ! -f "${APP_DIR}/library/localization/queue_schema.py" ]]; then
    return 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would add translation_queue two-stage columns and remap completed rows"
    return 0
fi

_dm015_out="$(_dm015_run_py '
import sqlite3, sys
from localization.queue_schema import ensure_queue_schema
conn = sqlite3.connect(sys.argv[1])
if conn.execute("SELECT 1 FROM sqlite_master WHERE type=\"table\" AND name=\"translation_queue\"").fetchone() is None:
    print("no translation_queue table yet — the API creates it with the new columns")
else:
    print(f"two-stage columns present; {ensure_queue_schema(conn)} completed row(s) reopened for narration")
conn.close()
')" && echo "  translation_queue: ${_dm015_out}" \
    || echo "  WARNING: migration 015 failed — the API applies the same schema on its next start"
unset _dm015_out
