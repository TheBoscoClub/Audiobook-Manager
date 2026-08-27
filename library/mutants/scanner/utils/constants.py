"""
Canonical definitions for scanner constants and shared filtering helpers.

All scanner modules should import SUPPORTED_FORMATS and is_cover_art_file
from here rather than defining their own copies.
"""

from pathlib import Path

# Supported audiobook file extensions
SUPPORTED_FORMATS = [".m4b", ".opus", ".m4a", ".mp3"]


def is_cover_art_file(filename: str | Path) -> bool:
    """Check whether a filename represents a cover art sidecar file.

    Cover art files use the naming convention ``<title>.cover.<ext>``
    (e.g. ``MyBook.cover.opus``).

    Args:
        filename: A filename string or Path object. Only the final
            component (``Path.name``) is inspected.

    Returns:
        True if the filename contains ``.cover.`` (case-insensitive).
    """
    name = filename.name if isinstance(filename, Path) else filename
    return ".cover." in name.lower()
