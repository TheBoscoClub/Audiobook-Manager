"""
Shared utility functions for the Audiobooks library.
Consolidates common operations used across scanner and scripts.
"""

import hashlib
import re
from pathlib import Path

# Default chunk size for file operations (8MB)
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024


def calculate_sha256(
    filepath: Path | str, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> str | None:
    """
    Calculate SHA-256 hash of a file.

    Args:
        filepath: Path to the file to hash
        chunk_size: Size of chunks to read (default 8MB for efficiency)

    Returns:
        Hexadecimal SHA-256 hash string, or None on error
    """
    sha256 = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()
    except (IOError, OSError):
        return None


def normalize_title(title: str) -> str:
    """
    Normalize audiobook title for matching/comparison.

    Performs the following normalizations:
    - Removes common audiobook suffixes: (Unabridged), [Unabridged], [Tantor],
      (Audible Audio Edition)
    - Removes genre suffixes: ": A Novel", ": A Memoir"
    - Removes all punctuation except spaces
    - Converts to lowercase
    - Collapses multiple spaces to single space

    Args:
        title: The title string to normalize

    Returns:
        Normalized title for comparison, or empty string if input is empty/None

    Example:
        >>> normalize_title("The Great Novel: A Novel (Unabridged)")
        'the great novel'
    """
    if not title:
        return ""
    # Remove common audiobook suffixes (case-insensitive)
    title = re.sub(r"\s*\(Unabridged\)\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\[Unabridged\]\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\[Tantor\]\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(Audible Audio Edition\)\s*$", "", title, flags=re.IGNORECASE)
    # Remove genre suffixes
    title = re.sub(r"\s*:\s*A Novel\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*:\s*A Memoir\s*$", "", title, flags=re.IGNORECASE)
    # Remove punctuation (keep only word characters and spaces)
    title = re.sub(r"[^\w\s]", "", title)
    # Lowercase and collapse whitespace
    title = " ".join(title.lower().split())
    return title


def sanitize_filename(name: str, max_length: int = 255) -> str:
    """
    Sanitize a string for use as a filename.

    Args:
        name: The string to sanitize
        max_length: Maximum length for the filename (default 255)

    Returns:
        Sanitized filename string
    """
    if not name:
        return "Unknown"

    # Remove or replace invalid characters
    # Keep: alphanumeric, spaces, hyphens, underscores, periods
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)

    # Replace multiple spaces with single space
    sanitized = re.sub(r"\s+", " ", sanitized)

    # Strip leading/trailing whitespace and periods
    sanitized = sanitized.strip(" .")

    # Limit length
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    return sanitized or "Unknown"
