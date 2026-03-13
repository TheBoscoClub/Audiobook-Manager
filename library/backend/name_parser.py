"""
Multi-author/narrator name parser.

Parses delimited name strings into individual names and generates
sort keys in "Last, First" format. Handles three tiers:
- Tier 1: Structured metadata (multiple separate tags) - handled by caller
- Tier 2: Delimiter-based splitting (this module)
- Tier 3: Single name fallback (this module)
"""

import re

# Known performance/production group names - always narrators, never authors.
# If detected in author metadata, caller should redirect to narrator list.
GROUP_NAMES = frozenset(
    {
        "full cast",
        "bbc radio",
        "bbc radio 4",
        "bbc radio drama",
        "various authors",
        "various narrators",
        "various",
        "audiobook",
        "unknown author",
        "unknown narrator",
    }
)

# Last name prefixes that should stay attached to the surname
LAST_NAME_PREFIXES = frozenset(
    {
        "le",
        "de",
        "la",
        "van",
        "von",
        "der",
        "den",
        "del",
        "da",
        "di",
        "du",
        "el",
        "al",
        "bin",
        "ibn",
        "mac",
        "mc",
        "o'",
    }
)

# Names to treat as empty/unknown
EMPTY_NAMES = frozenset(
    {
        "unknown author",
        "unknown narrator",
        "audiobook",
        "",
    }
)


def is_group_name(name: str) -> bool:
    """Check if a name is a known group/ensemble name."""
    if not name:
        return False
    return name.strip().lower() in GROUP_NAMES


def generate_sort_name(name: str | None) -> str:
    """Generate a 'Last, First' sort key from a single person's name.

    Returns:
        Sort name string. Empty string for None/unknown names.
        Group names returned as-is (no first/last split).
    """
    if not name or name.strip().lower() in EMPTY_NAMES:
        return ""

    clean = name.strip()

    # Strip role suffixes: "(editor)", "(translator)", etc.
    clean = re.sub(r"\s*\([^)]*\)\s*$", "", clean).strip()

    # Strip "Author - role" format
    if " - " in clean:
        clean = clean.split(" - ")[0].strip()

    if not clean:
        return ""

    # Group names: return as-is
    if is_group_name(clean):
        return clean

    words = clean.split()

    if len(words) == 1:
        return words[0]

    # Determine where last name starts (handle prefixes)
    last_start = len(words) - 1
    if len(words) > 2 and words[-2].lower().rstrip("'") in LAST_NAME_PREFIXES:
        last_start = len(words) - 2

    first_parts = words[:last_start]
    last_parts = words[last_start:]

    last_name = " ".join(last_parts)
    if first_parts:
        first_name = " ".join(first_parts)
        return f"{last_name}, {first_name}"
    return last_name


def parse_names(raw: str | None) -> list[str]:
    """Parse a potentially multi-name string into individual names.

    Splitting priority:
    1. Semicolons (;)
    2. " and " (with spaces)
    3. " & " (with spaces)
    4. Commas - with Last,First vs Author1,Author2 heuristic

    Returns:
        List of individual name strings, stripped and cleaned.
        Empty list for None/empty input.
    """
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    # Tier 2a: Semicolons - least ambiguous
    if ";" in text:
        return _clean_parts(text.split(";"))

    # Tier 2b: " and " - with spaces to avoid matching "Anderson"
    if " and " in text.lower():
        # Split on " and " case-insensitively
        parts = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
        if len(parts) > 1:
            return _clean_parts(parts)

    # Tier 2c: " & " - with spaces
    if " & " in text:
        return _clean_parts(text.split(" & "))

    # Tier 2d: Commas - need disambiguation
    if "," in text:
        return _parse_comma_separated(text)

    # Tier 3: Single name
    return [text.strip()]


def _clean_parts(parts: list[str]) -> list[str]:
    """Strip whitespace and role suffixes, filter empties."""
    result = []
    for p in parts:
        clean = re.sub(r"\s*\([^)]*\)\s*$", "", p.strip()).strip()
        if clean:
            result.append(clean)
    return result


def _parse_comma_separated(text: str) -> list[str]:
    """Handle comma-separated names with Last,First disambiguation.

    Heuristic:
    - Two tokens, each single word: "King, Stephen" -> Last, First -> one name
    - Two tokens, first multi-word + second single word: "de Saint-Exupery, Antoine"
      -> compound Last, First -> one name
    - Multi-word tokens on both sides: "Stephen King, Peter Straub" -> multiple authors
    - Alternating single words (all tokens single word): pairs of Last, First
    - If any token has spaces/hyphens: conservative treatment, flag for review
    """
    parts = [p.strip() for p in text.split(",") if p.strip()]

    if len(parts) == 2:
        # Two parts: is it "Last, First" or "Author1, Author2"?
        words_a = parts[0].split()
        words_b = parts[1].split()

        if len(words_b) == 1 and len(words_a) >= 1:
            # Second part is a single word (first name).
            # Could be "King, Stephen" or "de Saint-Exupery, Antoine".
            # Both are "Last, First" format.
            if len(words_a) == 1:
                # Simple: "King, Stephen" -> "Stephen King"
                return [f"{parts[1]} {parts[0]}"]
            # Compound last name: "de Saint-Exupery, Antoine" -> "Antoine de Saint-Exupery"
            return [f"{parts[1]} {parts[0]}"]

        if len(words_a) > 1 and len(words_b) > 1:
            # Both sides multi-word: "Stephen King, Peter Straub" -> two authors
            return _clean_parts(parts)

        # One multi-word, one single but first part is single word
        # e.g. "Stephen, King Peter" - unusual, treat as two parts
        return _clean_parts(parts)

    if len(parts) > 2:
        # Check if ALL parts are single words -> alternating Last, First pairs
        all_single = all(len(p.split()) == 1 and "-" not in p for p in parts)
        if all_single and len(parts) % 2 == 0:
            # Pair them up: Last1, First1, Last2, First2
            names = []
            for i in range(0, len(parts), 2):
                names.append(f"{parts[i + 1]} {parts[i]}")
            return names

        # Not all single words - treat as multiple authors separated by commas
        return _clean_parts(parts)

    return [text.strip()]
