# Multi-Author/Narrator Sorting Design

**Date**: 2026-03-13
**Branch**: `sort_fix`
**Status**: Approved
**Breaking**: Yes (backend schema change, API response shape change)

## Problem

Sorting by author or narrator fails when a book has multiple authors or narrators. The current system stores a flat text string ("Stephen King, Peter Straub") and extracts only the first name into `author_last_name`/`author_first_name` sort columns. Co-authors and co-narrators are silently dropped from sorting. This has been a known issue since the project's inception.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Where should a multi-author book appear when sorted by author? | Under each author (many-to-many) | Library catalog behavior — users expect to find a book under any of its authors |
| Narrators treated the same? | Yes, symmetrical | Same problem, same solution |
| UI presentation | Collapsible author/narrator group headers | Clearest mental model; authors as sections, books nested under them |
| Group header content | Individual author name only | Card itself carries full metadata |
| Database approach | Full normalization (new tables + junctions) | Fix the data model properly rather than work around it |
| Metadata extraction priority | Structured tags first, delimiter parsing fallback | Let metadata do the work when it can |
| Existing data migration | Migration script + background re-scan | Immediate population from DB, then improve accuracy from file metadata |
| Genre/subgenre | Future phase | Current display works well; prove the pattern on authors/narrators first |

## Schema

### New Tables

```sql
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS narrators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id INTEGER NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id)
);

CREATE TABLE IF NOT EXISTS book_narrators (
    book_id INTEGER NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    narrator_id INTEGER NOT NULL REFERENCES narrators(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, narrator_id)
);

CREATE INDEX IF NOT EXISTS idx_authors_sort ON authors(sort_name);
CREATE INDEX IF NOT EXISTS idx_narrators_sort ON narrators(sort_name);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_book_narrators_narrator ON book_narrators(narrator_id);
```

### Foreign Key Enforcement

All database connections MUST set `PRAGMA foreign_keys = ON` before any operations on the new tables. Without this, SQLite silently ignores `REFERENCES` and `ON DELETE CASCADE`. This pragma must be set per-connection (it does not persist). The existing schema has the same latent gap — this migration is the occasion to fix it globally.

### Existing Columns Retained

The `author`, `narrator`, `author_first_name`, `author_last_name`, `narrator_first_name`, `narrator_last_name` columns on the `audiobooks` table remain as denormalized display caches. The new junction tables are the source of truth for sorting and grouping.

### Sync Direction: Normalized → Flat

When admin correction endpoints modify the normalized tables (merge, rename, reassign), the flat `author`/`narrator` columns on the `audiobooks` table MUST be regenerated from the junction tables. This triggers the existing FTS update triggers on the `audiobooks` table, keeping full-text search in sync. The flat columns are always derived from the normalized tables, never the reverse.

### `audiobooks_full` View

The existing `audiobooks_full` view will NOT be modified in this phase. It continues to use the flat `author`/`narrator` columns for search and filtering. The grouped endpoint uses direct JOINs against the new tables instead of going through the view. If genre/subgenre normalization happens in a future phase, the view may be redesigned then.

## Name Parsing & Metadata Extraction

### Three-Tier Strategy (priority order)

**Tier 1 — Structured metadata tags**: When ffprobe returns multiple separate tag entries for artist/composer/narrator, each becomes its own record. Highest fidelity.

**Tier 2 — Delimiter-based splitting**: Single string with multiple names. Split on recognized delimiters in order:

1. Semicolons (`;`)
2. `" and "` (with spaces, avoids matching "Anderson", "Rand")
3. `" & "` (with spaces)
4. Commas — with "Last, First" vs "Author1, Author2" heuristic

**Tier 3 — Single name fallback**: No delimiters found. Parse into `sort_name` using prefix logic (de, van, von, le, etc.).

### Comma Disambiguation Heuristic

- `"King, Stephen"` — two single-word tokens → "Last, First" → one author
- `"Stephen King, Peter Straub"` — multi-word tokens → multiple authors
- `"King, Stephen, Straub, Peter"` — alternating single-word pattern where ALL tokens are single words → pairs of "Last, First"
- The alternating-single-word heuristic ONLY applies when every token is a single word. If any token contains spaces, hyphens, or prefixes (e.g., `"de Saint-Exupery, Antoine, Straub, Peter"`), fall back to conservative single-author interpretation and flag for manual review
- When ambiguous → treat as single author (conservative), flag for manual review

### Group Name Redirection

Known performance/production group names ("Full Cast", "BBC Radio", etc.) are **always narrators**, never authors. If detected in author metadata, they are stripped from the author list and inserted into the narrator list. This is a one-directional correction — narrator names never redirect to author.

### Sort Name Generation

Each individual name produces a `sort_name` in "Last, First" form. Group names ("Full Cast", "BBC Radio") are stored as-is with no first/last split.

## API Design

### Modified Flat Endpoint: `GET /api/audiobooks`

Response enriched with structured arrays alongside existing flat strings:

```json
{
  "id": 42,
  "title": "The Talisman",
  "author": "Stephen King, Peter Straub",
  "authors": [
    {"id": 1, "name": "Stephen King", "sort_name": "King, Stephen", "position": 0},
    {"id": 2, "name": "Peter Straub", "sort_name": "Straub, Peter", "position": 1}
  ],
  "narrator": "Frank Muller",
  "narrators": [
    {"id": 5, "name": "Frank Muller", "sort_name": "Muller, Frank", "position": 0}
  ]
}
```

### New Grouped Endpoint: `GET /api/audiobooks/grouped?by=author|narrator`

Returns pre-grouped results sorted by `sort_name`:

```json
{
  "groups": [
    {
      "key": {"id": 1, "name": "Stephen King", "sort_name": "King, Stephen"},
      "books": [
        {"id": 42, "title": "The Talisman", "author": "Stephen King, Peter Straub"},
        {"id": 13, "title": "It", "author": "Stephen King"}
      ]
    },
    {
      "key": {"id": 2, "name": "Peter Straub", "sort_name": "Straub, Peter"},
      "books": [
        {"id": 42, "title": "The Talisman", "author": "Stephen King, Peter Straub"}
      ]
    }
  ],
  "total_groups": 2,
  "total_books": 2
}
```

`total_books` is the deduplicated count. `?by=narrator` works identically.

### Grouped Endpoint Pagination

The grouped endpoint returns all groups in a single response (no pagination). At the current scale (~500 authors, ~800 books), the full response is approximately 200-400 KB — well within acceptable payload size. If the library grows to the point where this becomes a performance concern (thousands of authors), pagination by group can be added as a future enhancement. Books within each group are not independently paginated — each group contains all its books.

### Back-Office Correction Endpoints

**Rename**: `PUT /api/admin/authors/{id}`

```json
// Request
{"name": "Stephen King", "sort_name": "King, Stephen"}
// Response: 200 with updated author object
```

**Merge duplicates**: `POST /api/admin/authors/merge`

```json
// Request — merge source(s) into target, reassign all books, delete sources
{"source_ids": [3, 7], "target_id": 1}
// Response: 200 with target author object and count of books reassigned
```

**Reassign book authors**: `PUT /api/admin/books/{id}/authors`

```json
// Request — full replacement of the book's author list
{"author_ids": [1, 2], "positions": [0, 1]}
// Response: 200 with updated book object including authors array
```

Same set of endpoints for narrators (`/api/admin/narrators/{id}`, `/api/admin/narrators/merge`, `/api/admin/books/{id}/narrators`).

All admin endpoints regenerate the flat `author`/`narrator` columns on affected `audiobooks` rows after modification (see Sync Direction above).

### Existing Sort Parameters

The flat endpoint's `?sort=author_last` and `?sort=narrator_last` parameters continue to work, sorting by the denormalized `author_last_name`/`narrator_last_name` columns. These provide single-author sorting as before. For proper multi-author sorting, the frontend uses the grouped endpoint instead. The flat sort parameters are not deprecated — they serve the flat view where grouping isn't needed.

## Frontend / UI

### Grouped View (author/narrator sorts)

- Library view switches from flat card grid to collapsible grouped sections
- Group headers labeled with individual name in "Last, First" format
- Clicking a header collapses/expands the group
- Book cards displayed identically to today; card shows full author string
- A book appears under every author/narrator it belongs to
- Library header shows **deduplicated** book count; group headers show per-group count

### Flat View (all other sorts)

No change. Title, duration, and other sorts use the existing flat grid.

### Data Flow

- User selects author/narrator sort → frontend calls `/api/audiobooks/grouped?by=author` (or `narrator`)
- User selects any other sort → frontend calls existing `/api/audiobooks` flat endpoint
- JS rendering toggles between flat and grouped mode based on active endpoint

### No Changes To

Genre/subgenre display, search behavior, player, or any other existing UI.

## Migration Strategy

### Phase 1 — Schema Migration

Runs once during upgrade. Creates the four new tables and indices. Non-destructive to existing `audiobooks` table.

### Phase 2 — Data Migration

Python script reads every `audiobooks` row, parses flat `author`/`narrator` strings using Tier 2/3 parsing, populates new tables. Deduplicates as it goes. Logs ambiguous parses to a migration report file. App is fully functional after this phase.

**Books with NULL/empty authors or narrators**: If a book's `author` or `narrator` column is NULL or empty, it gets zero junction rows for that relationship. In the grouped view, these books appear in a synthetic "Unknown Author" or "Unknown Narrator" group sorted to the end of the list. The migration report flags these for manual review.

### Phase 3 — Background Re-Scan

Separate process re-reads audio files with ffprobe for structured metadata (Tier 1). Where structured tags provide better data than Phase 2 parsing, updates the junction tables. Runs asynchronously. Triggered manually or via back-office "re-scan library" button.

### Rollback

Drop the four new tables to revert. Existing flat columns are untouched. Grouped API endpoint returns 404; frontend falls back to flat sorting.

### upgrade.sh Integration

Detects whether new tables exist. If not, runs Phases 1 and 2 automatically. Phase 3 is manual/back-office triggered.

## Testing Strategy

### Unit Tests (dev machine)

- Name parser: all delimiter patterns, "Last, First" vs multi-author disambiguation, group name redirection to narrators, prefix handling, mononyms, edge cases
- Sort name generation: verify "Last, First" output for various name formats
- Migration script: run against dev database copy, verify junction table population and deduplication

### Integration Tests (<test-vm-name> VM)

- Schema migration: fresh install and upgrade-from-pre-migration both work
- API flat endpoint: `authors`/`narrators` arrays present
- API grouped endpoint: correct grouping, sort order, deduplicated count, multi-author books under each group
- Back-office correction endpoints: merge, rename, reassign
- Rollback: drop tables, verify graceful fallback

### Frontend Tests (VM, Playwright)

- Grouped view renders for author/narrator sorts
- Collapsible group headers work
- Multi-author books appear under each relevant group
- Deduplicated count in library header
- Sort mode transitions (grouped ↔ flat) work correctly
- No regressions on genre/subgenre display

### Re-Scan Test

Seed test library with files containing structured multi-artist tags. Verify Tier 1 extraction produces better results than Tier 2 parsing.

### Parallel Testing

Clone <qa-vm-name> for parallel test streams against a production-sized dataset (801 books, 492 authors). Validates data-dependent parsing on real metadata while <test-vm-name> handles fresh-install and schema-migration scenarios.

## Future Work

- Apply the same normalized pattern to genres and subgenres (pending success of author/narrator implementation)
