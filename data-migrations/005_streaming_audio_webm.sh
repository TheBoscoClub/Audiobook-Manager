#!/bin/bash
# Data migration 005: remux streaming-audio from Ogg-Opus to WebM-Opus (v8.3.2)
#
# Chromium-based browsers (Brave, Chrome, Edge) do not accept
# `audio/ogg; codecs=opus` in MediaSource.addSourceBuffer — only the WebM
# and MP4 containers are supported for Opus via MSE. The v8.3.1/8.3.2
# streaming pipeline wrote per-segment files with an `.opus` extension
# inside an Ogg container, which silently broke audio playback for the
# intended end user (zh-Hans, Brave). The fix re-writes the worker and
# consolidator to produce WebM-Opus going forward, and this migration
# remuxes any legacy files left behind by prior releases so no paid
# RunPod Whisper / DeepL work is thrown away.
#
# Remux is a pure container swap via `ffmpeg -c copy -f webm` — no
# re-encoding, no quality loss, takes ~20 ms per segment.
#
# Required after upgrades from any version < 8.3.2 to >= 8.3.2.
# Idempotent: skips if the `.webm` sibling already exists. Safe to re-run.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.2"

_dm005_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm005_find() {
    local root="$1"
    local pattern="$2"
    if [[ -n "$USE_SUDO" ]]; then
        sudo find "$root" -type f -name "$pattern" 2>/dev/null
    else
        find "$root" -type f -name "$pattern" 2>/dev/null
    fi
}

_dm005_remux_one() {
    local src="$1"
    local dst="${src%.opus}.webm"

    # Idempotency: skip if the .webm sibling already exists and is non-empty.
    if [[ -n "$USE_SUDO" ]]; then
        if sudo test -s "$dst"; then
            sudo rm -f "$src"
            return 0
        fi
    else
        if [[ -s "$dst" ]]; then
            rm -f "$src"
            return 0
        fi
    fi

    if [[ -n "$USE_SUDO" ]]; then
        if ! sudo -u audiobooks ffmpeg -hide_banner -loglevel error -y \
            -i "$src" -c copy -f webm "$dst" 2>/dev/null; then
            return 1
        fi
        sudo rm -f "$src"
    else
        if ! ffmpeg -hide_banner -loglevel error -y \
            -i "$src" -c copy -f webm "$dst" 2>/dev/null; then
            return 1
        fi
        rm -f "$src"
    fi
    return 0
}

# Resolve streaming-audio root. Preference order:
#   1. $AUDIOBOOKS_STREAMING_AUDIO_DIR from /etc/audiobooks/audiobooks.conf
#   2. $AUDIOBOOKS_VAR_DIR/streaming-audio
#   3. /var/lib/audiobooks/streaming-audio (final fallback)
_dm005_audio_root=""
if [[ -f "/etc/audiobooks/audiobooks.conf" ]]; then
    _dm005_audio_root=$(grep -oP '^AUDIOBOOKS_STREAMING_AUDIO_DIR=\K.*' \
        /etc/audiobooks/audiobooks.conf 2>/dev/null || true)
    _dm005_audio_root="${_dm005_audio_root%\"}"
    _dm005_audio_root="${_dm005_audio_root#\"}"
fi
if [[ -z "$_dm005_audio_root" ]]; then
    _dm005_var_dir=$(grep -oP '^AUDIOBOOKS_VAR_DIR=\K.*' \
        /etc/audiobooks/audiobooks.conf 2>/dev/null || echo "")
    _dm005_var_dir="${_dm005_var_dir%\"}"
    _dm005_var_dir="${_dm005_var_dir#\"}"
    _dm005_audio_root="${_dm005_var_dir:-/var/lib/audiobooks}/streaming-audio"
fi

if [[ ! -d "$_dm005_audio_root" ]]; then
    return 0
fi

# Count legacy .opus files. If none, fast-path out.
_dm005_opus_count=$(_dm005_find "$_dm005_audio_root" "*.opus" | wc -l)
if [[ "$_dm005_opus_count" -eq 0 ]]; then
    return 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [005] DRY RUN: would remux $_dm005_opus_count streaming-audio .opus files to .webm"
    return 0
fi

echo "  [005] Remuxing $_dm005_opus_count streaming-audio files from Ogg-Opus to WebM-Opus..."

_dm005_ok=0
_dm005_fail=0
while IFS= read -r _dm005_file; do
    if _dm005_remux_one "$_dm005_file"; then
        _dm005_ok=$((_dm005_ok + 1))
    else
        _dm005_fail=$((_dm005_fail + 1))
        echo "  [005]   FAILED: $_dm005_file"
    fi
done < <(_dm005_find "$_dm005_audio_root" "*.opus")

echo "  [005] Remux complete: $_dm005_ok succeeded, $_dm005_fail failed"

# Update DB rows in chapter_translations_audio where audio_path still
# references chapter.opus under the streaming-audio tree.
if [[ -f "$DB_PATH" ]]; then
    _dm005_db_rows=$(_dm005_sqlite \
        "SELECT COUNT(*) FROM chapter_translations_audio WHERE audio_path LIKE '%/streaming-audio/%/chapter.opus';" \
        2>/dev/null || echo "0")
    if [[ "$_dm005_db_rows" -gt 0 ]]; then
        _dm005_sqlite \
            "UPDATE chapter_translations_audio SET audio_path = REPLACE(audio_path, '/chapter.opus', '/chapter.webm') WHERE audio_path LIKE '%/streaming-audio/%/chapter.opus';" \
            >/dev/null 2>&1
        echo "  [005] Updated $_dm005_db_rows chapter_translations_audio row(s) chapter.opus -> chapter.webm"
    fi
fi

if [[ $_dm005_fail -gt 0 ]]; then
    echo "  [005] WARNING: $_dm005_fail file(s) failed to remux — check ffmpeg availability"
fi
