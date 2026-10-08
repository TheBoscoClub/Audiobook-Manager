# Opus Audio Metadata Location

**Opus files store metadata in stream-level tags, NOT format-level tags.**

- MP3/M4A/M4B: container tags → `format.tags`
- Opus/Ogg: Vorbis comments on the audio stream → `streams[0].tags` (`jq '.format.tags'` returns `null`/empty)
- Always run ffprobe with `-show_streams`, not just `-show_format`; read `format.tags`, and if empty fall back to `streams[0].tags`

## Affected Code

- `library/scanner/metadata_utils.py` - `run_ffprobe()` already uses `-show_streams`
- `library/tests/test_metadata_consistency.py` - `get_file_metadata()` checks both locations
- Any new code reading audio metadata must handle both locations
