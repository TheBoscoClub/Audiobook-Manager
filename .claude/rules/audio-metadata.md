# Opus Audio Metadata Location

**Opus files store metadata in stream-level tags, NOT format-level tags.**

## The Issue

When extracting metadata from `.opus` files using ffprobe:
- **Wrong**: `ffprobe ... | jq '.format.tags'` -> Returns `null` or empty
- **Correct**: `ffprobe ... | jq '.streams[0].tags'` -> Returns actual metadata

## Why This Matters

This project converts audiobooks to Opus format. When reading metadata:
```python
# WRONG - will return None for Opus files
tags = data.get("format", {}).get("tags", {})

# CORRECT - check both locations
tags = data.get("format", {}).get("tags", {})
if not tags:
    streams = data.get("streams", [])
    if streams:
        tags = streams[0].get("tags", {})
```

## Technical Background

- MP3/M4A/M4B: Metadata in container format (`format.tags`)
- Opus/Ogg: Metadata in Vorbis comments on the audio stream (`streams[0].tags`)
- Always use `-show_streams` with ffprobe, not just `-show_format`

## Affected Code

- `library/scanner/metadata_utils.py` - `run_ffprobe()` already uses `-show_streams`
- `library/tests/test_metadata_consistency.py` - `get_file_metadata()` checks both locations
- Any new code reading audio metadata must handle both locations
