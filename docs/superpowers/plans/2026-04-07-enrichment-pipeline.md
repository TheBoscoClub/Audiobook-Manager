# Enrichment Pipeline Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix ASIN extraction from 3 sources, build a multi-provider enrichment chain (Local → Audible → Google Books → Open Library), backfill 1860 existing books, and auto-trigger enrichment for all new books.

**Architecture:** Tiered provider chain where each provider fills only empty fields, never overwrites. Local extraction always runs (no API calls), then Audible (if ASIN), then Google Books/Open Library fallbacks (title+author search). Chain short-circuits when all target fields are populated.

**Tech Stack:** Python 3.14, SQLite, urllib (stdlib — no new dependencies), systemd timers, pytest + unittest.mock

---

## File Structure

| File | Responsibility | Status |
|------|---------------|--------|
| `library/backend/schema.sql` | Add `enrichment_source TEXT` column | Modify |
| `library/scanner/metadata_utils.py` | Replace `extract_asin_from_chapters_json()` with 3-source `extract_asin()` | Modify |
| `library/scripts/enrichment/__init__.py` | Chain orchestrator — `enrich_book()` runs providers in order | Create |
| `library/scripts/enrichment/base.py` | `EnrichmentProvider` base class | Create |
| `library/scripts/enrichment/provider_local.py` | ASIN from voucher/filename, series from tags/title | Create |
| `library/scripts/enrichment/provider_audible.py` | Audible catalog API (refactored from `enrich_single.py:73-384`) | Create |
| `library/scripts/enrichment/provider_google.py` | Google Books API (refactored from `enrich_single.py:182-445`) | Create |
| `library/scripts/enrichment/provider_openlibrary.py` | Open Library API (refactored from `enrich_single.py:207-474`) | Create |
| `library/scripts/enrich_single.py` | Thin wrapper calling new orchestrator (backward compat) | Modify |
| `library/scripts/backfill_enrichment.py` | CLI for ASIN recovery + bulk enrichment | Create |
| `library/scanner/add_new_audiobooks.py` | Update import path for new orchestrator | Modify |
| `systemd/audiobook-enrichment.timer` | Nightly catch-up timer | Create |
| `systemd/audiobook-enrichment.service` | Oneshot service for timer | Create |
| `install.sh` | Enable timer, run schema migration | Modify |
| `library/tests/test_enrichment_providers.py` | Tests for all providers + orchestrator | Create |
| `library/tests/test_asin_extraction.py` | Tests for 3-source ASIN extraction | Create |
| `library/tests/test_backfill_enrichment.py` | Tests for backfill script | Create |

---

### Task 1: Schema Migration — Add `enrichment_source` Column

**Files:**
- Modify: `library/backend/schema.sql:4-63`
- Create: `library/tests/test_enrichment_schema.py`

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_enrichment_schema.py`:

```python
"""Tests for enrichment_source column in audiobooks schema."""

import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


class TestEnrichmentSourceColumn:
    """Verify enrichment_source column exists and works correctly."""

    @pytest.fixture
    def db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        yield conn
        conn.close()

    def test_column_exists(self, db):
        """enrichment_source column must exist in audiobooks table."""
        cursor = db.execute("PRAGMA table_info(audiobooks)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "enrichment_source" in columns

    def test_default_is_null(self, db):
        """enrichment_source defaults to NULL for new rows."""
        db.execute(
            "INSERT INTO audiobooks (title, file_path) VALUES (?, ?)",
            ("Test Book", "/test/book.opus"),
        )
        cursor = db.execute(
            "SELECT enrichment_source FROM audiobooks WHERE title = 'Test Book'"
        )
        assert cursor.fetchone()[0] is None

    def test_accepts_provider_names(self, db):
        """enrichment_source accepts known provider name strings."""
        for source in ("local", "audible", "google_books", "openlibrary"):
            db.execute(
                "INSERT INTO audiobooks (title, file_path, enrichment_source) "
                "VALUES (?, ?, ?)",
                (f"Book {source}", f"/test/{source}.opus", source),
            )
        cursor = db.execute(
            "SELECT enrichment_source FROM audiobooks ORDER BY id"
        )
        values = [row[0] for row in cursor.fetchall()]
        assert values == ["local", "audible", "google_books", "openlibrary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd library && python -m pytest tests/test_enrichment_schema.py -v`
Expected: FAIL — `enrichment_source` column does not exist yet

- [ ] **Step 3: Add the column to schema.sql**

In `library/backend/schema.sql`, after line 51 (`isbn_enriched_at TIMESTAMP`), add:

```sql
    enrichment_source TEXT,             -- Which provider enriched: local, audible, google_books, openlibrary
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd library && python -m pytest tests/test_enrichment_schema.py -v`
Expected: PASS — all 3 tests green

- [ ] **Step 5: Commit**

```bash
git add library/backend/schema.sql library/tests/test_enrichment_schema.py
git commit -m "feat: add enrichment_source column to audiobooks schema"
```

---

### Task 2: ASIN Extraction — 3-Source `extract_asin()`

**Files:**
- Modify: `library/scanner/metadata_utils.py:288-308`
- Create: `library/tests/test_asin_extraction.py`

- [ ] **Step 1: Write the failing tests**

Create `library/tests/test_asin_extraction.py`:

```python
"""Tests for multi-source ASIN extraction.

Sources checked in order:
1. chapters.json (existing behavior)
2. .voucher file (new — content_license.asin)
3. Source filename (new — {ASIN}_Title-*.aaxc pattern)
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner.metadata_utils import extract_asin


class TestExtractAsinFromChaptersJson:
    """Source 1: chapters.json in the same directory as the audiobook."""

    def test_extracts_asin_from_chapters_json(self, tmp_path):
        book_dir = tmp_path / "Author" / "Title"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Title.opus"
        opus_file.touch()

        chapters = {
            "content_metadata": {
                "content_reference": {"asin": "B08G9PRS1K"}
            }
        }
        (book_dir / "chapters.json").write_text(json.dumps(chapters))

        assert extract_asin(opus_file) == "B08G9PRS1K"

    def test_returns_none_when_no_chapters_json(self, tmp_path):
        book_dir = tmp_path / "Author" / "Title"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Title.opus"
        opus_file.touch()

        assert extract_asin(opus_file) is None

    def test_returns_none_on_malformed_json(self, tmp_path):
        book_dir = tmp_path / "Author" / "Title"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Title.opus"
        opus_file.touch()
        (book_dir / "chapters.json").write_text("{bad json")

        assert extract_asin(opus_file) is None


class TestExtractAsinFromVoucher:
    """Source 2: .voucher files in the Sources directory."""

    def test_extracts_asin_from_voucher(self, tmp_path):
        """Voucher has content_license.asin — should be found."""
        # Library file
        book_dir = tmp_path / "Library" / "Author" / "Revenge Prey"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Revenge Prey.opus"
        opus_file.touch()

        # Source dir with voucher
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {
            "content_license": {
                "asin": "B0D7JLGFST",
                "content_metadata": {
                    "content_reference": {"asin": "B0D7JLGFST"}
                },
            }
        }
        voucher_file = sources_dir / "B0D7JLGFST_Revenge_Prey-AAX_44_128.voucher"
        voucher_file.write_text(json.dumps(voucher))

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result == "B0D7JLGFST"

    def test_voucher_not_checked_without_sources_dir(self, tmp_path):
        """Without sources_dir, only chapters.json is checked."""
        book_dir = tmp_path / "Author" / "Title"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Title.opus"
        opus_file.touch()
        # No chapters.json, no sources_dir
        assert extract_asin(opus_file) is None

    def test_voucher_fallback_when_no_chapters_json(self, tmp_path):
        """Voucher is checked when chapters.json doesn't exist."""
        book_dir = tmp_path / "Library" / "Author" / "Cool Book"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Cool Book.opus"
        opus_file.touch()

        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B01ABCDEF0"}}
        (sources_dir / "B01ABCDEF0_Cool_Book-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result == "B01ABCDEF0"


class TestExtractAsinFromFilename:
    """Source 3: ASIN from source filename pattern {ASIN}_Title-*.aaxc."""

    def test_extracts_asin_from_aaxc_filename(self, tmp_path):
        book_dir = tmp_path / "Library" / "Author" / "Some Title"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Some Title.opus"
        opus_file.touch()

        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        # Create .aaxc file with ASIN in name (no voucher)
        (sources_dir / "B07EXAMPLE1_Some_Title-AAX_44_128.aaxc").touch()

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result == "B07EXAMPLE1"

    def test_filename_asin_must_start_with_b_or_digit(self, tmp_path):
        """Only valid ASINs (10 alphanumeric, starting with B or digit)."""
        book_dir = tmp_path / "Library" / "Author" / "Bad"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Bad.opus"
        opus_file.touch()

        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        # Invalid — starts with X, not a valid ASIN prefix
        (sources_dir / "XXXXXXXXXZ_Bad-AAX_44_128.aaxc").touch()

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result is None


class TestAsinPriority:
    """chapters.json takes priority over voucher, voucher over filename."""

    def test_chapters_json_wins_over_voucher(self, tmp_path):
        book_dir = tmp_path / "Library" / "Author" / "Dual"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Dual.opus"
        opus_file.touch()

        # chapters.json with ASIN-A
        chapters = {
            "content_metadata": {
                "content_reference": {"asin": "ASIN_FROM_C"}
            }
        }
        (book_dir / "chapters.json").write_text(json.dumps(chapters))

        # Voucher with ASIN-B
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "ASIN_FROM_V"}}
        (sources_dir / "ASIN_FROM_V_Dual-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result == "ASIN_FROM_C"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_asin_extraction.py -v`
Expected: FAIL — `extract_asin` does not exist (only `extract_asin_from_chapters_json`)

- [ ] **Step 3: Implement `extract_asin()` in metadata_utils.py**

Replace the `extract_asin_from_chapters_json` function (lines 288-308) in `library/scanner/metadata_utils.py` with:

```python
def _extract_asin_from_chapters_json(filepath: Path) -> Optional[str]:
    """Source 1: Extract ASIN from chapters.json alongside the audiobook."""
    chapters_path = filepath.parent / "chapters.json"
    if not chapters_path.exists():
        return None
    try:
        with open(chapters_path, "r") as f:
            chapters_data = json.load(f)
        content_metadata = chapters_data.get("content_metadata", {})
        content_reference = content_metadata.get("content_reference", {})
        return content_reference.get("asin")
    except (json.JSONDecodeError, IOError):
        return None


def _normalize_title_for_matching(title: str) -> str:
    """Strip punctuation and lowercase for fuzzy title matching."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _extract_asin_from_voucher(filepath: Path, sources_dir: Path) -> Optional[str]:
    """Source 2: Extract ASIN from .voucher files in Sources directory.

    Matches voucher to library book by checking if the book's title appears
    in the voucher filename (normalized comparison).
    """
    book_title = _normalize_title_for_matching(filepath.stem)
    if not book_title:
        return None

    try:
        voucher_files = list(sources_dir.glob("*.voucher"))
    except OSError:
        return None

    for voucher_path in voucher_files:
        voucher_title = _normalize_title_for_matching(
            voucher_path.stem.split("_", 1)[-1].rsplit("-", 1)[0].replace("_", " ")
        )
        if not voucher_title or book_title not in voucher_title:
            continue
        try:
            with open(voucher_path, "r") as f:
                voucher_data = json.load(f)
            asin = voucher_data.get("content_license", {}).get("asin")
            if not asin:
                # Fallback: nested content_metadata
                asin = (
                    voucher_data.get("content_license", {})
                    .get("content_metadata", {})
                    .get("content_reference", {})
                    .get("asin")
                )
            if asin:
                return asin
        except (json.JSONDecodeError, IOError):
            continue
    return None


_ASIN_FILENAME_RE = re.compile(r"^([B0-9][A-Z0-9]{9})_(.+)-AAX", re.IGNORECASE)


def _extract_asin_from_filename(filepath: Path, sources_dir: Path) -> Optional[str]:
    """Source 3: Extract ASIN from source filename pattern {ASIN}_Title-*.aaxc."""
    book_title = _normalize_title_for_matching(filepath.stem)
    if not book_title:
        return None

    try:
        source_files = list(sources_dir.glob("*.aaxc"))
    except OSError:
        return None

    for source_path in source_files:
        m = _ASIN_FILENAME_RE.match(source_path.name)
        if not m:
            continue
        candidate_asin = m.group(1)
        source_title = _normalize_title_for_matching(
            m.group(2).replace("_", " ")
        )
        if book_title in source_title or source_title in book_title:
            return candidate_asin
    return None


def extract_asin(
    filepath: Path, sources_dir: Optional[Path] = None
) -> Optional[str]:
    """Extract ASIN from any available source, checked in priority order.

    1. chapters.json (same directory as audiobook)
    2. .voucher file in Sources directory (if sources_dir provided)
    3. Source filename pattern in Sources directory (if sources_dir provided)
    """
    # Source 1: chapters.json (always available)
    asin = _extract_asin_from_chapters_json(filepath)
    if asin:
        return asin

    # Sources 2 & 3 require sources_dir
    if sources_dir and sources_dir.is_dir():
        asin = _extract_asin_from_voucher(filepath, sources_dir)
        if asin:
            return asin

        asin = _extract_asin_from_filename(filepath, sources_dir)
        if asin:
            return asin

    return None
```

Also update the call site in `_build_metadata_dict()` (line 379):

Change:
```python
    asin = extract_asin_from_chapters_json(filepath)
```
To:
```python
    asin = extract_asin(filepath)
```

Note: The scanner doesn't have access to the Sources directory at scan time (it scans the Library). The `sources_dir` parameter is used by the Local provider during enrichment, not during scanning. The scanner call continues to work with just `chapters.json`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_asin_extraction.py -v`
Expected: PASS — all tests green

- [ ] **Step 5: Run existing metadata tests to verify no regression**

Run: `cd library && python -m pytest tests/test_metadata_utils.py tests/test_asin.py -v`
Expected: PASS — existing tests unaffected

- [ ] **Step 6: Commit**

```bash
git add library/scanner/metadata_utils.py library/tests/test_asin_extraction.py
git commit -m "feat: multi-source ASIN extraction (chapters.json, voucher, filename)"
```

---

### Task 3: Provider Base Class

**Files:**
- Create: `library/scripts/enrichment/__init__.py` (empty for now — orchestrator comes in Task 7)
- Create: `library/scripts/enrichment/base.py`
- Create: `library/tests/test_enrichment_providers.py` (base class tests)

- [ ] **Step 1: Create package structure**

Create `library/scripts/enrichment/__init__.py`:

```python
"""Enrichment pipeline — multi-provider metadata enrichment for audiobooks."""
```

- [ ] **Step 2: Write the failing test for base class**

Create `library/tests/test_enrichment_providers.py`:

```python
"""Tests for enrichment provider chain.

All external API calls are mocked. Database operations use real
in-memory SQLite initialized from schema.sql.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment.base import EnrichmentProvider

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


def init_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def insert_test_book(db_path: Path, **overrides) -> int:
    defaults = {
        "title": "Test Book",
        "author": "Test Author",
        "file_path": "/test/book.opus",
    }
    defaults.update(overrides)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO audiobooks (title, author, file_path) VALUES (?, ?, ?)",
        (defaults["title"], defaults["author"], defaults["file_path"]),
    )
    book_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return book_id


class TestEnrichmentProviderBase:
    """Test the provider base class interface."""

    def test_base_class_has_name(self):
        class TestProvider(EnrichmentProvider):
            name = "test"

            def can_enrich(self, book: dict) -> bool:
                return True

            def enrich(self, book: dict) -> dict:
                return {"series": "Test Series"}

        p = TestProvider()
        assert p.name == "test"

    def test_base_class_requires_name(self):
        with pytest.raises(TypeError):
            # Abstract — can't instantiate without name
            EnrichmentProvider()

    def test_can_enrich_returns_bool(self):
        class AlwaysProvider(EnrichmentProvider):
            name = "always"

            def can_enrich(self, book: dict) -> bool:
                return True

            def enrich(self, book: dict) -> dict:
                return {}

        p = AlwaysProvider()
        assert p.can_enrich({"title": "X"}) is True

    def test_enrich_returns_dict(self):
        class FieldProvider(EnrichmentProvider):
            name = "field"

            def can_enrich(self, book: dict) -> bool:
                return True

            def enrich(self, book: dict) -> dict:
                return {"series": "Alpha", "series_sequence": 1.0}

        p = FieldProvider()
        result = p.enrich({"title": "X"})
        assert result == {"series": "Alpha", "series_sequence": 1.0}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestEnrichmentProviderBase -v`
Expected: FAIL — `scripts.enrichment.base` does not exist

- [ ] **Step 4: Implement the base class**

Create `library/scripts/enrichment/base.py`:

```python
"""Base class for enrichment providers."""

from abc import ABC, abstractmethod


class EnrichmentProvider(ABC):
    """Base class for metadata enrichment providers.

    Each provider fills fields that are currently empty/null.
    Later providers never overwrite earlier ones.
    """

    name: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "name", ""):
            raise TypeError(f"{cls.__name__} must define a non-empty 'name' attribute")

    def __init__(self):
        if not self.name:
            raise TypeError(
                f"{type(self).__name__} must define a non-empty 'name' attribute"
            )

    @abstractmethod
    def can_enrich(self, book: dict) -> bool:
        """Return True if this provider might have data for this book."""
        ...

    @abstractmethod
    def enrich(self, book: dict) -> dict:
        """Return dict of field_name → value for fields this provider can fill.

        Only return fields that have actual data. The orchestrator handles
        merge logic (only fills empty fields).
        """
        ...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestEnrichmentProviderBase -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add library/scripts/enrichment/__init__.py library/scripts/enrichment/base.py library/tests/test_enrichment_providers.py
git commit -m "feat: enrichment provider base class with ABC interface"
```

---

### Task 4: Local Provider

**Files:**
- Create: `library/scripts/enrichment/provider_local.py`
- Modify: `library/tests/test_enrichment_providers.py` (add local provider tests)

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_enrichment_providers.py`:

```python
import json
from unittest.mock import patch

from scripts.enrichment.provider_local import LocalProvider


class TestLocalProvider:
    """Test local file-based enrichment (no API calls)."""

    def test_can_enrich_always_true(self):
        p = LocalProvider()
        assert p.can_enrich({"title": "Any Book"}) is True

    def test_extracts_asin_from_voucher(self, tmp_path):
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B0D7JLGFST"}}
        (sources_dir / "B0D7JLGFST_Revenge_Prey-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

        p = LocalProvider(sources_dir=sources_dir)
        book = {
            "title": "Revenge Prey",
            "author": "Author Name",
            "file_path": "/lib/Author Name/Revenge Prey/Revenge Prey.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("asin") == "B0D7JLGFST"

    def test_extracts_series_from_tags(self):
        p = LocalProvider()
        book = {
            "title": "Book Title",
            "author": "Author",
            "file_path": "/lib/Author/Book Title/Book Title.opus",
            "asin": "B123456789",
            "series": "",
            "series_part": "3",
        }
        result = p.enrich(book)
        # series_part tag is returned as series_sequence
        assert result.get("series_sequence") == 3.0

    def test_parses_series_from_title_colon_format(self):
        p = LocalProvider()
        book = {
            "title": "Dark Tower: The Gunslinger, Book 1",
            "author": "Stephen King",
            "file_path": "/lib/King/DT/dt.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("series") == "The Gunslinger"
        assert result.get("series_sequence") == 1.0

    def test_parses_series_from_title_paren_format(self):
        p = LocalProvider()
        book = {
            "title": "Gone Girl (Amazing Amy Book 3)",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("series") == "Amazing Amy"
        assert result.get("series_sequence") == 3.0

    def test_parses_series_novel_format(self):
        p = LocalProvider()
        book = {
            "title": "Reckless: A Jack Reacher Novel",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "",
        }
        result = p.enrich(book)
        assert result.get("series") == "Jack Reacher"

    def test_skips_series_if_already_populated(self):
        p = LocalProvider()
        book = {
            "title": "Book: Some Series, Book 5",
            "author": "Author",
            "file_path": "/lib/a/b/c.opus",
            "asin": None,
            "series": "Existing Series",
        }
        result = p.enrich(book)
        assert "series" not in result  # should NOT overwrite

    def test_skips_asin_if_already_populated(self, tmp_path):
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B0NEWONE00"}}
        (sources_dir / "B0NEWONE00_Book-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

        p = LocalProvider(sources_dir=sources_dir)
        book = {
            "title": "Book",
            "author": "Author",
            "file_path": "/lib/a/Book/Book.opus",
            "asin": "B0EXISTING",
            "series": "",
        }
        result = p.enrich(book)
        assert "asin" not in result  # should NOT overwrite existing ASIN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestLocalProvider -v`
Expected: FAIL — `provider_local` does not exist

- [ ] **Step 3: Implement the local provider**

Create `library/scripts/enrichment/provider_local.py`:

```python
"""Local enrichment provider — extracts metadata from files without API calls.

Sources:
1. ASIN from .voucher files and source filenames
2. Series from audio tags (series, series-part)
3. Series from title parsing (regex patterns)
"""

import re
from pathlib import Path
from typing import Optional

from scripts.enrichment.base import EnrichmentProvider

# Import the multi-source ASIN extractor
from scanner.metadata_utils import extract_asin

# Title-based series parsing patterns (from populate_series_from_audible.py)
TITLE_SERIES_PATTERNS = [
    # "Title: Series, Book N" or "Title: Series #N"
    re.compile(
        r"^.+?:\s+(.+?),?\s+(?:Book|#)\s*(\d+(?:\.\d+)?)\s*(?:\(|$)",
        re.IGNORECASE,
    ),
    # "Title (Series Name Book N)"
    re.compile(
        r"\((.+?)\s+(?:Book|#)\s*(\d+(?:\.\d+)?)\)",
        re.IGNORECASE,
    ),
    # "Title: A Series Name Novel" (no number)
    re.compile(
        r"^.+?:\s+(?:A\s+)?(.+?)\s+Novel\s*(?:\(|$)",
        re.IGNORECASE,
    ),
]


def _parse_sequence(seq_str: str) -> Optional[float]:
    """Parse sequence string to a number."""
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


def _parse_series_from_title(title: str) -> tuple[str, Optional[float]]:
    """Extract series name and number from title string."""
    if not title:
        return ("", None)
    clean = re.sub(r"\s*\((Un)?abridged\)\s*$", "", title, flags=re.IGNORECASE)
    for pattern in TITLE_SERIES_PATTERNS:
        m = pattern.search(clean)
        if m:
            groups = m.groups()
            series_name = groups[0].strip().rstrip(",")
            seq = None
            if len(groups) > 1 and groups[1]:
                seq = _parse_sequence(groups[1])
            return (series_name, seq)
    return ("", None)


class LocalProvider(EnrichmentProvider):
    """Extract metadata from local files without any API calls.

    - ASIN from voucher files and source filenames
    - Series from embedded audio tags
    - Series from title parsing (regex fallback)
    """

    name = "local"

    def __init__(self, sources_dir: Optional[Path] = None):
        super().__init__()
        self.sources_dir = sources_dir

    def can_enrich(self, book: dict) -> bool:
        return True  # always runs — no external dependency

    def enrich(self, book: dict) -> dict:
        result = {}
        file_path = Path(book.get("file_path", ""))

        # ASIN recovery (only if not already set)
        if not book.get("asin"):
            asin = extract_asin(file_path, sources_dir=self.sources_dir)
            if asin:
                result["asin"] = asin

        # Series from tags (only if not already set)
        if not book.get("series"):
            # series_part tag → series_sequence
            series_part = book.get("series_part", "")
            if series_part:
                seq = _parse_sequence(series_part)
                if seq is not None:
                    result["series_sequence"] = seq

            # Title-based series parsing (last resort)
            series_name, seq = _parse_series_from_title(book.get("title", ""))
            if series_name:
                result["series"] = series_name
                if seq is not None and "series_sequence" not in result:
                    result["series_sequence"] = seq

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestLocalProvider -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add library/scripts/enrichment/provider_local.py library/tests/test_enrichment_providers.py
git commit -m "feat: local enrichment provider (ASIN recovery + title series parsing)"
```

---

### Task 5: Audible Provider

**Files:**
- Create: `library/scripts/enrichment/provider_audible.py`
- Modify: `library/tests/test_enrichment_providers.py` (add Audible provider tests)

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_enrichment_providers.py`:

```python
from unittest.mock import MagicMock

from scripts.enrichment.provider_audible import AudibleProvider

# Sample Audible API response for mocking
SAMPLE_AUDIBLE_PRODUCT = {
    "title": "Revenge Prey",
    "subtitle": "A Thriller",
    "language": "English",
    "format_type": "Unabridged",
    "runtime_length_min": 480,
    "release_date": "2025-01-15",
    "publisher_summary": "<p>Summary text</p>",
    "series": [
        {"title": "Prey Series", "sequence": "5"}
    ],
    "rating": {
        "overall_distribution": {"display_average_rating": 4.5, "num_ratings": 1200},
        "performance_distribution": {"display_average_rating": 4.7},
        "story_distribution": {"display_average_rating": 4.3},
        "num_reviews": 300,
    },
    "product_images": {"500": "https://m.media-amazon.com/images/I/image500.jpg"},
    "sample_url": "https://samples.audible.com/sample.mp3",
    "sku": "SK_1234",
    "is_adult_product": False,
    "category_ladders": [
        {
            "ladder": [
                {"name": "Mystery", "id": "cat1"},
                {"name": "Thriller", "id": "cat2"},
            ]
        }
    ],
    "editorial_reviews": ["Great book!", {"review": "Superb!", "source": "NYT"}],
    "authors": [{"name": "John Author", "asin": "AUTH123"}],
}


class TestAudibleProvider:
    """Test Audible catalog API enrichment provider."""

    def test_can_enrich_with_asin(self):
        p = AudibleProvider()
        assert p.can_enrich({"asin": "B0D7JLGFST"}) is True

    def test_cannot_enrich_without_asin(self):
        p = AudibleProvider()
        assert p.can_enrich({"asin": None}) is False
        assert p.can_enrich({"asin": ""}) is False
        assert p.can_enrich({}) is False

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_enriches_series_and_ratings(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT
        p = AudibleProvider()
        book = {"asin": "B0D7JLGFST", "series": "", "title": "Revenge Prey"}
        result = p.enrich(book)

        assert result["series"] == "Prey Series"
        assert result["series_sequence"] == 5.0
        assert result["rating_overall"] == 4.5
        assert result["subtitle"] == "A Thriller"
        assert result["language"] == "English"

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_skips_series_if_populated(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT
        p = AudibleProvider()
        book = {"asin": "B0D7JLGFST", "series": "Existing", "title": "X"}
        result = p.enrich(book)

        assert "series" not in result  # must not overwrite

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_returns_empty_on_api_failure(self, mock_fetch):
        mock_fetch.return_value = None
        p = AudibleProvider()
        book = {"asin": "B0D7JLGFST", "series": "", "title": "X"}
        result = p.enrich(book)
        assert result == {}

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_categories(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT
        p = AudibleProvider()
        book = {"asin": "B0D7JLGFST", "series": "", "title": "X"}
        result = p.enrich(book)

        assert "categories" in result
        assert len(result["categories"]) == 2
        assert result["categories"][0]["category_name"] == "Mystery"

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_editorial_reviews(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT
        p = AudibleProvider()
        book = {"asin": "B0D7JLGFST", "series": "", "title": "X"}
        result = p.enrich(book)

        assert "editorial_reviews" in result
        assert len(result["editorial_reviews"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestAudibleProvider -v`
Expected: FAIL — `provider_audible` does not exist

- [ ] **Step 3: Implement the Audible provider**

Create `library/scripts/enrichment/provider_audible.py`:

```python
"""Audible catalog API enrichment provider.

Queries the Audible product API for metadata including series, ratings,
categories, editorial reviews, and cover art URLs.

Refactored from enrich_single.py (lines 40-384).
"""

import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from scripts.enrichment.base import EnrichmentProvider

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

RATE_LIMIT_DELAY = 0.3  # seconds between API calls
_last_call_time = 0.0


def _rate_limit():
    """Enforce minimum delay between API calls."""
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    _last_call_time = time.monotonic()


def _fetch_audible_product(asin: str) -> Optional[dict]:
    """Query Audible API for full product data."""
    _rate_limit()
    url = (
        f"{AUDIBLE_API}/{asin}"
        f"?response_groups={ALL_RESPONSE_GROUPS}"
        f"&marketplace={MARKETPLACE}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("product")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            time.sleep(30)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    return data.get("product")
            except Exception:
                return None
        return None
    except (urllib.error.URLError, TimeoutError):
        return None


def _parse_sequence(seq_str: str) -> Optional[float]:
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


def _get_best_image_url(product: dict) -> Optional[str]:
    images = product.get("product_images", {})
    for size in ["2400", "1024", "500", "252"]:
        if size in images:
            return images[size]
    if images:
        return next(iter(images.values()))
    return None


class AudibleProvider(EnrichmentProvider):
    """Enrich from Audible catalog API using ASIN.

    Fills: series, series_sequence, subtitle, language, ratings,
    categories, editorial_reviews, cover URL, sample URL.
    """

    name = "audible"

    def can_enrich(self, book: dict) -> bool:
        return bool(book.get("asin"))

    def enrich(self, book: dict) -> dict:
        product = _fetch_audible_product(book["asin"])
        if not product:
            return {}

        result = {}
        existing_series = book.get("series", "")

        # Series — only if not already populated
        if not existing_series:
            series_list = product.get("series", [])
            if series_list:
                s = series_list[0]
                series_title = s.get("title")
                if series_title:
                    result["series"] = series_title
                seq = _parse_sequence(s.get("sequence", ""))
                if seq is not None:
                    result["series_sequence"] = seq

        # Core fields
        for field, key in [
            ("subtitle", "subtitle"),
            ("language", "language"),
            ("format_type", "format_type"),
            ("runtime_length_min", "runtime_length_min"),
            ("publisher_summary", "publisher_summary"),
            ("audible_sku", "sku"),
            ("sample_url", "sample_url"),
        ]:
            val = product.get(key)
            if val is not None:
                result[field] = val

        # Release date
        release_date = (
            product.get("release_date")
            or product.get("publication_datetime", "")[:10]
            or None
        )
        if release_date:
            result["release_date"] = release_date

        # Ratings
        rating_data = _extract_rating(product)
        for field, val in rating_data.items():
            if val is not None:
                result[field] = val

        # Image URL
        image_url = _get_best_image_url(product)
        if image_url:
            result["audible_image_url"] = image_url

        # Adult product flag
        if product.get("is_adult_product"):
            result["is_adult_product"] = 1

        # Content type
        content_type = product.get("content_type")
        if content_type:
            result["content_type"] = content_type

        # Categories (stored separately in audible_categories table)
        categories = _extract_categories(product)
        if categories:
            result["categories"] = categories

        # Editorial reviews (stored separately in editorial_reviews table)
        reviews = _extract_editorial_reviews(product)
        if reviews:
            result["editorial_reviews"] = reviews

        # Author ASINs (for cross-reference)
        author_asins = []
        for contributor in product.get("authors", []):
            a_asin = contributor.get("asin")
            a_name = contributor.get("name")
            if a_asin and a_name:
                author_asins.append({"name": a_name, "asin": a_asin})
        if author_asins:
            result["author_asins"] = author_asins

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestAudibleProvider -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add library/scripts/enrichment/provider_audible.py library/tests/test_enrichment_providers.py
git commit -m "feat: Audible enrichment provider (refactored from enrich_single.py)"
```

---

### Task 6: Google Books Provider

**Files:**
- Create: `library/scripts/enrichment/provider_google.py`
- Modify: `library/tests/test_enrichment_providers.py` (add Google Books tests)

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_enrichment_providers.py`:

```python
from scripts.enrichment.provider_google import GoogleBooksProvider

SAMPLE_GOOGLE_VOLUME = {
    "title": "Revenge Prey",
    "authors": ["John Author"],
    "publisher": "Big Publishing",
    "publishedDate": "2025-01-15",
    "description": "A thrilling tale of vengeance.",
    "industryIdentifiers": [
        {"type": "ISBN_13", "identifier": "9781234567890"}
    ],
    "language": "en",
    "pageCount": 320,
    "imageLinks": {"thumbnail": "https://books.google.com/thumb.jpg"},
    "categories": ["Fiction / Thrillers"],
}


class TestGoogleBooksProvider:
    """Test Google Books API enrichment provider."""

    def test_can_enrich_when_series_empty(self):
        p = GoogleBooksProvider()
        assert p.can_enrich({"series": "", "title": "X", "author": "Y"}) is True

    def test_cannot_enrich_when_series_populated(self):
        p = GoogleBooksProvider()
        assert p.can_enrich({"series": "Existing Series"}) is False

    def test_needs_title(self):
        p = GoogleBooksProvider()
        assert p.can_enrich({"series": "", "title": "", "author": "Y"}) is False

    @patch("scripts.enrichment.provider_google._query_google_books")
    def test_enriches_isbn_and_description(self, mock_query):
        mock_query.return_value = SAMPLE_GOOGLE_VOLUME
        p = GoogleBooksProvider()
        book = {"series": "", "title": "Revenge Prey", "author": "John Author",
                "isbn": None, "description": "", "language": None}
        result = p.enrich(book)

        assert result.get("isbn") == "9781234567890"
        assert "thrilling" in result.get("description", "").lower()
        assert result.get("language") == "English"

    @patch("scripts.enrichment.provider_google._query_google_books")
    def test_returns_empty_on_no_results(self, mock_query):
        mock_query.return_value = None
        p = GoogleBooksProvider()
        book = {"series": "", "title": "X", "author": "Y"}
        result = p.enrich(book)
        assert result == {}

    @patch("scripts.enrichment.provider_google._query_google_books")
    def test_does_not_overwrite_existing_fields(self, mock_query):
        mock_query.return_value = SAMPLE_GOOGLE_VOLUME
        p = GoogleBooksProvider()
        book = {"series": "", "title": "X", "author": "Y",
                "isbn": "EXISTING_ISBN", "description": "Existing desc",
                "language": "French"}
        result = p.enrich(book)

        assert "isbn" not in result
        assert "description" not in result
        assert "language" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestGoogleBooksProvider -v`
Expected: FAIL — `provider_google` does not exist

- [ ] **Step 3: Implement the Google Books provider**

Create `library/scripts/enrichment/provider_google.py`:

```python
"""Google Books API enrichment provider.

Searches by title + author for ISBN, description, language, categories.
Used as fallback when Audible doesn't provide series info.

Refactored from enrich_single.py (lines 182-445).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from scripts.enrichment.base import EnrichmentProvider

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
RATE_LIMIT_DELAY = 0.5
_last_call_time = 0.0

LANG_MAP = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "zh": "Chinese",
    "ko": "Korean", "ru": "Russian", "ar": "Arabic", "nl": "Dutch",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "pl": "Polish",
    "fi": "Finnish",
}


def _rate_limit():
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    _last_call_time = time.monotonic()


def _query_google_books(
    title: str, author: Optional[str] = None, isbn: Optional[str] = None
) -> Optional[dict]:
    """Query Google Books API. Returns volumeInfo dict or None."""
    _rate_limit()
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


class GoogleBooksProvider(EnrichmentProvider):
    """Enrich from Google Books API using title + author search.

    Fills: isbn, description, language, published_date, published_year,
    categories, publisher, page_count, thumbnail.
    """

    name = "google_books"

    def can_enrich(self, book: dict) -> bool:
        return not book.get("series") and bool(book.get("title"))

    def enrich(self, book: dict) -> dict:
        gb_data = _query_google_books(
            title=book.get("title", ""),
            author=book.get("author"),
            isbn=book.get("isbn"),
        )
        if not gb_data:
            return {}

        result = {}

        # ISBN
        if not book.get("isbn"):
            for ident in gb_data.get("industryIdentifiers", []):
                if ident.get("type") in ("ISBN_13", "ISBN_10"):
                    result["isbn"] = ident["identifier"]
                    break

        # Description
        if not book.get("description"):
            desc = gb_data.get("description")
            if desc:
                result["description"] = desc

        # Language (expand 2-letter code)
        if not book.get("language"):
            lang = gb_data.get("language")
            if lang:
                result["language"] = LANG_MAP.get(lang, lang) if len(lang) == 2 else lang

        # Published date/year
        if not book.get("published_year"):
            pub_date = gb_data.get("publishedDate")
            if pub_date:
                result["published_date"] = pub_date[:10]
                try:
                    result["published_year"] = int(pub_date[:4])
                except ValueError:
                    pass

        # Series from title parsing in Google's data
        # (Google Books sometimes has seriesInfo)
        series_info = gb_data.get("seriesInfo", {})
        if series_info and not book.get("series"):
            vol_series = series_info.get("volumeSeries", [])
            if vol_series:
                s = vol_series[0]
                series_title = s.get("seriesId", "")
                if series_title:
                    result["series"] = series_title

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestGoogleBooksProvider -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add library/scripts/enrichment/provider_google.py library/tests/test_enrichment_providers.py
git commit -m "feat: Google Books enrichment provider (ISBN, description, language fallback)"
```

---

### Task 7: Open Library Provider

**Files:**
- Create: `library/scripts/enrichment/provider_openlibrary.py`
- Modify: `library/tests/test_enrichment_providers.py` (add Open Library tests)

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_enrichment_providers.py`:

```python
from scripts.enrichment.provider_openlibrary import OpenLibraryProvider

SAMPLE_OL_DOC = {
    "title": "Revenge Prey",
    "author_name": ["John Author"],
    "isbn": ["9781234567890"],
    "first_publish_year": 2024,
    "subject": ["Fiction", "Thriller"],
    "series": ["Prey Series"],
}


class TestOpenLibraryProvider:
    """Test Open Library API enrichment provider."""

    def test_can_enrich_when_series_empty(self):
        p = OpenLibraryProvider()
        assert p.can_enrich({"series": "", "title": "X"}) is True

    def test_cannot_enrich_when_series_populated(self):
        p = OpenLibraryProvider()
        assert p.can_enrich({"series": "Has Series"}) is False

    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_enriches_series_and_isbn(self, mock_query):
        mock_query.return_value = SAMPLE_OL_DOC
        p = OpenLibraryProvider()
        book = {"series": "", "title": "Revenge Prey", "author": "John Author",
                "isbn": None}
        result = p.enrich(book)

        assert result.get("series") == "Prey Series"
        assert result.get("isbn") == "9781234567890"
        assert result.get("first_publish_year") == 2024

    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_returns_empty_on_no_results(self, mock_query):
        mock_query.return_value = None
        p = OpenLibraryProvider()
        book = {"series": "", "title": "X", "author": "Y"}
        result = p.enrich(book)
        assert result == {}

    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_does_not_overwrite_isbn(self, mock_query):
        mock_query.return_value = SAMPLE_OL_DOC
        p = OpenLibraryProvider()
        book = {"series": "", "title": "X", "author": "Y", "isbn": "EXISTING"}
        result = p.enrich(book)
        assert "isbn" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestOpenLibraryProvider -v`
Expected: FAIL — `provider_openlibrary` does not exist

- [ ] **Step 3: Implement the Open Library provider**

Create `library/scripts/enrichment/provider_openlibrary.py`:

```python
"""Open Library API enrichment provider.

Searches by title + author for series, ISBN, subjects.
Last-resort fallback when Audible and Google Books don't have series info.

Refactored from enrich_single.py (lines 207-474).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from scripts.enrichment.base import EnrichmentProvider

OPENLIBRARY_API = "https://openlibrary.org"
RATE_LIMIT_DELAY = 1.0  # Open Library is rate-sensitive
_last_call_time = 0.0


def _rate_limit():
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    _last_call_time = time.monotonic()


def _query_openlibrary(title: str, author: Optional[str] = None) -> Optional[dict]:
    """Query Open Library search API. Returns first result doc or None."""
    _rate_limit()
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


class OpenLibraryProvider(EnrichmentProvider):
    """Enrich from Open Library search API using title + author.

    Fills: series, isbn, first_publish_year, subjects.
    """

    name = "openlibrary"

    def can_enrich(self, book: dict) -> bool:
        return not book.get("series") and bool(book.get("title"))

    def enrich(self, book: dict) -> dict:
        ol_data = _query_openlibrary(
            title=book.get("title", ""),
            author=book.get("author"),
        )
        if not ol_data:
            return {}

        result = {}

        # Series
        series_list = ol_data.get("series", [])
        if series_list and not book.get("series"):
            result["series"] = series_list[0]

        # ISBN
        if not book.get("isbn"):
            isbns = ol_data.get("isbn", [])
            if isbns:
                result["isbn"] = isbns[0]

        # First publish year
        fpy = ol_data.get("first_publish_year")
        if fpy and not book.get("published_year"):
            result["first_publish_year"] = fpy

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_enrichment_providers.py::TestOpenLibraryProvider -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add library/scripts/enrichment/provider_openlibrary.py library/tests/test_enrichment_providers.py
git commit -m "feat: Open Library enrichment provider (series, ISBN last-resort fallback)"
```

---

### Task 8: Enrichment Orchestrator

**Files:**
- Modify: `library/scripts/enrichment/__init__.py` (replace stub with orchestrator)
- Create: `library/tests/test_enrichment_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

Create `library/tests/test_enrichment_orchestrator.py`:

```python
"""Tests for the enrichment orchestrator (chain runner)."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment import enrich_book
from scripts.enrichment.base import EnrichmentProvider

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def _insert_book(db_path: Path, **overrides) -> int:
    defaults = {
        "title": "Test Book",
        "author": "Test Author",
        "file_path": "/test/book.opus",
    }
    defaults.update(overrides)
    conn = sqlite3.connect(db_path)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    cursor = conn.execute(
        f"INSERT INTO audiobooks ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    book_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return book_id


class FakeProvider(EnrichmentProvider):
    """Test provider that returns configurable fields."""

    name = "fake"

    def __init__(self, fields: dict, can: bool = True):
        super().__init__()
        self._fields = fields
        self._can = can

    def can_enrich(self, book: dict) -> bool:
        return self._can

    def enrich(self, book: dict) -> dict:
        return dict(self._fields)


class TestEnrichBookOrchestrator:
    """Test the enrich_book chain orchestrator."""

    def test_fills_empty_fields(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)
        book_id = _insert_book(db_path)

        providers = [FakeProvider({"series": "Alpha Series", "series_sequence": 1.0})]
        result = enrich_book(book_id, db_path, providers=providers)

        assert result["fields_updated"] > 0
        assert result["enrichment_source"] == "fake"

        # Verify DB was updated
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT series, series_sequence FROM audiobooks WHERE id = ?", (book_id,))
        row = cursor.fetchone()
        conn.close()
        assert row[0] == "Alpha Series"
        assert row[1] == 1.0

    def test_later_provider_does_not_overwrite(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)
        book_id = _insert_book(db_path)

        p1 = FakeProvider({"series": "First", "series_sequence": 1.0})
        p1.name = "first"
        p2 = FakeProvider({"series": "Second", "series_sequence": 2.0})
        p2.name = "second"
        result = enrich_book(book_id, db_path, providers=[p1, p2])

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT series FROM audiobooks WHERE id = ?", (book_id,))
        assert cursor.fetchone()[0] == "First"
        conn.close()

    def test_skips_provider_that_cannot_enrich(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)
        book_id = _insert_book(db_path)

        p1 = FakeProvider({"series": "Skipped"}, can=False)
        p2 = FakeProvider({"series": "Used"})
        p2.name = "used"
        result = enrich_book(book_id, db_path, providers=[p1, p2])

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT series FROM audiobooks WHERE id = ?", (book_id,))
        assert cursor.fetchone()[0] == "Used"
        conn.close()

    def test_sets_enrichment_source(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)
        book_id = _insert_book(db_path)

        providers = [FakeProvider({"series": "Alpha"})]
        enrich_book(book_id, db_path, providers=providers)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT enrichment_source FROM audiobooks WHERE id = ?", (book_id,)
        )
        assert cursor.fetchone()[0] == "fake"
        conn.close()

    def test_sets_audible_enriched_at(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)
        book_id = _insert_book(db_path)

        providers = [FakeProvider({"series": "Alpha"})]
        enrich_book(book_id, db_path, providers=providers)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT audible_enriched_at FROM audiobooks WHERE id = ?", (book_id,)
        )
        assert cursor.fetchone()[0] is not None
        conn.close()

    def test_returns_error_for_missing_book(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)

        result = enrich_book(9999, db_path, providers=[])
        assert result["errors"]

    def test_handles_provider_exception_gracefully(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_db(db_path)
        book_id = _insert_book(db_path)

        class CrashProvider(EnrichmentProvider):
            name = "crash"
            def can_enrich(self, book): return True
            def enrich(self, book): raise RuntimeError("API down")

        p_good = FakeProvider({"series": "Fallback"})
        p_good.name = "good"
        result = enrich_book(book_id, db_path, providers=[CrashProvider(), p_good])

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT series FROM audiobooks WHERE id = ?", (book_id,))
        assert cursor.fetchone()[0] == "Fallback"
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_enrichment_orchestrator.py -v`
Expected: FAIL — `enrich_book` not importable from `scripts.enrichment`

- [ ] **Step 3: Implement the orchestrator**

Replace `library/scripts/enrichment/__init__.py` with:

```python
"""Enrichment pipeline — multi-provider metadata enrichment for audiobooks.

Usage:
    from scripts.enrichment import enrich_book
    result = enrich_book(book_id=42, db_path=Path("/path/to/db"))
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scripts.enrichment.base import EnrichmentProvider

# Columns that can be enriched (written to audiobooks table directly)
ENRICHABLE_COLUMNS = {
    "asin", "series", "series_sequence", "subtitle", "language", "format_type",
    "runtime_length_min", "release_date", "publisher_summary", "rating_overall",
    "rating_performance", "rating_story", "num_ratings", "num_reviews",
    "audible_image_url", "sample_url", "audible_sku", "is_adult_product",
    "merchandising_summary", "content_type", "isbn", "description",
    "published_date", "published_year", "enrichment_source",
}

# Columns to load from DB for provider input
BOOK_QUERY_COLUMNS = (
    "id, title, author, asin, isbn, series, series_sequence, "
    "description, language, published_year, file_path, "
    "audible_enriched_at, enrichment_source"
)


def _default_providers(sources_dir: Optional[Path] = None) -> list[EnrichmentProvider]:
    """Create the default provider chain."""
    from scripts.enrichment.provider_audible import AudibleProvider
    from scripts.enrichment.provider_google import GoogleBooksProvider
    from scripts.enrichment.provider_local import LocalProvider
    from scripts.enrichment.provider_openlibrary import OpenLibraryProvider

    return [
        LocalProvider(sources_dir=sources_dir),
        AudibleProvider(),
        GoogleBooksProvider(),
        OpenLibraryProvider(),
    ]


def _make_result() -> dict:
    return {
        "fields_updated": 0,
        "enrichment_source": None,
        "providers_run": [],
        "errors": [],
    }


def enrich_book(
    book_id: int,
    db_path: Path,
    providers: Optional[list[EnrichmentProvider]] = None,
    sources_dir: Optional[Path] = None,
    quiet: bool = False,
) -> dict:
    """Run enrichment chain for a single book.

    Each provider fills fields that are currently empty/null.
    Later providers never overwrite earlier ones.
    The chain records which provider first enriched the book.
    """
    result = _make_result()

    if not db_path or not db_path.exists():
        result["errors"].append(f"Database not found: {db_path}")
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"SELECT {BOOK_QUERY_COLUMNS} FROM audiobooks WHERE id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        if not row:
            result["errors"].append(f"Book ID {book_id} not found")
            return result

        book = dict(row)
        if not quiet:
            print(f"  Enriching: {book['title']} (ID {book_id})")

        if providers is None:
            providers = _default_providers(sources_dir=sources_dir)

        # Accumulated updates — only fill empty fields
        merged_updates = {}
        first_enriching_provider = None
        # Side-table data (categories, reviews, author_asins)
        side_data = {}

        for provider in providers:
            try:
                if not provider.can_enrich(book):
                    continue

                fields = provider.enrich(book)
                result["providers_run"].append(provider.name)

                if not fields:
                    continue

                # Merge: only fill fields that are still empty
                for key, val in fields.items():
                    if key in ("categories", "editorial_reviews", "author_asins"):
                        # Side-table data — first provider wins
                        if key not in side_data:
                            side_data[key] = val
                        continue
                    if key in ENRICHABLE_COLUMNS and key not in merged_updates:
                        # Only fill if the DB value is also empty
                        current_val = book.get(key)
                        if not current_val and current_val != 0:
                            merged_updates[key] = val
                            # Update the in-memory book for subsequent providers
                            book[key] = val

                if not first_enriching_provider and fields:
                    first_enriching_provider = provider.name

            except Exception as e:
                if not quiet:
                    print(
                        f"    Warning: {provider.name} failed: {e}",
                        file=sys.stderr,
                    )
                result["errors"].append(f"{provider.name}: {e}")
                continue

        # Write merged updates to DB
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if merged_updates or first_enriching_provider:
            merged_updates["audible_enriched_at"] = now
            if first_enriching_provider:
                merged_updates["enrichment_source"] = first_enriching_provider

            update_cols = []
            update_params = []
            for col, val in merged_updates.items():
                if col in ENRICHABLE_COLUMNS or col == "audible_enriched_at":
                    update_cols.append(f"{col} = ?")
                    update_params.append(val)

            if update_cols:
                update_params.append(book_id)
                sql = f"UPDATE audiobooks SET {', '.join(update_cols)} WHERE id = ?"  # nosec B608
                cursor.execute(sql, update_params)
                result["fields_updated"] = len(update_cols) - 1  # exclude timestamp

        # Write side-table data
        if "categories" in side_data:
            cursor.execute(
                "DELETE FROM audible_categories WHERE audiobook_id = ?",
                (book_id,),
            )
            for cat in side_data["categories"]:
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

        if "editorial_reviews" in side_data:
            cursor.execute(
                "DELETE FROM editorial_reviews WHERE audiobook_id = ?",
                (book_id,),
            )
            for review in side_data["editorial_reviews"]:
                cursor.execute(
                    "INSERT INTO editorial_reviews "
                    "(audiobook_id, review_text, source) VALUES (?, ?, ?)",
                    (book_id, review["review_text"], review["source"]),
                )

        if "author_asins" in side_data:
            for auth in side_data["author_asins"]:
                cursor.execute(
                    "UPDATE authors SET asin = ? WHERE name = ? "
                    "AND (asin IS NULL OR asin = '')",
                    (auth["asin"], auth["name"]),
                )

        conn.commit()
        result["enrichment_source"] = first_enriching_provider

        if not quiet:
            print(f"    Total: {result['fields_updated']} fields enriched")
            print(f"    Providers: {', '.join(result['providers_run'])}")

    finally:
        conn.close()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_enrichment_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Run all enrichment tests together**

Run: `cd library && python -m pytest tests/test_enrichment_schema.py tests/test_asin_extraction.py tests/test_enrichment_providers.py tests/test_enrichment_orchestrator.py -v`
Expected: PASS — all tests green

- [ ] **Step 6: Commit**

```bash
git add library/scripts/enrichment/__init__.py library/tests/test_enrichment_orchestrator.py
git commit -m "feat: enrichment chain orchestrator with merge-only-empty semantics"
```

---

### Task 9: Backward-Compatible Wrapper in `enrich_single.py`

**Files:**
- Modify: `library/scripts/enrich_single.py:645-709`
- Modify: `library/scanner/add_new_audiobooks.py:45-54`

- [ ] **Step 1: Write the failing test**

Append to `library/tests/test_enrichment_orchestrator.py`:

```python
class TestBackwardCompatibility:
    """enrich_single.enrich_book() must still work with same signature."""

    def test_enrich_single_delegates_to_orchestrator(self, tmp_path):
        """The old import path must work and call the new orchestrator."""
        from scripts.enrich_single import enrich_book as legacy_enrich

        db_path = tmp_path / "compat.db"
        _init_db(db_path)
        book_id = _insert_book(db_path, asin="B0TESTCOMPAT")

        # Mock the Audible API to avoid real calls
        with patch("scripts.enrichment.provider_audible._fetch_audible_product") as mock:
            mock.return_value = None
            result = legacy_enrich(book_id=book_id, db_path=db_path, quiet=True)

        assert isinstance(result, dict)
        assert "fields_updated" in result or "errors" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd library && python -m pytest tests/test_enrichment_orchestrator.py::TestBackwardCompatibility -v`
Expected: FAIL — `enrich_single.enrich_book` still uses old implementation

- [ ] **Step 3: Replace enrich_single.py's main function with delegation**

In `library/scripts/enrich_single.py`, replace the `enrich_book` function (lines 645-709) with:

```python
def enrich_book(
    book_id: int,
    db_path: Path | None = None,
    quiet: bool = False,
) -> dict:
    """Enrich a single audiobook by database ID.

    Thin wrapper for backward compatibility — delegates to the new
    enrichment chain orchestrator.
    """
    from scripts.enrichment import enrich_book as chain_enrich

    resolved_db = _resolve_enrich_db_path(db_path)
    if resolved_db is None:
        return {"fields_updated": 0, "errors": ["No database path"]}

    result = chain_enrich(
        book_id=book_id,
        db_path=resolved_db,
        quiet=quiet,
    )

    # Map new result format to legacy format for callers that expect it
    legacy = {
        "audible_enriched": result.get("enrichment_source") == "audible",
        "isbn_enriched": result.get("enrichment_source") in ("google_books", "openlibrary"),
        "fields_updated": result.get("fields_updated", 0),
        "errors": result.get("errors", []),
    }
    return legacy
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd library && python -m pytest tests/test_enrichment_orchestrator.py::TestBackwardCompatibility -v`
Expected: PASS

- [ ] **Step 5: Run existing enrich_single tests to verify no regression**

Run: `cd library && python -m pytest tests/test_enrich_single.py -v`
Expected: PASS — existing tests should still work (they mock at the API level)

- [ ] **Step 6: Commit**

```bash
git add library/scripts/enrich_single.py
git commit -m "refactor: enrich_single.py delegates to new enrichment chain orchestrator"
```

---

### Task 10: Backfill Script

**Files:**
- Create: `library/scripts/backfill_enrichment.py`
- Create: `library/tests/test_backfill_enrichment.py`

- [ ] **Step 1: Write the failing tests**

Create `library/tests/test_backfill_enrichment.py`:

```python
"""Tests for the enrichment backfill script."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def _insert_books(db_path: Path, count: int) -> list[int]:
    conn = sqlite3.connect(db_path)
    ids = []
    for i in range(count):
        cursor = conn.execute(
            "INSERT INTO audiobooks (title, author, file_path) VALUES (?, ?, ?)",
            (f"Book {i}", f"Author {i}", f"/test/book_{i}.opus"),
        )
        ids.append(cursor.lastrowid)
    conn.commit()
    conn.close()
    return ids


class TestBackfillAsinRecovery:
    """Phase 1: ASIN recovery from voucher files."""

    def test_recovers_asin_from_voucher(self, tmp_path):
        from scripts.backfill_enrichment import recover_asins

        db_path = tmp_path / "test.db"
        _init_db(db_path)

        # Insert book with no ASIN
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO audiobooks (title, author, file_path) VALUES (?, ?, ?)",
            ("Cool Book", "Author", "/lib/Author/Cool Book/Cool Book.opus"),
        )
        conn.commit()
        conn.close()

        # Create voucher
        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "B0VOUCHER1"}}
        (sources_dir / "B0VOUCHER1_Cool_Book-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

        count = recover_asins(db_path, sources_dir)
        assert count >= 1

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT asin FROM audiobooks WHERE title = 'Cool Book'")
        assert cursor.fetchone()[0] == "B0VOUCHER1"
        conn.close()

    def test_skips_books_with_existing_asin(self, tmp_path):
        from scripts.backfill_enrichment import recover_asins

        db_path = tmp_path / "test.db"
        _init_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO audiobooks (title, author, file_path, asin) VALUES (?, ?, ?, ?)",
            ("Has ASIN", "Author", "/lib/a/b.opus", "B0EXISTING"),
        )
        conn.commit()
        conn.close()

        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()

        count = recover_asins(db_path, sources_dir)
        assert count == 0


class TestBackfillEnrichmentPhase:
    """Phase 2: Enrichment chain for un-enriched books."""

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    @patch("scripts.enrichment.provider_google._query_google_books")
    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_enriches_unenriched_books(self, mock_ol, mock_gb, mock_aud, tmp_path):
        from scripts.backfill_enrichment import run_enrichment_pass

        mock_aud.return_value = None
        mock_gb.return_value = None
        mock_ol.return_value = None

        db_path = tmp_path / "test.db"
        _init_db(db_path)
        _insert_books(db_path, 3)

        stats = run_enrichment_pass(db_path, limit=10)
        assert stats["total"] == 3
        assert stats["processed"] == 3

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    @patch("scripts.enrichment.provider_google._query_google_books")
    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_skips_already_enriched(self, mock_ol, mock_gb, mock_aud, tmp_path):
        from scripts.backfill_enrichment import run_enrichment_pass

        mock_aud.return_value = None
        mock_gb.return_value = None
        mock_ol.return_value = None

        db_path = tmp_path / "test.db"
        _init_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO audiobooks (title, author, file_path, audible_enriched_at) "
            "VALUES (?, ?, ?, ?)",
            ("Done", "Author", "/test/done.opus", "2026-01-01 00:00:00"),
        )
        conn.commit()
        conn.close()

        stats = run_enrichment_pass(db_path, limit=10)
        assert stats["total"] == 0

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    @patch("scripts.enrichment.provider_google._query_google_books")
    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_respects_limit(self, mock_ol, mock_gb, mock_aud, tmp_path):
        from scripts.backfill_enrichment import run_enrichment_pass

        mock_aud.return_value = None
        mock_gb.return_value = None
        mock_ol.return_value = None

        db_path = tmp_path / "test.db"
        _init_db(db_path)
        _insert_books(db_path, 10)

        stats = run_enrichment_pass(db_path, limit=3)
        assert stats["processed"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd library && python -m pytest tests/test_backfill_enrichment.py -v`
Expected: FAIL — `backfill_enrichment` does not exist

- [ ] **Step 3: Implement the backfill script**

Create `library/scripts/backfill_enrichment.py`:

```python
#!/usr/bin/env python3
"""
Enrichment Backfill Script
============================
Phase 1: Recover ASINs from voucher files (no API calls)
Phase 2: Run enrichment chain on all un-enriched books

Usage:
    python3 backfill_enrichment.py --db /path/to/db
    python3 backfill_enrichment.py --db /path/to/db --asin-only
    python3 backfill_enrichment.py --db /path/to/db --dry-run
    python3 backfill_enrichment.py --db /path/to/db --limit 10
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from library.config import DATABASE_PATH, AUDIOBOOKS_SOURCES
except ImportError:
    try:
        from config import DATABASE_PATH
    except ImportError:
        DATABASE_PATH = None
    AUDIOBOOKS_SOURCES = None


def _normalize_title(title: str) -> str:
    """Strip punctuation and lowercase for fuzzy matching."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def recover_asins(
    db_path: Path,
    sources_dir: Path,
    dry_run: bool = False,
) -> int:
    """Phase 1: Recover ASINs from voucher files and source filenames.

    Returns the number of ASINs recovered.
    """
    if not sources_dir or not sources_dir.is_dir():
        print(f"Sources directory not found: {sources_dir}")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get books missing ASIN
    cursor = conn.execute(
        "SELECT id, title, file_path FROM audiobooks "
        "WHERE asin IS NULL OR asin = ''"
    )
    books = cursor.fetchall()
    if not books:
        print("No books with missing ASIN.")
        conn.close()
        return 0

    print(f"Found {len(books)} books with missing ASIN.")

    # Build voucher index: normalized_title → (asin, voucher_path)
    asin_re = re.compile(r"^([B0-9][A-Z0-9]{9})_(.+)-AAX", re.IGNORECASE)
    voucher_index = {}
    for vf in sources_dir.glob("*.voucher"):
        m = asin_re.match(vf.stem + "-AAX")  # stem doesn't have extension
        if not m:
            # Try parsing the full name
            m = asin_re.match(vf.name.replace(".voucher", "-AAX"))
        if m:
            asin_candidate = m.group(1)
            title_part = _normalize_title(m.group(2).replace("_", " "))
            voucher_index[title_part] = asin_candidate
        else:
            # Try reading the voucher JSON
            try:
                with open(vf) as f:
                    data = json.load(f)
                asin_candidate = data.get("content_license", {}).get("asin")
                if asin_candidate:
                    title_part = _normalize_title(
                        vf.stem.split("_", 1)[-1].rsplit("-", 1)[0].replace("_", " ")
                    )
                    if title_part:
                        voucher_index[title_part] = asin_candidate
            except (json.JSONDecodeError, IOError):
                continue

    # Also index .aaxc filenames
    for af in sources_dir.glob("*.aaxc"):
        m = asin_re.match(af.name)
        if m:
            asin_candidate = m.group(1)
            title_part = _normalize_title(m.group(2).replace("_", " "))
            if title_part not in voucher_index:
                voucher_index[title_part] = asin_candidate

    if not voucher_index:
        print("No voucher/source files found with ASINs.")
        conn.close()
        return 0

    print(f"Built index of {len(voucher_index)} source ASINs.")

    recovered = 0
    for book in books:
        book_title = _normalize_title(book["title"])
        if not book_title:
            continue

        # Try exact match first, then substring
        asin = voucher_index.get(book_title)
        if not asin:
            for idx_title, idx_asin in voucher_index.items():
                if book_title in idx_title or idx_title in book_title:
                    asin = idx_asin
                    break

        if asin:
            if dry_run:
                print(f"  WOULD SET: {book['title']} → ASIN {asin}")
            else:
                conn.execute(
                    "UPDATE audiobooks SET asin = ? WHERE id = ?",
                    (asin, book["id"]),
                )
                print(f"  SET: {book['title']} → ASIN {asin}")
            recovered += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"Recovered {recovered} ASINs from source files.")
    return recovered


def run_enrichment_pass(
    db_path: Path,
    sources_dir: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Phase 2: Run enrichment chain on all un-enriched books.

    Returns stats dict with total, processed, enriched, skipped counts.
    """
    from scripts.enrichment import enrich_book

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = (
        "SELECT id, title FROM audiobooks "
        "WHERE audible_enriched_at IS NULL "
        "ORDER BY id"
    )
    if limit:
        query += f" LIMIT {limit}"

    cursor = conn.execute(query)
    books = cursor.fetchall()
    conn.close()

    total = len(books)
    stats = {"total": total, "processed": 0, "enriched": 0, "skipped": 0, "errors": 0}

    if not books:
        print("No un-enriched books found.")
        return stats

    print(f"Enriching {total} books...")

    for i, book in enumerate(books, 1):
        if dry_run:
            print(f"  [{i}/{total}] WOULD ENRICH: {book['title']}")
            stats["processed"] += 1
            continue

        print(f"  [{i}/{total}] {book['title']}")
        try:
            result = enrich_book(
                book_id=book["id"],
                db_path=db_path,
                sources_dir=sources_dir,
                quiet=True,
            )
            stats["processed"] += 1
            if result.get("fields_updated", 0) > 0:
                stats["enriched"] += 1
            else:
                stats["skipped"] += 1
            if result.get("errors"):
                stats["errors"] += 1
        except Exception as e:
            print(f"    Error: {e}", file=sys.stderr)
            stats["errors"] += 1
            stats["processed"] += 1

    print(
        f"\nEnrichment complete: {stats['enriched']}/{stats['processed']} enriched, "
        f"{stats['skipped']} skipped, {stats['errors']} errors"
    )
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill enrichment for existing audiobook library"
    )
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database")
    parser.add_argument(
        "--sources", type=str, default=None, help="Path to Sources directory"
    )
    parser.add_argument(
        "--asin-only", action="store_true", help="Only recover ASINs (no API calls)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit enrichment to N books"
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DATABASE_PATH
    if not db_path:
        print("Error: --db required or DATABASE_PATH must be configured", file=sys.stderr)
        sys.exit(1)
    db_path = Path(db_path)

    sources_dir = Path(args.sources) if args.sources else None
    if sources_dir is None and AUDIOBOOKS_SOURCES:
        sources_dir = Path(AUDIOBOOKS_SOURCES)

    # Phase 1: ASIN Recovery
    if sources_dir:
        print("=" * 50)
        print("PHASE 1: ASIN Recovery")
        print("=" * 50)
        recover_asins(db_path, sources_dir, dry_run=args.dry_run)
        print()

    if args.asin_only:
        return

    # Phase 2: Enrichment Chain
    print("=" * 50)
    print("PHASE 2: Enrichment Chain")
    print("=" * 50)
    run_enrichment_pass(
        db_path,
        sources_dir=sources_dir,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd library && python -m pytest tests/test_backfill_enrichment.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add library/scripts/backfill_enrichment.py library/tests/test_backfill_enrichment.py
git commit -m "feat: enrichment backfill script (ASIN recovery + bulk enrichment)"
```

---

### Task 11: Systemd Timer and Service

**Files:**
- Create: `systemd/audiobook-enrichment.timer`
- Create: `systemd/audiobook-enrichment.service`

- [ ] **Step 1: Create the timer unit**

Create `systemd/audiobook-enrichment.timer`:

```ini
[Unit]
Description=Nightly audiobook metadata enrichment
After=network.target

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Create the service unit**

Create `systemd/audiobook-enrichment.service`:

```ini
[Unit]
Description=Audiobook metadata enrichment (backfill)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=audiobooks
Group=audiobooks
EnvironmentFile=-/etc/audiobooks/audiobooks.conf
ExecStart=/bin/bash -c '${AUDIOBOOKS_HOME}/venv/bin/python ${AUDIOBOOKS_HOME}/library/scripts/backfill_enrichment.py --db ${AUDIOBOOKS_DATABASE} --sources ${AUDIOBOOKS_SOURCES}'
TimeoutStartSec=3600
StandardOutput=journal
StandardError=journal

# Security hardening
ProtectSystem=strict
ReadWritePaths=/var/lib/audiobooks
ReadOnlyPaths=/srv/audiobooks
NoNewPrivileges=true
PrivateTmp=true
```

- [ ] **Step 3: Verify unit files parse correctly**

Run: `systemd-analyze verify systemd/audiobook-enrichment.timer systemd/audiobook-enrichment.service 2>&1 || true`
Expected: No critical errors (warnings about missing EnvironmentFile are expected in dev)

- [ ] **Step 4: Commit**

```bash
git add systemd/audiobook-enrichment.timer systemd/audiobook-enrichment.service
git commit -m "feat: systemd timer for nightly enrichment backfill"
```

---

### Task 12: Install Script Integration

**Files:**
- Modify: `install.sh` (schema migration + timer enablement)

- [ ] **Step 1: Add schema migration after DB initialization**

In `install.sh`, after the database initialization block (around line 1439 `echo "  Created: $db_file"`), add a schema migration section. Find the block that ends with `fi` after the `schema.sql` application and add after it:

```bash
    # Apply schema migrations for existing databases
    # Add enrichment_source column if missing (idempotent)
    if [[ -f "$db_file" ]]; then
        local has_col
        has_col=$(sqlite3 "$db_file" "PRAGMA table_info(audiobooks)" | grep -c "enrichment_source" || true)
        if [[ "$has_col" -eq 0 ]]; then
            sudo -u audiobooks sqlite3 "$db_file" "ALTER TABLE audiobooks ADD COLUMN enrichment_source TEXT"
            echo "  Migrated: added enrichment_source column"
        fi
    fi
```

- [ ] **Step 2: Add timer enablement in the systemd section**

Find where other timers are enabled (search for `enable.*timer` or the downloader timer). Add alongside existing timer enablement:

```bash
    # Enable enrichment timer
    if [[ -f "${APP_DIR}/systemd/audiobook-enrichment.timer" ]]; then
        sudo cp "${APP_DIR}/systemd/audiobook-enrichment.timer" /etc/systemd/system/
        sudo cp "${APP_DIR}/systemd/audiobook-enrichment.service" /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable audiobook-enrichment.timer
        echo "  Enabled: audiobook-enrichment.timer (nightly at 03:00)"
    fi
```

- [ ] **Step 3: Verify install.sh syntax**

Run: `bash -n install.sh`
Expected: No syntax errors

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat: install.sh schema migration + enrichment timer enablement"
```

---

### Task 13: Integration Test — Full Chain End-to-End

**Files:**
- Modify: `library/tests/test_enrichment_orchestrator.py` (add integration test)

- [ ] **Step 1: Write the integration test**

Append to `library/tests/test_enrichment_orchestrator.py`:

```python
class TestFullChainIntegration:
    """Integration test: book with no ASIN goes through full provider chain."""

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    @patch("scripts.enrichment.provider_google._query_google_books")
    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_non_audible_book_gets_enriched_via_fallbacks(
        self, mock_ol, mock_gb, mock_aud, tmp_path
    ):
        """Book without ASIN skips Audible, falls back to Google Books."""
        mock_aud.return_value = None  # should not be called (no ASIN)
        mock_gb.return_value = {
            "title": "LibriVox Classic",
            "description": "A public domain recording.",
            "language": "en",
            "industryIdentifiers": [
                {"type": "ISBN_13", "identifier": "9780000000001"}
            ],
        }
        mock_ol.return_value = None

        db_path = tmp_path / "integration.db"
        _init_db(db_path)
        book_id = _insert_book(
            db_path,
            title="LibriVox Classic",
            author="Public Domain",
            file_path="/lib/PD/Classic/Classic.opus",
        )

        result = enrich_book(book_id, db_path, quiet=True)

        # Audible was skipped (no ASIN), Google Books provided data
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT isbn, description, language, enrichment_source, "
            "audible_enriched_at FROM audiobooks WHERE id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        conn.close()

        assert row[0] == "9780000000001"  # isbn
        assert "public domain" in row[1].lower()  # description
        assert row[2] == "English"  # language
        assert row[3] == "google_books"  # enrichment_source
        assert row[4] is not None  # audible_enriched_at (timestamp set)

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    @patch("scripts.enrichment.provider_google._query_google_books")
    @patch("scripts.enrichment.provider_openlibrary._query_openlibrary")
    def test_audible_book_enriched_with_series(
        self, mock_ol, mock_gb, mock_aud, tmp_path
    ):
        """Book with ASIN gets series from Audible, skips other providers."""
        mock_aud.return_value = {
            "series": [{"title": "Jack Reacher", "sequence": "27"}],
            "rating": {
                "overall_distribution": {"display_average_rating": 4.5, "num_ratings": 500},
                "performance_distribution": {"display_average_rating": 4.6},
                "story_distribution": {"display_average_rating": 4.4},
                "num_reviews": 100,
            },
            "subtitle": "A Thriller",
            "language": "English",
        }
        mock_gb.return_value = None
        mock_ol.return_value = None

        db_path = tmp_path / "integration2.db"
        _init_db(db_path)
        book_id = _insert_book(
            db_path,
            title="Revenge Prey",
            author="Lee Child",
            file_path="/lib/Child/RP/rp.opus",
            asin="B0D7JLGFST",
        )

        result = enrich_book(book_id, db_path, quiet=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT series, series_sequence, rating_overall, enrichment_source "
            "FROM audiobooks WHERE id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        conn.close()

        assert row[0] == "Jack Reacher"
        assert row[1] == 27.0
        assert row[2] == 4.5
        assert row[3] == "audible"
```

- [ ] **Step 2: Run the integration tests**

Run: `cd library && python -m pytest tests/test_enrichment_orchestrator.py::TestFullChainIntegration -v`
Expected: PASS

- [ ] **Step 3: Run ALL enrichment tests as a final check**

Run: `cd library && python -m pytest tests/test_enrichment_schema.py tests/test_asin_extraction.py tests/test_enrichment_providers.py tests/test_enrichment_orchestrator.py tests/test_backfill_enrichment.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add library/tests/test_enrichment_orchestrator.py
git commit -m "test: full-chain integration tests for enrichment pipeline"
```

---

### Task 14: Linting and Final Cleanup

- [ ] **Step 1: Run ruff on all new/modified files**

Run:
```bash
cd library && ruff check scripts/enrichment/ scripts/backfill_enrichment.py scanner/metadata_utils.py tests/test_enrichment_*.py tests/test_asin_extraction.py tests/test_backfill_enrichment.py
```
Expected: No errors (fix any that appear)

- [ ] **Step 2: Run ruff format**

Run:
```bash
cd library && ruff format scripts/enrichment/ scripts/backfill_enrichment.py scanner/metadata_utils.py tests/test_enrichment_*.py tests/test_asin_extraction.py tests/test_backfill_enrichment.py
```

- [ ] **Step 3: Run existing test suite to check for regressions**

Run: `cd library && python -m pytest tests/test_metadata_utils.py tests/test_enrich_single.py tests/test_asin.py tests/test_schema.py -v`
Expected: ALL PASS

- [ ] **Step 4: Run shellcheck on systemd units**

Run: `shellcheck systemd/audiobook-enrichment.service 2>&1 || true`
(Service files aren't shell scripts, so this is informational only)

- [ ] **Step 5: Commit any formatting fixes**

```bash
git add -A
git commit -m "style: lint and format enrichment pipeline code"
```

---

## Summary

| Task | Component | Files Created | Files Modified |
|------|-----------|--------------|----------------|
| 1 | Schema migration | `test_enrichment_schema.py` | `schema.sql` |
| 2 | ASIN extraction | `test_asin_extraction.py` | `metadata_utils.py` |
| 3 | Provider base | `enrichment/__init__.py`, `base.py` | `test_enrichment_providers.py` |
| 4 | Local provider | `provider_local.py` | `test_enrichment_providers.py` |
| 5 | Audible provider | `provider_audible.py` | `test_enrichment_providers.py` |
| 6 | Google Books provider | `provider_google.py` | `test_enrichment_providers.py` |
| 7 | Open Library provider | `provider_openlibrary.py` | `test_enrichment_providers.py` |
| 8 | Orchestrator | `enrichment/__init__.py` | `test_enrichment_orchestrator.py` |
| 9 | Backward compat | — | `enrich_single.py` |
| 10 | Backfill script | `backfill_enrichment.py`, `test_backfill_enrichment.py` | — |
| 11 | Systemd timer | `audiobook-enrichment.timer`, `.service` | — |
| 12 | Install integration | — | `install.sh` |
| 13 | Integration tests | — | `test_enrichment_orchestrator.py` |
| 14 | Lint & cleanup | — | Various |
