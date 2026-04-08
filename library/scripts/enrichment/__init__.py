"""Enrichment pipeline — multi-provider metadata enrichment for audiobooks.

Orchestrates a chain of providers: Local → Audible → Google Books → Open Library.
Each provider fills only empty fields. The chain short-circuits when all target
fields are populated.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scanner.metadata_utils import extract_topics

from scripts.enrichment.base import EnrichmentProvider
from scripts.enrichment.provider_audible import AudibleProvider
from scripts.enrichment.provider_google import GoogleBooksProvider
from scripts.enrichment.provider_local import LocalProvider
from scripts.enrichment.provider_openlibrary import OpenLibraryProvider

logger = logging.getLogger(__name__)

# Columns that providers can fill (must exist in audiobooks table)
_SCALAR_COLUMNS = {
    "asin",
    "series",
    "series_sequence",
    "subtitle",
    "language",
    "format_type",
    "runtime_length_min",
    "release_date",
    "publisher_summary",
    "rating_overall",
    "rating_performance",
    "rating_story",
    "num_ratings",
    "num_reviews",
    "audible_image_url",
    "sample_url",
    "audible_sku",
    "is_adult_product",
    "content_type",
    "isbn",
    "description",
    "published_date",
    "published_year",
    "publisher",
    "page_count",
}

# Side-table data keys (not columns on audiobooks)
_SIDE_TABLE_KEYS = {
    "categories",
    "editorial_reviews",
    "author_asins",
    "google_categories",
    "ol_subjects",
}

# Fields where the schema default should be treated as "unfilled" during merge.
# content_type defaults to 'Product' in schema, but the Audible API may return
# a more specific type (Podcast, Show, Episode, Lecture, etc.).
_DEFAULT_AS_EMPTY = {
    "content_type": "Product",
}


def _load_book(cursor: sqlite3.Cursor, book_id: int) -> dict | None:
    """Load a book row as a plain dict."""
    cursor.execute("SELECT * FROM audiobooks WHERE id = ?", (book_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def _merge_updates(current: dict, provider_result: dict) -> dict:
    """Merge provider result into accumulated updates.

    Only fills fields that are currently empty/null in the book AND
    not already filled by a prior provider.
    """
    merged = {}
    for key, value in provider_result.items():
        if key in _SIDE_TABLE_KEYS:
            # Side-table data always passes through (overwrite is fine)
            merged[key] = value
            continue
        if key not in _SCALAR_COLUMNS:
            continue
        # Only fill if book's current value is empty or matches a schema default
        current_val = current.get(key)
        default_val = _DEFAULT_AS_EMPTY.get(key)
        if (
            current_val is None
            or current_val == ""
            or current_val == 0
            or (default_val is not None and current_val == default_val)
        ):
            merged[key] = value
    return merged


def _apply_scalar_updates(
    cursor: sqlite3.Cursor,
    book_id: int,
    updates: dict,
    enrichment_source: str,
) -> int:
    """Write scalar column updates to the audiobooks table."""
    scalar = {
        k: v
        for k, v in updates.items()
        if k in _SCALAR_COLUMNS and k not in _SIDE_TABLE_KEYS
    }
    if not scalar:
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    scalar["audible_enriched_at"] = now
    scalar["enrichment_source"] = enrichment_source

    # Column names are validated against _SCALAR_COLUMNS (hardcoded allowlist)
    for col in scalar:
        if col not in _SCALAR_COLUMNS and col not in (
            "audible_enriched_at",
            "enrichment_source",
        ):
            raise ValueError(f"Invalid column name: {col}")
    set_clause = ", ".join(f"{col} = ?" for col in scalar)
    params = list(scalar.values()) + [book_id]
    cursor.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"UPDATE audiobooks SET {set_clause} WHERE id = ?",  # noqa: S608
        params,
    )
    return len(scalar) - 2  # Don't count timestamp + source as "fields"


def _apply_categories(
    cursor: sqlite3.Cursor, book_id: int, categories: list[dict]
) -> None:
    """Write categories to the audible_categories side table."""
    cursor.execute("DELETE FROM audible_categories WHERE audiobook_id = ?", (book_id,))
    for cat in categories:
        cursor.execute(
            """INSERT INTO audible_categories
               (audiobook_id, category_path, category_name, root_category, depth, audible_category_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                book_id,
                cat.get("category_path", ""),
                cat.get("category_name", ""),
                cat.get("root_category", ""),
                cat.get("depth", 0),
                cat.get("audible_category_id", ""),
            ),
        )


def _apply_genres_from_categories(
    cursor: sqlite3.Cursor, book_id: int, categories: list[dict]
) -> None:
    """Populate audiobook_genres from Audible category names.

    Extracts all category_name values from the category hierarchy and inserts
    them into the genres/audiobook_genres tables. Skips genres that already
    exist for this book. This bridges the gap between the audible_categories
    side table and the genres system that collections depend on.
    """
    # Get existing genres for this book
    cursor.execute(
        """SELECT g.name FROM genres g
           JOIN audiobook_genres ag ON g.id = ag.genre_id
           WHERE ag.audiobook_id = ?""",
        (book_id,),
    )
    existing = {row[0] for row in cursor.fetchall()}

    # Remove the generic "general" placeholder if we have real genre data
    if existing == {"general"} and categories:
        cursor.execute(
            "DELETE FROM audiobook_genres WHERE audiobook_id = ?", (book_id,)
        )
        existing = set()

    for cat in categories:
        name = cat.get("category_name", "")
        if not name or name in existing:
            continue
        # Get or create genre
        cursor.execute("SELECT id FROM genres WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            genre_id = row[0]
        else:
            cursor.execute("INSERT INTO genres (name) VALUES (?)", (name,))
            genre_id = cursor.lastrowid
        cursor.execute(
            "INSERT OR IGNORE INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
            (book_id, genre_id),
        )
        existing.add(name)


def _apply_topics_from_summary(
    cursor: sqlite3.Cursor, book_id: int, summary: str
) -> None:
    """Extract and populate topics from a book's publisher summary.

    Uses the scanner's extract_topics() to pull keywords from the description,
    then populates the topics/audiobook_topics tables. Replaces the generic
    "general" topic if real topics are found.
    """
    topics = extract_topics(summary)
    if not topics or topics == ["general"]:
        return

    # Check existing topics
    cursor.execute(
        """SELECT t.name FROM topics t
           JOIN audiobook_topics at ON t.id = at.topic_id
           WHERE at.audiobook_id = ?""",
        (book_id,),
    )
    existing = {row[0] for row in cursor.fetchall()}

    # Replace "general" placeholder with real topics
    if existing == {"general"}:
        cursor.execute(
            "DELETE FROM audiobook_topics WHERE audiobook_id = ?", (book_id,)
        )
        existing = set()

    for topic_name in topics:
        if topic_name in existing or topic_name == "general":
            continue
        cursor.execute("SELECT id FROM topics WHERE name = ?", (topic_name,))
        row = cursor.fetchone()
        if row:
            topic_id = row[0]
        else:
            cursor.execute("INSERT INTO topics (name) VALUES (?)", (topic_name,))
            topic_id = cursor.lastrowid
        cursor.execute(
            "INSERT OR IGNORE INTO audiobook_topics (audiobook_id, topic_id) VALUES (?, ?)",
            (book_id, topic_id),
        )
        existing.add(topic_name)


def _apply_editorial_reviews(
    cursor: sqlite3.Cursor, book_id: int, reviews: list[dict]
) -> None:
    """Write editorial reviews to the editorial_reviews side table."""
    cursor.execute("DELETE FROM editorial_reviews WHERE audiobook_id = ?", (book_id,))
    for review in reviews:
        cursor.execute(
            """INSERT INTO editorial_reviews (audiobook_id, review_text, source)
               VALUES (?, ?, ?)""",
            (book_id, review.get("review_text", ""), review.get("source", "")),
        )


def _default_providers(sources_dir: Path | None = None) -> list[EnrichmentProvider]:
    """Return the default provider chain."""
    return [
        LocalProvider(sources_dir=sources_dir),
        AudibleProvider(),
        GoogleBooksProvider(),
        OpenLibraryProvider(),
    ]


def enrich_book(
    book_id: int,
    db_path: Path | None = None,
    quiet: bool = False,
    sources_dir: Path | None = None,
    providers: list[EnrichmentProvider] | None = None,
) -> dict:
    """Run the enrichment chain for a single book.

    Returns a result dict compatible with enrich_single.py's format:
    {audible_enriched, isbn_enriched, fields_updated, errors, providers_used}
    """
    result = {
        "audible_enriched": False,
        "isbn_enriched": False,
        "fields_updated": 0,
        "errors": [],
        "providers_used": [],
    }

    if db_path is None:
        result["errors"].append("No database path")
        return result

    if providers is None:
        providers = _default_providers(sources_dir)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        book = _load_book(cursor, book_id)
        if not book:
            result["errors"].append(f"Book ID {book_id} not found")
            return result

        if not quiet:
            logger.info("Enriching: %s (ID %d)", book.get("title", "?"), book_id)

        all_updates: dict = {}
        winning_provider = ""

        for provider in providers:
            if not provider.can_enrich({**book, **all_updates}):
                continue

            # Pass the merged view (book + accumulated updates) to each provider
            merged_view = {
                **book,
                **{k: v for k, v in all_updates.items() if k not in _SIDE_TABLE_KEYS},
            }
            provider_data = provider.enrich(merged_view)
            if not provider_data:
                continue

            new_fields = _merge_updates(merged_view, provider_data)
            if new_fields:
                all_updates.update(new_fields)
                result["providers_used"].append(provider.name)
                if not winning_provider:
                    winning_provider = provider.name

                if provider.name == "audible":
                    result["audible_enriched"] = True
                elif provider.name in ("google_books", "openlibrary"):
                    result["isbn_enriched"] = True

            # Short-circuit: if series is populated, later fallback providers won't help
            series_val = all_updates.get("series") or book.get("series")
            if series_val and result["audible_enriched"]:
                break

        # Apply all accumulated updates
        if all_updates:
            fields = _apply_scalar_updates(
                cursor, book_id, all_updates, winning_provider or "local"
            )
            result["fields_updated"] = max(fields, 0)

            if "categories" in all_updates:
                _apply_categories(cursor, book_id, all_updates["categories"])
                _apply_genres_from_categories(
                    cursor, book_id, all_updates["categories"]
                )

            if "editorial_reviews" in all_updates:
                _apply_editorial_reviews(
                    cursor, book_id, all_updates["editorial_reviews"]
                )

            # Extract topics from publisher_summary if available
            summary = all_updates.get("publisher_summary") or book.get(
                "publisher_summary", ""
            )
            if summary:
                _apply_topics_from_summary(cursor, book_id, summary)

            conn.commit()

            if not quiet:
                logger.info(
                    "  %d fields updated by %s",
                    result["fields_updated"],
                    ", ".join(result["providers_used"]),
                )
    except Exception as e:
        result["errors"].append(str(e))
        logger.exception("Enrichment failed for book %d", book_id)
    finally:
        conn.close()

    return result
