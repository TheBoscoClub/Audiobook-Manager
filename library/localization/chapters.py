"""Chapter extraction and audio splitting for audiobook files.

Extracts chapter boundaries from embedded metadata (ffprobe) or Audible
sidecar files (chapters.json), then splits the audio into per-chapter
temporary files using ffmpeg stream copy (no re-encoding).
"""

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Chapter:
    index: int
    title: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def start_sec(self) -> float:
        return self.start_ms / 1000.0

    @property
    def end_sec(self) -> float:
        return self.end_ms / 1000.0


def extract_chapters(audio_path: Path) -> list[Chapter]:
    """Extract chapter list from an audiobook file.

    Tries embedded chapter metadata first (ffprobe -show_chapters), then
    falls back to an Audible chapters.json sidecar in the same directory.
    Returns an empty list if no chapter data is found.
    """
    chapters = _chapters_from_ffprobe(audio_path)
    if not chapters:
        chapters = _chapters_from_sidecar(audio_path)
    if chapters:
        logger.info(
            "Found %d chapters in %s (total %.1f min)",
            len(chapters),
            audio_path.name,
            sum(c.duration_ms for c in chapters) / 60_000,
        )
    return chapters


def _chapters_from_ffprobe(audio_path: Path) -> list[Chapter]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_chapters",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.warning("ffprobe chapter extraction failed: %s", e)
        return []

    chapters = []
    for ch in data.get("chapters", []):
        start_ms = int(float(ch.get("start_time", 0)) * 1000)
        end_ms = int(float(ch.get("end_time", 0)) * 1000)
        tags = ch.get("tags", {})
        title = tags.get("title", f"Chapter {ch.get('id', len(chapters)) + 1}")
        chapters.append(
            Chapter(
                index=len(chapters),
                title=title,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
    return chapters


def _chapters_from_sidecar(audio_path: Path) -> list[Chapter]:
    sidecar = audio_path.parent / "chapters.json"
    if not sidecar.exists():
        return []
    try:
        with open(sidecar) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("chapters.json parse failed: %s", e)
        return []

    chapter_info = data.get("content_metadata", {}).get("chapter_info", {})
    raw_chapters = chapter_info.get("chapters", [])
    if not raw_chapters:
        return []

    chapters = []
    for i, ch in enumerate(raw_chapters):
        start_ms = ch.get("start_offset_ms", 0)
        length_ms = ch.get("length_ms", 0)
        title = ch.get("title", f"Chapter {i + 1}")
        chapters.append(
            Chapter(
                index=i,
                title=title,
                start_ms=start_ms,
                end_ms=start_ms + length_ms,
            )
        )
    return chapters


def split_chapter(
    audio_path: Path,
    chapter: Chapter,
    output_dir: Path | None = None,
) -> Path:
    """Extract a single chapter from an audiobook as a temporary file.

    Uses ffmpeg stream copy — no re-encoding, instant extraction.
    Caller is responsible for cleaning up the returned file.
    """
    suffix = audio_path.suffix or ".opus"
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"ch{chapter.index:03d}{suffix}"
    else:
        tmp = tempfile.NamedTemporaryFile(
            suffix=suffix,
            prefix=f"ch{chapter.index:03d}_",
            delete=False,
        )
        tmp.close()
        out_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{chapter.start_sec:.3f}",
        "-to",
        f"{chapter.end_sec:.3f}",
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-map_metadata",
        "-1",
        str(out_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        logger.error("ffmpeg chapter split failed: %s", result.stderr[-500:])
        raise RuntimeError(
            f"Failed to extract chapter {chapter.index}: {result.stderr[-200:]}"
        )
    return out_path
