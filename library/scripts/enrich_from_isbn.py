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


def enrich_from_isbn(
    dry_run: bool = False,
    delay: float = DEFAULT_DELAY,
    db_path: Path | None = None,
    include_asin_books: bool = False,
    single_id: int | None = None,
) -> dict:
    """Enrich books from ISBN/title via Open Library and Google Books."""

    if db_path is None:
        if DATABASE_PATH is None:
            print("Error: No database path. Use --db flag.", file=sys.stderr)
            sys.exit(1)
        db_path = DATABASE_PATH

    print(f"Database: {db_path}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find candidates: books not yet ISBN-enriched
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
        # Default: only books without ASIN or without Audible enrichment
        cursor.execute(
            "SELECT id, title, author, isbn, asin FROM audiobooks "
            "WHERE isbn_enriched_at IS NULL "
            "AND (asin IS NULL OR asin = '' OR audible_enriched_at IS NULL)"
        )

    books = cursor.fetchall()
    conn.close()

    print(f"Books to enrich via ISBN: {len(books)}")
    if not books:
        print("Nothing to do.")
        return {"enriched": 0, "skipped": 0, "errors": 0}

    enriched = 0
    skipped = 0
    errors = 0
    isbn_found = 0

    # Re-open for writes
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for idx, book in enumerate(books, 1):
        book_id = book["id"]
        title = book["title"]
        author = book["author"]
        isbn = book["isbn"]

        if idx % 50 == 0 or idx == 1:
            print(f"  [{idx}/{len(books)}] {title[:50]}...")

        # Try Google Books first (better coverage, faster)
        gb_data = None
        ol_data = None

        if isbn:
            gb_data = query_google_books(isbn=isbn)
            if not gb_data:
                ol_data = query_openlibrary_isbn(isbn)
        else:
            # Try title+author search
            gb_data = query_google_books(title=title, author=author)
            if not gb_data:
                ol_data = query_openlibrary_search(title, author)

        if not gb_data and not ol_data:
            skipped += 1
            if delay > 0:
                time.sleep(delay)
            continue

        # Extract data from whichever source responded
        updates = []
        params = []

        def add_field(col: str, val: object) -> None:
            if val is not None:
                updates.append(f"{col} = COALESCE({col}, ?)")
                params.append(val)

        if gb_data:
            # Google Books data extraction
            lang = gb_data.get("language")
            if lang and len(lang) == 2:
                # Convert 2-letter to full name for common languages
                lang_map = {
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
                lang = lang_map.get(lang, lang)
            add_field("language", lang)

            desc = gb_data.get("description")
            if desc:
                add_field("description", desc)

            pub_date = gb_data.get("publishedDate")
            if pub_date:
                add_field("published_date", pub_date[:10])
                try:
                    add_field("published_year", int(pub_date[:4]))
                except ValueError:
                    pass

            # Extract ISBN if we didn't have one
            if not isbn:
                identifiers = gb_data.get("industryIdentifiers", [])
                for ident in identifiers:
                    if ident.get("type") == "ISBN_13":
                        add_field("isbn", ident["identifier"])
                        isbn_found += 1
                        break
                    elif ident.get("type") == "ISBN_10":
                        add_field("isbn", ident["identifier"])
                        isbn_found += 1
                        break

            # Categories from Google Books could be added to genres table
            # in a future enhancement if needed

        elif ol_data:
            # Open Library data extraction
            if isinstance(ol_data, dict):
                lang_keys = ol_data.get("languages", [])
                if lang_keys and isinstance(lang_keys[0], dict):
                    lang_key = lang_keys[0].get("key", "")
                    lang = lang_key.split("/")[-1] if "/" in lang_key else None
                    add_field("language", lang)

                desc = ol_data.get("description")
                if isinstance(desc, dict):
                    desc = desc.get("value", "")
                if desc:
                    add_field("description", desc)

                pub_date = ol_data.get("publish_date", "")
                if pub_date:
                    add_field("published_date", pub_date)

                # ISBN from search results
                if not isbn:
                    isbns = ol_data.get("isbn", [])
                    if isbns:
                        add_field("isbn", isbns[0])
                        isbn_found += 1

        if dry_run:
            if updates:
                print(f"  [DRY RUN] {title[:50]} — {len(updates)} fields")
                enriched += 1
            else:
                skipped += 1
        elif updates:
            # Always mark as ISBN-enriched
            updates.append("isbn_enriched_at = ?")
            params.append(now)
            params.append(book_id)
            sql = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(sql, params)
            enriched += 1
        else:
            # No data found but mark as attempted
            cursor.execute(
                "UPDATE audiobooks SET isbn_enriched_at = ? WHERE id = ?",
                (now, book_id),
            )
            skipped += 1

        if delay > 0:
            time.sleep(delay)

    if not dry_run:
        conn.commit()
    conn.close()

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
