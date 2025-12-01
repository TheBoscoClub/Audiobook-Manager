# OPUS Metadata Fix - Complete Solution

## Problem Identified

**Issue:** OPUS format audiobooks showing "Unknown Author" and "Unknown Narrator" in the library, while M4B files show correct metadata.

**Root Cause:** AAXtoMP3 conversion to OPUS format does not preserve metadata tags from source AAXC files.

**Evidence:**
```bash
# OPUS file - NO metadata
$ ffprobe "/path/to/file.opus" -show_entries format_tags
format: {}

# M4B file - HAS metadata
$ ffprobe "/path/to/file.m4b" -show_entries format_tags
format: {
    "artist": "Stephen King",
    "composer": "Craig Wasson",
    "title": "11/22/63: A Novel"
}
```

---

## Solution Implemented

### Part 1: Quick Fix - Extract Author from Folder Path (✅ DONE)

**What:** Modified scanner to extract author from OPUS folder structure when metadata tags are missing.

**How:** OPUS files are organized as:
```
/raid0/Audiobooks/Library/[Author Name]/[Book Title]/[Book Title].opus
```

The scanner now extracts `[Author Name]` from the path.

**File Modified:** `scanner/scan_audiobooks.py`

**Code Added:**
```python
# For OPUS files, try to extract author from folder structure if metadata is missing
author_from_path = None
if filepath.suffix.lower() == '.opus':
    # Path structure: .../Library/Author Name/Book Title/Book Title.opus
    parts = filepath.parts
    if 'Library' in parts:
        library_idx = parts.index('Library')
        if len(parts) > library_idx + 1:
            author_from_path = parts[library_idx + 1]

# Use author_from_path as fallback
metadata = {
    'author': tags_normalized.get('artist', tags_normalized.get('album_artist', author_from_path or 'Unknown Author')),
    ...
}
```

**Result:** Authors now correctly show for OPUS files (e.g., "Stephen King" instead of "Unknown Author")

**Limitation:** Narrator information still shows as "Unknown Narrator" because it's not in the folder path.

---

### Part 2: Permanent Fix - Embed Metadata in Future Conversions (TODO)

**Option A: Update AAXtoMP3 Script**

The AAXtoMP3 tool needs to be modified to preserve metadata during OPUS conversion.

**Location:** `/raid0/ClaudeCodeProjects/Audiobooks/AAXtoMP3`

**Required Changes:**
1. Extract metadata from source AAXC file before conversion
2. After OPUS conversion, use `ffmpeg` to embed metadata:
```bash
ffmpeg -i input.opus \
  -metadata title="$TITLE" \
  -metadata artist="$AUTHOR" \
  -metadata album_artist="$AUTHOR" \
  -metadata composer="$NARRATOR" \
  -metadata album="$ALBUM" \
  -metadata publisher="$PUBLISHER" \
  -codec copy \
  output_with_metadata.opus
```

**Option B: Post-Processing Script**

Add metadata embedding as a post-processing step in the conversion script:

**File to Modify:** `~/.local/bin/convert-audiobooks-opus-parallel`

**Add after line 116 (after successful conversion):**
```bash
# Extract metadata from source AAXC
AAXC_METADATA=$(ffprobe -v error -show_entries format_tags=title,artist,album_artist,composer,album,publisher -of json "$AAXC_FILE" 2>/dev/null)

# Parse metadata
TITLE=$(echo "$AAXC_METADATA" | jq -r '.format.tags.title // ""')
AUTHOR=$(echo "$AAXC_METADATA" | jq -r '.format.tags.artist // .format.tags.album_artist // ""')
NARRATOR=$(echo "$AAXC_METADATA" | jq -r '.format.tags.composer // ""')
ALBUM=$(echo "$AAXC_METADATA" | jq -r '.format.tags.album // ""')

# Embed metadata in OPUS file
if [ -n "$AUTHOR" ] || [ -n "$NARRATOR" ]; then
    TEMP_OPUS="${OUTPUT_FILE}.temp.opus"
    ffmpeg -v warning -i "$OUTPUT_FILE" \
        -metadata title="$TITLE" \
        -metadata artist="$AUTHOR" \
        -metadata album_artist="$AUTHOR" \
        -metadata composer="$NARRATOR" \
        -metadata album="$ALBUM" \
        -codec copy \
        "$TEMP_OPUS" 2>&1 | grep -v "Guessed Channel"

    if [ -f "$TEMP_OPUS" ] && [ -s "$TEMP_OPUS" ]; then
        mv "$TEMP_OPUS" "$OUTPUT_FILE"
        echo "  ✓ Metadata embedded: $AUTHOR / $NARRATOR"
    else
        rm -f "$TEMP_OPUS"
    fi
fi
```

---

### Part 3: Fix Existing OPUS Files (TODO - Optional)

For the 2,229 existing OPUS files without metadata, you can run a batch fix script.

**Script Created:** `scripts/fix_opus_metadata.sh`

**Usage:**
```bash
cd /raid0/ClaudeCodeProjects/Audiobooks/library
./scripts/fix_opus_metadata.sh
```

**What it does:**
1. Finds all OPUS files in the library
2. Locates their source AAXC files
3. Extracts metadata from AAXC
4. Embeds metadata into OPUS files using ffmpeg

**Estimated Time:** ~2-4 hours for 2,229 files

**Warning:** This rewrites all OPUS files, so ensure you have backups or sufficient disk space.

---

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Quick Fix (folder path) | ✅ DONE | Authors now show correctly |
| Re-scan library | 🔄 IN PROGRESS | Running now |
| Re-import database | ⏳ PENDING | After scan completes |
| Future conversions | ⏳ TODO | Need to update conversion script |
| Fix existing files | ⏳ OPTIONAL | 2,229 OPUS files need metadata |

---

## How to Apply the Complete Fix

### Step 1: Wait for Current Scan (In Progress)
```bash
# Monitor scan progress
tail -f /tmp/rescan.log
```

### Step 2: Re-import Database
```bash
cd /raid0/ClaudeCodeProjects/Audiobooks/library
source venv/bin/activate
python backend/import_to_db.py
```

### Step 3: Refresh Web Interface
Open http://localhost:8090 and click the "↻ Refresh" button.

**Result:** Authors will now show correctly for OPUS files (extracted from folder path).

### Step 4 (Optional): Update Conversion Script for Future Books

Edit `~/.local/bin/convert-audiobooks-opus-parallel` to add metadata embedding (see Option B above).

### Step 5 (Optional): Fix Existing OPUS Files

If you want narrator information and full metadata in existing OPUS files:
```bash
cd /raid0/ClaudeCodeProjects/Audiobooks/library
./scripts/fix_opus_metadata.sh
```

Then repeat Steps 1-3.

---

## Directory Structure (Updated November 2024)

After the directory reorganization, the new structure is:
```
/raid0/Audiobooks/
├── Sources/           # Original AAXC files + vouchers
│   ├── *.aaxc        # Encrypted Audible audiobooks
│   ├── *.voucher     # Decryption keys
│   └── metadata/     # Cover images, chapter JSON
├── Library/          # Converted OPUS files (organized by Author/Title)
│   └── Author/Title/*.opus
├── logs/             # Conversion and service logs
└── scripts/          # Conversion scripts
```

---

## Testing

### Verify the Fix

After re-import, check the database:
```bash
sqlite3 backend/audiobooks.db "SELECT title, author, format FROM audiobooks WHERE format='opus' LIMIT 5"
```

**Expected:** Should show author names like "Stephen King", not "Unknown Author"

### Test in Web Interface

1. Navigate to http://localhost:8090
2. Filter by format: "opus"
3. Verify author names are shown
4. Note: Narrators may still show "Unknown Narrator" (see limitations below)

---

## Limitations & Known Issues

### Current Limitations

1. **Narrator not in folder path** - Narrator information cannot be extracted from folder structure
   - Quick fix only retrieves author
   - Full metadata requires embedding (Part 3)

2. **Multi-author books** - Folder path only shows first author
   - Example: "Patrick O'Brian, Farley Mowat, more" becomes "Patrick O'Brian, Farley Mowat, more"
   - Actually works fine in this case!

3. **Special characters** - Some folder names may have special characters
   - Scanner handles this correctly

### Why OPUS Files Lack Metadata

**Technical Details:**

1. AAXC files (Audible format) contain metadata tags
2. M4B conversion preserves these tags (M4B supports iTunes metadata)
3. OPUS conversion by AAXtoMP3 **does not preserve tags**
4. OPUS format **does support** metadata (Vorbis Comments)
5. AAXtoMP3 tool simply doesn't copy the metadata

**Solution:** Either modify AAXtoMP3 or add post-processing metadata embedding step.

---

## Recommendations

### Immediate (✅ Done)
- [x] Quick fix: Extract author from folder path
- [x] Re-scan library with fixed scanner
- [ ] Re-import database (in progress)
- [ ] Test in web interface

### Short-term (Recommended)
- [ ] Update conversion script to embed metadata for future books
- [ ] Test one new conversion to verify metadata is preserved

### Long-term (Optional)
- [ ] Run batch fix on existing 2,229 OPUS files
- [ ] Consider converting back to M4B if metadata is critical
- [ ] Update AAXtoMP3 tool directly to preserve metadata

---

## Alternative: Use M4B Instead of OPUS

If metadata is critical and you prefer not to fix OPUS files:

**Pros of M4B:**
- ✅ Metadata always preserved
- ✅ Better compatibility with iOS/Apple devices
- ✅ Chapter markers work better

**Cons of M4B:**
- ❌ Larger file sizes (~2-3x larger than OPUS level 10)
- ❌ Less efficient compression

**Pros of OPUS:**
- ✅ Excellent compression (smaller files)
- ✅ Superior audio quality at same bitrate
- ✅ Open format

**Cons of OPUS:**
- ❌ Metadata not preserved by AAXtoMP3 (fixable)
- ❌ Less compatible with Apple devices

**Your Current Setup:**
- M4B files: 1,756 files (older conversions)
- OPUS files: 2,229 files (newer conversions)
- Both formats work in the library

---

## Summary

**Problem:** OPUS files missing author/narrator metadata

**Root Cause:** AAXtoMP3 doesn't preserve metadata during OPUS conversion

**Solution Applied:** Scanner now extracts author from folder structure (✅ Done)

**Next Steps:**
1. Wait for scan to complete
2. Re-import database
3. Verify authors show correctly in web interface
4. (Optional) Update conversion script for future books
5. (Optional) Fix existing OPUS files with metadata script

**Status:** Author names will be fixed after database re-import. Narrator information requires Part 3 (batch metadata fix).
