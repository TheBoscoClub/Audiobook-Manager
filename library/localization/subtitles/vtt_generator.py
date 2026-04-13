"""Generate WebVTT subtitle files from timestamped cues."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class VTTCue:
    """A single subtitle cue with timing and text."""

    start_ms: int
    end_ms: int
    text: str


def _format_timestamp(ms: int) -> str:
    """Format milliseconds as VTT timestamp (HH:MM:SS.mmm)."""
    hours = ms // 3_600_000
    minutes = (ms % 3_600_000) // 60_000
    seconds = (ms % 60_000) // 1_000
    millis = ms % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def generate_vtt(cues: list[VTTCue], output_path: Path) -> Path:
    """Write a list of VTTCues to a WebVTT file.

    Args:
        cues: Ordered list of subtitle cues.
        output_path: Path to write the .vtt file.

    Returns:
        The output path for convenience.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["WEBVTT", ""]
    for i, cue in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(
            f"{_format_timestamp(cue.start_ms)} --> {_format_timestamp(cue.end_ms)}"
        )
        lines.append(cue.text)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def generate_dual_vtt(
    source_cues: list[VTTCue],
    translated_cues: list[VTTCue],
    output_path: Path,
) -> Path:
    """Write a dual-language VTT file with both source and translated text.

    Each cue shows both languages stacked (source on top, translation below).
    """
    if len(source_cues) != len(translated_cues):
        raise ValueError(
            f"Cue count mismatch: {len(source_cues)} source vs {len(translated_cues)} translated"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["WEBVTT", ""]
    for i, (src, tr) in enumerate(zip(source_cues, translated_cues), 1):
        lines.append(str(i))
        lines.append(
            f"{_format_timestamp(src.start_ms)} --> {_format_timestamp(src.end_ms)}"
        )
        lines.append(src.text)
        lines.append(tr.text)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
