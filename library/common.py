"""
Shared utility functions for the Audiobooks library.
Consolidates common operations used across scanner and scripts.
"""

import hashlib
import ipaddress
import re
from pathlib import Path
from typing import Optional

# Default chunk size for file operations (8MB)
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024

# Hostname form of the loopback interface. Reaches this code as a literal
# string in forwarded headers, where no IP parse applies.
_LOOPBACK_NAMES = frozenset({"localhost"})


def is_loopback_address(address: Optional[str]) -> bool:
    """Return True if ``address`` denotes the local machine.

    Single canonical loopback test for the whole project — shared by the
    reverse proxy (which decides what client address to forward) and the
    Flask ``localhost_only`` / ``admin_or_localhost`` decorators (which
    authorize on it). Two independent string comparisons against
    ``("127.0.0.1", "::1", "localhost")`` used to answer this question, and
    both missed the rest of 127.0.0.0/8 and IPv4-mapped IPv6 forms.

    Accepts every textual form that arrives in ``request.remote_addr`` or a
    forwarding header: anything in 127.0.0.0/8, ``::1``, IPv4-mapped
    equivalents such as ``::ffff:127.0.0.1``, and the literal ``localhost``.

    Fails closed: an empty or unparseable value is NOT loopback, because
    callers use a True result to grant access.
    """
    if not address:
        return False
    candidate = address.strip()
    if not candidate:
        return False
    if candidate.lower() in _LOOPBACK_NAMES:
        return True
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped.is_loopback
    return parsed.is_loopback


def calculate_sha256(filepath: Path | str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str | None:
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
    except (IOError, OSError):  # fmt: skip
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
