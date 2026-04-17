# Enrichment Pipeline Redesign

**Date**: 2026-04-07
**Status**: Approved
**Scope**: Fix ASIN extraction, build multi-source enrichment chain, backfill existing library, automate enrichment for all new books

## Problem

Three gaps in the metadata enrichment pipeline leave most books without series info, ratings, or categories:

1. **ASIN extraction broken for newer Audible format** — Scanner only checks `chapters.json` for ASINs. Newer `.aaxc` + `.voucher` pairs store the ASIN in the voucher file and source filename, but the scanner ignores both. ~311 of 1860 books lack `chapters.json` and therefore have `asin=NULL`.

2. **Enrichment never auto-triggers reliably** — `add_new_audiobooks.py` calls `enrich_book()` post-insert, but it silently skips when ASIN is null. No systemd timer runs bulk enrichment. Result: `audible_enriched_at` is NULL for 100% of books.

3. **No enrichment path for non-Audible content** — The system assumes all books come from Audible. Future content from LibriVox, Google Play, Internet Archive, Project Gutenberg, Chirp, and other sources has no ASIN and no enrichment path.

## Design

### Architecture: Tiered Provider Chain

Enrichment runs as a provider chain. Each provider fills in fields that are still empty — later providers never overwrite earlier ones. The chain short-circuits: if all target fields are populated after a provider runs, remaining providers are skipped.

```text
Book inserted → Local Extraction → Audible API → Google Books → Open Library → Done
                (always runs)      (if ASIN)      (if series    (if series
                                                   still empty)  still empty)
```

### Component 1: ASIN Extraction Fix (`metadata_utils.py`)

Expand `extract_asin_from_chapters_json()` into `extract_asin()` that checks three sources in order:

1. **chapters.json** — existing behavior, `content_metadata.content_reference.asin`
2. **Voucher file** — find `.voucher` in Sources dir matching the book's title/author, parse `content_license.asin`
3. **Source filename** — match pattern `{ASIN}_Title-*.aaxc` in Sources dir, extract leading ASIN (10-char alphanumeric starting with `B`)

The voucher and filename lookups need access to the Sources directory path, which comes from config (`AUDIOBOOKS_SOURCES`). Pass it as an optional parameter — when not provided (e.g., in tests), only chapters.json is checked.

**Source-to-library matching**: The scanner processes files in the Library directory. To find the corresponding source file in Sources, match by normalized title (strip punctuation, case-insensitive) against source filenames. The source filename pattern is `{ASIN}_{Title}-{format}.aaxc`.

### Component 2: Enrichment Providers (`library/scripts/enrichment/`)

New package with a provider interface and four implementations:

```text
library/scripts/enrichment/
├── __init__.py          # Chain orchestrator
├── base.py              # Provider base class
├── provider_local.py    # Local tag/file extraction
├── provider_audible.py  # Audible catalog API
├── provider_google.py   # Google Books API
└── provider_openlibrary.py  # Open Library API
```

#### Provider Base Class

```python
class EnrichmentProvider:
    name: str  # e.g., "audible", "google_books"

    def can_enrich(self, book: dict) -> bool:
        """Return True if this provider might have data for this book."""
        ...

    def enrich(self, book: dict) -> dict:
        """Return dict of field_name → value for fields this provider can fill."""
        ...
```

#### Provider: Local (`provider_local.py`)

- Always runs (no external calls)
- Extracts ASIN from voucher/filename/chapters.json (Component 1)
- Extracts series from embedded audio tags (`series`, `series-part`)
- Title-based series parsing as last resort — regex patterns from `populate_series_from_audible.py`: `"Title: Series Name, Book N"`, `"Title (Series Name Book N)"`, `"Title: A Series Name Novel"`
- Extracts any other metadata from tags that the scanner missed

#### Provider: Audible (`provider_audible.py`)

- Runs if book has an ASIN
- Queries `https://api.audible.com/1.0/catalog/products/{ASIN}` with all response groups
- Fills: series, series_sequence, subtitle, language, ratings (overall/performance/story), num_ratings, categories, editorial_reviews, audible_image_url, sample_url
- Multi-series resolution: prefer series with most members in library, then shortest title
- Rate limiting: 0.3s between calls, exponential backoff on 429
- Sets `audible_enriched_at` timestamp on success
- Refactored from existing code in `enrich_single.py` (lines 40-175)

#### Provider: Google Books (`provider_google.py`)

- Runs if series is still empty after Audible
- Searches by title + author: `https://www.googleapis.com/books/v1/volumes?q=intitle:{title}+inauthor:{author}`
- Fills: series (from `volumeInfo.seriesInfo` or title parsing), isbn, categories, description, publisher, published_date, page_count, thumbnail
- Rate limiting: 0.5s between calls
- Refactored from existing code in `enrich_single.py` (lines 182-204)

#### Provider: Open Library (`provider_openlibrary.py`)

- Runs if series is still empty after Google Books
- Searches: `https://openlibrary.org/search.json?title={title}&author={author}`
- Fills: series (from `series` field in search results), subjects, isbn, first_publish_year
- Rate limiting: 1.0s between calls (Open Library is rate-sensitive)
- Refactored from existing code in `enrich_single.py` (lines 207+)

### Component 3: Enrichment Orchestrator (`library/scripts/enrichment/__init__.py`)

```python
def enrich_book(book_id: int, db_path: Path, providers: list[EnrichmentProvider] | None = None) -> dict:
    """Run enrichment chain for a single book. Returns dict of fields updated."""
```

- Loads book from DB
- Runs each provider in order
- Merges results: only fills fields that are currently empty/null
- Writes updates to DB in a single UPDATE
- Returns summary of what was filled and by which provider
- Replaces the current `enrich_single.py:enrich_book()` function (same signature for backward compatibility)

### Component 4: Backfill Script (`library/scripts/backfill_enrichment.py`)

One-time script to fix the existing library:

**Phase 1 — ASIN Recovery** (no API calls):

- Scan Sources directory for `.voucher` files
- Extract ASIN from each voucher's JSON and filename
- Match to Library books by normalized title
- UPDATE books SET asin = ? WHERE asin IS NULL OR asin = ''
- Report: "Recovered N ASINs from voucher files"

**Phase 2 — Enrichment Chain**:

- Query all books where `audible_enriched_at IS NULL`
- Run enrichment chain on each
- Progress reporting: "Enriched N/M books (series: X, ratings: Y, skipped: Z)"
- Rate limiting respected across all providers
- Resumable: skips books already enriched (checks `audible_enriched_at`)

**CLI**:

```bash
# Full backfill (ASIN recovery + enrichment)
python3 library/scripts/backfill_enrichment.py --db /path/to/db

# ASIN recovery only (no API calls)
python3 library/scripts/backfill_enrichment.py --db /path/to/db --asin-only

# Dry run
python3 library/scripts/backfill_enrichment.py --db /path/to/db --dry-run

# Limit to N books (for testing)
python3 library/scripts/backfill_enrichment.py --db /path/to/db --limit 10
```

### Component 5: Automatic Enrichment Trigger

**Post-insert hook** (existing path, fixed):

- `add_new_audiobooks.py:_run_post_insert_hooks()` already calls `enrich_book()`
- The new orchestrator replaces the old function with the same signature
- Now works even without ASIN (Google Books/Open Library fallback)

**Systemd timer** (new, catch-up for missed books):

```ini
# audiobook-enrichment.timer
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

# audiobook-enrichment.service
[Service]
Type=oneshot
ExecStart=/opt/audiobooks/venv/bin/python /opt/audiobooks/library/scripts/backfill_enrichment.py
```

Runs nightly at 3 AM. The backfill script is idempotent — it only processes books where `audible_enriched_at IS NULL`, so it's safe to run repeatedly.

### Component 6: Schema Addition

One new column to track which provider enriched the book:

```sql
ALTER TABLE audiobooks ADD COLUMN enrichment_source TEXT;
-- Values: "audible", "google_books", "openlibrary", "local", NULL (not enriched)
```

This tells us WHERE the series/metadata came from, useful for debugging and for knowing whether re-enrichment with a better provider might improve the data.

## Files Modified

| File | Change |
|------|--------|
| `library/scanner/metadata_utils.py` | Replace `extract_asin_from_chapters_json()` with `extract_asin()` checking 3 sources |
| `library/scanner/add_new_audiobooks.py` | Import new orchestrator instead of old `enrich_single.py` |
| `library/scripts/enrichment/__init__.py` | New — orchestrator |
| `library/scripts/enrichment/base.py` | New — provider base class |
| `library/scripts/enrichment/provider_local.py` | New — local file extraction |
| `library/scripts/enrichment/provider_audible.py` | New — refactored from `enrich_single.py` |
| `library/scripts/enrichment/provider_google.py` | New — refactored from `enrich_single.py` |
| `library/scripts/enrichment/provider_openlibrary.py` | New — refactored from `enrich_single.py` |
| `library/scripts/backfill_enrichment.py` | New — one-time + periodic backfill |
| `library/scripts/enrich_single.py` | Becomes thin wrapper calling new orchestrator (backward compat) |
| `library/scripts/populate_series_from_audible.py` | Deprecated — functionality absorbed into provider chain |
| `library/backend/schema.sql` | Add `enrichment_source TEXT` column |
| `systemd/audiobook-enrichment.timer` | New — nightly catch-up timer |
| `systemd/audiobook-enrichment.service` | New — oneshot service for timer |
| `install.sh` | Enable new timer, run schema migration |

## Files NOT Modified

- `convert-audiobooks-opus-parallel` — converter stays as-is. ASIN extraction moves to scanner time, not conversion time. The converter's job is audio transcoding, not metadata management.
- `library/web-v2/js/library.js` — card rendering already handles series display correctly. No UI changes needed.
- `library/web-v2/css/library.css` — `.book-series` styling already exists and works.

## Error Handling

- Provider failures are non-fatal — log warning, continue to next provider
- API timeouts: 10s per request, 3 retries with exponential backoff
- Rate limit (429): wait 30s, retry once, then skip
- Database errors: rollback transaction, re-raise (these are real failures)
- Missing Sources directory: skip voucher/filename ASIN extraction, log info

## Testing

- Unit tests for each provider with mocked API responses
- Unit test for ASIN extraction from voucher files (use test fixtures)
- Unit test for source filename ASIN parsing
- Integration test: full chain with a book that has no ASIN (verifies Google Books/Open Library fallback)
- All tests run locally (no VM needed) — providers are pure functions with injectable HTTP clients

## Migration Path

1. Deploy code changes (new enrichment package, updated scanner)
2. Run schema migration (add `enrichment_source` column)
3. Run backfill script (Phase 1: ASIN recovery, Phase 2: enrichment chain)
4. Enable systemd timer for nightly catch-up
5. Verify: `SELECT COUNT(*) FROM audiobooks WHERE series != '' AND series IS NOT NULL` should increase significantly
