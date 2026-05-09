#!/bin/bash
# Data migration 013: backfill sampler-completion chapter consolidation (v8.3.10.6)
#
# Production recovery for books whose sampler completed BEFORE v8.3.10.6
# wired the sampler-completion → consolidate-chapter path. Symptom on iOS
# (Safari / Chrome iOS / any WKWebView): user opens a sampler-only book and
# gets a perpetual loading spinner because the player falls back to MSE
# WebM, which iOS does not support.
#
# Fix: for every sampler_jobs row at status='complete' that has NO
# corresponding chapter_translations_audio row for any of its sampler
# chapters, run _consolidate_chapter_audio() to concatenate the per-segment
# WebM files into chapter.webm and INSERT the missing row. The row is then
# served by /translated-audio and played via native <audio src=…>, which
# iOS supports fine.
#
# Idempotency:
#   - Skips books that already have ANY chapter_translations_audio row for
#     the (audiobook_id, locale, chapter_index) tuple
#   - All work driven by INSERT OR REPLACE inside the existing consolidation
#     helper — re-running is safe
#   - ffmpeg concat is idempotent (overwrites the same chapter.webm path)
#
# Variables set by caller (apply_data_migrations in upgrade.sh):
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"
#   APP_DIR       — /opt/audiobooks (for venv python + library/ on PYTHONPATH)
#
# Notes:
#   - Migration 010 backfills chapter_translations_audio rows from
#     stand-alone chapter.webm files already on disk. That covers the prior
#     window where consolidation ran but the INSERT was unstable.
#   - This migration (013) targets the inverse: the sampler completed but
#     consolidation NEVER RAN at all, so chapter.webm doesn't exist yet on
#     disk. We have to produce it by concatenating per-segment WebM files.

# shellcheck disable=SC2154

MIN_VERSION="8.3.10.6"

_dm013_python_helper() {
    if [[ -n "${VENV_PYTHON:-}" ]] && [[ -x "${VENV_PYTHON}" ]]; then
        printf "%s" "${VENV_PYTHON}"
    elif [[ -x /opt/audiobooks/library/venv/bin/python3 ]]; then
        printf "/opt/audiobooks/library/venv/bin/python3"
    elif command -v python3 >/dev/null 2>&1; then
        printf "%s" "$(command -v python3)"
    else
        printf ""
    fi
}

_dm013_resolve_streaming_root() {
    local cfg="/etc/audiobooks/audiobooks.conf"
    if [[ -f "$cfg" ]]; then
        # shellcheck disable=SC1090
        source "$cfg" 2>/dev/null || true
    fi
    if [[ -f /usr/local/lib/audiobooks/audiobook-config.sh ]]; then
        # shellcheck disable=SC1091
        source /usr/local/lib/audiobooks/audiobook-config.sh 2>/dev/null || true
    fi
    if [[ -n "${AUDIOBOOKS_STREAMING_AUDIO_DIR:-}" ]]; then
        printf "%s" "$AUDIOBOOKS_STREAMING_AUDIO_DIR"
    elif [[ -n "${AUDIOBOOKS_VAR_DIR:-}" ]]; then
        printf "%s/streaming-audio" "$AUDIOBOOKS_VAR_DIR"
    else
        printf "/var/lib/audiobooks/streaming-audio"
    fi
}

run_migration() {
    local py
    py="$(_dm013_python_helper)"
    if [[ -z "$py" ]]; then
        echo "  [013] no python3 found — skipping"
        return 0
    fi

    local app_lib="${APP_DIR:-/opt/audiobooks}/library"
    if [[ ! -d "$app_lib" ]]; then
        echo "  [013] app library not found at $app_lib — skipping"
        return 0
    fi

    local streaming_root
    streaming_root="$(_dm013_resolve_streaming_root)"
    if [[ ! -d "$streaming_root" ]]; then
        echo "  [013] streaming-audio root absent ($streaming_root) — nothing to backfill"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [013] DRY RUN: would consolidate chapter.webm for sampler-complete rows"
        return 0
    fi

    local sudo_prefix=""
    if [[ -n "$USE_SUDO" ]]; then
        sudo_prefix="sudo -u audiobooks"
    fi

    echo "  [013] Backfilling sampler-completion consolidation..."
    # The Python driver imports streaming_translate's existing
    # _consolidate_chapter_audio helper so the file layout, security
    # checks (path containment), ffmpeg invocation, and chapter row
    # writes are byte-identical to the live runtime. No duplication.
    #
    # The helper pulls the streaming-audio root from
    # streaming_translate._streaming_audio_root, which is set by
    # init_streaming_routes at app boot. The driver replicates that
    # binding before invoking the helper.
    if ! AUDIOBOOKS_STREAMING_AUDIO_DIR="$streaming_root" \
        DB_PATH_FOR_BACKFILL="$DB_PATH" \
        APP_LIB_FOR_BACKFILL="$app_lib" \
        $sudo_prefix env \
        AUDIOBOOKS_STREAMING_AUDIO_DIR="$streaming_root" \
        DB_PATH_FOR_BACKFILL="$DB_PATH" \
        APP_LIB_FOR_BACKFILL="$app_lib" \
        "$py" - <<'PYEOF'; then
import logging
import os
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="  [013-py] %(message)s")

DB = os.environ["DB_PATH_FOR_BACKFILL"]
APP_LIB = os.environ["APP_LIB_FOR_BACKFILL"]
ROOT = Path(os.environ["AUDIOBOOKS_STREAMING_AUDIO_DIR"]).resolve()

sys.path.insert(0, APP_LIB)

# Import the helper from the running app's code so behavior matches the
# live runtime (single source of truth for path layout / ffmpeg / DB writes).
from backend.api_modular import streaming_translate as st  # type: ignore  # noqa: E402

st._streaming_audio_root = ROOT

conn = sqlite3.connect(DB, timeout=30)
conn.row_factory = sqlite3.Row

# Find every sampler_jobs row that's complete but missing chapter rows.
# We work per (audiobook_id, locale) and consolidate every sampler chapter.
sampler_rows = conn.execute(
    "SELECT audiobook_id, locale FROM sampler_jobs "
    "WHERE status = 'complete' "
    "ORDER BY audiobook_id, locale"
).fetchall()

processed = 0
inserted = 0
skipped = 0
failed = 0

for r in sampler_rows:
    audiobook_id = r["audiobook_id"]
    locale = r["locale"]
    sampler_chapters = [
        row["chapter_index"]
        for row in conn.execute(
            "SELECT DISTINCT chapter_index FROM streaming_segments "
            "WHERE audiobook_id = ? AND locale = ? AND origin = 'sampler' "
            "AND state = 'completed' "
            "ORDER BY chapter_index",
            (audiobook_id, locale),
        ).fetchall()
    ]
    if not sampler_chapters:
        skipped += 1
        continue
    for ch_idx in sampler_chapters:
        # Skip chapters that already have a row (full-chapter consolidation
        # may have already produced one — leave it alone, INSERT OR REPLACE
        # inside the helper would otherwise overwrite a full row with a
        # sampler-only partial).
        existing = conn.execute(
            "SELECT 1 FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
            (audiobook_id, ch_idx, locale),
        ).fetchone()
        if existing is not None:
            skipped += 1
            continue
        try:
            st._consolidate_chapter_audio(conn, audiobook_id, int(ch_idx), locale)
        except Exception as exc:
            failed += 1
            print(
                f"  [013-py] FAIL book={audiobook_id} ch={ch_idx} locale={locale}: {exc}"
            )
            continue
        # Did the helper actually insert a row?
        post = conn.execute(
            "SELECT 1 FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
            (audiobook_id, ch_idx, locale),
        ).fetchone()
        if post is not None:
            inserted += 1
        else:
            # Helper logged its own warning (e.g. missing per-segment files,
            # paths outside root). Counted as skipped, not failed.
            skipped += 1
        processed += 1

conn.commit()
conn.close()

print(
    f"  [013-py] sampler_jobs scanned: {len(sampler_rows)}, "
    f"chapter consolidations attempted: {processed}, inserted: {inserted}, "
    f"skipped: {skipped}, failed: {failed}"
)
PYEOF
        echo "  [013] ERROR: backfill helper failed"
        return 1
    fi
    return 0
}
