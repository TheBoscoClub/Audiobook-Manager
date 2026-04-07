# Multi-Author/Narrator Sorting Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize author/narrator data into many-to-many relationships so books with multiple authors/narrators appear under each person in grouped sort views.

**Architecture:** New `authors`, `narrators`, `book_authors`, `book_narrators` tables following the existing junction pattern (`audiobook_genres`). A name parser module handles delimiter splitting and sort name generation. The flat API endpoint is enriched with author/narrator arrays; a new grouped endpoint returns pre-grouped results for the frontend's collapsible author/narrator views.

**Tech Stack:** Python 3.14, Flask, SQLite3, vanilla JavaScript

**Spec:** `docs/superpowers/specs/2026-03-13-multi-author-sorting-design.md`

**Branch:** `sort_fix` (main is frozen)

**BTRFS snapshots:** Create a snapshot after each Task completes.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `library/backend/name_parser.py` | Name parsing: delimiter splitting, sort name generation, group name detection, comma disambiguation |
| `library/backend/migrations/011_multi_author_narrator.sql` | Schema migration DDL for new tables + indices |
| `library/backend/migrations/migrate_to_normalized_authors.py` | Phase 2 data migration script |
| `library/backend/api_modular/grouped.py` | New grouped endpoint blueprint (`/api/audiobooks/grouped`) |
| `library/tests/test_name_parser.py` | Unit tests for name parser |
| `library/tests/test_grouped_api.py` | Integration tests for grouped endpoint |
| `library/tests/test_migration_authors.py` | Tests for data migration correctness |
| `library/backend/api_modular/admin_authors.py` | Admin correction endpoints (rename, merge, reassign) for authors and narrators |
| `library/backend/migrations/__init__.py` | Empty package init (enables test imports) |
| `library/tests/conftest_grouped.py` | Test fixtures for grouped/admin API tests (client, populated_db, multi_author_db) |

### Modified Files

| File | Changes |
|------|---------|
| `library/backend/api_modular/core.py:27-33` | Add `PRAGMA foreign_keys = ON` to `get_db()` |
| `library/backend/schema.sql` | Append new table DDL after line 96 (audiobook_topics) |
| `library/backend/api_modular/__init__.py` | Register `grouped_bp` blueprint |
| `library/backend/api_modular/audiobooks.py:267-314` | Add batch author/narrator array loading to response |
| `library/web-v2/js/library.js:1484-1496` | Add grouped rendering mode alongside flat `renderBooks()` |
| `library/web-v2/css/library.css` (or inline) | CSS for group headers, collapse states |

**Deferred to Phase 3 follow-up (not in this plan):**

- `library/scanner/metadata_utils.py` — Tier 1 structured tag extraction (re-scan)
- `library/scripts/populate_sort_fields.py` — junction table population (superseded by migration script for existing data; scanner will handle new data once Phase 3 is implemented)

---

## Parallelization Notes

- **Tasks 1 + 2** can run in parallel (no code dependencies)
- **Tasks 4 + 5 + 7** can run in parallel once Task 3 is complete
- **Tasks 9 + 10** are sequential (VM-dependent)
- **Task 6** (frontend) can run in parallel with Tasks 4-5 if stubbed API data is used

## Chunk 1: Foundation — Name Parser and Schema

### Task 1: Name Parser Module

**Files:**

- Create: `library/backend/name_parser.py`
- Create: `library/tests/test_name_parser.py`

- [ ] **Step 1: Write failing tests for single-name parsing**

```python
# library/tests/test_name_parser.py
"""Tests for multi-author/narrator name parser."""

import pytest
from library.backend.name_parser import parse_names, generate_sort_name

class TestGenerateSortName:
    """Test sort name generation from individual names."""

    def test_simple_two_part(self):
        assert generate_sort_name("Stephen King") == "King, Stephen"

    def test_initials(self):
        assert generate_sort_name("J.R.R. Tolkien") == "Tolkien, J.R.R."

    def test_prefix_le(self):
        assert generate_sort_name("John le Carré") == "le Carré, John"

    def test_prefix_van(self):
        assert generate_sort_name("Ludwig van Beethoven") == "van Beethoven, Ludwig"

    def test_prefix_de(self):
        assert generate_sort_name("Antoine de Saint-Exupéry") == "de Saint-Exupéry, Antoine"

    def test_single_name(self):
        assert generate_sort_name("Plato") == "Plato"

    def test_three_part_name(self):
        assert generate_sort_name("Arthur Conan Doyle") == "Doyle, Arthur Conan"

    def test_group_name_full_cast(self):
        assert generate_sort_name("Full Cast") == "Full Cast"

    def test_group_name_bbc_radio(self):
        assert generate_sort_name("BBC Radio") == "BBC Radio"

    def test_role_suffix_stripped(self):
        assert generate_sort_name("Neil Gaiman (editor)") == "Gaiman, Neil"

    def test_dash_role_stripped(self):
        assert generate_sort_name("Stephen Fry - introductions") == "Fry, Stephen"

    def test_none_returns_empty(self):
        assert generate_sort_name(None) == ""

    def test_unknown_author(self):
        assert generate_sort_name("Unknown Author") == ""

    def test_unknown_narrator(self):
        assert generate_sort_name("Unknown Narrator") == ""


class TestParseNames:
    """Test multi-name parsing with delimiter detection."""

    def test_single_author(self):
        assert parse_names("Stephen King") == ["Stephen King"]

    def test_semicolon_separated(self):
        assert parse_names("Stephen King; Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_and_separated(self):
        assert parse_names("Stephen King and Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_ampersand_separated(self):
        assert parse_names("Stephen King & Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_comma_multiple_authors(self):
        # Multi-word names on each side = multiple authors
        assert parse_names("Stephen King, Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_comma_last_first_format(self):
        # Single word on each side = "Last, First"
        assert parse_names("King, Stephen") == ["Stephen King"]

    def test_comma_last_first_with_prefix(self):
        # "de Saint-Exupéry, Antoine" has multi-word/hyphenated last name
        # Conservative: treat as single author in Last, First format
        result = parse_names("de Saint-Exupéry, Antoine")
        assert result == ["Antoine de Saint-Exupéry"]

    def test_three_authors_semicolon(self):
        result = parse_names("Author One; Author Two; Author Three")
        assert result == ["Author One", "Author Two", "Author Three"]

    def test_strips_whitespace(self):
        assert parse_names("  Stephen King ;  Peter Straub  ") == ["Stephen King", "Peter Straub"]

    def test_empty_returns_empty_list(self):
        assert parse_names("") == []
        assert parse_names(None) == []

    def test_group_name_in_author_context_flagged(self):
        """Group names should be detectable for redirection to narrators."""
        from library.backend.name_parser import is_group_name
        assert is_group_name("Full Cast") is True
        assert is_group_name("BBC Radio") is True
        assert is_group_name("Stephen King") is False

    def test_alternating_single_word_pairs(self):
        # "King, Stephen, Straub, Peter" - all single words = Last,First pairs
        result = parse_names("King, Stephen, Straub, Peter")
        assert result == ["Stephen King", "Peter Straub"]

    def test_mixed_word_count_conservative(self):
        # Not all single words - conservative single author
        result = parse_names("de Saint-Exupéry, Antoine, Straub, Peter")
        # Ambiguous - should treat conservatively
        assert len(result) >= 1  # At minimum, don't crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_name_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.backend.name_parser'`

- [ ] **Step 3: Implement name_parser.py**

```python
# library/backend/name_parser.py
"""
Multi-author/narrator name parser.

Parses delimited name strings into individual names and generates
sort keys in "Last, First" format. Handles three tiers:
- Tier 1: Structured metadata (multiple separate tags) — handled by caller
- Tier 2: Delimiter-based splitting (this module)
- Tier 3: Single name fallback (this module)
"""

import re

# Known performance/production group names — always narrators, never authors.
# If detected in author metadata, caller should redirect to narrator list.
GROUP_NAMES = frozenset({
    "full cast",
    "bbc radio",
    "bbc radio 4",
    "bbc radio drama",
    "various authors",
    "various narrators",
    "various",
    "audiobook",
    "unknown author",
    "unknown narrator",
})

# Last name prefixes that should stay attached to the surname
LAST_NAME_PREFIXES = frozenset({
    "le", "de", "la", "van", "von", "der", "den", "del", "da", "di", "du",
    "el", "al", "bin", "ibn", "mac", "mc", "o'",
})

# Names to treat as empty/unknown
EMPTY_NAMES = frozenset({
    "unknown author",
    "unknown narrator",
    "audiobook",
    "",
})


def is_group_name(name: str) -> bool:
    """Check if a name is a known group/ensemble name."""
    if not name:
        return False
    return name.strip().lower() in GROUP_NAMES


def generate_sort_name(name: str | None) -> str:
    """Generate a 'Last, First' sort key from a single person's name.

    Returns:
        Sort name string. Empty string for None/unknown names.
        Group names returned as-is (no first/last split).
    """
    if not name or name.strip().lower() in EMPTY_NAMES:
        return ""

    clean = name.strip()

    # Strip role suffixes: "(editor)", "(translator)", etc.
    clean = re.sub(r"\s*\([^)]*\)\s*$", "", clean).strip()

    # Strip "Author - role" format
    if " - " in clean:
        clean = clean.split(" - ")[0].strip()

    if not clean:
        return ""

    # Group names: return as-is
    if is_group_name(clean):
        return clean

    words = clean.split()

    if len(words) == 1:
        return words[0]

    # Determine where last name starts (handle prefixes)
    last_start = len(words) - 1
    if len(words) > 2 and words[-2].lower().rstrip("'") in LAST_NAME_PREFIXES:
        last_start = len(words) - 2

    first_parts = words[:last_start]
    last_parts = words[last_start:]

    last_name = " ".join(last_parts)
    if first_parts:
        first_name = " ".join(first_parts)
        return f"{last_name}, {first_name}"
    return last_name


def parse_names(raw: str | None) -> list[str]:
    """Parse a potentially multi-name string into individual names.

    Splitting priority:
    1. Semicolons (;)
    2. " and " (with spaces)
    3. " & " (with spaces)
    4. Commas — with Last,First vs Author1,Author2 heuristic

    Returns:
        List of individual name strings, stripped and cleaned.
        Empty list for None/empty input.
    """
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    # Tier 2a: Semicolons — least ambiguous
    if ";" in text:
        return _clean_parts(text.split(";"))

    # Tier 2b: " and " — with spaces to avoid matching "Anderson"
    if " and " in text.lower():
        # Split on " and " case-insensitively
        parts = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
        if len(parts) > 1:
            return _clean_parts(parts)

    # Tier 2c: " & " — with spaces
    if " & " in text:
        return _clean_parts(text.split(" & "))

    # Tier 2d: Commas — need disambiguation
    if "," in text:
        return _parse_comma_separated(text)

    # Tier 3: Single name
    return [text.strip()]


def _clean_parts(parts: list[str]) -> list[str]:
    """Strip whitespace and role suffixes, filter empties."""
    result = []
    for p in parts:
        clean = re.sub(r"\s*\([^)]*\)\s*$", "", p.strip()).strip()
        if clean:
            result.append(clean)
    return result


def _parse_comma_separated(text: str) -> list[str]:
    """Handle comma-separated names with Last,First disambiguation.

    Heuristic:
    - Two tokens, each single word: "King, Stephen" → Last, First → one name
    - Multi-word tokens: "Stephen King, Peter Straub" → multiple authors
    - Alternating single words (all tokens single word): pairs of Last, First
    - If any token has spaces/hyphens: conservative single-author, flag for review
    """
    parts = [p.strip() for p in text.split(",") if p.strip()]

    if len(parts) == 2:
        # Two parts: is it "Last, First" or "Author1, Author2"?
        words_a = parts[0].split()
        words_b = parts[1].split()
        if len(words_a) == 1 and len(words_b) == 1:
            # "King, Stephen" → single author in Last, First format
            return [f"{parts[1]} {parts[0]}"]
        else:
            # "Stephen King, Peter Straub" → two authors
            return _clean_parts(parts)

    if len(parts) > 2:
        # Check if ALL parts are single words → alternating Last, First pairs
        all_single = all(len(p.split()) == 1 and "-" not in p for p in parts)
        if all_single and len(parts) % 2 == 0:
            # Pair them up: Last1, First1, Last2, First2
            names = []
            for i in range(0, len(parts), 2):
                names.append(f"{parts[i + 1]} {parts[i]}")
            return names

        # Not all single words — treat as multiple authors separated by commas
        # But first check if first part looks like "Last, First" (2 parts, single words)
        # This handles "King, Stephen, Straub, Peter" edge case already covered above
        return _clean_parts(parts)

    return [text.strip()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_name_parser.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run linters**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && ruff check library/backend/name_parser.py library/tests/test_name_parser.py && ruff format --check library/backend/name_parser.py library/tests/test_name_parser.py`
Expected: No issues

- [ ] **Step 6: Commit**

```bash
git add library/backend/name_parser.py library/tests/test_name_parser.py
git commit -m "feat: add multi-name parser with delimiter splitting and sort key generation"
```

- [ ] **Step 7: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task1-name-parser
```

---

### Task 2: Schema Migration

**Files:**

- Create: `library/backend/migrations/011_multi_author_narrator.sql`
- Modify: `library/backend/schema.sql` (append after line 96)
- Modify: `library/backend/api_modular/core.py:27-33` (add foreign keys pragma)

- [ ] **Step 0: Create migrations **init**.py for test imports**

```bash
touch library/backend/migrations/__init__.py
```

- [ ] **Step 1: Write the migration SQL file**

```sql
-- library/backend/migrations/011_multi_author_narrator.sql
-- Migration: Add normalized author/narrator tables with many-to-many junctions
-- Non-destructive: existing audiobooks columns are untouched

PRAGMA foreign_keys = ON;

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
    book_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id),
    FOREIGN KEY (book_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS book_narrators (
    book_id INTEGER NOT NULL,
    narrator_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, narrator_id),
    FOREIGN KEY (book_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (narrator_id) REFERENCES narrators(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_authors_sort ON authors(sort_name);
CREATE INDEX IF NOT EXISTS idx_narrators_sort ON narrators(sort_name);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_book_narrators_narrator ON book_narrators(narrator_id);
```

- [ ] **Step 2: Append same DDL to schema.sql after audiobook_topics table (line 96)**

Insert after the `audiobook_topics` table definition, before the FTS virtual table. Use the same `FOREIGN KEY` syntax style as existing junction tables.

- [ ] **Step 3: Add PRAGMA foreign_keys to get_db()**

In `library/backend/api_modular/core.py`, add after line 29 (`conn.row_factory = sqlite3.Row`):

```python
conn.execute("PRAGMA foreign_keys=ON")
```

- [ ] **Step 4: Write a quick test that verifies foreign keys are enabled**

```python
# Add to library/tests/test_api.py or a new test_schema.py
def test_foreign_keys_enabled(self):
    """Verify PRAGMA foreign_keys is ON for all connections."""
    conn = get_db(self.db_path)
    result = conn.execute("PRAGMA foreign_keys").fetchone()
    assert result[0] == 1
    conn.close()
```

- [ ] **Step 5: Run the test**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_schema.py -v -k test_foreign_keys`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add library/backend/migrations/011_multi_author_narrator.sql library/backend/schema.sql library/backend/api_modular/core.py library/tests/test_schema.py
git commit -m "feat: add normalized author/narrator schema with foreign key enforcement"
```

- [ ] **Step 7: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task2-schema
```

---

## Chunk 2: Data Migration

### Task 3: Phase 2 Data Migration Script

**Files:**

- Create: `library/backend/migrations/migrate_to_normalized_authors.py`
- Create: `library/tests/test_migration_authors.py`

- [ ] **Step 1: Write failing test for migration**

```python
# library/tests/test_migration_authors.py
"""Tests for author/narrator data migration from flat columns to normalized tables."""

import sqlite3
import tempfile
import pytest
from pathlib import Path


def create_test_db(db_path):
    """Create a minimal DB with schema and test data."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    # Create minimal audiobooks table
    conn.execute("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            narrator TEXT,
            author_last_name TEXT,
            author_first_name TEXT,
            narrator_last_name TEXT,
            narrator_first_name TEXT,
            file_path TEXT UNIQUE NOT NULL,
            content_type TEXT DEFAULT 'Product'
        )
    """)
    # Create new normalized tables from migration SQL
    migration_sql = (Path(__file__).parent.parent / "backend" / "migrations" / "011_multi_author_narrator.sql").read_text()
    conn.executescript(migration_sql)
    return conn


class TestMigration:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.conn = create_test_db(self.db_path)

    def teardown_method(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _insert_book(self, title, author, narrator="Test Narrator"):
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path) VALUES (?, ?, ?, ?)",
            (title, author, narrator, f"/fake/{title}.opus")
        )
        self.conn.commit()

    def test_single_author_migrated(self):
        self._insert_book("It", "Stephen King")
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)

        authors = self.conn.execute("SELECT name, sort_name FROM authors").fetchall()
        assert len(authors) == 1
        assert authors[0][0] == "Stephen King"
        assert authors[0][1] == "King, Stephen"

        links = self.conn.execute("SELECT * FROM book_authors").fetchall()
        assert len(links) == 1
        assert links[0][2] == 0  # position

    def test_multi_author_creates_both(self):
        self._insert_book("The Talisman", "Stephen King, Peter Straub")
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)

        authors = self.conn.execute("SELECT name FROM authors ORDER BY name").fetchall()
        assert len(authors) == 2
        names = {a[0] for a in authors}
        assert "Stephen King" in names
        assert "Peter Straub" in names

        links = self.conn.execute("SELECT * FROM book_authors ORDER BY position").fetchall()
        assert len(links) == 2

    def test_deduplication(self):
        self._insert_book("It", "Stephen King")
        self._insert_book("The Shining", "Stephen King")
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)

        authors = self.conn.execute("SELECT name FROM authors").fetchall()
        assert len(authors) == 1  # Deduplicated

        links = self.conn.execute("SELECT * FROM book_authors").fetchall()
        assert len(links) == 2  # Two books linked

    def test_narrator_migrated(self):
        self._insert_book("It", "Stephen King", "Steven Weber")
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)

        narrators = self.conn.execute("SELECT name, sort_name FROM narrators").fetchall()
        assert len(narrators) == 1
        assert narrators[0][0] == "Steven Weber"
        assert narrators[0][1] == "Weber, Steven"

    def test_null_author_no_junction_row(self):
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path) VALUES (?, NULL, ?, ?)",
            ("Orphan Book", "Some Narrator", "/fake/orphan.opus")
        )
        self.conn.commit()
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)

        links = self.conn.execute(
            "SELECT * FROM book_authors WHERE book_id = (SELECT id FROM audiobooks WHERE title='Orphan Book')"
        ).fetchall()
        assert len(links) == 0

    def test_group_name_redirected_to_narrator(self):
        self._insert_book("Drama", "Full Cast", "Someone Else")
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)

        # "Full Cast" should NOT be in authors
        authors = self.conn.execute("SELECT name FROM authors").fetchall()
        author_names = {a[0] for a in authors}
        assert "Full Cast" not in author_names

        # "Full Cast" SHOULD be in narrators
        narrators = self.conn.execute("SELECT name FROM narrators").fetchall()
        narrator_names = {n[0] for n in narrators}
        assert "Full Cast" in narrator_names

    def test_idempotent(self):
        """Running migration twice should not duplicate data."""
        self._insert_book("It", "Stephen King")
        from library.backend.migrations.migrate_to_normalized_authors import migrate
        migrate(self.db_path)
        migrate(self.db_path)  # Second run

        authors = self.conn.execute("SELECT name FROM authors").fetchall()
        assert len(authors) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_migration_authors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.backend.migrations.migrate_to_normalized_authors'`

- [ ] **Step 3: Implement migration script**

```python
# library/backend/migrations/migrate_to_normalized_authors.py
"""
Phase 2 Data Migration: Populate normalized author/narrator tables
from existing flat text columns in the audiobooks table.

Usage:
    python -m library.backend.migrations.migrate_to_normalized_authors [--db-path PATH] [--dry-run]

Idempotent: safe to run multiple times. Uses INSERT OR IGNORE for deduplication.
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from backend.name_parser import parse_names, generate_sort_name, is_group_name

logger = logging.getLogger(__name__)


def migrate(db_path: str, dry_run: bool = False) -> dict:
    """Run the author/narrator normalization migration.

    Returns:
        Dict with migration statistics.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    stats = {
        "books_processed": 0,
        "authors_created": 0,
        "narrators_created": 0,
        "author_links": 0,
        "narrator_links": 0,
        "group_redirections": 0,
        "ambiguous": [],
    }

    rows = conn.execute(
        "SELECT id, title, author, narrator FROM audiobooks"
    ).fetchall()

    for row in rows:
        book_id = row["id"]
        stats["books_processed"] += 1

        # --- Authors ---
        author_names = parse_names(row["author"]) if row["author"] else []
        narrator_names = parse_names(row["narrator"]) if row["narrator"] else []

        # Redirect group names from author to narrator
        redirected = []
        clean_authors = []
        for name in author_names:
            if is_group_name(name):
                redirected.append(name)
                stats["group_redirections"] += 1
            else:
                clean_authors.append(name)

        # Add redirected names to narrators (avoid duplicates)
        for name in redirected:
            if name not in narrator_names:
                narrator_names.append(name)

        # Insert authors and link
        for pos, name in enumerate(clean_authors):
            sort_name = generate_sort_name(name)
            if not sort_name:
                continue
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO authors (name, sort_name) VALUES (?, ?)",
                    (name, sort_name),
                )
                author_id = conn.execute(
                    "SELECT id FROM authors WHERE name = ?", (name,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO book_authors (book_id, author_id, position) VALUES (?, ?, ?)",
                    (book_id, author_id, pos),
                )
                stats["author_links"] += 1

        # Insert narrators and link
        for pos, name in enumerate(narrator_names):
            sort_name = generate_sort_name(name)
            if not sort_name:
                continue
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO narrators (name, sort_name) VALUES (?, ?)",
                    (name, sort_name),
                )
                narrator_id = conn.execute(
                    "SELECT id FROM narrators WHERE name = ?", (name,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO book_narrators (book_id, narrator_id, position) VALUES (?, ?, ?)",
                    (book_id, narrator_id, pos),
                )
                stats["narrator_links"] += 1

    if not dry_run:
        conn.commit()

    stats["authors_created"] = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
    stats["narrators_created"] = conn.execute("SELECT COUNT(*) FROM narrators").fetchone()[0]

    conn.close()

    logger.info(
        "Migration complete: %d books, %d authors, %d narrators, %d author-links, %d narrator-links, %d group redirections",
        stats["books_processed"], stats["authors_created"], stats["narrators_created"],
        stats["author_links"], stats["narrator_links"], stats["group_redirections"],
    )

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Migrate to normalized authors/narrators")
    parser.add_argument("--db-path", type=str, help="Path to database")
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    args = parser.parse_args()

    db_path = args.db_path
    if not db_path:
        from config import DATABASE_PATH
        db_path = str(DATABASE_PATH)

    result = migrate(db_path, dry_run=args.dry_run)
    print(f"Migration stats: {result}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_migration_authors.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run linters**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && ruff check library/backend/migrations/migrate_to_normalized_authors.py library/tests/test_migration_authors.py && ruff format --check library/backend/migrations/migrate_to_normalized_authors.py library/tests/test_migration_authors.py`

- [ ] **Step 6: Commit**

```bash
git add library/backend/migrations/migrate_to_normalized_authors.py library/tests/test_migration_authors.py
git commit -m "feat: add data migration script for normalized author/narrator tables"
```

- [ ] **Step 7: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task3-migration
```

---

## Chunk 3: API — Enriched Flat Endpoint and Grouped Endpoint

### Task 4: Enrich Flat API Endpoint with Author/Narrator Arrays

**Files:**

- Modify: `library/backend/api_modular/audiobooks.py:274-365` (add batch author/narrator loading)

- [ ] **Step 1: Write failing test**

Add to existing test infrastructure or create:

```python
# In library/tests/test_grouped_api.py
"""Tests for enriched audiobook API responses with author/narrator arrays."""

def test_flat_endpoint_includes_authors_array(client, populated_db):
    """GET /api/audiobooks should include authors array per book."""
    resp = client.get("/api/audiobooks")
    data = resp.get_json()
    book = data["audiobooks"][0]
    assert "authors" in book
    assert isinstance(book["authors"], list)
    assert all("id" in a and "name" in a and "sort_name" in a and "position" in a for a in book["authors"])

def test_flat_endpoint_includes_narrators_array(client, populated_db):
    """GET /api/audiobooks should include narrators array per book."""
    resp = client.get("/api/audiobooks")
    data = resp.get_json()
    book = data["audiobooks"][0]
    assert "narrators" in book
    assert isinstance(book["narrators"], list)

def test_flat_endpoint_preserves_flat_author_string(client, populated_db):
    """Flat author/narrator strings still present for backwards compatibility."""
    resp = client.get("/api/audiobooks")
    data = resp.get_json()
    book = data["audiobooks"][0]
    assert "author" in book
    assert isinstance(book["author"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `authors` key not in response

- [ ] **Step 3: Add batch author/narrator loading to audiobooks.py**

In `library/backend/api_modular/audiobooks.py`, after the existing batch queries (genres, eras, topics, supplements — around line 325), add:

```python
# Batch: authors for all books in one query
cursor.execute(
    f"""
    SELECT ba.book_id, a.id, a.name, a.sort_name, ba.position
    FROM authors a
    JOIN book_authors ba ON a.id = ba.author_id
    WHERE ba.book_id IN ({placeholders})
    ORDER BY ba.position
    """,
    book_ids,
)
authors_map: dict[int, list[dict]] = {}
for r in cursor.fetchall():
    authors_map.setdefault(r["book_id"], []).append({
        "id": r["id"], "name": r["name"],
        "sort_name": r["sort_name"], "position": r["position"],
    })

# Batch: narrators for all books in one query
cursor.execute(
    f"""
    SELECT bn.book_id, n.id, n.name, n.sort_name, bn.position
    FROM narrators n
    JOIN book_narrators bn ON n.id = bn.narrator_id
    WHERE bn.book_id IN ({placeholders})
    ORDER BY bn.position
    """,
    book_ids,
)
narrators_map: dict[int, list[dict]] = {}
for r in cursor.fetchall():
    narrators_map.setdefault(r["book_id"], []).append({
        "id": r["id"], "name": r["name"],
        "sort_name": r["sort_name"], "position": r["position"],
    })
```

Then in the per-book assignment loop (around line 345), add:

```python
book["authors"] = authors_map.get(bid, [])
book["narrators"] = narrators_map.get(bid, [])
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add library/backend/api_modular/audiobooks.py library/tests/test_grouped_api.py
git commit -m "feat: enrich flat audiobooks endpoint with authors/narrators arrays"
```

---

### Task 5: New Grouped Endpoint

**Files:**

- Create: `library/backend/api_modular/grouped.py`
- Modify: `library/backend/api_modular/__init__.py` (register blueprint)
- Add to: `library/tests/test_grouped_api.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to library/tests/test_grouped_api.py

def test_grouped_by_author(client, populated_db):
    """GET /api/audiobooks/grouped?by=author returns author-grouped results."""
    resp = client.get("/api/audiobooks/grouped?by=author")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "groups" in data
    assert "total_groups" in data
    assert "total_books" in data
    # Each group has key and books
    for group in data["groups"]:
        assert "key" in group
        assert "id" in group["key"]
        assert "name" in group["key"]
        assert "sort_name" in group["key"]
        assert "books" in group
        assert len(group["books"]) > 0

def test_grouped_by_narrator(client, populated_db):
    """GET /api/audiobooks/grouped?by=narrator works identically."""
    resp = client.get("/api/audiobooks/grouped?by=narrator")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "groups" in data

def test_grouped_invalid_by(client, populated_db):
    """Invalid 'by' parameter returns 400."""
    resp = client.get("/api/audiobooks/grouped?by=invalid")
    assert resp.status_code == 400

def test_grouped_multi_author_appears_in_both(client, multi_author_db):
    """A book with two authors appears in both author groups."""
    resp = client.get("/api/audiobooks/grouped?by=author")
    data = resp.get_json()
    # Find the multi-author book
    groups_with_talisman = [
        g for g in data["groups"]
        if any(b["title"] == "The Talisman" for b in g["books"])
    ]
    assert len(groups_with_talisman) == 2  # Under both King and Straub

def test_grouped_total_books_deduplicated(client, multi_author_db):
    """total_books should be deduplicated count."""
    resp = client.get("/api/audiobooks/grouped?by=author")
    data = resp.get_json()
    # total_books should count The Talisman once, not twice
    assert data["total_books"] == data["total_books"]  # Verify it's present
    # More specific: count unique book IDs across all groups
    all_book_ids = set()
    for g in data["groups"]:
        for b in g["books"]:
            all_book_ids.add(b["id"])
    assert data["total_books"] == len(all_book_ids)

def test_grouped_sorted_by_sort_name(client, populated_db):
    """Groups should be sorted alphabetically by sort_name."""
    resp = client.get("/api/audiobooks/grouped?by=author")
    data = resp.get_json()
    sort_names = [g["key"]["sort_name"] for g in data["groups"]]
    assert sort_names == sorted(sort_names, key=str.lower)

def test_grouped_books_within_group_sorted_by_title(client, populated_db):
    """Books within each group sorted alphabetically by title."""
    resp = client.get("/api/audiobooks/grouped?by=author")
    data = resp.get_json()
    for group in data["groups"]:
        titles = [b["title"] for b in group["books"]]
        assert titles == sorted(titles, key=str.lower)

def test_grouped_respects_audiobook_filter(client, populated_db):
    """Grouped endpoint should exclude non-audiobook content types."""
    resp = client.get("/api/audiobooks/grouped?by=author")
    data = resp.get_json()
    for group in data["groups"]:
        for book in group["books"]:
            assert book.get("content_type", "Product") in ("Product", "Performance", "Speech", None)
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement grouped.py**

```python
# library/backend/api_modular/grouped.py
"""Grouped audiobook endpoint — returns books grouped by author or narrator."""

import logging

from flask import Blueprint, Response, jsonify, request

from .auth import guest_allowed
from .core import FlaskResponse, get_db

logger = logging.getLogger(__name__)

grouped_bp = Blueprint("grouped", __name__)

AUDIOBOOK_FILTER = (
    "(a.content_type IN ('Product', 'Performance', 'Speech') OR a.content_type IS NULL)"
)


def init_grouped_routes(db_path):
    """Initialize grouped endpoint routes."""

    @grouped_bp.route("/api/audiobooks/grouped", methods=["GET"])
    @guest_allowed
    def get_grouped() -> FlaskResponse:
        """Get audiobooks grouped by author or narrator.

        Query params:
            by: 'author' or 'narrator' (required)
        """
        group_by = request.args.get("by", "").strip().lower()
        if group_by not in ("author", "narrator"):
            return jsonify({"error": "Parameter 'by' must be 'author' or 'narrator'"}), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        if group_by == "author":
            person_table = "authors"
            junction_table = "book_authors"
            person_fk = "author_id"
        else:
            person_table = "narrators"
            junction_table = "book_narrators"
            person_fk = "narrator_id"

        # Get all persons sorted by sort_name
        persons = cursor.execute(
            f"SELECT id, name, sort_name FROM {person_table} ORDER BY sort_name COLLATE NOCASE"
        ).fetchall()

        # Get all book-person links with book data, filtered to audiobooks only
        cursor.execute(
            f"""
            SELECT
                j.{person_fk} as person_id, j.position,
                a.id, a.title, a.author, a.narrator, a.publisher, a.series,
                a.series_sequence, a.edition, a.asin, a.duration_hours,
                a.duration_formatted, a.file_size_mb, a.file_path,
                a.cover_path, a.format, a.quality, a.published_year,
                a.content_type, a.description
            FROM {junction_table} j
            JOIN audiobooks a ON j.book_id = a.id
            WHERE {AUDIOBOOK_FILTER}
            ORDER BY a.title COLLATE NOCASE
            """,
        )
        rows = cursor.fetchall()

        # Build person_id -> [books] map
        books_by_person: dict[int, list[dict]] = {}
        all_book_ids: set[int] = set()
        for r in rows:
            person_id = r["person_id"]
            book = {
                "id": r["id"], "title": r["title"],
                "author": r["author"], "narrator": r["narrator"],
                "publisher": r["publisher"], "series": r["series"],
                "series_sequence": r["series_sequence"],
                "edition": r["edition"], "asin": r["asin"],
                "duration_hours": r["duration_hours"],
                "duration_formatted": r["duration_formatted"],
                "file_size_mb": r["file_size_mb"],
                "cover_path": r["cover_path"], "format": r["format"],
                "quality": r["quality"],
                "published_year": r["published_year"],
                "content_type": r["content_type"],
            }
            books_by_person.setdefault(person_id, []).append(book)
            all_book_ids.add(r["id"])

        # Also add "Unknown" group for books with no junction rows
        orphan_query = f"""
            SELECT id, title, author, narrator, publisher, series,
                   series_sequence, edition, asin, duration_hours,
                   duration_formatted, file_size_mb, cover_path,
                   format, quality, published_year, content_type
            FROM audiobooks a
            WHERE {AUDIOBOOK_FILTER}
              AND a.id NOT IN (SELECT book_id FROM {junction_table})
            ORDER BY title COLLATE NOCASE
        """
        orphans = cursor.execute(orphan_query).fetchall()

        conn.close()

        # Build response groups
        groups = []
        for person in persons:
            pid = person["id"]
            if pid in books_by_person:
                groups.append({
                    "key": {
                        "id": pid,
                        "name": person["name"],
                        "sort_name": person["sort_name"],
                    },
                    "books": books_by_person[pid],
                })

        # Add Unknown group at end if orphans exist
        if orphans:
            unknown_label = "Unknown Author" if group_by == "author" else "Unknown Narrator"
            orphan_books = [dict(r) for r in orphans]
            for b in orphan_books:
                all_book_ids.add(b["id"])
            groups.append({
                "key": {"id": None, "name": unknown_label, "sort_name": "zzz_unknown"},
                "books": orphan_books,
            })

        return jsonify({
            "groups": groups,
            "total_groups": len(groups),
            "total_books": len(all_book_ids),
        })

    return grouped_bp
```

- [ ] **Step 4: Register blueprint in **init**.py**

In `library/backend/api_modular/__init__.py`, import and register:

```python
from .grouped import grouped_bp, init_grouped_routes
# In create_app():
init_grouped_routes(db_path)
app.register_blueprint(grouped_bp)
```

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Run linters**

- [ ] **Step 7: Commit**

```bash
git add library/backend/api_modular/grouped.py library/backend/api_modular/__init__.py library/tests/test_grouped_api.py
git commit -m "feat: add grouped audiobook endpoint for author/narrator sort views"
```

- [ ] **Step 8: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task5-grouped-api
```

---

## Chunk 4: Frontend — Grouped View

### Task 6: Frontend Grouped Rendering

**Files:**

- Modify: `library/web-v2/js/library.js`

- [ ] **Step 1: Add grouped rendering method**

Add new method `renderGroupedBooks(data, groupBy)` to the `AudiobookLibraryV2` class. This renders collapsible author/narrator groups instead of the flat grid.

Key implementation points:

- New method `renderGroupedBooks(data, groupBy)` that generates group headers with collapse/expand toggles + book count per group
- Each group header shows the person's name in "Last, First" format (from `sort_name`)
- Book cards use existing `createBookCard()` — no changes to card rendering
- Library header shows deduplicated `total_books` count from API response
- CSS for `.group-header`, `.group-books`, `.group-collapsed` states

- [ ] **Step 2: Modify fetch/sort logic to use grouped endpoint**

When `sort_field` is `author_last`, `author_first`, `narrator_last`, or `narrator_first`, call `/api/audiobooks/grouped?by=author` (or `narrator`) instead of the flat endpoint. Route the response to `renderGroupedBooks()`.

For all other sort fields, continue using the flat endpoint and `renderBooks()`.

Key implementation points:

- In `fetchBooks()` (around line 1440), check if sort requires grouping
- If grouped: fetch from grouped endpoint, call `renderGroupedBooks()`
- If flat: existing behavior unchanged
- Update pagination display (grouped view has no pagination — shows all groups)

- [ ] **Step 3: Add CSS for group headers**

Add styles for:

- `.author-group` — container for each group
- `.group-header` — clickable, shows person name + book count + collapse indicator
- `.group-header:hover` — highlight on hover
- `.group-books` — grid of book cards within a group (reuse existing grid styles)
- `.group-collapsed .group-books` — hidden when collapsed

- [ ] **Step 4: Test manually in dev mode**

Run dev server: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m library.backend.api_modular`
Open browser to dev port, sort by author, verify:

- Groups render with headers
- Collapse/expand works
- Multi-author books appear in multiple groups
- Switching to title sort returns to flat view
- Book count in header is deduplicated

- [ ] **Step 5: Commit**

```bash
git add library/web-v2/js/library.js library/web-v2/css/
git commit -m "feat: add grouped author/narrator view with collapsible sections in frontend"
```

- [ ] **Step 6: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task6-frontend
```

---

## Chunk 5: Back-Office Admin Endpoints and upgrade.sh Integration

### Task 7: Admin Correction Endpoints

**Files:**

- Create: `library/backend/api_modular/admin_authors.py`
- Modify: `library/backend/api_modular/__init__.py` (register blueprint)

- [ ] **Step 1: Write failing tests for admin endpoints**

Tests for rename, merge, and reassign operations. Each test verifies the operation succeeds and that flat columns are regenerated.

- [ ] **Step 2: Implement admin_authors.py**

Endpoints:

- `PUT /api/admin/authors/<id>` — rename, update sort_name, regenerate flat columns
- `POST /api/admin/authors/merge` — merge source_ids into target_id, reassign books, delete sources, regenerate flat columns
- `PUT /api/admin/books/<id>/authors` — full replacement of book's author list, regenerate flat column
- Same three endpoints for narrators

Each endpoint that modifies normalized data must regenerate the flat `author`/`narrator` column on affected `audiobooks` rows by joining through the junction table and concatenating names in position order.

- [ ] **Step 3: Register blueprint**

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add library/backend/api_modular/admin_authors.py library/backend/api_modular/__init__.py library/tests/test_admin_authors.py
git commit -m "feat: add admin endpoints for author/narrator correction (rename, merge, reassign)"
```

---

### Task 8: upgrade.sh Integration

**Files:**

- Modify: `upgrade.sh`

- [ ] **Step 1: Add migration detection to upgrade.sh**

After the existing deployment steps (rsync, venv rebuild, etc.), add a check:

```bash
# Check if normalized author tables exist
if ! sqlite3 "$DB_PATH" "SELECT 1 FROM authors LIMIT 1" 2>/dev/null; then
    echo "Running author/narrator normalization migration..."
    sqlite3 "$DB_PATH" < "$TARGET_DIR/library/backend/migrations/011_multi_author_narrator.sql"
    python3 "$TARGET_DIR/library/backend/migrations/migrate_to_normalized_authors.py" --db-path "$DB_PATH"
fi
```

**Note:** `install.sh` needs NO changes — it already runs `schema.sql` which now includes the new DDL. Fresh installs get empty normalized tables; data migration only applies to upgrades of existing databases.

- [ ] **Step 2: Test upgrade path on dev database**

- [ ] **Step 3: Commit**

```bash
git add upgrade.sh
git commit -m "feat: integrate author/narrator migration into upgrade.sh"
```

- [ ] **Step 4: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task8-upgrade
```

---

## Chunk 6: VM Integration Testing

### Task 9: Full Integration Test on test-audiobook-cachyos

**Files:** No new files — uses existing test infrastructure

- [ ] **Step 1: Revert test VM to pristine snapshot**

```bash
sudo virsh snapshot-revert test-audiobook-cachyos pristine-275g-2026-03-01
```

- [ ] **Step 2: Deploy sort_fix branch to test VM**

```bash
./upgrade.sh --from-project . --remote 192.168.122.104 --yes
```

(The VM auto-detects pristine state and runs `install.sh --system` first)

- [ ] **Step 3: Verify migration ran automatically**

SSH to VM, check:

```bash
sqlite3 /var/lib/audiobooks/db/audiobooks.db "SELECT COUNT(*) FROM authors"
sqlite3 /var/lib/audiobooks/db/audiobooks.db "SELECT COUNT(*) FROM book_authors"
```

- [ ] **Step 4: Test grouped API endpoint**

```bash
curl -s http://192.168.122.104:5001/api/audiobooks/grouped?by=author | python3 -m json.tool | head -50
curl -s http://192.168.122.104:5001/api/audiobooks/grouped?by=narrator | python3 -m json.tool | head -50
```

- [ ] **Step 5: Test flat API still works with arrays**

```bash
curl -s "http://192.168.122.104:5001/api/audiobooks?per_page=1" | python3 -m json.tool
```

Verify `authors` and `narrators` arrays present.

- [ ] **Step 6: Playwright UI test**

Run Playwright tests against the test VM for:

- Grouped view renders when sorting by author/narrator
- Collapsible headers work
- Multi-author books appear in multiple groups
- Flat view works for other sorts
- No regressions on genre/subgenre display

- [ ] **Step 7: BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-task9-integration
```

---

### Task 10: Production-Scale Validation on qa-audiobooks Clone

**Files:** No new files

- [ ] **Step 1: Clone qa-audiobook-cachyos for parallel testing**

```bash
sudo virt-clone --original qa-audiobook-cachyos --name sort-fix-validation --auto-clone
sudo virsh start sort-fix-validation
```

- [ ] **Step 2: Deploy sort_fix branch**

```bash
./upgrade.sh --from-project . --remote <clone-ip> --yes
```

- [ ] **Step 3: Validate migration on 801-book dataset**

Check migration stats:

- Author count should be >= 492 (existing unique authors, may increase with multi-author splitting)
- Every book should have at least one author junction row
- No orphaned junction rows

- [ ] **Step 4: Validate grouped endpoint performance**

Measure response time for grouped endpoint with full dataset:

```bash
time curl -s "http://<clone-ip>:5001/api/audiobooks/grouped?by=author" > /dev/null
time curl -s "http://<clone-ip>:5001/api/audiobooks/grouped?by=narrator" > /dev/null
```

Acceptable: < 2 seconds.

- [ ] **Step 5: Spot-check known multi-author books**

Query specific books known to have multiple authors and verify they appear in multiple groups.

- [ ] **Step 6: Cleanup clone VM**

```bash
sudo virsh destroy sort-fix-validation
sudo virsh undefine sort-fix-validation --remove-all-storage
```

- [ ] **Step 7: Final BTRFS snapshot**

```bash
sudo btrfs subvolume snapshot -r /hddRaid1/ClaudeCodeProjects/Audiobook-Manager /hddRaid1/ClaudeCodeProjects/Audiobook-Manager-snap-complete
```

---

## Summary

| Task | Component | New Files | Modified Files |
|------|-----------|-----------|---------------|
| 1 | Name parser | 2 | 0 |
| 2 | Schema migration | 1 | 2 |
| 3 | Data migration | 2 | 0 |
| 4 | Enriched flat API | 0 | 1 (+tests) |
| 5 | Grouped endpoint | 1 | 1 (+tests) |
| 6 | Frontend grouped view | 0 | 1-2 |
| 7 | Admin correction endpoints | 1 | 1 (+tests) |
| 8 | upgrade.sh integration | 0 | 1 |
| 9 | VM integration test | 0 | 0 |
| 10 | Production-scale validation | 0 | 0 |

**Total new files:** 7
**Total modified files:** ~7
**Estimated commits:** 10-12

---

### Task 11: Version Bump, CHANGELOG, and Final Commit

**Files:**

- Modify: `CHANGELOG.md`
- Modify: `VERSION`

- [ ] **Step 1: Update VERSION**

Bump to next minor version (this is a feature with breaking backend changes).

- [ ] **Step 2: Update CHANGELOG.md**

Add entry under new version:

```markdown
## [X.Y.Z] - 2026-03-13

### Added
- Normalized author/narrator database schema (many-to-many relationships)
- Books with multiple authors/narrators now appear under each person in sorted views
- Collapsible author/narrator group headers in library UI
- Grouped API endpoint (`/api/audiobooks/grouped?by=author|narrator`)
- Admin correction endpoints for author/narrator rename, merge, and reassign
- Automatic data migration from flat text columns to normalized tables

### Changed
- Flat API endpoint now includes `authors` and `narrators` arrays per book
- SQLite connections now enforce foreign keys (`PRAGMA foreign_keys = ON`)

### Breaking
- API response shape changed (new `authors`/`narrators` arrays in flat endpoint)
- Database schema expanded (4 new tables, migration runs automatically on upgrade)
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md VERSION
git commit -m "chore: bump version and update CHANGELOG for multi-author sorting"
```

---

After Task 11 passes, the `sort_fix` branch is ready for review before merging to `main`.
