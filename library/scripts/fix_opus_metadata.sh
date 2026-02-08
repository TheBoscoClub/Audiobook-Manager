#!/usr/bin/env zsh
# OPUS Metadata Fixer - Extract ALL metadata from AAXC and apply to OPUS files
# Fixes: Author, Narrator, Publisher, Title, Cover Art, Date, Genre, Comment
#
# Features:
# - Multiple AAXC search methods (ASIN, ISBN, title match, fuzzy match)
# - Extracts cover art to web/covers directory
# - Full logging to /tmp/opus_metadata_fix.log
# - Progress tracking every 50 files

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Load configuration
source "$PROJECT_DIR/lib/audiobook-config.sh"

# Use configured paths
AUDIOBOOKS_DIR="$AUDIOBOOKS_DATA"
OPUS_DIR="$AUDIOBOOKS_LIBRARY"
SOURCES_DIR="$AUDIOBOOKS_SOURCES"
COVER_DIR="$AUDIOBOOKS_COVERS"
LOG_FILE="/tmp/opus_metadata_fix.log"

mkdir -p "$COVER_DIR"

echo "================================================================" | tee "$LOG_FILE"
echo "  OPUS Complete Metadata Fixer" | tee -a "$LOG_FILE"
echo "================================================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "This will fix ALL metadata for OPUS files:" | tee -a "$LOG_FILE"
echo "  ✓ Author" | tee -a "$LOG_FILE"
echo "  ✓ Narrator" | tee -a "$LOG_FILE"
echo "  ✓ Publisher" | tee -a "$LOG_FILE"
echo "  ✓ Title" | tee -a "$LOG_FILE"
echo "  ✓ Cover Art" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Estimated time: 2-4 hours for ~2,229 files" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Starting metadata fix..." | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

FIXED=0
FAILED=0
SKIPPED=0
TOTAL=0

# Find all OPUS files (exclude cover files)
while IFS= read -r OPUS_FILE; do
    ((TOTAL++))

    BASENAME=$(basename "$OPUS_FILE" .opus)

    # Skip if we've already processed this many (for resuming)
    if [ $((TOTAL % 50)) -eq 0 ]; then
        echo "" | tee -a "$LOG_FILE"
        echo "Progress: $TOTAL processed, $FIXED fixed, $SKIPPED skipped, $FAILED failed" | tee -a "$LOG_FILE"
        echo "Latest: $BASENAME" | tee -a "$LOG_FILE"
        echo "" | tee -a "$LOG_FILE"
    fi

    # Try to find matching AAXC file
    AAXC_FILE=""

    # Method 1: Search by ASIN (B followed by 9 alphanumeric chars)
    ASIN=$(echo "$BASENAME" | grep -oE 'B[0-9A-Z]{9}' | head -1)
    if [ -n "$ASIN" ]; then
        AAXC_FILE=$(find "$SOURCES_DIR" -maxdepth 1 -name "${ASIN}*.aaxc" -type f 2>/dev/null | head -1)
    fi

    # Method 2: Search by ISBN (10 or 13 digit number at start)
    if [ -z "$AAXC_FILE" ]; then
        ISBN=$(echo "$BASENAME" | grep -oE '^[0-9]{10,13}' | head -1)
        if [ -n "$ISBN" ]; then
            AAXC_FILE=$(find "$SOURCES_DIR" -maxdepth 1 -name "${ISBN}*.aaxc" -type f 2>/dev/null | head -1)
        fi
    fi

    # Method 3: Direct title match (AAXC files starting with title)
    if [ -z "$AAXC_FILE" ]; then
        # Normalize basename: remove colons, convert spaces to underscores
        NORMALIZED_TITLE=$(echo "$BASENAME" | sed 's/:/_/g' | sed 's/ /_/g')
        AAXC_FILE=$(find "$SOURCES_DIR" -maxdepth 1 -name "${NORMALIZED_TITLE}*.aaxc" -type f 2>/dev/null | head -1)
    fi

    # Method 4: Fuzzy title match (try first few words)
    if [ -z "$AAXC_FILE" ]; then
        # Get first 3 words of title, normalize
        TITLE_START=$(echo "$BASENAME" | sed 's/:/_/g' | cut -d' ' -f1-3 | sed 's/ /_/g')
        if [ -n "$TITLE_START" ] && [ ${#TITLE_START} -gt 5 ]; then
            AAXC_FILE=$(find "$SOURCES_DIR" -maxdepth 1 -name "${TITLE_START}*.aaxc" -type f 2>/dev/null | head -1)
        fi
    fi

    if [ -z "$AAXC_FILE" ] || [ ! -f "$AAXC_FILE" ]; then
        echo "⊘ SKIP: $BASENAME (source AAXC not found)" >> "$LOG_FILE"
        ((SKIPPED++))
        continue
    fi

    # Extract ALL metadata from AAXC
    TITLE=$(ffprobe -v error -show_entries format_tags=title -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    AUTHOR=$(ffprobe -v error -show_entries format_tags=artist -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    ALBUM_ARTIST=$(ffprobe -v error -show_entries format_tags=album_artist -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    NARRATOR=$(ffprobe -v error -show_entries format_tags=composer -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    ALBUM=$(ffprobe -v error -show_entries format_tags=album -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    PUBLISHER=$(ffprobe -v error -show_entries format_tags=publisher -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    DATE=$(ffprobe -v error -show_entries format_tags=date -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    GENRE=$(ffprobe -v error -show_entries format_tags=genre -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    COMMENT=$(ffprobe -v error -show_entries format_tags=comment -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)

    # Use AUTHOR if ALBUM_ARTIST is empty
    if [ -z "$ALBUM_ARTIST" ]; then
        ALBUM_ARTIST="$AUTHOR"
    fi

    # Skip if no metadata found
    if [ -z "$AUTHOR" ] && [ -z "$NARRATOR" ] && [ -z "$TITLE" ]; then
        echo "⊘ SKIP: $BASENAME (no metadata in source AAXC)" >> "$LOG_FILE"
        ((SKIPPED++))
        continue
    fi

    # Extract cover art from AAXC
    FILE_HASH=$(echo "$OPUS_FILE" | md5sum | cut -d' ' -f1)
    COVER_PATH="$COVER_DIR/${FILE_HASH}.jpg"

    if [ ! -f "$COVER_PATH" ]; then
        ffmpeg -nostdin -v quiet -i "$AAXC_FILE" -an -vcodec copy "$COVER_PATH" 2>/dev/null
    fi

    # Create temp file for metadata update
    TEMP_FILE="${OPUS_FILE}.tmp.opus"

    # Build ffmpeg metadata arguments
    METADATA_ARGS=()
    [ -n "$TITLE" ] && METADATA_ARGS+=(-metadata "title=$TITLE")
    [ -n "$AUTHOR" ] && METADATA_ARGS+=(-metadata "artist=$AUTHOR")
    [ -n "$ALBUM_ARTIST" ] && METADATA_ARGS+=(-metadata "album_artist=$ALBUM_ARTIST")
    [ -n "$NARRATOR" ] && METADATA_ARGS+=(-metadata "composer=$NARRATOR")
    [ -n "$ALBUM" ] && METADATA_ARGS+=(-metadata "album=$ALBUM")
    [ -n "$PUBLISHER" ] && METADATA_ARGS+=(-metadata "publisher=$PUBLISHER")
    [ -n "$DATE" ] && METADATA_ARGS+=(-metadata "date=$DATE")
    [ -n "$GENRE" ] && METADATA_ARGS+=(-metadata "genre=$GENRE")
    [ -n "$COMMENT" ] && METADATA_ARGS+=(-metadata "comment=$COMMENT")

    # Copy file and add metadata
    # Redirect to temp file first to avoid path corruption in error messages
    FFMPEG_LOG=$(mktemp)
    ffmpeg -nostdin -v warning -i "$OPUS_FILE" \
        "${METADATA_ARGS[@]}" \
        -codec copy \
        "$TEMP_FILE" 2>&1 | tee "$FFMPEG_LOG" | grep -v "Guessed Channel" >> "$LOG_FILE"

    # If ffmpeg failed, log the full error
    if [ ! -f "$TEMP_FILE" ] || [ ! -s "$TEMP_FILE" ]; then
        grep -v "Guessed Channel" "$FFMPEG_LOG" >> "$LOG_FILE"
    fi
    rm -f "$FFMPEG_LOG"

    if [ -f "$TEMP_FILE" ] && [ -s "$TEMP_FILE" ]; then
        mv "$TEMP_FILE" "$OPUS_FILE"
        {
            echo "✓ FIXED: $BASENAME"
            echo "    Author: $AUTHOR"
            echo "    Narrator: $NARRATOR"
            echo "    Publisher: $PUBLISHER"
        } >> "$LOG_FILE"
        ((FIXED++))
    else
        echo "✗ FAIL: $BASENAME (ffmpeg error)" >> "$LOG_FILE"
        rm -f "$TEMP_FILE"
        ((FAILED++))
    fi

done < <(find "$OPUS_DIR" -name "*.opus" -type f ! -name "*.cover.opus" 2>/dev/null)

echo "" | tee -a "$LOG_FILE"
echo "================================================================" | tee -a "$LOG_FILE"
echo "  COMPLETE" | tee -a "$LOG_FILE"
echo "================================================================" | tee -a "$LOG_FILE"
echo "  Total files:    $TOTAL" | tee -a "$LOG_FILE"
echo "  Fixed:          $FIXED" | tee -a "$LOG_FILE"
echo "  Skipped:        $SKIPPED" | tee -a "$LOG_FILE"
echo "  Failed:         $FAILED" | tee -a "$LOG_FILE"
echo "================================================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Log file: $LOG_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Next steps:" | tee -a "$LOG_FILE"
echo "  1. Re-scan library: cd scanner && python3 scan_audiobooks.py" | tee -a "$LOG_FILE"
echo "  2. Re-import database: python backend/import_to_db.py" | tee -a "$LOG_FILE"
echo "  3. Refresh web interface (click Refresh button)" | tee -a "$LOG_FILE"
