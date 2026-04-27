#!/bin/bash
# Data migration 010: backfill chapter_translations_audio for orphan chapter.webm files (v8.3.9)
#
# Production-recovery for sites where streaming consolidation produced a
# chapter.webm on disk at <streaming_audio_root>/<book_id>/ch<NNN>/<locale>/
# but the corresponding chapter_translations_audio row was never inserted.
# Without that row, GET /api/audiobooks/<book>/translated-audio?locale=xx
# omits the chapter, so the front-end legacy player can't load it. For
# books with mostly-cached chapters but missing ch=0, the player loads
# ch=1 first and skips the intro entirely.
#
# Root cause was a window where the consolidation insert path was unstable;
# re-running the consolidation broadcast doesn't re-insert. This migration
# scans the streaming-audio tree and INSERT-OR-IGNOREs a row for every
# chapter.webm whose (book_id, chapter_index, locale) tuple is missing.
#
# Idempotency: uses INSERT OR IGNORE keyed on the table's UNIQUE constraint,
# so repeated runs are harmless.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.9"

# The streaming-audio root is the only host-specific value. We resolve it via
# the project's canonical config helper so this migration works on any
# install layout (default or operator-customised).
_dm010_resolve_streaming_root() {
    # Prefer reading the running app's config, else fall back to the
    # documented default. We never hardcode an operator path.
    local cfg="/etc/audiobooks/audiobooks.conf"
    if [[ -f "$cfg" ]]; then
        # shellcheck disable=SC1090
        source "$cfg" 2>/dev/null || true
    fi
    # Lib config (if present) is authoritative.
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

_dm010_python_helper() {
    # Find a usable python3 — prefer the installed app's venv so ffprobe
    # and the project's resolved config helpers are available.
    if [[ -x /opt/audiobooks/library/venv/bin/python3 ]]; then
        printf "/opt/audiobooks/library/venv/bin/python3"
    elif command -v python3 >/dev/null 2>&1; then
        printf "%s" "$(command -v python3)"
    else
        printf ""
    fi
}

run_migration() {
    local root
    root="$(_dm010_resolve_streaming_root)"
    if [[ ! -d "$root" ]]; then
        echo "  [010] streaming-audio root not present ($root) — nothing to backfill"
        return 0
    fi

    local py
    py="$(_dm010_python_helper)"
    if [[ -z "$py" ]]; then
        echo "  [010] no python3 found — cannot run backfill helper, skipping"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [010] DRY RUN: would scan $root for chapter.webm files missing in chapter_translations_audio"
        return 0
    fi

    # The Python helper does the actual work (sqlite3 INSERT OR IGNORE +
    # ffprobe duration). The shell migration is just a thin orchestrator.
    local sudo_prefix=""
    if [[ -n "$USE_SUDO" ]]; then
        sudo_prefix="sudo -u audiobooks"
    fi

    echo "  [010] Scanning $root for orphan chapter.webm files..."
    if ! $sudo_prefix "$py" - <<PYEOF
import json, re, sqlite3, subprocess, sys
from pathlib import Path

DB = "$DB_PATH"
ROOT = Path("$root")

VOICE_FOR_LOCALE = {"zh-Hans": "zh-CN-XiaoxiaoNeural"}
CHAPTER_RE = re.compile(r"^ch(\d{3})\$")

def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
            check=True, capture_output=True, timeout=30,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None

conn = sqlite3.connect(DB, timeout=30)
existing = {
    (r[0], r[1], r[2])
    for r in conn.execute(
        "SELECT audiobook_id, chapter_index, locale FROM chapter_translations_audio"
    )
}
valid_books = {
    r[0] for r in conn.execute("SELECT id FROM audiobooks")
}

inserted = 0
for book_dir in sorted(ROOT.iterdir()):
    if not (book_dir.is_dir() and book_dir.name.isdigit()):
        continue
    book_id = int(book_dir.name)
    if book_id not in valid_books:
        continue
    for ch_dir in sorted(book_dir.iterdir()):
        m = CHAPTER_RE.match(ch_dir.name)
        if not m:
            continue
        ch_idx = int(m.group(1))
        for loc_dir in ch_dir.iterdir():
            if not loc_dir.is_dir():
                continue
            locale = loc_dir.name
            if locale not in VOICE_FOR_LOCALE:
                continue
            webm = loc_dir / "chapter.webm"
            if not webm.is_file():
                continue
            if (book_id, ch_idx, locale) in existing:
                continue
            duration = probe_duration(webm)
            voice = VOICE_FOR_LOCALE[locale]
            conn.execute(
                "INSERT OR IGNORE INTO chapter_translations_audio "
                "(audiobook_id, chapter_index, locale, audio_path, "
                "tts_provider, tts_voice, duration_seconds) "
                "VALUES (?, ?, ?, ?, 'streaming', ?, ?)",
                (book_id, ch_idx, locale, str(webm), voice, duration),
            )
            inserted += 1

conn.commit()
conn.close()
print(f"  [010] backfilled {inserted} chapter_translations_audio rows")
PYEOF
    then
        echo "  [010] ERROR: backfill helper failed"
        return 1
    fi
    return 0
}
