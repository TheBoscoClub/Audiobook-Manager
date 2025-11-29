#!/bin/bash
# Fix OPUS Metadata - Extract metadata from AAXC and apply to OPUS files
# This script fixes existing OPUS files that are missing author/narrator metadata

AUDIOBOOKS_DIR="/raid0/Audiobooks"
OPUS_DIR="/raid0/Audiobooks/Audiobooks-Converted-Opus-nocomp"

echo "================================================================"
echo "  OPUS Metadata Fixer"
echo "================================================================"
echo ""
echo "This script will:"
echo "  1. Find OPUS files with missing metadata"
echo "  2. Locate their source AAXC files"
echo "  3. Copy metadata from AAXC to OPUS"
echo ""

FIXED=0
FAILED=0
TOTAL=0

# Find all OPUS files
while IFS= read -r OPUS_FILE; do
    ((TOTAL++))

    BASENAME=$(basename "$OPUS_FILE" .opus)

    # Try to find matching AAXC file
    # Search pattern: B*_${BASENAME}*.aaxc or similar
    SEARCH_PATTERN=$(echo "$BASENAME" | sed 's/_/ /g; s/-AAX.*//; s/:.*//')

    AAXC_FILE=$(find "$AUDIOBOOKS_DIR" -maxdepth 1 -name "*.aaxc" -type f | while read -r f; do
        if echo "$(basename "$f")" | grep -qi "$SEARCH_PATTERN"; then
            echo "$f"
            break
        fi
    done)

    if [ -z "$AAXC_FILE" ]; then
        echo "⊘ SKIP: $BASENAME (source AAXC not found)"
        ((FAILED++))
        continue
    fi

    # Extract metadata from AAXC
    TITLE=$(ffprobe -v error -show_entries format_tags=title -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)
    AUTHOR=$(ffprobe -v error -show_entries format_tags=artist,album_artist -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null | head -1)
    NARRATOR=$(ffprobe -v error -show_entries format_tags=composer,narrator -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null | head -1)
    ALBUM=$(ffprobe -v error -show_entries format_tags=album -of default=noprint_wrappers=1:nokey=1 "$AAXC_FILE" 2>/dev/null)

    if [ -z "$AUTHOR" ] && [ -z "$NARRATOR" ]; then
        echo "⊘ SKIP: $BASENAME (no metadata in source)"
        ((FAILED++))
        continue
    fi

    # Create temp file for metadata update
    TEMP_FILE="${OPUS_FILE}.tmp.opus"

    # Copy file and add metadata
    ffmpeg -v warning -i "$OPUS_FILE" \
        -metadata title="$TITLE" \
        -metadata artist="$AUTHOR" \
        -metadata album_artist="$AUTHOR" \
        -metadata composer="$NARRATOR" \
        -metadata album="$ALBUM" \
        -codec copy \
        "$TEMP_FILE" 2>&1 | grep -v "Guessed Channel"

    if [ -f "$TEMP_FILE" ] && [ -s "$TEMP_FILE" ]; then
        mv "$TEMP_FILE" "$OPUS_FILE"
        echo "✓ FIXED: $BASENAME"
        echo "    Author: $AUTHOR"
        echo "    Narrator: $NARRATOR"
        ((FIXED++))
    else
        echo "✗ FAIL: $BASENAME (ffmpeg error)"
        rm -f "$TEMP_FILE"
        ((FAILED++))
    fi

    # Progress every 10 files
    if [ $((TOTAL % 10)) -eq 0 ]; then
        echo ""
        echo "Progress: $TOTAL processed, $FIXED fixed, $FAILED failed/skipped"
        echo ""
    fi

done < <(find "$OPUS_DIR" -name "*.opus" -type f 2>/dev/null)

echo ""
echo "================================================================"
echo "  COMPLETE"
echo "================================================================"
echo "  Total files:    $TOTAL"
echo "  Fixed:          $FIXED"
echo "  Failed/Skipped: $FAILED"
echo "================================================================"
echo ""
echo "Next steps:"
echo "  1. Re-scan library: cd scanner && python3 scan_audiobooks.py"
echo "  2. Re-import database: python backend/import_to_db.py"
echo "  3. Refresh web interface (click Refresh button)"
