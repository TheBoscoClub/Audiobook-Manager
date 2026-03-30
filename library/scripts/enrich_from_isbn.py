#!/usr/bin/env python3
"""
ISBN-Based Metadata Enrichment (Fallback)
==========================================
For audiobooks that lack ASINs (or where Audible API returned no data),
queries Open Library and Google Books APIs using ISBN or title+author.

Fills gaps: language, publisher, description, genres/subjects, publication
year, cover URLs, and ISBN itself (via title matching).

Usage:
    python3 enrich_from_isbn.py --db /path/to/audiobooks.db [--dry-run]
    python3 enrich_from_isbn.py --db /path/to/audiobooks.db --all  # include ASIN books
    python3 enrich_from_isbn.py --db /path/to/audiobooks.db --id 42
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
OPENLIBRARY_API = "https://openlibrary.org"
DEFAULT_DELAY = 0.6

LANG_MAP = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "nl": "Dutch",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "pl": "Polish",
    "fi": "Finnish",
}


def query_google_books(
    isbn: str | None = None, title: str | None = None, author: str | None = None
) -> dict | None:
    """Query Google Books API by ISBN or title+author."""
    if isbn:
        q = f"isbn:{isbn}"
    elif title:
        q = f"intitle:{title}"
        if author:
            q += f"+inauthor:{author}"
    else:
        return None

    url = f"{GOOGLE_BOOKS_API}?q={urllib.parse.quote(q)}&maxResults=1"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            items = data.get("items", [])
            if items:
                return items[0].get("volumeInfo", {})
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"  Google Books error: {e}", file=sys.stderr)
    return None


def query_openlibrary_isbn(isbn: str) -> dict | None:
    """Query Open Library by ISBN."""
    url = f"{OPENLIBRARY_API}/isbn/{isbn}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def query_openlibrary_search(title: str, author: str | None = None) -> dict | None:
    """Search Open Library by title+author."""
    params = {"title": title, "limit": "1"}
    if author:
        params["author"] = author
    url = f"{OPENLIBRARY_API}/search.json?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            docs = data.get("docs", [])
            return docs[0] if docs else None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"  OpenLibrary search error: {e}", file=sys.stderr)
    return None


def _resolve_db_path(db_path: Path | None) -> Path:
    """Resolve database path from argument or config, exit on failure."""
    if db_path is not None:
        return db_path
    if DATABASE_PATH is None:
        print("Error: No database path. Use --db flag.", file=sys.stderr)
        sys.exit(1)
    return DATABASE_PATH


def _fetch_isbn_candidates(
    db_path: Path, include_asin_books: bool, single_id: int | None,
) -> list:
    """Fetch candidate books for ISBN enrichment."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if single_id is not None:
        cursor.execute(
            "SELECT id, title, author, isbn, asin FROM audiobooks WHERE id = ?",
            (single_id,),
        )
    elif include_asin_books:
        cursor.execute(
            "SELECT id, title, author, isbn, asin FROM audiobooks "
            "WHERE isbn_enriched_at IS NULL"
        )
    else:
        cursor.execute(
            "SELECT id, title, author, isbn, asin FROM audiobooks "
            "WHERE isbn_enriched_at IS NULL "
            "AND (asin IS NULL OR asin = '' OR audible_enriched_at IS NULL)"
        )

    books = cursor.fetchall()
    conn.close()
    return books


def _fetch_source_data(isbn: str | None, title: str,
                       author: str) -> tuple[dict | None, dict | None]:
    """Fetch data from Google Books or Open Library.

    Returns (gb_data, ol_data).
    """
    if isbn:
        gb_data = query_google_books(isbn=isbn)
        ol_data = query_openlibrary_isbn(isbn) if not gb_data else None
        return gb_data, ol_data

    gb_data = query_google_books(title=title, author=author)
    if gb_data:
        return gb_data, None
    ol_data = query_openlibrary_search(title, author)
    return None, ol_data


def _extract_gb_fields(gb_data: dict, isbn: str | None) -> tuple[list, list, int]:
    """Extract update fields from Google Books data.

    Returns (updates, params, isbn_found_count).
    """
    updates = []
    params = []
    isbn_found = 0

    lang = gb_data.get("language")
    if lang and len(lang) == 2:
        lang = LANG_MAP.get(lang, lang)
    if lang:
        updates.append("language = COALESCE(language, ?)")
        params.append(lang)

    desc = gb_data.get("description")
    if desc:
        updates.append("description = COALESCE(description, ?)")
        params.append(desc)

    pub_date = gb_data.get("publishedDate")
    if pub_date:
        updates.append("published_date = COALESCE(published_date, ?)")
        params.append(pub_date[:10])
        try:
            updates.append("published_year = COALESCE(published_year, ?)")
            params.append(int(pub_date[:4]))
        except ValueError:
            pass

    if not isbn:
        identifiers = gb_data.get("industryIdentifiers", [])
        for ident in identifiers:
            if ident.get("type") in ("ISBN_13", "ISBN_10"):
                updates.append("isbn = COALESCE(isbn, ?)")
                params.append(ident["identifier"])
                isbn_found = 1
                break

    return updates, params, isbn_found


def _add_coalesce_field(updates: list, params: list, col: str, val) -> None:
    """Add a COALESCE update field if value is truthy."""
    if val:
        updates.append(f"{col} = COALESCE({col}, ?)")
        params.append(val)


def _extract_ol_language(ol_data: dict) -> str | None:
    """Extract language from Open Library data."""
    lang_keys = ol_data.get("languages", [])
    if not lang_keys or not isinstance(lang_keys[0], dict):
        return None
    lang_key = lang_keys[0].get("key", "")
    return lang_key.split("/")[-1] if "/" in lang_key else None


def _extract_ol_description(ol_data: dict) -> str:
    """Extract description from Open Library data."""
    desc = ol_data.get("description")
    if isinstance(desc, dict):
        return desc.get("value", "")
    return desc or ""


def _extract_ol_fields(ol_data: dict, isbn: str | None) -> tuple[list, list, int]:
    """Extract update fields from Open Library data.

    Returns (updates, params, isbn_found_count).
    """
    updates = []
    params = []

    if not isinstance(ol_data, dict):
        return updates, params, 0

    _add_coalesce_field(updates, params, "language", _extract_ol_language(ol_data))
    _add_coalesce_field(updates, params, "description", _extract_ol_description(ol_data))
    _add_coalesce_field(updates, params, "published_date", ol_data.get("publish_date", ""))

    isbn_found = 0
    if not isbn:
        isbns = ol_data.get("isbn", [])
        if isbns:
            _add_coalesce_field(updates, params, "isbn", isbns[0])
            isbn_found = 1

    return updates, params, isbn_found


def _enrich_one_book(
    cursor, book: dict, now: str, dry_run: bool, delay: float,
) -> tuple[str, int]:
    """Enrich a single book from ISBN sources.

    Returns (status, isbn_found_count) where status is 'enriched', 'skipped',
    or 'error'.
    """
    book_id = book["id"]
    title = book["title"]
    author = book["author"]
    isbn = book["isbn"]

    gb_data, ol_data = _fetch_source_data(isbn, title, author)

    if not gb_data and not ol_data:
        if delay > 0:
            time.sleep(delay)
        return "skipped", 0

    updates = []
    params = []
    isbn_found = 0

    if gb_data:
        updates, params, isbn_found = _extract_gb_fields(gb_data, isbn)
    elif ol_data:
        updates, params, isbn_found = _extract_ol_fields(ol_data, isbn)

    if dry_run:
        if updates:
            print(f"  [DRY RUN] {title[:50]} — {len(updates)} fields")
            return "enriched", isbn_found
        return "skipped", 0

    if updates:
        updates.append("isbn_enriched_at = ?")
        params.append(now)
        params.append(book_id)
        sql = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"  # nosec B608
        cursor.execute(sql, params)
        return "enriched", isbn_found

    # No data found but mark as attempted
    cursor.execute(
        "UPDATE audiobooks SET isbn_enriched_at = ? WHERE id = ?",
        (now, book_id),
    )
    return "skipped", 0


def _print_isbn_summary(books: list, enriched: int, skipped: int,
                        errors: int, isbn_found: int, dry_run: bool) -> dict:
    """Print ISBN enrichment summary and return results dict."""
    results = {
        "total": len(books),
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
        "isbn_found": isbn_found,
    }

    print(f"\n{'=' * 60}")
    print(f"ISBN ENRICHMENT RESULTS {'(DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 60}")
    print(f"Books processed:   {len(books)}")
    print(f"Enriched:          {enriched}")
    print(f"No data found:     {skipped}")
    print(f"Errors:            {errors}")
    print(f"ISBNs discovered:  {isbn_found}")

    return results


def enrich_from_isbn(
    dry_run: bool = False,
    delay: float = DEFAULT_DELAY,
    db_path: Path | None = None,
    include_asin_books: bool = False,
    single_id: int | None = None,
) -> dict:
    """Enrich books from ISBN/title via Open Library and Google Books."""
    db_path = _resolve_db_path(db_path)
    print(f"Database: {db_path}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    books = _fetch_isbn_candidates(db_path, include_asin_books, single_id)
    print(f"Books to enrich via ISBN: {len(books)}")
    if not books:
        print("Nothing to do.")
        return {"enriched": 0, "skipped": 0, "errors": 0}

    enriched = 0
    skipped = 0
    errors = 0
    isbn_found = 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for idx, book in enumerate(books, 1):
        if idx % 50 == 0 or idx == 1:
            print(f"  [{idx}/{len(books)}] {book['title'][:50]}...")

        status, found = _enrich_one_book(cursor, dict(book), now, dry_run, delay)
        isbn_found += found

        if status == "enriched":
            enriched += 1
        elif status == "skipped":
            skipped += 1
        else:
            errors += 1

        if delay > 0:
            time.sleep(delay)

    if not dry_run:
        conn.commit()
    conn.close()

    return _print_isbn_summary(books, enriched, skipped, errors, isbn_found, dry_run)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Enrich audiobook metadata from ISBN (Open Library + Google Books)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing to DB",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds between API calls (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to SQLite database (default: from config)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include books that already have ASINs",
    )
    parser.add_argument(
        "--id",
        type=int,
        default=None,
        help="Enrich a single book by database ID",
    )
    args = parser.parse_args()

    db = Path(args.db) if args.db else None
    enrich_from_isbn(
        dry_run=args.dry_run,
        delay=args.delay,
        db_path=db,
        include_asin_books=args.all,
        single_id=args.id,
    )


if __name__ == "__main__":
    main()
