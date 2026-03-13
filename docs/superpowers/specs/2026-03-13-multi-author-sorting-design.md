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
CREATE TABLE authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE narrators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE book_authors (
    book_id INTEGER NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id)
);

CREATE TABLE book_narrators (
    book_id INTEGER NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    narrator_id INTEGER NOT NULL REFERENCES narrators(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, narrator_id)
);

CREATE INDEX idx_authors_sort ON authors(sort_name);
CREATE INDEX idx_narrators_sort ON narrators(sort_name);
CREATE INDEX idx_book_authors_author ON book_authors(author_id);
CREATE INDEX idx_book_narrators_narrator ON book_narrators(narrator_id);
```

### Existing Columns Retained

The `author`, `narrator`, `author_first_name`, `author_last_name`, `narrator_first_name`, `narrator_last_name` columns on the `audiobooks` table remain as denormalized display caches. The new junction tables are the source of truth for sorting and grouping.

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
- `"King, Stephen, Straub, Peter"` — alternating single-word pattern → pairs of "Last, First"
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

### Back-Office Correction Endpoints

- `PUT /api/admin/authors/{id}` — rename, update sort_name
- `POST /api/admin/authors/merge` — merge duplicate author records
- `POST /api/admin/books/{id}/authors` — reassign authors to a book
- Same set of endpoints for narrators

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

### Integration Tests (test-audiobook-cachyos VM)

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

Clone qa-audiobooks-cachyos for parallel test streams against a production-sized dataset (801 books, 492 authors). Validates data-dependent parsing on real metadata while test-audiobook-cachyos handles fresh-install and schema-migration scenarios.

## Future Work

- Apply the same normalized pattern to genres and subgenres (pending success of author/narrator implementation)
