#!/bin/bash
# Safely Remove Corrupted Duplicate M4B Files
# These are failed conversions - valid versions exist in other locations/formats
# NOTE: After 2024-11 directory reorganization, M4B conversions are deprecated
# All audiobooks are now converted to OPUS and stored in $AUDIOBOOKS_LIBRARY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration
source "$SCRIPT_DIR/../../lib/audiobooks-config.sh"

CORRUPTED_DIR="$AUDIOBOOKS_LIBRARY"
LOG_FILE="$AUDIOBOOKS_LOGS/cleanup_duplicates.log"

echo "========================================"
echo "  Corrupted Duplicate Cleanup"
echo "========================================"
echo ""
echo "This script will remove corrupted (0-byte) audio files from:"
echo "  $CORRUPTED_DIR"
echo ""
echo "These files are safe to delete because:"
echo "  - Valid versions exist in the Library"
echo "  - Source AAXC files are preserved in $AUDIOBOOKS_SOURCES"
echo ""
read -p "Continue with cleanup? (y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cleanup canceled."
    exit 0
fi

mkdir -p "$(dirname "$LOG_FILE")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting cleanup of corrupted duplicates" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Find and remove all corrupted M4B files (< 100KB)
REMOVED=0
while IFS= read -r FILE; do
    SIZE=$(stat -c%s "$FILE" 2>/dev/null)
    BASENAME=$(basename "$FILE")

    if [ "$SIZE" -lt 102400 ]; then
        echo "Removing: $BASENAME ($SIZE bytes)" | tee -a "$LOG_FILE"
        rm -f "$FILE"
        ((REMOVED++))
    fi
done < <(find "$CORRUPTED_DIR" -name "*.m4b" -type f 2>/dev/null)

echo "" | tee -a "$LOG_FILE"
echo "========================================"
echo "Cleanup complete!"
echo "  Removed: $REMOVED corrupted duplicate files"
echo "  Log: $LOG_FILE"
echo "========================================"
echo ""
echo "NOTE: 6 audiobooks are truly missing and need re-download:"
echo "  1. Let's Pretend This Never Happened: A Mostly True Memoir"
echo "  2. Louise de La Valliere"
echo "  3. The Android's Dream"
echo "  4. The Surgeon's Mate: Aubrey-Maturin Series Book 7"
echo "  5. The Throat: Blue Rose Trilogy Book 3"
echo "  6. Treason's Harbour: The Aubrey-Maturin Series Book 9"
echo ""
echo "These will be re-downloaded automatically on the next scheduled"
echo "download run (within 4 hours) now that the corrupted duplicates"
echo "have been removed."
