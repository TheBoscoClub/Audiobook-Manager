#!/usr/bin/env zsh
# Multi-Format Audiobook Source Watcher
# Watches drop folders for new audiobooks from various sources
# Processes ZIP files and MP3 directories into OPUS format
#
# Supported sources:
#   - Google Play (ZIP with chapter MP3s)
#   - Chirp (MP3 files, often single file or chapters)
#   - Librivox (MP3 files from public domain)
#   - Other (generic MP3/ZIP support)
#
# ==============================================================================
# WARNING: EXPERIMENTAL / NOT FULLY TESTED
# ==============================================================================
# This script handles non-AAXC audiobook formats (ZIP, MP3, M4A, M4B).
# These formats are NOT fully tested and may not work as expected.
#
# The ONLY fully tested and verified format is Audible's AAXC format,
# which is handled by the main audiobook conversion pipeline
# (audiobook-convert, audiobook-download, etc.)
#
# TO ENABLE EXPERIMENTAL FORMAT SUPPORT AT YOUR OWN RISK:
# 1. Uncomment the WATCH_DIRS array entries below
# 2. Uncomment the function calls in scan_directory()
# 3. Understand that metadata extraction, chapter detection, and conversion
#    may fail or produce incorrect results for non-AAXC formats
# ==============================================================================

set -euo pipefail

# Load configuration
SCRIPT_DIR="${0:A:h}"
if [[ -f "/usr/local/lib/audiobooks/audiobook-config.sh" ]]; then
    source /usr/local/lib/audiobooks/audiobook-config.sh
elif [[ -f "$SCRIPT_DIR/../../lib/audiobook-config.sh" ]]; then
    source "$SCRIPT_DIR/../../lib/audiobook-config.sh"
fi

# Configuration - use environment or defaults
AUDIOBOOKS_BASE="${AUDIOBOOKS_DATA:-/srv/audiobooks}"

# ==============================================================================
# DISABLED: Non-AAXC source directories (experimental, not fully tested)
# ==============================================================================
# These watch directories are disabled because the formats they handle
# (Google Play ZIPs, Chirp MP3s, Librivox MP3s, generic MP3/ZIP) have not
# been fully tested. Enable at your own risk by uncommenting.
# ==============================================================================
WATCH_DIRS=(
    # EXPERIMENTAL - Google Play audiobook ZIPs with chapter MP3s
    # Known issues: metadata extraction may be incomplete, chapter ordering
    # may be incorrect for some releases
    # "$AUDIOBOOKS_BASE/Sources-GooglePlay"

    # EXPERIMENTAL - Chirp audiobook MP3s (single file or chapters)
    # Known issues: often missing metadata, cover art extraction unreliable
    # "$AUDIOBOOKS_BASE/Sources-Chirp"

    # EXPERIMENTAL - Librivox public domain MP3s
    # Known issues: inconsistent file naming, metadata often minimal,
    # multiple readers/sections may not be handled correctly
    # "$AUDIOBOOKS_BASE/Sources-Librivox"

    # EXPERIMENTAL - Generic MP3/ZIP support for other sources
    # Known issues: highly variable quality, no guaranteed metadata format
    # "$AUDIOBOOKS_BASE/Sources-Other"
)

OUTPUT_DIR="${AUDIOBOOKS_LIBRARY:-$AUDIOBOOKS_BASE/Library}"
LOG_DIR="${AUDIOBOOKS_LOGS:-$AUDIOBOOKS_BASE/logs}"
PROCESSOR_SCRIPT="${AUDIOBOOKS_HOME:-/opt/audiobooks}/library/scripts/google_play_processor.py"
VENV_PYTHON="${AUDIOBOOKS_VENV:-${AUDIOBOOKS_HOME:-/opt/audiobooks}/library/venv}/bin/python"
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

# ==============================================================================
# EXPERIMENTAL FUNCTION: Process a ZIP file
# ==============================================================================
# This function processes ZIP files containing chapter MP3s (e.g., Google Play).
# NOT FULLY TESTED - metadata extraction and chapter ordering may be incorrect.
# ==============================================================================
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

# ==============================================================================
# EXPERIMENTAL FUNCTION: Process a directory of MP3 files
# ==============================================================================
# This function processes directories containing chapter MP3s.
# NOT FULLY TESTED - chapter detection and metadata extraction may fail.
# ==============================================================================
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

# ==============================================================================
# EXPERIMENTAL FUNCTION: Process a single MP3 file
# ==============================================================================
# This function processes single-file MP3 audiobooks.
# NOT FULLY TESTED - metadata extraction is often incomplete for single files.
# ==============================================================================
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

    # ==========================================================================
    # DISABLED: ZIP file processing (experimental, not fully tested)
    # ==========================================================================
    # ZIP processing is disabled because metadata extraction and chapter
    # ordering have not been fully tested across all source types.
    # To enable at your own risk, uncomment the following block:
    # ==========================================================================
    # # Process ZIP files
    # while IFS= read -r -d '' zip_file; do
    #     # Skip if currently being written (check if modified in last 10 seconds)
    #     local mtime=$(stat -c %Y "$zip_file" 2>/dev/null || echo 0)
    #     local now=$(date +%s)
    #     if (( now - mtime < 10 )); then
    #         log "WAIT: $zip_file still being written..."
    #         continue
    #     fi
    #
    #     process_zip "$zip_file" "$source_type" && found_work=true
    # done < <(find "$watch_dir" -maxdepth 1 -name "*.zip" -type f -print0 2>/dev/null)

    # ==========================================================================
    # DISABLED: MP3/M4A/M4B directory processing (experimental, not fully tested)
    # ==========================================================================
    # Directory processing for audio files is disabled because chapter detection
    # and metadata extraction have not been fully tested.
    # To enable at your own risk, uncomment the following block:
    # ==========================================================================
    # # Process directories containing audio files (MP3, M4A, M4B)
    # while IFS= read -r -d '' subdir; do
    #     # Skip the watch directory itself
    #     [[ "$subdir" == "$watch_dir" ]] && continue
    #
    #     # Check if directory contains audio files
    #     if find "$subdir" -maxdepth 1 \( -name "*.mp3" -o -name "*.MP3" -o -name "*.m4a" -o -name "*.M4A" -o -name "*.m4b" -o -name "*.M4B" \) -type f | grep -q .; then
    #         process_mp3_directory "$subdir" "$source_type" && found_work=true
    #     fi
    # done < <(find "$watch_dir" -maxdepth 1 -type d -print0 2>/dev/null)

    # ==========================================================================
    # DISABLED: Loose audio file processing (experimental, not fully tested)
    # ==========================================================================
    # Single-file audiobook processing is disabled because metadata extraction
    # is often incomplete or incorrect for standalone files.
    # To enable at your own risk, uncomment the following block:
    # ==========================================================================
    # # Process loose audio files (single-file audiobooks)
    # while IFS= read -r -d '' audio_file; do
    #     # Skip if currently being written
    #     local mtime=$(stat -c %Y "$audio_file" 2>/dev/null || echo 0)
    #     local now=$(date +%s)
    #     if (( now - mtime < 10 )); then
    #         log "WAIT: $audio_file still being written..."
    #         continue
    #     fi
    #
    #     process_single_mp3 "$audio_file" "$source_type" && found_work=true
    # done < <(find "$watch_dir" -maxdepth 1 \( -name "*.mp3" -o -name "*.MP3" -o -name "*.m4a" -o -name "*.M4A" -o -name "*.m4b" -o -name "*.M4B" \) -type f -print0 2>/dev/null)

    $found_work && return 0 || return 1
}

# Main loop
main() {
    log "========================================="
    log "MULTIFORMAT AUDIOBOOK CONVERTER STARTING"
    log "========================================="

    # Check if any watch directories are enabled
    if [[ ${#WATCH_DIRS[@]} -eq 0 ]]; then
        log ""
        log "WARNING: No watch directories are enabled!"
        log ""
        log "All non-AAXC format processing is currently DISABLED because these"
        log "formats have not been fully tested and may not work as expected."
        log ""
        log "The ONLY fully tested format is Audible's AAXC format, which is"
        log "handled by the main audiobook pipeline (audiobook-convert)."
        log ""
        log "To enable experimental format support at your own risk:"
        log "  1. Edit this script: $0"
        log "  2. Uncomment the desired WATCH_DIRS entries"
        log "  3. Uncomment the processing blocks in scan_directory()"
        log ""
        log "Exiting - nothing to watch."
        exit 0
    fi

    log "Watch directories:"
    for dir in "${WATCH_DIRS[@]}"; do
        log "  - $dir"
    done
    log "Output: $OUTPUT_DIR"
    log "Scan interval: ${SCAN_INTERVAL}s"
    log ""

    # Create lock file
    echo $$ > "$PROCESSING_LOCK"
    trap 'rm -f "$PROCESSING_LOCK"' EXIT

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
