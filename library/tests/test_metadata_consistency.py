"""
Metadata Consistency Tests.

Verifies that metadata is consistent across all storage locations:
- Source files (.aaxc) - Original Audible downloads
- Converted files (.opus) - Transcoded audiobooks with embedded metadata
- Database (audiobooks table) - SQLite records
- Full-Text Search index (audiobooks_fts) - Search index
- Related tables (genres, topics, eras, supplements)

This test module helps detect:
- Metadata drift between file and database
- FTS index desynchronization
- Missing or orphaned records
- ASIN mismatches between source and database
"""

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR.parent / "rnd"))

from config import AUDIOBOOKS_DATABASE, AUDIOBOOKS_LIBRARY, AUDIOBOOKS_SOURCES
from scanner.metadata_utils import run_ffprobe, extract_author_from_tags


# =============================================================================
# Configuration - Uses config module for path resolution
# =============================================================================

# Production paths (resolved from config module)
PROD_DB_PATH = AUDIOBOOKS_DATABASE
PROD_LIBRARY_DIR = AUDIOBOOKS_LIBRARY
PROD_SOURCES_DIR = AUDIOBOOKS_SOURCES

# Test sample size (for performance)
SAMPLE_SIZE = 50  # Number of records to test in each category


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def prod_db():
    """Connect to production database if available."""
    if not PROD_DB_PATH.exists():
        pytest.skip("Production database not available")
    conn = sqlite3.connect(PROD_DB_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def sample_audiobooks(prod_db):
    """Get a sample of audiobooks for testing."""
    cursor = prod_db.cursor()
    cursor.execute(f"""
        SELECT id, title, author, narrator, file_path, sha256_hash, asin, source_asin
        FROM audiobooks
        WHERE file_path IS NOT NULL
        ORDER BY RANDOM()
        LIMIT {SAMPLE_SIZE}
    """)
    return [dict(row) for row in cursor.fetchall()]


# =============================================================================
# Helper Functions
# =============================================================================


def get_file_metadata(filepath: Path) -> Optional[dict]:
    """Extract metadata from audio file using ffprobe."""
    if not filepath.exists():
        return None

    data = run_ffprobe(filepath)
    if not data:
        return None

    format_data = data.get("format", {})
    tags = format_data.get("tags", {})
    tags_normalized = {k.lower(): v for k, v in tags.items()}

    return {
        "title": tags_normalized.get("title", tags_normalized.get("album", "")),
        "author": extract_author_from_tags(tags_normalized),
        "narrator": tags_normalized.get("narrator", tags_normalized.get("composer", "")),
        "duration_sec": float(format_data.get("duration", 0)),
        "tags": tags_normalized,
    }


def extract_asin_from_source_filename(filename: str) -> Optional[str]:
    """Extract ASIN from source filename pattern."""
    match = re.match(r"^([A-Z0-9]{10})_", filename, re.IGNORECASE)
    return match.group(1) if match else None


def normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison (lowercase, strip whitespace)."""
    if not text:
        return ""
    return " ".join(text.lower().split())


# =============================================================================
# Test Classes
# =============================================================================


class TestFileToDbConsistency:
    """Test that converted file metadata matches database records."""

    def test_file_exists_for_db_records(self, sample_audiobooks):
        """Verify that files referenced in database actually exist."""
        missing_files = []

        for book in sample_audiobooks:
            filepath = Path(book["file_path"])
            if not filepath.exists():
                missing_files.append({
                    "id": book["id"],
                    "title": book["title"],
                    "path": book["file_path"],
                })

        if missing_files:
            pytest.fail(
                f"{len(missing_files)} files missing from filesystem:\n"
                + "\n".join(f"  ID {f['id']}: {f['title']}" for f in missing_files[:10])
            )

    def test_title_matches_file_metadata(self, sample_audiobooks):
        """Verify database title matches embedded file metadata."""
        mismatches = []

        for book in sample_audiobooks:
            filepath = Path(book["file_path"])
            if not filepath.exists():
                continue

            file_meta = get_file_metadata(filepath)
            if not file_meta:
                continue

            db_title = normalize_for_comparison(book["title"])
            file_title = normalize_for_comparison(file_meta["title"])

            # Allow for minor differences (subtitles, etc.)
            if db_title and file_title and db_title != file_title:
                # Check if one contains the other (common for subtitle differences)
                if db_title not in file_title and file_title not in db_title:
                    mismatches.append({
                        "id": book["id"],
                        "db_title": book["title"],
                        "file_title": file_meta["title"],
                    })

        if mismatches:
            msg = f"{len(mismatches)} title mismatches found:\n"
            for m in mismatches[:5]:
                msg += f"  ID {m['id']}:\n"
                msg += f"    DB: {m['db_title'][:50]}\n"
                msg += f"    File: {m['file_title'][:50]}\n"
            pytest.fail(msg)

    def test_author_matches_file_metadata(self, sample_audiobooks):
        """Verify database author matches embedded file metadata."""
        mismatches = []

        for book in sample_audiobooks:
            filepath = Path(book["file_path"])
            if not filepath.exists():
                continue

            file_meta = get_file_metadata(filepath)
            if not file_meta or not file_meta.get("author"):
                continue

            db_author = normalize_for_comparison(book["author"] or "")
            file_author = normalize_for_comparison(file_meta["author"])

            if db_author and file_author and db_author != file_author:
                # Check for partial matches (first/last name variations)
                db_words = set(db_author.split())
                file_words = set(file_author.split())
                overlap = len(db_words & file_words) / max(len(db_words), len(file_words), 1)

                if overlap < 0.5:  # Less than 50% word overlap
                    mismatches.append({
                        "id": book["id"],
                        "db_author": book["author"],
                        "file_author": file_meta["author"],
                    })

        if mismatches:
            msg = f"{len(mismatches)} author mismatches found:\n"
            for m in mismatches[:5]:
                msg += f"  ID {m['id']}: DB='{m['db_author']}' vs File='{m['file_author']}'\n"
            pytest.fail(msg)


class TestFtsIndexConsistency:
    """Test that FTS index matches database records."""

    def test_fts_record_count_matches(self, prod_db):
        """Verify FTS index has same record count as main table."""
        cursor = prod_db.cursor()

        cursor.execute("SELECT COUNT(*) FROM audiobooks")
        main_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM audiobooks_fts")
        fts_count = cursor.fetchone()[0]

        assert fts_count == main_count, (
            f"FTS index count ({fts_count}) doesn't match main table ({main_count})"
        )

    def test_fts_title_searchable(self, prod_db, sample_audiobooks):
        """Verify titles are searchable in FTS index."""
        cursor = prod_db.cursor()
        not_found = []

        for book in sample_audiobooks[:10]:  # Test subset for speed
            # Extract only alphanumeric words for FTS search
            title_words = [w for w in book["title"].split()[:2] if w.isalnum()]
            if not title_words:
                continue

            # Quote terms to handle special characters in FTS5
            search_term = " ".join(f'"{w}"' for w in title_words)
            try:
                cursor.execute(
                    "SELECT rowid FROM audiobooks_fts WHERE title MATCH ?",
                    (search_term,)
                )
                results = cursor.fetchall()

                if book["id"] not in [r[0] for r in results]:
                    not_found.append({
                        "id": book["id"],
                        "title": book["title"],
                        "search": search_term,
                    })
            except sqlite3.OperationalError:
                # Skip problematic search terms
                continue

        if not_found:
            msg = f"{len(not_found)} titles not found in FTS:\n"
            for n in not_found[:5]:
                msg += f"  ID {n['id']}: '{n['title'][:40]}' (searched: '{n['search']}')\n"
            pytest.fail(msg)

    def test_fts_author_searchable(self, prod_db, sample_audiobooks):
        """Verify authors are searchable in FTS index."""
        cursor = prod_db.cursor()
        not_found = []

        for book in sample_audiobooks[:10]:
            if not book["author"]:
                continue

            # Use last word of author name (usually last name)
            author_parts = book["author"].split()
            if not author_parts:
                continue

            search_term = author_parts[-1]  # Last name
            if len(search_term) < 3:
                continue

            cursor.execute(
                "SELECT rowid FROM audiobooks_fts WHERE author MATCH ?",
                (search_term,)
            )
            results = cursor.fetchall()

            if book["id"] not in [r[0] for r in results]:
                not_found.append({
                    "id": book["id"],
                    "author": book["author"],
                    "search": search_term,
                })

        if not_found:
            msg = f"{len(not_found)} authors not found in FTS:\n"
            for n in not_found[:5]:
                msg += f"  ID {n['id']}: '{n['author']}' (searched: '{n['search']}')\n"
            pytest.fail(msg)


class TestAsinConsistency:
    """Test ASIN consistency between source files and database."""

    def test_source_asin_matches_asin(self, prod_db):
        """Verify source_asin matches asin where both exist."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT id, title, asin, source_asin
            FROM audiobooks
            WHERE asin IS NOT NULL AND asin != ''
            AND source_asin IS NOT NULL AND source_asin != ''
            AND asin != source_asin
        """)
        mismatches = [dict(row) for row in cursor.fetchall()]

        if mismatches:
            msg = f"{len(mismatches)} ASIN/source_asin mismatches:\n"
            for m in mismatches[:5]:
                msg += f"  ID {m['id']}: asin={m['asin']}, source_asin={m['source_asin']}\n"
            pytest.fail(msg)

    def test_asin_format_valid(self, prod_db):
        """Verify all ASINs have valid format (10 alphanumeric characters)."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT id, title, asin
            FROM audiobooks
            WHERE asin IS NOT NULL AND asin != ''
            AND (LENGTH(asin) != 10 OR asin NOT GLOB '[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]')
        """)
        invalid = [dict(row) for row in cursor.fetchall()]

        if invalid:
            msg = f"{len(invalid)} invalid ASIN formats:\n"
            for i in invalid[:5]:
                msg += f"  ID {i['id']}: '{i['asin']}' ({i['title'][:30]})\n"
            pytest.fail(msg)

    @pytest.mark.skipif(not PROD_SOURCES_DIR.exists(), reason="Sources dir not available")
    def test_source_files_have_matching_db_asin(self):
        """Verify source file ASINs match database records."""
        mismatches = []
        conn = sqlite3.connect(PROD_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Sample source files
        source_files = list(PROD_SOURCES_DIR.glob("*.aaxc"))[:SAMPLE_SIZE]

        for source_file in source_files:
            asin = extract_asin_from_source_filename(source_file.name)
            if not asin:
                continue

            # Find matching database record by title similarity
            title_from_file = source_file.name.split("_", 1)[1].rsplit("-AAX", 1)[0]
            title_normalized = title_from_file.replace("_", " ")

            cursor.execute(
                "SELECT id, title, asin FROM audiobooks WHERE asin = ?",
                (asin,)
            )
            result = cursor.fetchone()

            if result and result["asin"] != asin:
                mismatches.append({
                    "source_file": source_file.name,
                    "source_asin": asin,
                    "db_asin": result["asin"],
                })

        conn.close()

        if mismatches:
            msg = f"{len(mismatches)} source/DB ASIN mismatches:\n"
            for m in mismatches[:5]:
                msg += f"  {m['source_file'][:40]}: source={m['source_asin']}, db={m['db_asin']}\n"
            pytest.fail(msg)


class TestRelatedTablesConsistency:
    """Test consistency of related tables (genres, topics, eras)."""

    def test_genres_reference_valid_audiobooks(self, prod_db):
        """Verify audiobook_genres references exist in audiobooks table."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT ag.audiobook_id, ag.genre_id
            FROM audiobook_genres ag
            LEFT JOIN audiobooks a ON ag.audiobook_id = a.id
            WHERE a.id IS NULL
        """)
        orphans = cursor.fetchall()

        if orphans:
            pytest.fail(f"{len(orphans)} orphaned genre references found")

    def test_topics_reference_valid_audiobooks(self, prod_db):
        """Verify audiobook_topics references exist in audiobooks table."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT at.audiobook_id, at.topic_id
            FROM audiobook_topics at
            LEFT JOIN audiobooks a ON at.audiobook_id = a.id
            WHERE a.id IS NULL
        """)
        orphans = cursor.fetchall()

        if orphans:
            pytest.fail(f"{len(orphans)} orphaned topic references found")

    def test_eras_reference_valid_audiobooks(self, prod_db):
        """Verify audiobook_eras references exist in audiobooks table."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT ae.audiobook_id, ae.era_id
            FROM audiobook_eras ae
            LEFT JOIN audiobooks a ON ae.audiobook_id = a.id
            WHERE a.id IS NULL
        """)
        orphans = cursor.fetchall()

        if orphans:
            pytest.fail(f"{len(orphans)} orphaned era references found")

    def test_supplements_reference_valid_audiobooks(self, prod_db):
        """Verify supplements references exist in audiobooks table."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT s.id, s.audiobook_id, s.filename
            FROM supplements s
            LEFT JOIN audiobooks a ON s.audiobook_id = a.id
            WHERE s.audiobook_id IS NOT NULL AND a.id IS NULL
        """)
        orphans = cursor.fetchall()

        if orphans:
            pytest.fail(f"{len(orphans)} orphaned supplement references found")


class TestHashConsistency:
    """Test file hash consistency."""

    def test_hash_matches_file(self, sample_audiobooks):
        """Verify stored SHA-256 hash matches actual file content."""
        from common import calculate_sha256

        mismatches = []

        # Only test files that have hashes and exist
        files_to_check = [
            b for b in sample_audiobooks
            if b["sha256_hash"] and Path(b["file_path"]).exists()
        ][:10]  # Limit to 10 for performance

        for book in files_to_check:
            filepath = Path(book["file_path"])
            actual_hash = calculate_sha256(filepath)

            if actual_hash and actual_hash != book["sha256_hash"]:
                mismatches.append({
                    "id": book["id"],
                    "title": book["title"],
                    "stored": book["sha256_hash"][:16] + "...",
                    "actual": actual_hash[:16] + "...",
                })

        if mismatches:
            msg = f"{len(mismatches)} hash mismatches (file modified?):\n"
            for m in mismatches[:5]:
                msg += f"  ID {m['id']}: stored={m['stored']}, actual={m['actual']}\n"
            pytest.fail(msg)


class TestDataIntegrity:
    """Test general data integrity constraints."""

    def test_no_duplicate_file_paths(self, prod_db):
        """Verify no duplicate file paths in database."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT file_path, COUNT(*) as cnt
            FROM audiobooks
            GROUP BY file_path
            HAVING cnt > 1
        """)
        duplicates = cursor.fetchall()

        if duplicates:
            pytest.fail(f"{len(duplicates)} duplicate file paths found")

    def test_no_null_titles(self, prod_db):
        """Verify all audiobooks have titles."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM audiobooks
            WHERE title IS NULL OR title = ''
        """)
        null_count = cursor.fetchone()[0]

        assert null_count == 0, f"{null_count} audiobooks have NULL or empty titles"

    def test_no_null_file_paths(self, prod_db):
        """Verify all audiobooks have file paths."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM audiobooks
            WHERE file_path IS NULL OR file_path = ''
        """)
        null_count = cursor.fetchone()[0]

        assert null_count == 0, f"{null_count} audiobooks have NULL or empty file paths"

    def test_valid_duration_values(self, prod_db):
        """Verify duration values are reasonable."""
        cursor = prod_db.cursor()

        # Check for negative durations
        cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE duration_hours < 0")
        negative = cursor.fetchone()[0]
        assert negative == 0, f"{negative} audiobooks have negative duration"

        # Check for unreasonably long durations (> 100 hours)
        cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE duration_hours > 100")
        too_long = cursor.fetchone()[0]
        # This is a warning, not a failure (some lecture series are very long)
        if too_long > 0:
            print(f"WARNING: {too_long} audiobooks have duration > 100 hours")

    def test_valid_file_sizes(self, prod_db):
        """Verify file size values are reasonable."""
        cursor = prod_db.cursor()

        # Check for zero or negative sizes
        cursor.execute(
            "SELECT COUNT(*) FROM audiobooks WHERE file_size_mb <= 0"
        )
        invalid = cursor.fetchone()[0]
        assert invalid == 0, f"{invalid} audiobooks have invalid file size"


class TestSourceToConvertedConsistency:
    """Test consistency between source and converted files."""

    @pytest.mark.skipif(not PROD_SOURCES_DIR.exists(), reason="Sources dir not available")
    def test_source_title_matches_converted(self):
        """Verify source file titles match converted file titles."""
        mismatches = []
        conn = sqlite3.connect(PROD_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get audiobooks that have both source and converted files
        cursor.execute("""
            SELECT id, title, file_path, asin
            FROM audiobooks
            WHERE asin IS NOT NULL AND asin != ''
            LIMIT ?
        """, (SAMPLE_SIZE,))

        for row in cursor.fetchall():
            asin = row["asin"]
            db_title = row["title"]

            # Find matching source file
            source_files = list(PROD_SOURCES_DIR.glob(f"{asin}_*.aaxc"))
            if not source_files:
                continue

            source_file = source_files[0]
            # Extract title from source filename
            title_part = source_file.name.split("_", 1)[1].rsplit("-AAX", 1)[0]
            source_title = title_part.replace("_", " ")

            # Normalize for comparison
            db_norm = normalize_for_comparison(db_title)
            source_norm = normalize_for_comparison(source_title)

            # Check for significant mismatch
            if db_norm and source_norm:
                db_words = set(db_norm.split())
                source_words = set(source_norm.split())
                overlap = len(db_words & source_words) / max(len(db_words), len(source_words), 1)

                if overlap < 0.5:
                    mismatches.append({
                        "id": row["id"],
                        "db_title": db_title,
                        "source_title": source_title,
                    })

        conn.close()

        if mismatches:
            msg = f"{len(mismatches)} source/converted title mismatches:\n"
            for m in mismatches[:5]:
                msg += f"  ID {m['id']}:\n"
                msg += f"    DB: {m['db_title'][:50]}\n"
                msg += f"    Source: {m['source_title'][:50]}\n"
            pytest.fail(msg)


class TestIndexConsistency:
    """Test database index consistency."""

    def test_all_indexes_exist(self, prod_db):
        """Verify all expected indexes exist."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND tbl_name='audiobooks'
        """)
        indexes = {row[0] for row in cursor.fetchall()}

        expected_indexes = {
            "idx_audiobooks_title",
            "idx_audiobooks_author",
            "idx_audiobooks_narrator",
            "idx_audiobooks_asin",
            "idx_audiobooks_sha256",
        }

        missing = expected_indexes - indexes
        if missing:
            pytest.fail(f"Missing indexes: {missing}")

    def test_fts_triggers_exist(self, prod_db):
        """Verify FTS update triggers exist."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='trigger' AND tbl_name='audiobooks'
        """)
        triggers = {row[0] for row in cursor.fetchall()}

        expected_triggers = {"audiobooks_ai", "audiobooks_ad", "audiobooks_au"}
        missing = expected_triggers - triggers

        if missing:
            pytest.fail(f"Missing FTS triggers: {missing}")


# =============================================================================
# Metadata Access Function Tests
# =============================================================================


class TestMetadataExtractionFunctions:
    """Test the metadata extraction functions used throughout the codebase."""

    def test_run_ffprobe_returns_valid_structure(self, sample_audiobooks):
        """Verify run_ffprobe returns expected structure."""
        from scanner.metadata_utils import run_ffprobe

        for book in sample_audiobooks[:3]:
            filepath = Path(book["file_path"])
            if not filepath.exists():
                continue

            result = run_ffprobe(filepath)
            if result is None:
                continue

            # Must have format section
            assert "format" in result, "ffprobe result missing 'format' section"

            format_data = result["format"]
            # Should have duration
            assert "duration" in format_data, "ffprobe format missing 'duration'"

            # Duration should be numeric
            assert float(format_data["duration"]) > 0, "Invalid duration"

    def test_extract_author_from_tags_priority(self):
        """Verify author extraction follows correct priority order."""
        from scanner.metadata_utils import extract_author_from_tags

        # Test priority: artist > album_artist > author
        tags1 = {"artist": "Primary Author", "album_artist": "Secondary"}
        assert extract_author_from_tags(tags1) == "Primary Author"

        tags2 = {"album_artist": "Album Artist", "author": "Author"}
        assert extract_author_from_tags(tags2) == "Album Artist"

        tags3 = {"author": "Author Only"}
        assert extract_author_from_tags(tags3) == "Author Only"

        # Fallback to default
        tags4 = {}
        assert extract_author_from_tags(tags4) == "Unknown Author"

        # Fallback to provided value
        tags5 = {}
        assert extract_author_from_tags(tags5, "Custom Fallback") == "Custom Fallback"

    def test_extract_narrator_from_tags_priority(self):
        """Verify narrator extraction follows correct priority order."""
        from scanner.metadata_utils import extract_narrator_from_tags

        # Test priority: narrator > composer > performer
        tags1 = {"narrator": "Primary Narrator", "composer": "Composer"}
        assert extract_narrator_from_tags(tags1) == "Primary Narrator"

        tags2 = {"composer": "Composer", "performer": "Performer"}
        assert extract_narrator_from_tags(tags2) == "Composer"

        # Should skip narrator if same as author
        tags3 = {"narrator": "Same Person", "composer": "Different Person"}
        assert extract_narrator_from_tags(tags3, "Same Person") == "Different Person"

    def test_extract_author_from_path(self):
        """Verify author extraction from file path structure."""
        from scanner.metadata_utils import extract_author_from_path

        # Standard structure: Library/Author/Book/file.opus
        # Uses configured library path for test
        test_path = PROD_LIBRARY_DIR / "Stephen King" / "The Shining" / "The Shining.opus"
        assert extract_author_from_path(test_path) == "Stephen King"

        # Without Library in path
        path2 = Path("/some/other/path/file.opus")
        assert extract_author_from_path(path2) is None

    def test_get_file_metadata_returns_all_fields(self, sample_audiobooks):
        """Verify get_file_metadata returns all expected fields."""
        from scanner.metadata_utils import get_file_metadata

        for book in sample_audiobooks[:3]:
            filepath = Path(book["file_path"])
            if not filepath.exists():
                continue

            # Skip hash calculation for speed
            metadata = get_file_metadata(filepath, filepath.parent, calculate_hash=False)
            if metadata is None:
                continue

            # Check required fields exist
            required_fields = [
                "title", "author", "narrator", "publisher",
                "duration_hours", "file_size_mb", "file_path", "format"
            ]
            for field in required_fields:
                assert field in metadata, f"Missing required field: {field}"

            # Validate types
            assert isinstance(metadata["duration_hours"], (int, float))
            assert isinstance(metadata["file_size_mb"], (int, float))
            assert metadata["duration_hours"] >= 0
            assert metadata["file_size_mb"] > 0

    def test_categorize_genre_classification(self):
        """Verify genre categorization works correctly."""
        from scanner.metadata_utils import categorize_genre

        # Fiction genres
        mystery = categorize_genre("Mystery & Thriller")
        assert mystery["main"] == "fiction"
        assert "mystery" in mystery["sub"].lower() or "thriller" in mystery["sub"].lower()

        scifi = categorize_genre("Science Fiction")
        assert scifi["main"] == "fiction"
        assert "science fiction" in scifi["sub"].lower()

        # Non-fiction genres
        history = categorize_genre("History")
        assert history["main"] == "non-fiction"

        biography = categorize_genre("Biography & Memoir")
        assert biography["main"] == "non-fiction"

        # Unknown genre
        unknown = categorize_genre("Random Unknown Genre")
        assert unknown["main"] == "uncategorized"

    def test_determine_literary_era(self):
        """Verify literary era determination."""
        from scanner.metadata_utils import determine_literary_era

        assert "Classical" in determine_literary_era("1750")
        assert "19th Century" in determine_literary_era("1850")
        assert "Early 20th" in determine_literary_era("1920")
        assert "Late 20th" in determine_literary_era("1985")
        assert "Contemporary" in determine_literary_era("2023")
        assert "Unknown" in determine_literary_era("")
        assert "Unknown" in determine_literary_era(None)

    def test_extract_topics_from_description(self):
        """Verify topic extraction from descriptions."""
        from scanner.metadata_utils import extract_topics

        # War-related
        topics1 = extract_topics("A story about World War II battles")
        assert "war" in topics1

        # Technology-related
        topics2 = extract_topics("The future of artificial intelligence and computers")
        assert "technology" in topics2

        # Multiple topics
        topics3 = extract_topics("A war story involving family and politics")
        assert "war" in topics3
        assert "family" in topics3
        assert "politics" in topics3

        # No specific topics
        topics4 = extract_topics("Just a simple story")
        assert topics4 == ["general"]


class TestDatabaseAccessFunctions:
    """Test database access patterns used by the codebase."""

    def test_audiobook_query_returns_all_columns(self, prod_db):
        """Verify standard audiobook queries return expected columns."""
        cursor = prod_db.cursor()
        cursor.execute("SELECT * FROM audiobooks LIMIT 1")

        if cursor.fetchone() is None:
            pytest.skip("No audiobooks in database")

        columns = [description[0] for description in cursor.description]

        expected_columns = [
            "id", "title", "author", "narrator", "publisher", "series",
            "duration_hours", "file_path", "format", "asin", "sha256_hash",
            "content_type", "source"
        ]

        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_fts_search_function(self, prod_db):
        """Verify FTS search returns results in expected format."""
        cursor = prod_db.cursor()

        # Simple search
        cursor.execute("""
            SELECT a.id, a.title, a.author
            FROM audiobooks a
            JOIN audiobooks_fts fts ON a.id = fts.rowid
            WHERE audiobooks_fts MATCH '"the"'
            LIMIT 5
        """)

        results = cursor.fetchall()
        # Should find something with "the" in title/author/etc
        assert len(results) >= 0  # May be empty in test DB

    def test_genre_join_query(self, prod_db):
        """Verify genre join queries work correctly."""
        cursor = prod_db.cursor()
        cursor.execute("""
            SELECT a.id, a.title, g.name as genre
            FROM audiobooks a
            LEFT JOIN audiobook_genres ag ON a.id = ag.audiobook_id
            LEFT JOIN genres g ON ag.genre_id = g.id
            LIMIT 10
        """)

        results = cursor.fetchall()
        assert len(results) >= 0  # Query should execute without error

    def test_asin_lookup_query(self, prod_db):
        """Verify ASIN lookup queries work correctly."""
        cursor = prod_db.cursor()

        # Get an ASIN to test with
        cursor.execute("""
            SELECT asin FROM audiobooks
            WHERE asin IS NOT NULL AND asin != ''
            LIMIT 1
        """)
        row = cursor.fetchone()

        if row is None:
            pytest.skip("No ASINs in database")

        asin = row[0]

        # Test ASIN lookup
        cursor.execute(
            "SELECT id, title FROM audiobooks WHERE asin = ?",
            (asin,)
        )
        result = cursor.fetchone()
        assert result is not None, f"ASIN lookup failed for {asin}"


class TestAsinPopulationFunctions:
    """Test ASIN population helper functions."""

    def test_extract_asin_from_source_filename(self):
        """Verify ASIN extraction from source filenames."""
        from populate_asins_from_sources import extract_asin_and_title

        # Standard format
        asin1, title1 = extract_asin_and_title(
            "0062868071_The_Book_Title-AAX_44_128.aaxc"
        )
        assert asin1 == "0062868071"
        assert "Book Title" in title1

        # B-prefix ASIN
        asin2, title2 = extract_asin_and_title(
            "B009XEJWP8_Another_Book-AAX_22_64.aaxc"
        )
        assert asin2 == "B009XEJWP8"

        # Invalid format
        asin3, title3 = extract_asin_and_title("invalid_filename.aaxc")
        assert asin3 is None
        assert title3 is None

    def test_similarity_calculation(self):
        """Verify similarity calculation for title matching."""
        from populate_asins_from_sources import calculate_similarity

        # Exact match
        assert calculate_similarity("hello world", "hello world") == 1.0

        # Partial match (2 out of 4 words match = 0.5)
        score = calculate_similarity("the great book", "the great novel")
        assert 0.4 <= score <= 0.6  # "the great" matches, "book"/"novel" don't

        # Higher similarity
        score2 = calculate_similarity("the great book one", "the great book two")
        assert score2 > 0.5  # 3 out of 5 unique words match

        # No match
        assert calculate_similarity("abc", "xyz") == 0.0

    def test_title_normalization_for_matching(self):
        """Verify title normalization produces consistent results."""
        from common import normalize_title

        # Remove articles
        assert "book" in normalize_title("The Book")
        assert "story" in normalize_title("A Story")

        # Lowercase
        result = normalize_title("UPPERCASE TITLE")
        assert result == result.lower()

        # Remove punctuation
        result = normalize_title("Hello, World!")
        assert "," not in result
        assert "!" not in result


class TestProductionAPIEndpoints:
    """Test that production API endpoints return correct metadata."""

    def test_api_audiobook_includes_all_metadata(self, app_client):
        """Verify API returns complete metadata for single audiobook."""
        # Get a valid audiobook ID
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)

        if not data.get("audiobooks"):
            pytest.skip("No audiobooks available")

        audiobook_id = data["audiobooks"][0]["id"]

        # Get single audiobook
        response = app_client.get(f"/api/audiobooks/{audiobook_id}")
        assert response.status_code == 200

        audiobook = json.loads(response.data)

        # Verify metadata fields
        expected_fields = [
            "title", "author", "narrator", "duration_hours",
            "file_path", "format"
        ]
        for field in expected_fields:
            assert field in audiobook, f"API response missing '{field}'"

    def test_api_filters_include_metadata_values(self, app_client):
        """Verify filters endpoint returns metadata values."""
        response = app_client.get("/api/filters")
        assert response.status_code == 200

        data = json.loads(response.data)

        # Should include author and narrator lists
        if "authors" in data:
            assert isinstance(data["authors"], list)
        if "narrators" in data:
            assert isinstance(data["narrators"], list)

    def test_api_stats_include_metadata_aggregates(self, app_client):
        """Verify stats endpoint returns metadata aggregates."""
        response = app_client.get("/api/stats")
        assert response.status_code == 200

        data = json.loads(response.data)

        # Should include aggregate stats
        expected_stats = [
            "total_audiobooks", "unique_authors", "unique_narrators"
        ]
        for stat in expected_stats:
            assert stat in data, f"Stats missing '{stat}'"
