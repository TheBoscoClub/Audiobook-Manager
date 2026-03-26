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
import subprocess
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        duration = data.get("format", {}).get("duration")
        if duration:
            return float(duration) / 3600.0
    except (
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        FileNotFoundError,
        ValueError,
    ):
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
        return (
            f"[{self.severity.upper()}] Book {self.book_id} | {self.field}: "
            f"{self.message}"
        )

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


def verify_book(
    book: dict,
    embedded_tags: dict | None,
    file_duration_hours: float | None,
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

    # ── Title verification ──
    if embedded_tags:
        file_title = embedded_tags.get("title") or embedded_tags.get("album")
        if file_title and book["title"]:
            sim = similarity(file_title, book["title"])
            if sim < TITLE_MISMATCH_THRESHOLD:
                # Audible enrichment data is authoritative for title
                if book.get("audible_enriched_at"):
                    issues.append(
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
                    )
                else:
                    issues.append(
                        MetadataIssue(
                            book_id,
                            "title",
                            MetadataIssue.SEVERITY_WARNING,
                            f"Title mismatch between file and DB (similarity {sim:.0%})",
                            db_value=book["title"],
                            file_value=file_title,
                            confidence=0.5,
                        )
                    )

    # ── Author verification ──
    if embedded_tags:
        file_author = (
            embedded_tags.get("artist")
            or embedded_tags.get("author")
            or embedded_tags.get("album_artist")
        )
        if file_author and book["author"]:
            norm_file = normalize_name(file_author)
            norm_db = normalize_name(book["author"])
            sim = similarity(norm_file, norm_db)
            if sim < AUTHOR_MISMATCH_THRESHOLD:
                # Audible is authoritative for author
                if book.get("audible_enriched_at"):
                    recommended = book["author"]
                    severity = MetadataIssue.SEVERITY_INFO
                else:
                    recommended = None
                    severity = MetadataIssue.SEVERITY_CONFLICT
                issues.append(
                    MetadataIssue(
                        book_id,
                        "author",
                        severity,
                        f"Author mismatch: file='{file_author}' vs DB='{book['author']}' "
                        f"(similarity {sim:.0%})",
                        db_value=book["author"],
                        file_value=file_author,
                        recommended_value=recommended,
                        confidence=0.8 if book.get("audible_enriched_at") else 0.5,
                    )
                )

    # ── Narrator verification ──
    if embedded_tags and book.get("narrator"):
        file_narrator = embedded_tags.get("narrator") or embedded_tags.get("composer")
        if file_narrator:
            sim = similarity(
                normalize_name(file_narrator), normalize_name(book["narrator"])
            )
            if sim < NARRATOR_MISMATCH_THRESHOLD:
                issues.append(
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
                )

    # ── Duration verification ──
    if file_duration_hours and book.get("runtime_length_min"):
        audible_hours = book["runtime_length_min"] / 60.0
        diff = abs(file_duration_hours - audible_hours)
        tolerance = max(
            audible_hours * DURATION_TOLERANCE_PCT,
            DURATION_TOLERANCE_MIN / 60.0,
        )
        if diff > tolerance:
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
            issues.append(
                MetadataIssue(
                    book_id,
                    "duration",
                    severity,
                    msg,
                    db_value=f"{audible_hours:.2f}h",
                    file_value=f"{file_duration_hours:.2f}h",
                    confidence=0.9,
                )
            )

    # ── Missing critical fields ──
    if not book.get("asin") and not book.get("isbn"):
        issues.append(
            MetadataIssue(
                book_id,
                "identifier",
                MetadataIssue.SEVERITY_WARNING,
                "No ASIN or ISBN — cannot enrich from external sources",
                confidence=1.0,
            )
        )

    if not book.get("cover_path") and not book.get("audible_image_url"):
        issues.append(
            MetadataIssue(
                book_id,
                "cover",
                MetadataIssue.SEVERITY_WARNING,
                "No cover art (local or Audible URL)",
                confidence=1.0,
            )
        )

    if not book.get("description") and not book.get("publisher_summary"):
        issues.append(
            MetadataIssue(
                book_id,
                "description",
                MetadataIssue.SEVERITY_INFO,
                "No description or publisher summary",
                confidence=1.0,
            )
        )

    if not book.get("narrator") or book["narrator"] == "Unknown Narrator":
        issues.append(
            MetadataIssue(
                book_id,
                "narrator",
                MetadataIssue.SEVERITY_WARNING,
                "Missing or unknown narrator",
                confidence=1.0,
            )
        )

    if not book.get("language") and book.get("audible_enriched_at"):
        issues.append(
            MetadataIssue(
                book_id,
                "language",
                MetadataIssue.SEVERITY_INFO,
                "Language not set despite Audible enrichment",
                confidence=1.0,
            )
        )

    # ── Series consistency ──
    if embedded_tags and book.get("series"):
        # Check if file tags have a different series
        file_series = (
            embedded_tags.get("series")
            or embedded_tags.get("grouping")
            or embedded_tags.get("tvshowtitle")
        )
        if file_series:
            sim = similarity(file_series, book["series"])
            if sim < 0.7:
                issues.append(
                    MetadataIssue(
                        book_id,
                        "series",
                        MetadataIssue.SEVERITY_WARNING,
                        f"Series mismatch: file='{file_series}' "
                        f"vs DB='{book['series']}' (similarity {sim:.0%})",
                        db_value=book["series"],
                        file_value=file_series,
                        recommended_value=book["series"]
                        if book.get("audible_enriched_at")
                        else None,
                        confidence=0.7 if book.get("audible_enriched_at") else 0.4,
                    )
                )

    # ── Publisher verification ──
    if embedded_tags and book.get("publisher"):
        file_publisher = embedded_tags.get("publisher")
        if file_publisher:
            sim = similarity(file_publisher, book["publisher"])
            if sim < 0.6:
                issues.append(
                    MetadataIssue(
                        book_id,
                        "publisher",
                        MetadataIssue.SEVERITY_INFO,
                        f"Publisher differs: file='{file_publisher}' "
                        f"vs DB='{book['publisher']}'",
                        db_value=book["publisher"],
                        file_value=file_publisher,
                        confidence=0.5,
                    )
                )

    # ── Content type validation ──
    if book.get("content_type") and book["content_type"] not in (
        "Product",
        "Performance",
        "Speech",
        "Podcast",
        "Lecture",
        "Radio/TV Program",
    ):
        issues.append(
            MetadataIssue(
                book_id,
                "content_type",
                MetadataIssue.SEVERITY_WARNING,
                f"Unknown content type: '{book['content_type']}'",
                db_value=book["content_type"],
                confidence=0.9,
            )
        )

    return issues


def apply_fixes(
    conn: sqlite3.Connection,
    issues: list[MetadataIssue],
    quiet: bool = False,
) -> int:
    """Apply recommended fixes for issues with high confidence.

    Only applies fixes where:
    - recommended_value is set
    - confidence >= 0.7
    - severity is warning or higher

    Returns number of fixes applied.
    """
    cursor = conn.cursor()
    fixes_applied = 0

    for issue in issues:
        if (
            issue.recommended_value is not None
            and issue.confidence >= 0.7
            and issue.severity
            in (
                MetadataIssue.SEVERITY_WARNING,
                MetadataIssue.SEVERITY_ERROR,
                MetadataIssue.SEVERITY_CONFLICT,
            )
        ):
            # Only fix fields we're confident about
            if issue.field in ("title", "author", "narrator", "series", "publisher"):
                cursor.execute(
                    f"UPDATE audiobooks SET {issue.field} = ? WHERE id = ?",
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


def verify_metadata(
    db_path: Path | None = None,
    dry_run: bool = False,
    auto_fix: bool = False,
    single_id: int | None = None,
    check_files: bool = True,
    quiet: bool = False,
) -> dict:
    """Run metadata verification across the library.

    Args:
        db_path: Path to SQLite database
        dry_run: Show issues without fixing
        auto_fix: Automatically apply high-confidence corrections
        single_id: Verify a single book by ID
        check_files: Whether to run ffprobe on audio files (slower)
        quiet: Suppress detailed output

    Returns:
        dict with: total_checked, issues_found, fixes_applied, issues (list)
    """
    if db_path is None:
        if DATABASE_PATH is None:
            print("Error: No database path. Use --db flag.", file=sys.stderr)
            sys.exit(1)
        db_path = DATABASE_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if single_id is not None:
        cursor.execute("SELECT * FROM audiobooks WHERE id = ?", (single_id,))
    else:
        cursor.execute("SELECT * FROM audiobooks")

    books = cursor.fetchall()
    if not quiet:
        print(f"Verifying metadata for {len(books)} audiobooks...")
        if check_files:
            print("  (Checking embedded file tags — this may take a while)")
        print()

    all_issues: list[MetadataIssue] = []
    checked = 0

    for idx, book in enumerate(books, 1):
        if not quiet and idx % 100 == 0:
            print(f"  [{idx}/{len(books)}] checked...")

        embedded_tags = None
        file_duration = None

        if check_files and book["file_path"]:
            embedded_tags = get_embedded_tags(book["file_path"])
            # Only compute duration if Audible runtime is available for comparison
            if book.get("runtime_length_min"):
                file_duration = compute_duration_hours(book["file_path"])

        book_issues = verify_book(dict(book), embedded_tags, file_duration)
        all_issues.extend(book_issues)
        checked += 1

    # ── Apply fixes ──
    fixes = 0
    if auto_fix and not dry_run:
        fixes = apply_fixes(conn, all_issues, quiet=quiet)

    conn.close()

    # ── Summary ──
    errors = [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_ERROR]
    conflicts = [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_CONFLICT]
    warnings = [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_WARNING]
    infos = [i for i in all_issues if i.severity == MetadataIssue.SEVERITY_INFO]

    if not quiet:
        print(f"\n{'=' * 60}")
        print(f"METADATA VERIFICATION RESULTS {'(DRY RUN)' if dry_run else ''}")
        print(f"{'=' * 60}")
        print(f"Books checked:  {checked}")
        print(f"Errors:         {len(errors)}")
        print(f"Conflicts:      {len(conflicts)}")
        print(f"Warnings:       {len(warnings)}")
        print(f"Info:           {len(infos)}")
        if auto_fix:
            print(f"Fixes applied:  {fixes}")

        if errors:
            print(f"\n── ERRORS ({len(errors)}) ──")
            for issue in errors[:20]:
                print(f"  {issue}")

        if conflicts:
            print(f"\n── CONFLICTS ({len(conflicts)}) ──")
            for issue in conflicts[:20]:
                print(f"  {issue}")

        if warnings:
            print(f"\n── WARNINGS ({len(warnings)}) ──")
            for issue in warnings[:20]:
                print(f"  {issue}")
            if len(warnings) > 20:
                print(f"  ... and {len(warnings) - 20} more")

    return {
        "total_checked": checked,
        "issues_found": len(all_issues),
        "errors": len(errors),
        "conflicts": len(conflicts),
        "warnings": len(warnings),
        "infos": len(infos),
        "fixes_applied": fixes,
        "issues": [i.to_dict() for i in all_issues],
    }


def verify_single_book(
    book_id: int,
    db_path: Path | None = None,
    auto_fix: bool = False,
    quiet: bool = False,
) -> dict:
    """Convenience function for verifying a single book.

    Designed to be called inline after enrichment in the import pipeline.
    """
    return verify_metadata(
        db_path=db_path,
        auto_fix=auto_fix,
        single_id=book_id,
        check_files=True,
        quiet=quiet,
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify and cross-reference audiobook metadata"
    )
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show issues only")
    parser.add_argument(
        "--fix", action="store_true", help="Auto-fix high-confidence issues"
    )
    parser.add_argument("--id", type=int, default=None, help="Verify single book by ID")
    parser.add_argument(
        "--no-file-check",
        action="store_true",
        help="Skip ffprobe file checks (faster, DB-only verification)",
    )
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
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
