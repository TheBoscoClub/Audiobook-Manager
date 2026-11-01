# Opus Audio Metadata Location

**Opus files store metadata in stream-level tags, NOT format-level tags.**

- MP3/M4A/M4B: container tags → `format.tags`
- Opus/Ogg: Vorbis comments on the audio stream → `streams[0].tags` (`jq '.format.tags'` returns `null`/empty)
- Always run ffprobe with `-show_streams`, not just `-show_format`; read `format.tags`, and if empty fall back to `streams[0].tags`

## Affected Code

Any new code reading audio metadata must handle both locations. Files: `.claude/docs/rules-reference/audio-metadata.md`.
