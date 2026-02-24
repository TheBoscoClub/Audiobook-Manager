# Cover Art Recovery & Prevention

**Date**: 2026-02-24
**Status**: Approved

## Problem

645 of 1841 audiobooks have `cover_path = NULL` in the database despite having standalone `.jpg` cover files in their library directories. Root cause: AAXtoMP3 extracts cover art as `{title}.jpg` alongside the opus file, then tries to embed it into the opus using mutagen via `python3` — but system python3 lacks mutagen (only the app venv has it). The scanner's `extract_cover_art()` only checks for embedded video streams, so it finds nothing.

## Fix 1: Scanner fallback in `extract_cover_art()`

**File**: `library/scanner/metadata_utils.py`

After ffprobe finds no embedded art, look for standalone cover files in the same directory as the audio file:
1. `{stem}.jpg` — AAXtoMP3's naming convention
2. `{stem}.png`
3. `cover.jpg` — common convention
4. `cover.png`

If found, copy to `output_dir/{hash}.jpg` (same MD5-of-filepath hash convention). Return filename as normal. Embedded art extraction remains the priority path.

No caller changes needed — `scan_audiobooks.py`, `add_new_audiobooks.py`, and `import_to_db.py` all use `extract_cover_art()`.

## Fix 2: AAXtoMP3 venv python for mutagen

**File**: `converter/AAXtoMP3`

Use `AUDIOBOOKS_VENV_PYTHON` variable (default: `/opt/audiobooks/venv/bin/python`, fallback: `python3`) in three locations:
1. Mutagen availability check (~line 351)
2. `embed_ogg_cover()` heredoc (~line 147)
3. Guard before calling `embed_ogg_cover` (~line 1041)

## Recovery

After deploying both fixes, run the scanner. It will automatically pick up the 645 standalone `.jpg` files and populate `.covers/` + database.
