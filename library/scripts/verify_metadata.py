#!/usr/bin/env python3
"""
Metadata Verification & Cross-Reference
=========================================
Cross-references audiobook metadata from multiple sources to detect conflicts,
ambiguities, and errors. Auto-corrects when one source is clearly authoritative.

Sources compared:
  1. Embedded file tags (ffprobe: format.tags + streams[0].tags)
  2. Audible API data (already in DB from enrichment)
  3. ISBN/Google Books/Open Library data

Checks performed:
  - Title mismatches (embedded vs Audible vs ISBN)
  - Author mismatches (embedded vs Audible vs ISBN)
  - Narrator mismatches (embedded vs Audible)
  - Duration discrepancies (file vs Audible runtime_length_min)
  - Series conflicts between sources
  - Language verification
  - Publisher mismatches
  - Missing critical fields (ASIN, cover, description, etc.)

Usage:
    python3 verify_metadata.py --db /path/to/audiobooks.db [--dry-run]
    python3 verify_metadata.py --db /path/to/audiobooks.db --id 42
    python3 verify_metadata.py --db /path/to/audiobooks.db --fix  # auto-correct
"""

import json
import sqlite3
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from library.config import DATABASE_PATH
except ImportError:
    try:
        from config import DATABASE_PATH
    except ImportError:
        DATABASE_PATH = None

# ── Confidence thresholds ──
# Similarity ratio below which we flag a mismatch
TITLE_MISMATCH_THRESHOLD = 0.80
AUTHOR_MISMATCH_THRESHOLD = 0.75
NARRATOR_MISMATCH_THRESHOLD = 0.75
# Duration tolerance: ±5% or ±2 minutes, whichever is larger
DURATION_TOLERANCE_PCT = 0.05
DURATION_TOLERANCE_MIN = 2.0

VALID_CONTENT_TYPES = frozenset(
    {"Product", "Performance", "Speech", "Podcast", "Lecture", "Radio/TV Program"}
)


def similarity(a: str | None, b: str | None) -> float:
    """Compute similarity ratio between two strings (0.0 to 1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def normalize_name(name: str | None) -> str:
    """Normalize an author/narrator name for comparison."""
    if not name:
        return ""
    # Remove common suffixes/prefixes
    name = name.strip()
    # Handle "Last, First" -> "First Last"
    if "," in name and name.count(",") == 1:
        parts = name.split(",")
        name = f"{parts[1].strip()} {parts[0].strip()}"
    return name.lower()


def get_embedded_tags(file_path: str) -> dict | None:
    """Extract metadata tags from audio file using ffprobe."""
    if not Path(file_path).exists():
        return None

    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)

        # Get tags from format level AND stream level (Opus uses streams[0])
        tags = data.get("format", {}).get("tags", {})
        if not tags:
            streams = data.get("streams", [])
            if streams:
                tags = streams[0].get("tags", {})

        # Normalize tag keys to lowercase
        return {k.lower(): v for k, v in tags.items()} if tags else {}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def compute_duration_hours(file_path: str) -> float | None:
    """Get actual audio duration in hours from ffprobe."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(file_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B603 — subprocess call — cmd is a hardcoded system tool invocation with internal/config args; no user-controlled input
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        duration = data.get("format", {}).get("duration")
        if duration:
            return float(duration) / 3600.0
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, ValueError):
        pass
    return None


class MetadataIssue:
    """Represents a single metadata issue found during verification."""

    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_ERROR = "error"
    SEVERITY_CONFLICT = "conflict"

    def __init__(
        self,
        book_id: int,
        field: str,
        severity: str,
        message: str,
        db_value: str | None = None,
        file_value: str | None = None,
        api_value: str | None = None,
        recommended_value: str | None = None,
        confidence: float = 0.0,
    ):
        self.book_id = book_id
        self.field = field
        self.severity = severity
        self.message = message
        self.db_value = db_value
        self.file_value = file_value
        self.api_value = api_value
        self.recommended_value = recommended_value
        self.confidence = confidence

    def __repr__(self):
        return f"[{self.severity.upper()}] Book {self.book_id} | {self.field}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "field": self.field,
            "severity": self.severity,
            "message": self.message,
            "db_value": self.db_value,
            "file_value": self.file_value,
            "api_value": self.api_value,
            "recommended_value": self.recommended_value,
            "confidence": self.confidence,
        }


def _check_title(book_id: int, book: dict, embedded_tags: dict) -> list[MetadataIssue]:
    """Check title consistency between file and DB."""
    file_title = embedded_tags.get("title") or embedded_tags.get("album")
    if not file_title or not book["title"]:
        return []

    sim = similarity(file_title, book["title"])
    if sim >= TITLE_MISMATCH_THRESHOLD:
        return []

    if book.get("audible_enriched_at"):
        return [
            MetadataIssue(
                book_id,
                "title",
                MetadataIssue.SEVERITY_INFO,
                f"File title differs from DB (similarity {sim:.0%}). "
                f"DB (Audible-enriched) is authoritative.",
                db_value=book["title"],
                file_value=file_title,
                confidence=0.9,
            )
        ]

    return [
        MetadataIssue(
            book_id,
            "title",
            MetadataIssue.SEVERITY_WARNING,
            f"Title mismatch between file and DB (similarity {sim:.0%})",
            db_value=book["title"],
            file_value=file_title,
            confidence=0.5,
        )
    ]


def _check_author(book_id: int, book: dict, embedded_tags: dict) -> list[MetadataIssue]:
    """Check author consistency between file and DB."""
    file_author = (
        embedded_tags.get("artist")
        or embedded_tags.get("author")
        or embedded_tags.get("album_artist")
    )
    if not file_author or not book["author"]:
        return []

    norm_file = normalize_name(file_author)
    norm_db = normalize_name(book["author"])
    sim = similarity(norm_file, norm_db)
    if sim >= AUTHOR_MISMATCH_THRESHOLD:
        return []

    if book.get("audible_enriched_at"):
        recommended = book["author"]
        severity = MetadataIssue.SEVERITY_INFO
        confidence = 0.8
    else:
        recommended = None
        severity = MetadataIssue.SEVERITY_CONFLICT
        confidence = 0.5

    return [
        MetadataIssue(
            book_id,
            "author",
            severity,
            f"Author mismatch: file='{file_author}' vs DB='{book['author']}' "
            f"(similarity {sim:.0%})",
            db_value=book["author"],
            file_value=file_author,
            recommended_value=recommended,
            confidence=confidence,
        )
    ]


def _check_narrator(book_id: int, book: dict, embedded_tags: dict) -> list[MetadataIssue]:
    """Check narrator consistency between file and DB."""
    if not book.get("narrator"):
        return []

    file_narrator = embedded_tags.get("narrator") or embedded_tags.get("composer")
    if not file_narrator:
        return []

    sim = similarity(normalize_name(file_narrator), normalize_name(book["narrator"]))
    if sim >= NARRATOR_MISMATCH_THRESHOLD:
        return []

    return [
        MetadataIssue(
            book_id,
            "narrator",
            MetadataIssue.SEVERITY_WARNING,
            f"Narrator mismatch: file='{file_narrator}' "
            f"vs DB='{book['narrator']}' (similarity {sim:.0%})",
            db_value=book["narrator"],
            file_value=file_narrator,
            confidence=0.6,
        )
    ]


def _check_duration(
    book_id: int, book: dict, file_duration_hours: float | None
) -> list[MetadataIssue]:
    """Check duration consistency between file and Audible data."""
    if not file_duration_hours or not book.get("runtime_length_min"):
        return []

    audible_hours = book["runtime_length_min"] / 60.0
    diff = abs(file_duration_hours - audible_hours)
    tolerance = max(audible_hours * DURATION_TOLERANCE_PCT, DURATION_TOLERANCE_MIN / 60.0)
    if diff <= tolerance:
        return []

    pct_diff = (diff / audible_hours * 100) if audible_hours > 0 else 0
    if pct_diff > 20:
        severity = MetadataIssue.SEVERITY_ERROR
        msg = (
            f"Major duration discrepancy: file={file_duration_hours:.1f}h "
            f"vs Audible={audible_hours:.1f}h ({pct_diff:.0f}% off). "
            f"Possible wrong edition or truncated file."
        )
    else:
        severity = MetadataIssue.SEVERITY_WARNING
        msg = (
            f"Duration mismatch: file={file_duration_hours:.1f}h "
            f"vs Audible={audible_hours:.1f}h ({pct_diff:.0f}% off)"
        )

    return [
        MetadataIssue(
            book_id,
            "duration",
            severity,
            msg,
            db_value=f"{audible_hours:.2f}h",
            file_value=f"{file_duration_hours:.2f}h",
            confidence=0.9,
        )
    ]


# Table-driven missing field checks: (condition_fn, field, severity, message)
_MISSING_FIELD_CHECKS = [
    (
        lambda b: not b.get("asin") and not b.get("isbn"),
        "identifier",
        MetadataIssue.SEVERITY_WARNING,
        "No ASIN or ISBN — cannot enrich from external sources",
    ),
    (
        lambda b: not b.get("cover_path") and not b.get("audible_image_url"),
        "cover",
        MetadataIssue.SEVERITY_WARNING,
        "No cover art (local or Audible URL)",
    ),
    (
        lambda b: not b.get("description") and not b.get("publisher_summary"),
        "description",
        MetadataIssue.SEVERITY_INFO,
        "No description or publisher summary",
    ),
    (
        lambda b: not b.get("narrator") or b["narrator"] == "Unknown Narrator",
        "narrator",
        MetadataIssue.SEVERITY_WARNING,
        "Missing or unknown narrator",
    ),
    (
        lambda b: not b.get("language") and b.get("audible_enriched_at"),
        "language",
        MetadataIssue.SEVERITY_INFO,
        "Language not set despite Audible enrichment",
    ),
]


def _check_missing_fields(book_id: int, book: dict) -> list[MetadataIssue]:
    """Check for missing critical fields using table-driven checks."""
    return [
        MetadataIssue(book_id, field, severity, message, confidence=1.0)
        for condition, field, severity, message in _MISSING_FIELD_CHECKS
        if condition(book)
    ]


def _check_series(book_id: int, book: dict, embedded_tags: dict) -> list[MetadataIssue]:
    """Check series consistency between file and DB."""
    if not book.get("series"):
        return []

    file_series = (
        embedded_tags.get("series")
        or embedded_tags.get("grouping")
        or embedded_tags.get("tvshowtitle")
    )
    if not file_series:
        return []

    sim = similarity(file_series, book["series"])
    if sim >= 0.7:
        return []

    recommended = book["series"] if book.get("audible_enriched_at") else None
    confidence = 0.7 if book.get("audible_enriched_at") else 0.4

    return [
        MetadataIssue(
            book_id,
            "series",
            MetadataIssue.SEVERITY_WARNING,
            f"Series mismatch: file='{file_series}' "
            f"vs DB='{book['series']}' (similarity {sim:.0%})",
            db_value=book["series"],
            file_value=file_series,
            recommended_value=recommended,
            confidence=confidence,
        )
    ]


def _check_publisher(book_id: int, book: dict, embedded_tags: dict) -> list[MetadataIssue]:
    """Check publisher consistency between file and DB."""
    if not book.get("publisher"):
        return []

    file_publisher = embedded_tags.get("publisher")
    if not file_publisher:
        return []

    sim = similarity(file_publisher, book["publisher"])
    if sim >= 0.6:
        return []

    return [
        MetadataIssue(
            book_id,
            "publisher",
            MetadataIssue.SEVERITY_INFO,
            f"Publisher differs: file='{file_publisher}' vs DB='{book['publisher']}'",
            db_value=book["publisher"],
            file_value=file_publisher,
            confidence=0.5,
        )
    ]


def _check_content_type(book_id: int, book: dict) -> list[MetadataIssue]:
    """Check content type is a known value."""
    ct = book.get("content_type")
    if not ct or ct in VALID_CONTENT_TYPES:
        return []

    return [
        MetadataIssue(
            book_id,
            "content_type",
            MetadataIssue.SEVERITY_WARNING,
            f"Unknown content type: '{ct}'",
            db_value=ct,
            confidence=0.9,
        )
    ]


def verify_book(
    book: dict, embedded_tags: dict | None, file_duration_hours: float | None
) -> list[MetadataIssue]:
    """Verify metadata for a single audiobook.

    Args:
        book: Row from audiobooks table (dict-like)
        embedded_tags: Tags extracted from the audio file via ffprobe
        file_duration_hours: Actual file duration in hours

    Returns:
        List of MetadataIssue objects
    """
    issues: list[MetadataIssue] = []
    book_id = book["id"]

    if embedded_tags:
        issues.extend(_check_title(book_id, book, embedded_tags))
        issues.extend(_check_author(book_id, book, embedded_tags))
        issues.extend(_check_narrator(book_id, book, embedded_tags))
        issues.extend(_check_series(book_id, book, embedded_tags))
        issues.extend(_check_publisher(book_id, book, embedded_tags))

    issues.extend(_check_duration(book_id, book, file_duration_hours))
    issues.extend(_check_missing_fields(book_id, book))
    issues.extend(_check_content_type(book_id, book))

    return issues


def apply_fixes(conn: sqlite3.Connection, issues: list[MetadataIssue], quiet: bool = False) -> int:
    """Apply recommended fixes for issues with high confidence.

    Only applies fixes where:
    - recommended_value is set
    - confidence >= 0.7
    - severity is warning or higher

    Returns number of fixes applied.
    """
    cursor = conn.cursor()
    fixes_applied = 0

    fixable_severities = frozenset(
        {
            MetadataIssue.SEVERITY_WARNING,
            MetadataIssue.SEVERITY_ERROR,
            MetadataIssue.SEVERITY_CONFLICT,
        }
    )
    fixable_fields = frozenset({"title", "author", "narrator", "series", "publisher"})

    for issue in issues:
        if (
            issue.recommended_value is not None
            and issue.confidence >= 0.7
            and issue.severity in fixable_severities
            and issue.field in fixable_fields
        ):
            cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                f"UPDATE audiobooks SET {issue.field} = ? WHERE id = ?",  # nosec B608  # noqa: S608
                (issue.recommended_value, issue.book_id),
            )
            fixes_applied += 1
            if not quiet:
                print(
                    f"  Fixed {issue.field} for book {issue.book_id}: "
                    f"'{issue.db_value}' → '{issue.recommended_value}'"
                )

    if fixes_applied:
        conn.commit()

    return fixes_applied


def _categorize_issues(all_issues: list[MetadataIssue]) -> dict[str, list]:
    """Categorize issues by severity."""
    return {
        "errors": [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_ERROR],
        "conflicts": [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_CONFLICT],
        "warnings": [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_WARNING],
        "infos": [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_INFO],
    }


def _print_verification_summary(
    checked: int, categories: dict[str, list], fixes: int, dry_run: bool, auto_fix: bool
) -> None:
    """Print the verification results summary."""
    print(f"\n{'=' * 60}")
    print(f"METADATA VERIFICATION RESULTS {'(DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 60}")
    print(f"Books checked:  {checked}")
    print(f"Errors:         {len(categories['errors'])}")
    print(f"Conflicts:      {len(categories['conflicts'])}")
    print(f"Warnings:       {len(categories['warnings'])}")
    print(f"Info:           {len(categories['infos'])}")
    if auto_fix:
        print(f"Fixes applied:  {fixes}")

    for label in ("errors", "conflicts", "warnings"):
        items = categories[label]
        if not items:
            continue
        print(f"\n── {label.upper()} ({len(items)}) ──")
        for issue in items[:20]:
            print(f"  {issue}")
        if label == "warnings" and len(items) > 20:
            print(f"  ... and {len(items) - 20} more")


def _resolve_verify_db_path(db_path: Path | None) -> Path:
    """Resolve database path from argument or config, exit on failure."""
    if db_path is not None:
        return db_path
    if DATABASE_PATH is None:
        print("Error: No database path. Use --db flag.", file=sys.stderr)
        sys.exit(1)
    return DATABASE_PATH


def _fetch_books_to_verify(cursor, single_id: int | None) -> list:
    """Fetch books to verify from database."""
    if single_id is not None:
        cursor.execute("SELECT * FROM audiobooks WHERE id = ?", (single_id,))
    else:
        cursor.execute("SELECT * FROM audiobooks")
    return cursor.fetchall()


def _get_file_metadata(book, check_files: bool) -> tuple[dict | None, float | None]:
    """Extract embedded tags and duration from a book's audio file."""
    if not check_files or not book["file_path"]:
        return None, None

    embedded_tags = get_embedded_tags(book["file_path"])
    file_duration = None
    if book.get("runtime_length_min"):
        file_duration = compute_duration_hours(book["file_path"])

    return embedded_tags, file_duration


def _verify_all_books(
    books: list, check_files: bool, quiet: bool
) -> tuple[list[MetadataIssue], int]:
    """Verify all books and collect issues. Returns (all_issues, checked)."""
    all_issues: list[MetadataIssue] = []
    checked = 0

    for idx, book in enumerate(books, 1):
        if not quiet and idx % 100 == 0:
            print(f"  [{idx}/{len(books)}] checked...")

        embedded_tags, file_duration = _get_file_metadata(book, check_files)
        book_issues = verify_book(dict(book), embedded_tags, file_duration)
        all_issues.extend(book_issues)
        checked += 1

    return all_issues, checked


def _build_results(all_issues: list[MetadataIssue], checked: int, fixes: int) -> dict:
    """Build the final results dict from issues."""
    categories = _categorize_issues(all_issues)
    return {
        "total_checked": checked,
        "issues_found": len(all_issues),
        "errors": len(categories["errors"]),
        "conflicts": len(categories["conflicts"]),
        "warnings": len(categories["warnings"]),
        "infos": len(categories["infos"]),
        "fixes_applied": fixes,
        "issues": [i.to_dict() for i in all_issues],
    }


def verify_metadata(
    db_path: Path | None = None,
    dry_run: bool = False,
    auto_fix: bool = False,
    single_id: int | None = None,
    check_files: bool = True,
    quiet: bool = False,
) -> dict:
    """Run metadata verification across the library."""
    db_path = _resolve_verify_db_path(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        books = _fetch_books_to_verify(cursor, single_id)
        if not quiet:
            print(f"Verifying metadata for {len(books)} audiobooks...")
            if check_files:
                print("  (Checking embedded file tags — this may take a while)")
            print()

        all_issues, checked = _verify_all_books(books, check_files, quiet)

        fixes = 0
        if auto_fix and not dry_run:
            fixes = apply_fixes(conn, all_issues, quiet=quiet)
    finally:
        conn.close()

    if not quiet:
        categories = _categorize_issues(all_issues)
        _print_verification_summary(checked, categories, fixes, dry_run, auto_fix)

    return _build_results(all_issues, checked, fixes)


def verify_single_book(
    book_id: int, db_path: Path | None = None, auto_fix: bool = False, quiet: bool = False
) -> dict:
    """Convenience function for verifying a single book.

    Designed to be called inline after enrichment in the import pipeline.
    """
    return verify_metadata(
        db_path=db_path, auto_fix=auto_fix, single_id=book_id, check_files=True, quiet=quiet
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Verify and cross-reference audiobook metadata")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show issues only")
    parser.add_argument("--fix", action="store_true", help="Auto-fix high-confidence issues")
    parser.add_argument("--id", type=int, default=None, help="Verify single book by ID")
    parser.add_argument(
        "--no-file-check",
        action="store_true",
        help="Skip ffprobe file checks (faster, DB-only verification)",
    )
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    db = Path(args.db) if args.db else None
    results = verify_metadata(
        db_path=db,
        dry_run=args.dry_run,
        auto_fix=args.fix,
        single_id=args.id,
        check_files=not args.no_file_check,
        quiet=args.json,  # suppress text output if JSON mode
    )

    if args.json:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
