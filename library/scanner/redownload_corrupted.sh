#!/usr/bin/env zsh
# Re-download Corrupted Audiobooks
# Reads the priority list and forces re-download of corrupted files

SCRIPT_DIR="${0:A:h}"

# Load configuration
source "$SCRIPT_DIR/../../lib/audiobook-config.sh"

PRIORITY_LIST="$SCRIPT_DIR/priority_audiobooks_to_redownload.txt"
DOWNLOAD_DIR="$AUDIOBOOKS_SOURCES"
LOG_FILE="$AUDIOBOOKS_LOGS/redownload_corrupted.log"
TEMP_DIR="/tmp/redownload-corrupted"
AUDIBLE_CMD="$HOME/.local/bin/audible"
LIBRARY_TSV="$TEMP_DIR/library.tsv"

# Rate limiting
DOWNLOAD_DELAY=30

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

mkdir -p "$TEMP_DIR" "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

notify() {
    local title="$1"
    local message="$2"
    local icon="${3:-audio-x-generic}"
    notify-send -a "Audiobook Re-Downloader" -i "$icon" "$title" "$message" 2>/dev/null
}

# Check if priority list exists
if [ ! -f "$PRIORITY_LIST" ]; then
    log "ERROR: Priority list not found at $PRIORITY_LIST"
    echo -e "${RED}ERROR: Priority list not found${NC}"
    exit 1
fi

log "=========================================="
log "Corrupted Audiobook Re-Downloader Started"
log "=========================================="

# Fetch library with ASINs and titles
log "Fetching library from Audible..."
"$AUDIBLE_CMD" library export -f tsv -o "$LIBRARY_TSV" 2>&1 | tee -a "$LOG_FILE"

if [ ! -s "$LIBRARY_TSV" ]; then
    log "ERROR: Failed to fetch library"
    echo -e "${RED}ERROR: Failed to fetch library${NC}"
    notify "Re-Downloader" "Failed to fetch Audible library" "dialog-error"
    exit 1
fi

# Parse priority list and extract unique titles
log "Parsing priority list..."
grep "^[0-9]" "$PRIORITY_LIST" | sed 's/^[0-9]*\. //' > "$TEMP_DIR/corrupted_titles.txt"

TOTAL_CORRUPTED=$(wc -l < "$TEMP_DIR/corrupted_titles.txt")
log "Found $TOTAL_CORRUPTED corrupted audiobooks in priority list"

# Match titles to ASINs
: > "$TEMP_DIR/asins_to_redownload.txt"
: > "$TEMP_DIR/match_log.txt"

while IFS= read -r TITLE; do
    # Clean the title (remove format indicators like "(OPUS)", "(M4B)", etc.)
    CLEAN_TITLE=$(echo "$TITLE" | sed 's/ (M4B)$//' | sed 's/ (OPUS)$//' | sed 's/ (MP3)$//')

    # Try exact match first
    ASIN=$(grep -F "$CLEAN_TITLE" "$LIBRARY_TSV" | head -1 | cut -f1)

    # If no exact match, try fuzzy match (first 50 chars)
    if [ -z "$ASIN" ]; then
        SEARCH_TITLE="${CLEAN_TITLE:0:50}"
        ASIN=$(grep -i "$SEARCH_TITLE" "$LIBRARY_TSV" | head -1 | cut -f1)
    fi

    if [ -n "$ASIN" ]; then
        echo "$ASIN" >> "$TEMP_DIR/asins_to_redownload.txt"
        echo "MATCHED: $TITLE -> $ASIN" >> "$TEMP_DIR/match_log.txt"
    else
        echo "NOT FOUND: $TITLE" >> "$TEMP_DIR/match_log.txt"
        log "WARNING: Could not find ASIN for: $TITLE"
    fi
done < "$TEMP_DIR/corrupted_titles.txt"

# Deduplicate ASINs
sort -u "$TEMP_DIR/asins_to_redownload.txt" -o "$TEMP_DIR/asins_to_redownload.txt"

MATCHED_COUNT=$(wc -l < "$TEMP_DIR/asins_to_redownload.txt")
NOT_FOUND=$((TOTAL_CORRUPTED - MATCHED_COUNT))

log "Matched $MATCHED_COUNT ASINs from $TOTAL_CORRUPTED corrupted titles"
if [ "$NOT_FOUND" -gt 0 ]; then
    log "WARNING: $NOT_FOUND titles could not be matched (check $TEMP_DIR/match_log.txt)"
fi

if [ "$MATCHED_COUNT" -eq 0 ]; then
    log "No ASINs matched. Exiting."
    exit 0
fi

notify "Re-Downloader" "Re-downloading $MATCHED_COUNT corrupted audiobooks..." "emblem-downloads"

# Remove corrupted files before re-downloading
log "Removing corrupted files..."
REMOVED_COUNT=0

while IFS= read -r ASIN; do
    # Find and remove all files with this ASIN (including converted files)
    find "$DOWNLOAD_DIR" -name "${ASIN}*" -type f \( -name "*.aaxc" -o -name "*.aax" \) -size 0 -delete 2>/dev/null && ((REMOVED_COUNT++))
    find "$AUDIOBOOKS_LIBRARY" -name "${ASIN}*" -type f -name "*.opus" -size 0 -delete 2>/dev/null
done < "$TEMP_DIR/asins_to_redownload.txt"

log "Removed $REMOVED_COUNT corrupted files"

# Re-download audiobooks
CURRENT=0
SUCCESS=0
FAILED=0

while IFS= read -r ASIN; do
    ((CURRENT++))

    # Get title
    TITLE=$(grep "^$ASIN" "$LIBRARY_TSV" | cut -f2 | head -1)
    [ -z "$TITLE" ] && TITLE="$ASIN"

    DISPLAY_TITLE="${TITLE:0:50}"
    [ ${#TITLE} -gt 50 ] && DISPLAY_TITLE="${DISPLAY_TITLE}..."

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CURRENT/$MATCHED_COUNT] Re-downloading: $DISPLAY_TITLE" >> "$LOG_FILE"
    echo -e "${CYAN}[$CURRENT/$MATCHED_COUNT]${NC} Re-downloading: $DISPLAY_TITLE"

    notify "Re-downloading" "[$CURRENT/$MATCHED_COUNT] $DISPLAY_TITLE" "emblem-downloads"

    # Download with AAXC (best quality)
    "$AUDIBLE_CMD" download \
        --aaxc \
        --asin "$ASIN" \
        --output-dir "$DOWNLOAD_DIR" \
        --filename-mode asin_ascii \
        --overwrite \
        --ignore-errors \
        2>> "$LOG_FILE"

    # Verify download
    if find "$DOWNLOAD_DIR" -name "${ASIN}*" -type f \( -name "*.aaxc" -o -name "*.aax" \) ! -size 0 -print -quit | grep -q .; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✓ Re-downloaded: $DISPLAY_TITLE" >> "$LOG_FILE"
        echo -e "${GREEN}✓ Re-downloaded:${NC} $DISPLAY_TITLE"
        ((SUCCESS++))
    else
        # Try AAX fallback
        echo "  Trying AAX fallback..." | tee -a "$LOG_FILE"
        "$AUDIBLE_CMD" download \
            --aax-fallback \
            --asin "$ASIN" \
            --output-dir "$DOWNLOAD_DIR" \
            --filename-mode asin_ascii \
            --overwrite \
            --ignore-errors \
            2>> "$LOG_FILE"

        if find "$DOWNLOAD_DIR" -name "${ASIN}*" -type f \( -name "*.aaxc" -o -name "*.aax" \) ! -size 0 -print -quit | grep -q .; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✓ Re-downloaded (AAX): $DISPLAY_TITLE" >> "$LOG_FILE"
            echo -e "${GREEN}✓ Re-downloaded (AAX):${NC} $DISPLAY_TITLE"
            ((SUCCESS++))
        else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✗ Failed to re-download: $DISPLAY_TITLE" >> "$LOG_FILE"
            echo -e "${RED}✗ Failed:${NC} $DISPLAY_TITLE"
            ((FAILED++))
        fi
    fi

    # Rate limiting
    if [ "$CURRENT" -lt "$MATCHED_COUNT" ]; then
        sleep $DOWNLOAD_DELAY
    fi

done < "$TEMP_DIR/asins_to_redownload.txt"

log "=========================================="
log "Re-download complete: $SUCCESS succeeded, $FAILED failed"
log "=========================================="

# Final notification
if [ $FAILED -eq 0 ]; then
    notify "Re-Downloader Complete" "Successfully re-downloaded $SUCCESS corrupted audiobooks!" "emblem-default"
else
    notify "Re-Downloader Complete" "Re-downloaded $SUCCESS audiobooks, $FAILED failed" "dialog-warning"
fi

echo -e "${GREEN}Re-download complete!${NC}"
echo "  Succeeded: $SUCCESS"
echo "  Failed: $FAILED"
echo ""
echo "The audiobook-converter service will automatically convert the re-downloaded files."
echo "Run the library scanner after conversion completes to update the web interface:"
echo "  cd $AUDIOBOOKS_HOME/library/scanner"
echo "  python3 scan_audiobooks.py"
