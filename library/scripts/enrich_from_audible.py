#!/usr/bin/env python3
"""
Comprehensive Audible Metadata Enrichment
==========================================
Queries the Audible public catalog API for ALL available metadata and populates
every field in the database. No authentication needed.

Uses a single API call per book with all response_groups to minimize HTTP
round-trips. Populates:
  - Series + sequence (if not already set)
  - Subtitle, language, format_type
  - Ratings (overall, performance, story) + counts
  - Hierarchical categories (category_ladders)
  - Editorial reviews
  - Publisher summary (HTML)
  - Release date, runtime, Audible SKU
  - Cover art URL, sample URL
  - Author ASINs (linked to normalized authors table)
  - is_adult_product flag, merchandising_summary

Usage:
    python3 enrich_from_audible.py --db /path/to/audiobooks.db [--dry-run] [--delay 0.3]
    python3 enrich_from_audible.py --db /path/to/audiobooks.db --force  # re-enrich all
    python3 enrich_from_audible.py --db /path/to/audiobooks.db --id 42  # single book
"""

import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
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

# All available response_groups for maximum data capture
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


def fetch_audible_product(asin: str) -> dict | None:
    """Query Audible API for full product data.

    Returns the product dict or None on failure.
    """
    url = f"{AUDIBLE_API}/{asin}?response_groups={ALL_RESPONSE_GROUPS}&marketplace={MARKETPLACE}"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})  # noqa: S310 — Request for fixed HTTPS Audible API endpoint; no user-controlled URL scheme

    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # Reason: URL built from trusted HTTPS constant (AUDIBLE_API) + validated ASIN from internal DB; not user-controlled scheme
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310  # nosec B310
            data = json.loads(resp.read())
            return data.get("product")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            print(f"  Rate limited on {asin} — waiting 30s", file=sys.stderr)
            time.sleep(30)
            return fetch_audible_product(asin)  # retry once
        print(f"  HTTP {e.code} for {asin}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  Network error for {asin}: {e}", file=sys.stderr)
        return None


def parse_sequence(seq_str: str) -> float | None:
    """Parse sequence string to a number. Handles '1', '1.5', etc."""
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


def extract_categories(product: dict) -> list[dict]:
    """Extract hierarchical categories from category_ladders.

    Returns list of dicts with: category_path, category_name, root_category,
    depth, audible_category_id
    """
    categories = []
    ladders = product.get("category_ladders", [])

    for ladder in ladders:
        ladder_items = ladder.get("ladder", [])
        if not ladder_items:
            continue

        # Build the full path and extract each level
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


def extract_editorial_reviews(product: dict) -> list[dict]:
    """Extract editorial reviews from product data."""
    reviews = []
    editorial = product.get("editorial_reviews", [])

    for review in editorial:
        text = review if isinstance(review, str) else review.get("review", "")
        source = review.get("source", "") if isinstance(review, dict) else ""
        if text:
            reviews.append({"review_text": text, "source": source})

    return reviews


def extract_series_info(
    product: dict, series_popularity: dict[str, int] | None = None
) -> tuple[str, float | None]:
    """Extract best series from product data.

    If series_popularity is provided, picks the series with the most members
    in our library (same logic as populate_series_from_audible.py).
    """
    series_list = product.get("series", [])
    if not series_list:
        return ("", None)

    if len(series_list) == 1:
        s = series_list[0]
        return (s.get("title", ""), parse_sequence(s.get("sequence", "")))

    if series_popularity is None:
        series_popularity = {}

    scored = []
    for s in series_list:
        title = s.get("title", "")
        pop = series_popularity.get(title, 0)
        seq = parse_sequence(s.get("sequence", ""))
        scored.append((pop, -len(title), title, seq))

    scored.sort(reverse=True)
    _, _, best_title, best_seq = scored[0]
    return (best_title, best_seq)


def extract_rating(product: dict) -> dict:
    """Extract rating data from product."""
    rating = product.get("rating", {})
    return {
        "rating_overall": rating.get("overall_distribution", {}).get("display_average_rating"),
        "rating_performance": rating.get("performance_distribution", {}).get(
            "display_average_rating"
        ),
        "rating_story": rating.get("story_distribution", {}).get("display_average_rating"),
        "num_ratings": rating.get("num_reviews"),
        "num_reviews": rating.get("overall_distribution", {}).get("num_ratings"),
    }


def extract_contributors(product: dict) -> tuple[list[dict], list[dict]]:
    """Extract authors and narrators with their ASINs.

    Returns (authors, narrators) where each is a list of
    {"name": str, "asin": str|None}
    """
    authors = []
    narrators = []

    for contributor in product.get("authors", []):
        authors.append({"name": contributor.get("name", ""), "asin": contributor.get("asin")})

    for contributor in product.get("narrators", []):
        narrators.append({"name": contributor.get("name", ""), "asin": contributor.get("asin")})

    return (authors, narrators)


def get_best_image_url(product: dict) -> str | None:
    """Get the highest-resolution cover image URL."""
    images = product.get("product_images", {})
    # Prefer largest first
    for size in ["2400", "1024", "500", "252"]:
        if size in images:
            return images[size]
    # Fallback to any available
    if images:
        return next(iter(images.values()))
    return None


def _resolve_db_path(db_path: Path | None) -> Path:
    """Resolve database path from argument or config, exit on failure."""
    if db_path is not None:
        return db_path
    if DATABASE_PATH is None:
        print("Error: No database path. Use --db flag.", file=sys.stderr)
        sys.exit(1)
    return DATABASE_PATH


def _fetch_candidates(db_path: Path, force: bool, single_id: int | None) -> list:
    """Fetch candidate books from the database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if single_id is not None:
        cursor.execute("SELECT id, asin, title, series FROM audiobooks WHERE id = ?", (single_id,))
    elif force:
        cursor.execute(
            "SELECT id, asin, title, series FROM audiobooks WHERE asin IS NOT NULL AND asin != ''"
        )
    else:
        cursor.execute(
            "SELECT id, asin, title, series FROM audiobooks "
            "WHERE asin IS NOT NULL AND asin != '' "
            "AND audible_enriched_at IS NULL"
        )

    books = cursor.fetchall()
    conn.close()
    return books


def _query_audible_api(books: list, delay: float) -> tuple[dict, dict, int]:
    """Query Audible API for all candidate books.

    Returns (products, series_counter, errors).
    """
    products: dict[int, dict] = {}
    series_counter: dict[str, int] = {}
    errors = 0

    for idx, book in enumerate(books, 1):
        book_id = book["id"]
        asin = book["asin"]

        if idx % 50 == 0 or idx == 1:
            print(f"  [{idx}/{len(books)}] Querying {asin}...")

        product = fetch_audible_product(asin)
        if product:
            products[book_id] = product
            for s in product.get("series", []):
                title = s.get("title", "")
                if title:
                    series_counter[title] = series_counter.get(title, 0) + 1
        else:
            errors += 1

        if delay > 0:
            time.sleep(delay)

    print(f"\n  API results: {len(products)} successful, {errors} errors")
    return products, series_counter, errors


def _extract_product_fields(product: dict) -> dict:
    """Extract flat fields from an Audible product for DB update."""
    release_date = (
        product.get("release_date") or product.get("publication_datetime", "")[:10] or None
    )
    return {
        "subtitle": product.get("subtitle"),
        "language": product.get("language"),
        "format_type": product.get("format_type"),
        "runtime_length_min": product.get("runtime_length_min"),
        "release_date": release_date,
        "publisher_summary": product.get("publisher_summary"),
        "sample_url": product.get("sample_url"),
        "audible_sku": product.get("sku"),
        "is_adult_product": 1 if product.get("is_adult_product") else 0,
        "merchandising_summary": product.get("merchandising_summary"),
        "content_type": product.get("content_type"),
    }


def _build_update_params(
    fields: dict,
    rating_data: dict,
    image_url: str | None,
    series_name: str,
    series_seq: float | None,
    has_existing_series: bool,
) -> tuple[list[str], list]:
    """Build SQL update clauses and params from extracted data."""
    updates = []
    params = []

    def add_field(col: str, val: object) -> None:
        if val is not None:
            updates.append(f"{col} = ?")
            params.append(val)

    if not has_existing_series:
        add_field("series", series_name or None)
        add_field("series_sequence", series_seq)

    for col, val in fields.items():
        if col == "content_type" and not val:
            continue
        add_field(col, val)

    add_field("rating_overall", rating_data.get("rating_overall"))
    add_field("rating_performance", rating_data.get("rating_performance"))
    add_field("rating_story", rating_data.get("rating_story"))
    add_field("num_ratings", rating_data.get("num_ratings"))
    add_field("num_reviews", rating_data.get("num_reviews"))
    add_field("audible_image_url", image_url)

    return updates, params


def _log_dry_run(
    product: dict, rating_data: dict, categories: list, series_name: str, fields: dict
) -> None:
    """Log a dry-run enrichment entry."""
    title = product.get("title", "?")
    info_parts = []
    if fields.get("subtitle"):
        info_parts.append(f"subtitle={fields['subtitle'][:40]}")
    if fields.get("language"):
        info_parts.append(f"lang={fields['language']}")
    if rating_data.get("rating_overall"):
        info_parts.append(f"rating={rating_data['rating_overall']}")
    if categories:
        info_parts.append(f"categories={len(categories)}")
    if series_name:
        info_parts.append(f"series={series_name}")
    info = ", ".join(info_parts) if info_parts else "minimal data"
    print(f"  [DRY RUN] {title[:50]} — {info}")


def _upsert_categories(cursor, book_id: int, categories: list[dict]) -> None:
    """Replace categories for a book."""
    cursor.execute("DELETE FROM audible_categories WHERE audiobook_id = ?", (book_id,))
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


def _upsert_reviews(cursor, book_id: int, reviews: list[dict]) -> None:
    """Replace editorial reviews for a book."""
    cursor.execute("DELETE FROM editorial_reviews WHERE audiobook_id = ?", (book_id,))
    for review in reviews:
        cursor.execute(
            "INSERT INTO editorial_reviews (audiobook_id, review_text, source) VALUES (?, ?, ?)",
            (book_id, review["review_text"], review["source"]),
        )


def _update_author_if_missing(cursor, book_id: int, authors: list[dict]) -> None:
    """Update author from Audible if current value is missing/unknown."""
    cursor.execute("SELECT author FROM audiobooks WHERE id = ?", (book_id,))
    current_author = cursor.fetchone()
    current_val = current_author[0] if current_author else None
    if current_val and current_val.strip().lower() not in ("unknown author", ""):
        return
    author_names = [a["name"] for a in authors if a.get("name")]
    if author_names:
        cursor.execute(
            "UPDATE audiobooks SET author = ? WHERE id = ?", (", ".join(author_names), book_id)
        )


def _update_author_asins(cursor, authors: list[dict]) -> None:
    """Update author ASINs in the normalized authors table."""
    for author_info in authors:
        author_asin = author_info.get("asin")
        author_name = author_info.get("name")
        if author_asin and author_name:
            cursor.execute(
                "UPDATE authors SET asin = ? WHERE name = ? AND (asin IS NULL OR asin = '')",
                (author_asin, author_name),
            )


def _update_narrator_if_missing(cursor, book_id: int, narrators: list[dict]) -> None:
    """Update narrator from Audible if current value is missing/unknown."""
    cursor.execute("SELECT narrator FROM audiobooks WHERE id = ?", (book_id,))
    current_narrator = cursor.fetchone()
    current_val = current_narrator[0] if current_narrator else None
    if current_val and current_val.strip().lower() not in ("unknown narrator", ""):
        return
    narrator_names = [n["name"] for n in narrators if n.get("name")]
    if narrator_names:
        cursor.execute(
            "UPDATE audiobooks SET narrator = ? WHERE id = ?", (", ".join(narrator_names), book_id)
        )


def _ensure_normalized_narrators(cursor, book_id: int, narrators: list[dict]) -> None:
    """Ensure normalized narrator entries exist and are linked."""
    for pos, narrator_info in enumerate(narrators):
        narrator_name = narrator_info.get("name")
        if not narrator_name:
            continue
        try:
            from backend.name_parser import generate_sort_name

            sort_name = generate_sort_name(narrator_name)
            cursor.execute(
                "INSERT OR IGNORE INTO narrators (name, sort_name) VALUES (?, ?)",
                (narrator_name, sort_name),
            )
            narrator_id = cursor.execute(
                "SELECT id FROM narrators WHERE name = ?", (narrator_name,)
            ).fetchone()
            if narrator_id:
                cursor.execute(
                    "INSERT OR IGNORE INTO book_narrators "
                    "(book_id, narrator_id, position) VALUES (?, ?, ?)",
                    (book_id, narrator_id[0], pos),
                )
        except (ImportError, sqlite3.DatabaseError):
            pass  # Normalized tables may not exist yet


def _has_existing_series(cursor, book_id: int) -> bool:
    """Check if book already has a series set."""
    cursor.execute("SELECT series FROM audiobooks WHERE id = ?", (book_id,))
    existing = cursor.fetchone()
    return bool(existing and existing[0] and existing[0] != "")


def _execute_book_update(cursor, book_id: int, updates: list, params: list, now: str) -> bool:
    """Execute the main audiobooks table update.

    Returns True on success, False on DB error.
    """
    updates.append("audible_enriched_at = ?")
    params.append(now)

    if not updates:
        return True

    params.append(book_id)
    sql = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"  # nosec B608  # noqa: S608
    try:
        cursor.execute(sql, params)  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        return True
    except sqlite3.DatabaseError as e:
        print(f"  DB ERROR on book_id={book_id}: {e}")
        print(f"    SQL: {sql[:200]}")
        print(f"    Params types: {[type(p).__name__ for p in params]}")
        return False


def _enrich_single_book(
    cursor, book_id: int, product: dict, series_counter: dict, now: str, dry_run: bool
) -> bool:
    """Enrich a single book from Audible product data.

    Returns True if enrichment succeeded, False on DB error.
    """
    series_name, series_seq = extract_series_info(product, series_counter)
    rating_data = extract_rating(product)
    categories = extract_categories(product)
    reviews = extract_editorial_reviews(product)
    authors, narrators = extract_contributors(product)
    image_url = get_best_image_url(product)
    fields = _extract_product_fields(product)

    if dry_run:
        _log_dry_run(product, rating_data, categories, series_name, fields)
        return True

    updates, params = _build_update_params(
        fields,
        rating_data,
        image_url,
        series_name,
        series_seq,
        _has_existing_series(cursor, book_id),
    )

    if not _execute_book_update(cursor, book_id, updates, params, now):
        return False

    _upsert_related_data(cursor, book_id, categories, reviews, authors, narrators)
    return True


def _upsert_related_data(
    cursor, book_id: int, categories: list, reviews: list, authors: list, narrators: list
) -> None:
    """Upsert categories, reviews, authors, and narrators."""
    if categories:
        _upsert_categories(cursor, book_id, categories)
    if reviews:
        _upsert_reviews(cursor, book_id, reviews)
    if authors:
        _update_author_if_missing(cursor, book_id, authors)
        _update_author_asins(cursor, authors)
    if narrators:
        _update_narrator_if_missing(cursor, book_id, narrators)
        _ensure_normalized_narrators(cursor, book_id, narrators)


def _print_summary(
    books: list, enriched: int, errors: int, series_counter: dict, dry_run: bool
) -> dict:
    """Print enrichment summary and return results dict."""
    results = {
        "total": len(books),
        "enriched": enriched,
        "errors": errors,
        "unique_series": len(series_counter),
    }

    print(f"\n{'=' * 60}")
    print(f"ENRICHMENT RESULTS {'(DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 60}")
    print(f"Books queried:     {len(books)}")
    print(f"Successfully enriched: {enriched}")
    print(f"API errors:        {errors}")
    print(f"Unique series found:   {len(series_counter)}")

    if series_counter:
        top = sorted(series_counter.items(), key=lambda x: -x[1])[:10]
        print("\nTop series:")
        for name, count in top:
            print(f"  {count:3d} books: {name}")

    return results


def enrich_from_audible(
    dry_run: bool = False,
    delay: float = DEFAULT_DELAY,
    db_path: Path | None = None,
    force: bool = False,
    single_id: int | None = None,
) -> dict:
    """Main enrichment function."""
    db_path = _resolve_db_path(db_path)
    print(f"Database: {db_path}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    books = _fetch_candidates(db_path, force, single_id)
    print(f"Books to enrich: {len(books)}")
    if not books:
        print("Nothing to do.")
        return {"enriched": 0, "skipped": 0, "errors": 0}

    print("\nPhase 1: Querying Audible API...")
    products, series_counter, errors = _query_audible_api(books, delay)

    print("\nPhase 2: Updating database...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    enriched = 0

    for book_id, product in products.items():
        success = _enrich_single_book(cursor, book_id, product, series_counter, now, dry_run)
        if success:
            enriched += 1
        else:
            errors += 1
            continue

        if enriched % 50 == 0:
            conn.commit()
            print(f"  Committed {enriched} books...")

    if not dry_run:
        conn.commit()
    conn.close()

    return _print_summary(books, enriched, errors, series_counter, dry_run)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Enrich audiobook metadata from Audible API (all fields)"
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
    parser.add_argument(
        "--force", action="store_true", help="Re-enrich all books (not just unenriched ones)"
    )
    parser.add_argument("--id", type=int, default=None, help="Enrich a single book by database ID")
    args = parser.parse_args()

    db = Path(args.db) if args.db else None
    enrich_from_audible(
        dry_run=args.dry_run, delay=args.delay, db_path=db, force=args.force, single_id=args.id
    )


if __name__ == "__main__":
    main()
