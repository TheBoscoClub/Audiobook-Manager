#!/bin/bash
# Multi-Format Audiobook Source Watcher
# Watches drop folders for new audiobooks from various sources
# Processes ZIP files and MP3 directories into OPUS format
#
# Supported sources:
#   - Google Play (ZIP with chapter MP3s)
#   - Chirp (MP3 files, often single file or chapters)
#   - Librivox (MP3 files from public domain)
#   - Other (generic MP3/ZIP support)

set -euo pipefail

# Configuration
AUDIOBOOKS_BASE="/raid0/Audiobooks"
WATCH_DIRS=(
    "$AUDIOBOOKS_BASE/Sources-GooglePlay"
    "$AUDIOBOOKS_BASE/Sources-Chirp"
    "$AUDIOBOOKS_BASE/Sources-Librivox"
    "$AUDIOBOOKS_BASE/Sources-Other"
)
OUTPUT_DIR="$AUDIOBOOKS_BASE/Library"
LOG_DIR="$AUDIOBOOKS_BASE/logs"
PROCESSOR_SCRIPT="/raid0/ClaudeCodeProjects/Audiobooks/library/scripts/google_play_processor.py"
VENV_PYTHON="/raid0/ClaudeCodeProjects/Audiobooks/library/venv/bin/python"
TRIGGER_DIR="/tmp/audiobook-triggers"
PROCESSING_LOCK="/tmp/multiformat-converter.lock"

# Scan interval when idle (seconds)
SCAN_INTERVAL=60

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$LOG_DIR" "$TRIGGER_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/multiformat-converter.log"
}

notify() {
    local title="$1"
    local message="$2"
    local icon="${3:-audio-x-generic}"
    notify-send -a "Multiformat Converter" -i "$icon" "$title" "$message" 2>/dev/null || true
}

# Determine source type from directory
get_source_type() {
    local dir="$1"
    case "$dir" in
        *GooglePlay*) echo "google_play" ;;
        *Chirp*) echo "chirp" ;;
        *Librivox*) echo "librivox" ;;
        *) echo "other" ;;
    esac
}

# Process a ZIP file
process_zip() {
    local zip_file="$1"
    local source_type="$2"
    local basename=$(basename "$zip_file" .zip)

    log "Processing ZIP: $basename (source: $source_type)"
    notify "Processing Audiobook" "$basename" "audio-x-generic"

    # Run the processor with low priority
    if nice -n 19 ionice -c 2 -n 7 "$VENV_PYTHON" "$PROCESSOR_SCRIPT" "$zip_file" \
        --output-dir "$OUTPUT_DIR" \
        --import-db \
        --execute \
        >> "$LOG_DIR/multiformat-converter.log" 2>&1; then

        log "SUCCESS: $basename converted and imported"
        notify "Audiobook Ready" "$basename" "emblem-ok-symbolic"

        # Move processed ZIP to archive
        local archive_dir="$AUDIOBOOKS_BASE/processed-sources"
        mkdir -p "$archive_dir"
        mv "$zip_file" "$archive_dir/"

        # Signal database update
        touch "$TRIGGER_DIR/conversion-complete"
        return 0
    else
        log "FAILED: $basename conversion failed"
        notify "Conversion Failed" "$basename" "dialog-error"

        # Move to failed directory for inspection
        local failed_dir="$AUDIOBOOKS_BASE/failed-sources"
        mkdir -p "$failed_dir"
        mv "$zip_file" "$failed_dir/"
        return 1
    fi
}

# Process a directory of MP3 files (chapters or single file)
process_mp3_directory() {
    local mp3_dir="$1"
    local source_type="$2"
    local dirname=$(basename "$mp3_dir")

    # Count MP3 files
    local mp3_count=$(find "$mp3_dir" -maxdepth 1 -name "*.mp3" -o -name "*.MP3" 2>/dev/null | wc -l)

    if [[ "$mp3_count" -eq 0 ]]; then
        log "SKIP: No MP3 files in $dirname"
        return 1
    fi

    log "Processing directory: $dirname ($mp3_count MP3 files, source: $source_type)"
    notify "Processing Audiobook" "$dirname" "audio-x-generic"

    # Run the processor on the directory with low priority
    if nice -n 19 ionice -c 2 -n 7 "$VENV_PYTHON" "$PROCESSOR_SCRIPT" "$mp3_dir" \
        --output-dir "$OUTPUT_DIR" \
        --import-db \
        --execute \
        >> "$LOG_DIR/multiformat-converter.log" 2>&1; then

        log "SUCCESS: $dirname converted and imported"
        notify "Audiobook Ready" "$dirname" "emblem-ok-symbolic"

        # Move processed directory to archive
        local archive_dir="$AUDIOBOOKS_BASE/processed-sources"
        mkdir -p "$archive_dir"
        mv "$mp3_dir" "$archive_dir/"

        # Signal database update
        touch "$TRIGGER_DIR/conversion-complete"
        return 0
    else
        log "FAILED: $dirname conversion failed"
        notify "Conversion Failed" "$dirname" "dialog-error"

        # Move to failed directory
        local failed_dir="$AUDIOBOOKS_BASE/failed-sources"
        mkdir -p "$failed_dir"
        mv "$mp3_dir" "$failed_dir/"
        return 1
    fi
}

# Process a single MP3 file (convert directly to OPUS)
process_single_mp3() {
    local mp3_file="$1"
    local source_type="$2"
    local basename=$(basename "$mp3_file" .mp3)
    basename=$(basename "$basename" .MP3)

    log "Processing single MP3: $basename (source: $source_type)"

    # Create a temp directory with just this file
    local temp_dir=$(mktemp -d)
    cp "$mp3_file" "$temp_dir/"

    if nice -n 19 ionice -c 2 -n 7 "$VENV_PYTHON" "$PROCESSOR_SCRIPT" "$temp_dir" \
        --output-dir "$OUTPUT_DIR" \
        --import-db \
        --execute \
        >> "$LOG_DIR/multiformat-converter.log" 2>&1; then

        log "SUCCESS: $basename converted"
        notify "Audiobook Ready" "$basename" "emblem-ok-symbolic"

        # Move processed file to archive
        local archive_dir="$AUDIOBOOKS_BASE/processed-sources"
        mkdir -p "$archive_dir"
        mv "$mp3_file" "$archive_dir/"

        rm -rf "$temp_dir"
        touch "$TRIGGER_DIR/conversion-complete"
        return 0
    else
        log "FAILED: $basename conversion failed"
        rm -rf "$temp_dir"
        return 1
    fi
}

# Scan a watch directory for new items
scan_directory() {
    local watch_dir="$1"
    local source_type=$(get_source_type "$watch_dir")
    local found_work=false

    # Skip if directory doesn't exist
    [[ ! -d "$watch_dir" ]] && return 1

    # Process ZIP files
    while IFS= read -r -d '' zip_file; do
        # Skip if currently being written (check if modified in last 10 seconds)
        local mtime=$(stat -c %Y "$zip_file" 2>/dev/null || echo 0)
        local now=$(date +%s)
        if (( now - mtime < 10 )); then
            log "WAIT: $zip_file still being written..."
            continue
        fi

        process_zip "$zip_file" "$source_type" && found_work=true
    done < <(find "$watch_dir" -maxdepth 1 -name "*.zip" -type f -print0 2>/dev/null)

    # Process directories containing audio files (MP3, M4A, M4B)
    while IFS= read -r -d '' subdir; do
        # Skip the watch directory itself
        [[ "$subdir" == "$watch_dir" ]] && continue

        # Check if directory contains audio files
        if find "$subdir" -maxdepth 1 \( -name "*.mp3" -o -name "*.MP3" -o -name "*.m4a" -o -name "*.M4A" -o -name "*.m4b" -o -name "*.M4B" \) -type f | grep -q .; then
            process_mp3_directory "$subdir" "$source_type" && found_work=true
        fi
    done < <(find "$watch_dir" -maxdepth 1 -type d -print0 2>/dev/null)

    # Process loose audio files (single-file audiobooks)
    while IFS= read -r -d '' audio_file; do
        # Skip if currently being written
        local mtime=$(stat -c %Y "$audio_file" 2>/dev/null || echo 0)
        local now=$(date +%s)
        if (( now - mtime < 10 )); then
            log "WAIT: $audio_file still being written..."
            continue
        fi

        process_single_mp3 "$audio_file" "$source_type" && found_work=true
    done < <(find "$watch_dir" -maxdepth 1 \( -name "*.mp3" -o -name "*.MP3" -o -name "*.m4a" -o -name "*.M4A" -o -name "*.m4b" -o -name "*.M4B" \) -type f -print0 2>/dev/null)

    $found_work && return 0 || return 1
}

# Main loop
main() {
    log "========================================="
    log "MULTIFORMAT AUDIOBOOK CONVERTER STARTING"
    log "========================================="
    log "Watch directories:"
    for dir in "${WATCH_DIRS[@]}"; do
        log "  - $dir"
    done
    log "Output: $OUTPUT_DIR"
    log "Scan interval: ${SCAN_INTERVAL}s"
    log ""

    # Create lock file
    echo $$ > "$PROCESSING_LOCK"
    trap "rm -f $PROCESSING_LOCK" EXIT

    while true; do
        local found_any=false

        for watch_dir in "${WATCH_DIRS[@]}"; do
            if scan_directory "$watch_dir"; then
                found_any=true
            fi
        done

        if ! $found_any; then
            # No work found, sleep before next scan
            sleep "$SCAN_INTERVAL"
        else
            # Work was done, quick rescan in case more files appeared
            sleep 5
        fi
    done
}

# Handle signals
trap 'log "Received SIGTERM, shutting down..."; exit 0' SIGTERM
trap 'log "Received SIGINT, shutting down..."; exit 0' SIGINT
trap 'log "Received SIGUSR1, forcing immediate scan..."; continue' SIGUSR1

main "$@"
