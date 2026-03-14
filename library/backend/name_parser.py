"""
Multi-author/narrator name parser.

Parses delimited name strings into individual names and generates
sort keys in "Last, First" format. Handles three tiers:
- Tier 1: Structured metadata (multiple separate tags) - handled by caller
- Tier 2: Delimiter-based splitting (this module)
- Tier 3: Single name fallback (this module)
"""

import re
import unicodedata

# Known performance/production group names - always narrators, never authors.
# If detected in author metadata, caller should redirect to narrator list.
# Role suffixes that indicate a contributor is NOT the primary author.
# Names with these suffixes are excluded from the authors table.
# Pattern: "Name - role" or "Name (role)"
ROLE_SUFFIXES = frozenset(
    {
        "adaptation",
        "adapter",
        "adaptor",
        "afterword",
        "compiler",
        "contributor",
        "cover",
        "editor",
        "essay",
        "foreword",
        "illustrator",
        "introduction",
        "narrator",
        "note",
        "preface",
        "retold",
        "translated",
        "translator",
    }
)

# Regex to detect "Name - role" pattern (case-insensitive)
# Allows optional trailing words for multi-word roles like "cover design"
_ROLE_SUFFIX_RE = re.compile(
    r"\s+-\s+(" + "|".join(re.escape(r) for r in sorted(ROLE_SUFFIXES)) + r")[\w\s]*$",
    re.IGNORECASE,
)

# Regex to detect "Name (role)" pattern
_ROLE_PAREN_RE = re.compile(
    r"\s*\((" + "|".join(re.escape(r) for r in sorted(ROLE_SUFFIXES)) + r")\w*\)\s*$",
    re.IGNORECASE,
)


def has_role_suffix(name: str) -> bool:
    """Check if a name has a role suffix like '- translator' or '(editor)'."""
    if not name:
        return False
    return bool(_ROLE_SUFFIX_RE.search(name)) or bool(_ROLE_PAREN_RE.search(name))


def strip_role_suffix(name: str) -> str:
    """Strip role suffix from a name, returning the clean name."""
    if not name:
        return name
    clean = _ROLE_SUFFIX_RE.sub("", name).strip()
    clean = _ROLE_PAREN_RE.sub("", clean).strip()
    return clean


# Credential/title suffixes that are NOT part of a person's name.
# These appear at the end of names: "Shari Y. Manning PhD", "Blaise Aguirre MD"
CREDENTIAL_SUFFIXES = frozenset(
    {
        "phd",
        "ph.d.",
        "md",
        "m.d.",
        "msw",
        "psyd",
        "do",
        "d.o.",
        "edd",
        "ed.d.",
        "jd",
        "j.d.",
        "rn",
        "r.n.",
        "lcsw",
        "lpc",
        "dds",
        "d.d.s.",
        "dmd",
        "d.m.d.",
        "mba",
        "m.b.a.",
        "ma",
        "m.a.",
        "ms",
        "m.s.",
        "mph",
        "m.p.h.",
    }
)

# Generational/honorific suffixes to strip from names
GENERATIONAL_SUFFIXES = frozenset(
    {
        "jr",
        "jr.",
        "sr",
        "sr.",
        "ii",
        "iii",
        "iv",
        "esq",
        "esq.",
    }
)

# Regex to strip trailing credential suffixes (one or more, comma-separated or space-separated)
_CREDENTIAL_RE = re.compile(
    r"[,\s]+(?:"
    + "|".join(re.escape(c) for c in sorted(CREDENTIAL_SUFFIXES, key=len, reverse=True))
    + r")\.?(?:[,\s]+(?:"
    + "|".join(re.escape(c) for c in sorted(CREDENTIAL_SUFFIXES, key=len, reverse=True))
    + r")\.?)*\s*$",
    re.IGNORECASE,
)

# Generational suffix at end of name: "Robert S. Mueller III"
_GENERATIONAL_RE = re.compile(
    r"[,\s]+(?:"
    + "|".join(
        re.escape(g) for g in sorted(GENERATIONAL_SUFFIXES, key=len, reverse=True)
    )
    + r")\s*$",
    re.IGNORECASE,
)


def strip_credentials(name: str) -> str:
    """Strip credential and generational suffixes from a name.

    Examples:
        "Shari Y. Manning PhD" -> "Shari Y. Manning"
        "Jeffrey M. Schwartz, M.D." -> "Jeffrey M. Schwartz"
        "Robert S. Mueller III" -> "Robert S. Mueller"
        "Tara Brach, PhD" -> "Tara Brach"
    """
    if not name:
        return name
    clean = _CREDENTIAL_RE.sub("", name).strip().rstrip(",").strip()
    clean = _GENERATIONAL_RE.sub("", clean).strip().rstrip(",").strip()
    return clean


# Words that are NOT valid person names when standing alone.
# These end up as "names" due to bad metadata splitting.
JUNK_NAMES = frozenset(
    {
        "more",
        "translator",
        "editor",
        "narrator",
        "author",
        "unknown",
        "md",
        "m.d.",
        "phd",
        "ph.d.",
        "psyd",
        "msw",
        "edd",
        "do",
        "jr",
        "sr",
        "ii",
        "iii",
        "iv",
    }
)


def is_junk_name(name: str) -> bool:
    """Check if a name is a standalone junk word, not a real person."""
    if not name:
        return True
    clean = name.strip().rstrip(".").lower()
    if clean in JUNK_NAMES:
        return True
    # Single word that's a credential
    if clean.replace(".", "") in {c.replace(".", "") for c in CREDENTIAL_SUFFIXES}:
        return True
    return False


def _strip_trailing_role_word(name: str) -> str:
    """Strip a bare role word from the end of a name.

    Handles: "David Coward Translator" -> "David Coward"
    Only strips if the last word is a known role and there are 2+ other words.
    """
    if not name:
        return name
    words = name.split()
    if len(words) >= 2 and words[-1].lower().rstrip("s") in ROLE_SUFFIXES:
        return " ".join(words[:-1])
    return name


def normalize_for_dedup(name: str) -> str:
    """Normalize a name for deduplication: lowercase, strip accents, normalize spacing.

    "Miéville" -> "mieville"
    "M. R." -> "m.r."
    "Le Carré" -> "le carre"
    """
    if not name:
        return ""
    # Normalize unicode (NFD) then strip combining marks (accents)
    nfkd = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase and normalize whitespace
    lower = stripped.lower().strip()
    # Remove spaces around periods for initial normalization: "M. R." -> "m.r."
    lower = re.sub(r"\.\s+", ".", lower)
    return lower


# Keywords that indicate a name is a brand/company, not a person.
# Names containing these words (case-insensitive) are excluded from
# both authors and narrators during migration.
BRAND_KEYWORDS = frozenset(
    {
        "publishing",
        "publications",
        "press",
        "media",
        "entertainment",
        "studio",
        "studios",
        "productions",
        "inc",
        "llc",
        "ltd",
        "corp",
        "learning",
        "academy",
        "institute",
        "foundation",
    }
)

# Exact brand names that don't contain a keyword but aren't person names.
BRAND_NAMES = frozenset(
    {
        "aaptiv",
        "cracked.com",
        "movewith",
        "wondery",
    }
)

# Patterns that indicate an organization, not a person
ORG_PATTERNS = [
    re.compile(
        r"\b(department of|office of|council|commission|bureau)\b", re.IGNORECASE
    ),
    re.compile(r"\bU\.?S\.?\s+(Department|Office|Government)\b", re.IGNORECASE),
    re.compile(r"\bSpecial Counsel", re.IGNORECASE),
]


def is_brand_name(name: str) -> bool:
    """Check if a name is a brand/company/publisher/organization, not a person."""
    if not name:
        return False
    lower = name.strip().lower()
    if lower in BRAND_NAMES:
        return True
    words = lower.split()
    if BRAND_KEYWORDS & set(words):
        return True
    # Check organization patterns
    for pattern in ORG_PATTERNS:
        if pattern.search(name):
            return True
    return False


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

    # Strip trailing bare role word: "David Coward Translator" -> "David Coward"
    clean = _strip_trailing_role_word(clean)

    # Strip credential suffixes: "Manning PhD" -> "Manning"
    clean = strip_credentials(clean)

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


def clean_name(name: str) -> str:
    """Clean a single name: strip roles, credentials, whitespace."""
    if not name:
        return ""
    clean = name.strip()
    # Strip "(role)" suffix
    clean = re.sub(r"\s*\([^)]*\)\s*$", "", clean).strip()
    # Strip "- role" suffix
    clean = _ROLE_SUFFIX_RE.sub("", clean).strip()
    # Strip bare trailing role word: "David Coward Translator"
    clean = _strip_trailing_role_word(clean)
    # Strip credentials: PhD, MD, M.D., etc.
    clean = strip_credentials(clean)
    # Clean up any trailing dashes or commas
    clean = clean.rstrip("-,").strip()
    return clean


def _clean_parts(parts: list[str]) -> list[str]:
    """Strip whitespace, roles, and credentials, filter empties and junk."""
    result = []
    for p in parts:
        cleaned = clean_name(p)
        if cleaned and not is_junk_name(cleaned):
            result.append(cleaned)
    return result


def _is_credential_word(text: str) -> bool:
    """Check if a string is just a credential suffix (PhD, MD, M.D., etc.)."""
    clean = text.strip().rstrip(".").lower().replace(".", "")
    return clean in {c.replace(".", "") for c in CREDENTIAL_SUFFIXES} or clean in {
        g.replace(".", "") for g in GENERATIONAL_SUFFIXES
    }


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

    # Filter out standalone credential/junk parts before disambiguation
    # "Tara Brach, PhD" -> filter PhD -> ["Tara Brach"]
    # "Jeffrey M. Schwartz, M.D., Rebecca Gladding, M.D., M.D." -> filter M.D.
    filtered = [p for p in parts if not _is_credential_word(p) and not is_junk_name(p)]
    if not filtered:
        return _clean_parts(parts)
    parts = filtered

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

    return _clean_parts(parts)
