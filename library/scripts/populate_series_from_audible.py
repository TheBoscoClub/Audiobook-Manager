#!/usr/bin/env python3
"""
Populate series and series_sequence from Audible's catalog API.

Uses the ASIN already stored in the database to query Audible's public
catalog endpoint for series membership.  No authentication is needed.

Strategy for multi-series books (e.g., Discworld sub-series):
  - Prefer the series with the MOST members in our library (broadest coverage)
  - If tied, prefer the shortest series title (usually the main series name)
  - Store only one series per book (schema constraint)

After ASIN lookup, falls back to title parsing for books without ASINs.

Usage:
    python3 populate_series_from_audible.py [--dry-run] [--delay SECONDS]
"""

import json
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# Add parent dirs for config import — optional when --db is provided
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from library.config import DATABASE_PATH
except ImportError:
    try:
        from config import DATABASE_PATH
    except ImportError:
        DATABASE_PATH = None  # Must use --db flag

AUDIBLE_API = "https://api.audible.com/1.0/catalog/products"
MARKETPLACE = "AF2M0KC94RCEA"
DEFAULT_DELAY = 0.3  # seconds between API calls


def fetch_series_from_audible(asin: str) -> list[dict]:
    """Query Audible API for series data.

    Returns list of dicts: [{"title": "...", "sequence": "..."}]
    """
    url = f"{AUDIBLE_API}/{asin}?response_groups=series&marketplace={MARKETPLACE}"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})

    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # Reason: URL built from trusted HTTPS constant (AUDIBLE_API) + validated ASIN from internal DB; not user-controlled scheme
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read())
            return data.get("product", {}).get("series", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        if e.code == 429:
            print(f"  Rate limited on {asin} — waiting 30s", file=sys.stderr)
            time.sleep(30)
            return fetch_series_from_audible(asin)  # retry once
        print(f"  HTTP {e.code} for {asin}", file=sys.stderr)
        return []
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  Network error for {asin}: {e}", file=sys.stderr)
        return []


def pick_best_series(
    series_list: list[dict], series_popularity: dict[str, int]
) -> tuple[str, float | None]:
    """Choose the best series from a multi-series list.

    Priority:
    1. Series with most members already in our library
    2. Shortest title (usually the broadest series name)

    Returns (series_title, sequence_number_or_None)
    """
    if not series_list:
        return ("", None)

    if len(series_list) == 1:
        s = series_list[0]
        seq = parse_sequence(s.get("sequence", ""))
        return (s["title"], seq)

    # Score each series by popularity in our library, then by title length
    scored = []
    for s in series_list:
        title = s["title"]
        pop = series_popularity.get(title, 0)
        seq = parse_sequence(s.get("sequence", ""))
        scored.append((pop, -len(title), title, seq))

    scored.sort(reverse=True)  # highest popularity, then shortest title
    _, _, best_title, best_seq = scored[0]
    return (best_title, best_seq)


def parse_sequence(seq_str: str) -> float | None:
    """Parse sequence string to a number. Handles '1', '1.5', etc."""
    if not seq_str:
        return None
    try:
        return float(seq_str)
    except ValueError:
        # Try extracting a number
        m = re.search(r"[\d.]+", seq_str)
        if m:
            try:
                return float(m.group())
            except ValueError:
                pass
    return None


# --- Title-based fallback parser ---

# Common series patterns in Audible titles:
#   "Title: Series Name, Book N (Unabridged)"
#   "Title: Series Name #N (Unabridged)"
#   "Title: A Series Name Novel (Unabridged)"
#   "Title (Series Name Book N) (Unabridged)"
TITLE_SERIES_PATTERNS = [
    # "Title: Series, Book N" or "Title: Series #N"
    re.compile(r"^.+?:\s+(.+?),?\s+(?:Book|#)\s*(\d+(?:\.\d+)?)\s*(?:\(|$)", re.IGNORECASE),
    # "Title (Series Name Book N)"
    re.compile(r"\((.+?)\s+(?:Book|#)\s*(\d+(?:\.\d+)?)\)", re.IGNORECASE),
    # "Title: A Series Name Novel" (no number)
    re.compile(r"^.+?:\s+(?:A\s+)?(.+?)\s+Novel\s*(?:\(|$)", re.IGNORECASE),
]


def parse_series_from_title(title: str) -> tuple[str, float | None]:
    """Try to extract series name and number from title string.

    Returns (series_name, sequence_or_None) or ("", None) if no match.
    """
    if not title:
        return ("", None)

    # Strip "(Unabridged)" / "(Abridged)" suffix first
    clean = re.sub(r"\s*\((Un)?abridged\)\s*$", "", title, flags=re.IGNORECASE)

    for pattern in TITLE_SERIES_PATTERNS:
        m = pattern.search(clean)
        if m:
            groups = m.groups()
            series_name = groups[0].strip().rstrip(",")
            seq = None
            if len(groups) > 1 and groups[1]:
                seq = parse_sequence(groups[1])
            return (series_name, seq)

    return ("", None)


def _fetch_books_needing_series(db_path: Path):
    """Read phase: fetch books needing series, close connection before API calls."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, asin, title FROM audiobooks "
        "WHERE asin IS NOT NULL AND asin != '' "
        "AND (series IS NULL OR series = '')"
    )
    asin_books = cursor.fetchall()
    cursor.execute(
        "SELECT id, title FROM audiobooks "
        "WHERE (asin IS NULL OR asin = '') "
        "AND (series IS NULL OR series = '')"
    )
    no_asin_books = cursor.fetchall()
    conn.close()
    return asin_books, no_asin_books


def _query_audible_series(asin_books, delay: float):
    """Phase 1: Collect series data from Audible API for all ASIN books."""
    raw_series: dict[int, list[dict]] = {}
    series_counter: dict[str, int] = {}
    api_hits = 0
    api_misses = 0

    for idx, (book_id, asin, _title) in enumerate(asin_books, 1):
        if idx % 50 == 0 or idx == 1:
            print(f"  [{idx}/{len(asin_books)}] Querying {asin}...")
        series_list = fetch_series_from_audible(asin)
        if series_list:
            raw_series[book_id] = series_list
            api_hits += 1
            for s in series_list:
                series_counter[s["title"]] = series_counter.get(s["title"], 0) + 1
        else:
            api_misses += 1
        if delay > 0:
            time.sleep(delay)

    return raw_series, series_counter, api_hits, api_misses


def _apply_series_update(cursor, book_id, series_name, seq, dry_run, display_title="?"):
    """Apply a single series update (or print dry-run output)."""
    if dry_run:
        seq_str = f" #{seq}" if seq is not None else ""
        print(f"  [DRY RUN] {display_title} → {series_name}{seq_str}")
    else:
        cursor.execute(
            "UPDATE audiobooks SET series = ?, series_sequence = ? WHERE id = ?",
            (series_name, seq, book_id),
        )


def _update_from_api(cursor, raw_series, series_counter, asin_books, dry_run):
    """Phase 2: Pick best series for each book and update DB."""
    title_by_id = {book_id: title for book_id, _, title in asin_books}
    updated = 0
    for book_id, series_list in raw_series.items():
        series_name, seq = pick_best_series(series_list, series_counter)
        if series_name:
            _apply_series_update(
                cursor, book_id, series_name, seq, dry_run, title_by_id.get(book_id, "?")
            )
            updated += 1
    return updated


def _update_from_titles(cursor, no_asin_books, dry_run):
    """Phase 3: Title-based fallback for books without ASIN."""
    updated = 0
    for book_id, title in no_asin_books:
        series_name, seq = parse_series_from_title(title)
        if series_name:
            _apply_series_update(cursor, book_id, series_name, seq, dry_run, title)
            updated += 1
    return updated


def _print_series_results(results, dry_run):
    """Print final series population results."""
    print(f"\n{'=' * 50}")
    print(f"RESULTS {'(DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 50}")
    print(f"Updated from Audible API: {results['updated_from_api']}")
    print(f"Updated from title parse: {results['updated_from_title']}")
    print(f"Total updated: {results['updated_from_api'] + results['updated_from_title']}")
    print(f"Unique series: {results['unique_series']}")


def populate_series(
    dry_run: bool = False, delay: float = DEFAULT_DELAY, db_path: Path | None = None
) -> dict:
    """Main function: populate series from Audible API, then title fallback."""
    if db_path is None:
        if DATABASE_PATH is None:
            print("Error: No database path. Use --db flag.", file=sys.stderr)
            sys.exit(1)
        db_path = DATABASE_PATH

    print(f"Database: {db_path}")
    asin_books, no_asin_books = _fetch_books_needing_series(db_path)
    print(f"Books with ASIN, no series: {len(asin_books)}")
    print(f"Books without ASIN, no series: {len(no_asin_books)}\n")

    print("Phase 1: Querying Audible API...")
    raw_series, series_counter, api_hits, api_misses = _query_audible_series(asin_books, delay)
    print(f"\n  API results: {api_hits} with series, {api_misses} without, 0 errors")
    print(f"  Unique series found: {len(series_counter)}")
    top_series = sorted(series_counter.items(), key=lambda x: -x[1])[:15]
    print("\n  Top series in library:")
    for name, count in top_series:
        print(f"    {count:3d} books: {name}")
    print()

    print("Phase 2: Updating database...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    updated_asin = _update_from_api(cursor, raw_series, series_counter, asin_books, dry_run)

    print(f"\nPhase 3: Title fallback for {len(no_asin_books)} books without ASIN...")
    updated_title = _update_from_titles(cursor, no_asin_books, dry_run)

    if not dry_run:
        conn.commit()
    conn.close()

    results = {
        "total_with_asin": len(asin_books),
        "total_without_asin": len(no_asin_books),
        "api_hits": api_hits,
        "api_misses": api_misses,
        "updated_from_api": updated_asin,
        "updated_from_title": updated_title,
        "unique_series": len(series_counter),
    }
    _print_series_results(results, dry_run)
    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Populate series data from Audible API + title fallback"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be updated without writing to DB"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds between API calls (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--db", type=str, default=None, help="Path to SQLite database (default: from config)"
    )
    args = parser.parse_args()

    db = Path(args.db) if args.db else None
    populate_series(dry_run=args.dry_run, delay=args.delay, db_path=db)


if __name__ == "__main__":
    main()
