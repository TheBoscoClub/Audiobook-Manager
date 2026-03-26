#!/usr/bin/env python3
"""
Single-Book Enrichment Module
===============================
Enriches one audiobook by database ID, using Audible API (if ASIN exists)
and ISBN fallback (Google Books + Open Library).

Designed to be called inline after each new book is inserted into the DB
by import_single.py or add_new_audiobooks.py.

Usage (CLI):
    python3 enrich_single.py --db /path/to/audiobooks.db --id 42

Usage (import):
    from scripts.enrich_single import enrich_book
    result = enrich_book(book_id=42, db_path=Path("/path/to/db"))
"""

import json
import re
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

# ── Audible API constants ──
AUDIBLE_API = "https://api.audible.com/1.0/catalog/products"
MARKETPLACE = "AF2M0KC94RCEA"
ALL_RESPONSE_GROUPS = ",".join(
    [
        "contributors",
        "category_ladders",
        "media",
        "product_attrs",
        "product_desc",
        "product_extended_attrs",
        "product_plan_details",
        "product_plans",
        "rating",
        "review_attrs",
        "reviews",
        "sample",
        "series",
        "sku",
        "relationships",
    ]
)

# ── ISBN API constants ──
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
OPENLIBRARY_API = "https://openlibrary.org"


# ═══════════════════════════════════════════════════════════
# Audible API helpers (same logic as enrich_from_audible.py)
# ═══════════════════════════════════════════════════════════


def _fetch_audible_product(asin: str) -> dict | None:
    """Query Audible API for full product data."""
    url = (
        f"{AUDIBLE_API}/{asin}"
        f"?response_groups={ALL_RESPONSE_GROUPS}"
        f"&marketplace={MARKETPLACE}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("product")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            time.sleep(5)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                    return data.get("product")
            except Exception:
                return None
        return None
    except (urllib.error.URLError, TimeoutError):
        return None


def _parse_sequence(seq_str: str) -> float | None:
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


def _extract_categories(product: dict) -> list[dict]:
    categories = []
    for ladder in product.get("category_ladders", []):
        ladder_items = ladder.get("ladder", [])
        if not ladder_items:
            continue
        path_parts = []
        for item in ladder_items:
            name = item.get("name", "")
            cat_id = item.get("id", "")
            if name:
                path_parts.append(name)
                categories.append(
                    {
                        "category_path": " > ".join(path_parts),
                        "category_name": name,
                        "root_category": path_parts[0],
                        "depth": len(path_parts),
                        "audible_category_id": cat_id,
                    }
                )
    return categories


def _extract_editorial_reviews(product: dict) -> list[dict]:
    reviews = []
    for review in product.get("editorial_reviews", []):
        text = review if isinstance(review, str) else review.get("review", "")
        source = review.get("source", "") if isinstance(review, dict) else ""
        if text:
            reviews.append({"review_text": text, "source": source})
    return reviews


def _extract_rating(product: dict) -> dict:
    rating = product.get("rating", {})
    return {
        "rating_overall": rating.get("overall_distribution", {}).get(
            "display_average_rating"
        ),
        "rating_performance": rating.get("performance_distribution", {}).get(
            "display_average_rating"
        ),
        "rating_story": rating.get("story_distribution", {}).get(
            "display_average_rating"
        ),
        "num_ratings": rating.get("num_reviews"),
        "num_reviews": rating.get("overall_distribution", {}).get("num_ratings"),
    }


def _get_best_image_url(product: dict) -> str | None:
    images = product.get("product_images", {})
    for size in ["2400", "1024", "500", "252"]:
        if size in images:
            return images[size]
    if images:
        return next(iter(images.values()))
    return None


# ═══════════════════════════════════════════════════════════
# ISBN API helpers (same logic as enrich_from_isbn.py)
# ═══════════════════════════════════════════════════════════


def _query_google_books(
    isbn: str | None = None, title: str | None = None, author: str | None = None
) -> dict | None:
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
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        pass
    return None


def _query_openlibrary_search(title: str, author: str | None = None) -> dict | None:
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
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


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


# ═══════════════════════════════════════════════════════════
# Main enrichment function
# ═══════════════════════════════════════════════════════════


def enrich_book(
    book_id: int,
    db_path: Path | None = None,
    quiet: bool = False,
) -> dict:
    """Enrich a single audiobook by database ID.

    First tries Audible API (if ASIN exists), then falls back to
    ISBN/Google Books/Open Library for remaining fields.

    Args:
        book_id: Database ID of the audiobook
        db_path: Path to SQLite database (default: from config)
        quiet: Suppress print output

    Returns:
        dict with keys: audible_enriched (bool), isbn_enriched (bool),
        fields_updated (int), errors (list[str])
    """
    if db_path is None:
        if DATABASE_PATH is None:
            return {
                "audible_enriched": False,
                "isbn_enriched": False,
                "fields_updated": 0,
                "errors": ["No database path"],
            }
        db_path = DATABASE_PATH

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "audible_enriched": False,
        "isbn_enriched": False,
        "fields_updated": 0,
        "errors": [],
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, title, author, asin, isbn, series FROM audiobooks WHERE id = ?",
        (book_id,),
    )
    book = cursor.fetchone()
    if not book:
        conn.close()
        result["errors"].append(f"Book ID {book_id} not found")
        return result

    title = book["title"]
    author = book["author"]
    asin = book["asin"]
    isbn = book["isbn"]
    existing_series = book["series"]

    if not quiet:
        print(f"  Enriching: {title} (ID {book_id})")

    # ── Audible enrichment (primary) ──
    if asin:
        product = _fetch_audible_product(asin)
        if product:
            updates = []
            params = []

            def add_field(col: str, val: object) -> None:
                if val is not None:
                    updates.append(f"{col} = ?")
                    params.append(val)

            # Series — only set if not already populated
            if not existing_series:
                series_list = product.get("series", [])
                if series_list:
                    s = series_list[0]
                    add_field("series", s.get("title"))
                    add_field("series_sequence", _parse_sequence(s.get("sequence", "")))

            # Core Audible fields
            add_field("subtitle", product.get("subtitle"))
            add_field("language", product.get("language"))
            add_field("format_type", product.get("format_type"))
            add_field("runtime_length_min", product.get("runtime_length_min"))

            release_date = (
                product.get("release_date")
                or product.get("publication_datetime", "")[:10]
                or None
            )
            add_field("release_date", release_date)
            add_field("publisher_summary", product.get("publisher_summary"))

            rating_data = _extract_rating(product)
            add_field("rating_overall", rating_data.get("rating_overall"))
            add_field("rating_performance", rating_data.get("rating_performance"))
            add_field("rating_story", rating_data.get("rating_story"))
            add_field("num_ratings", rating_data.get("num_ratings"))
            add_field("num_reviews", rating_data.get("num_reviews"))

            add_field("audible_image_url", _get_best_image_url(product))
            add_field("sample_url", product.get("sample_url"))
            add_field("audible_sku", product.get("sku"))
            add_field(
                "is_adult_product",
                1 if product.get("is_adult_product") else 0,
            )
            add_field("merchandising_summary", product.get("merchandising_summary"))
            content_type = product.get("content_type")
            if content_type:
                add_field("content_type", content_type)

            # Timestamp
            updates.append("audible_enriched_at = ?")
            params.append(now)

            if updates:
                params.append(book_id)
                sql = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"
                cursor.execute(sql, params)
                result["fields_updated"] += len(updates) - 1  # exclude timestamp
                result["audible_enriched"] = True

            # Categories
            categories = _extract_categories(product)
            if categories:
                cursor.execute(
                    "DELETE FROM audible_categories WHERE audiobook_id = ?",
                    (book_id,),
                )
                for cat in categories:
                    cursor.execute(
                        "INSERT INTO audible_categories "
                        "(audiobook_id, category_path, category_name, "
                        "root_category, depth, audible_category_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            book_id,
                            cat["category_path"],
                            cat["category_name"],
                            cat["root_category"],
                            cat["depth"],
                            cat["audible_category_id"],
                        ),
                    )

            # Editorial reviews
            reviews = _extract_editorial_reviews(product)
            if reviews:
                cursor.execute(
                    "DELETE FROM editorial_reviews WHERE audiobook_id = ?",
                    (book_id,),
                )
                for review in reviews:
                    cursor.execute(
                        "INSERT INTO editorial_reviews "
                        "(audiobook_id, review_text, source) VALUES (?, ?, ?)",
                        (book_id, review["review_text"], review["source"]),
                    )

            # Author ASINs
            for contributor in product.get("authors", []):
                a_asin = contributor.get("asin")
                a_name = contributor.get("name")
                if a_asin and a_name:
                    cursor.execute(
                        "UPDATE authors SET asin = ? WHERE name = ? "
                        "AND (asin IS NULL OR asin = '')",
                        (a_asin, a_name),
                    )

            if not quiet:
                print(
                    f"    Audible: {result['fields_updated']} fields,"
                    f" {len(categories)} categories,"
                    f" {len(reviews)} reviews"
                )
        else:
            if not quiet:
                print(f"    Audible: no data for ASIN {asin}")

    # ── ISBN enrichment (fallback for missing fields) ──
    # Check what's still missing after Audible enrichment
    cursor.execute(
        "SELECT language, description, published_year, isbn, isbn_enriched_at "
        "FROM audiobooks WHERE id = ?",
        (book_id,),
    )
    current = cursor.fetchone()

    # Skip ISBN if already done
    if current and current["isbn_enriched_at"] is None:
        needs_isbn = (
            not current["language"]
            or not current["description"]
            or not current["published_year"]
            or (not isbn and not current["isbn"])
        )

        if needs_isbn:
            gb_data = None
            ol_data = None

            current_isbn = isbn or current["isbn"]
            if current_isbn:
                gb_data = _query_google_books(isbn=current_isbn)
            else:
                gb_data = _query_google_books(title=title, author=author)
                if not gb_data:
                    ol_data = _query_openlibrary_search(title, author)

            isbn_updates = []
            isbn_params = []

            if gb_data:
                lang = gb_data.get("language")
                if lang and len(lang) == 2:
                    lang = LANG_MAP.get(lang, lang)
                if lang and not current["language"]:
                    isbn_updates.append("language = COALESCE(language, ?)")
                    isbn_params.append(lang)

                desc = gb_data.get("description")
                if desc and not current["description"]:
                    isbn_updates.append("description = COALESCE(description, ?)")
                    isbn_params.append(desc)

                pub_date = gb_data.get("publishedDate")
                if pub_date and not current["published_year"]:
                    isbn_updates.append("published_date = COALESCE(published_date, ?)")
                    isbn_params.append(pub_date[:10])
                    try:
                        isbn_updates.append(
                            "published_year = COALESCE(published_year, ?)"
                        )
                        isbn_params.append(int(pub_date[:4]))
                    except ValueError:
                        pass

                if not current_isbn:
                    identifiers = gb_data.get("industryIdentifiers", [])
                    for ident in identifiers:
                        if ident.get("type") in ("ISBN_13", "ISBN_10"):
                            isbn_updates.append("isbn = COALESCE(isbn, ?)")
                            isbn_params.append(ident["identifier"])
                            break

            elif ol_data and isinstance(ol_data, dict):
                desc = ol_data.get("description")
                if isinstance(desc, dict):
                    desc = desc.get("value", "")
                if desc and not current["description"]:
                    isbn_updates.append("description = COALESCE(description, ?)")
                    isbn_params.append(desc)

                if not current_isbn:
                    isbns = ol_data.get("isbn", [])
                    if isbns:
                        isbn_updates.append("isbn = COALESCE(isbn, ?)")
                        isbn_params.append(isbns[0])

            # Mark as ISBN-enriched regardless
            isbn_updates.append("isbn_enriched_at = ?")
            isbn_params.append(now)
            isbn_params.append(book_id)
            sql = f"UPDATE audiobooks SET {', '.join(isbn_updates)} WHERE id = ?"
            cursor.execute(sql, isbn_params)
            isbn_field_count = len(isbn_updates) - 1
            result["fields_updated"] += isbn_field_count
            result["isbn_enriched"] = isbn_field_count > 0

            if not quiet:
                src = (
                    "Google Books"
                    if gb_data
                    else ("OpenLibrary" if ol_data else "none")
                )
                print(f"    ISBN ({src}): {isbn_field_count} fields")
        else:
            # Nothing missing, just mark as attempted
            cursor.execute(
                "UPDATE audiobooks SET isbn_enriched_at = ? WHERE id = ?",
                (now, book_id),
            )

    conn.commit()
    conn.close()

    if not quiet:
        total = result["fields_updated"]
        print(f"    Total: {total} fields enriched")

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Enrich a single audiobook from Audible + ISBN sources"
    )
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--id", type=int, required=True, help="Audiobook database ID")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    db = Path(args.db) if args.db else None
    result = enrich_book(book_id=args.id, db_path=db, quiet=args.quiet)

    if result["errors"]:
        for err in result["errors"]:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"\nEnrichment complete: {result['fields_updated']} fields updated")
    if result["audible_enriched"]:
        print("  Source: Audible API")
    if result["isbn_enriched"]:
        print("  Source: ISBN (Google Books / Open Library)")


if __name__ == "__main__":
    main()
