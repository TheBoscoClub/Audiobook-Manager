"""Local enrichment provider — extracts metadata from files without API calls.

Sources:
1. ASIN from .voucher files and source filenames
2. Series from audio tags (series, series-part)
3. Series from title parsing (regex patterns)
"""

import re
from pathlib import Path
from typing import Optional

from scripts.enrichment.base import EnrichmentProvider
from scanner.metadata_utils import extract_asin

# Title-based series parsing patterns (from populate_series_from_audible.py)
TITLE_SERIES_PATTERNS = [
    re.compile(
        r"^.+?:\s+(.+?),?\s+(?:Book|#)\s*(\d+(?:\.\d+)?)\s*(?:\(|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\((.+?)\s+(?:Book|#)\s*(\d+(?:\.\d+)?)\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^.+?:\s+(?:A\s+)?(.+?)\s+Novel\s*(?:\(|$)",
        re.IGNORECASE,
    ),
]


def _parse_sequence(seq_str: str) -> Optional[float]:
    """Parse sequence string to a number."""
    if not seq_str:
        return None
    try:
        return float(seq_str)
    except ValueError:
        m = re.search(r"[\d.]+", seq_str)
        if m:
            try:
                return float(m.group())
            except ValueError:
                pass
    return None


def _parse_series_from_title(title: str) -> tuple[str, Optional[float]]:
    """Extract series name and number from title string."""
    if not title:
        return ("", None)
    clean = re.sub(r"\s*\((Un)?abridged\)\s*$", "", title, flags=re.IGNORECASE)
    for pattern in TITLE_SERIES_PATTERNS:
        m = pattern.search(clean)
        if m:
            groups = m.groups()
            series_name = groups[0].strip().rstrip(",")
            seq = None
            if len(groups) > 1 and groups[1]:
                seq = _parse_sequence(groups[1])
            return (series_name, seq)
    return ("", None)


class LocalProvider(EnrichmentProvider):
    """Extract metadata from local files without any API calls."""

    name = "local"

    def __init__(self, sources_dir: Optional[Path] = None):
        super().__init__()
        self.sources_dir = sources_dir

    def can_enrich(self, book: dict) -> bool:
        return True

    def enrich(self, book: dict) -> dict:
        result = {}
        file_path = Path(book.get("file_path", ""))

        if not book.get("asin"):
            asin = extract_asin(file_path, sources_dir=self.sources_dir)
            if asin:
                result["asin"] = asin

        if not book.get("series"):
            series_part = book.get("series_part", "")
            if series_part:
                seq = _parse_sequence(series_part)
                if seq is not None:
                    result["series_sequence"] = seq

            series_name, seq = _parse_series_from_title(book.get("title", ""))
            if series_name:
                result["series"] = series_name
                if seq is not None and "series_sequence" not in result:
                    result["series_sequence"] = seq

        return result
