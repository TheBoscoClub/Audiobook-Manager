# Opus Audio Metadata Location — reference (NOT auto-loaded)

Detail moved out of `.claude/rules/audio-metadata.md`, same headings. Verbatim; the rule file keeps the binding text.

## Affected Code

- `library/scanner/metadata_utils.py` - `run_ffprobe()` already uses `-show_streams`

- `library/tests/test_metadata_consistency.py` - `get_file_metadata()` checks both locations

- Any new code reading audio metadata must handle both locations
