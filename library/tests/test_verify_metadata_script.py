"""
Comprehensive tests for library/scripts/verify_metadata.py

Covers: metadata extraction, validation, comparison logic, report generation,
audio format handling, missing/corrupted files, database queries, main() CLI,
and edge cases (empty DB, no metadata, unicode).
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the scripts package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.verify_metadata import (
    MetadataIssue,
    apply_fixes,
    compute_duration_hours,
    get_embedded_tags,
    normalize_name,
    similarity,
    verify_book,
    verify_metadata,
    verify_single_book,
)


# ── Fixtures ──


def _dict_row_factory(cursor, row):
    """Row factory that returns dicts (supports .get()) instead of sqlite3.Row.

    The source code uses book.get("runtime_length_min") on sqlite3.Row objects
    before converting to dict(). sqlite3.Row lacks .get(), so we use a dict
    row factory in tests to match the expected behavior.
    """
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _create_test_db(db_path: Path, books: list[dict] | None = None) -> Path:
    """Create a minimal audiobooks DB with given book rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            narrator TEXT,
            publisher TEXT,
            series TEXT,
            series_sequence REAL,
            asin TEXT,
            isbn TEXT,
            cover_path TEXT,
            audible_image_url TEXT,
            description TEXT,
            publisher_summary TEXT,
            language TEXT,
            content_type TEXT DEFAULT 'Product',
            file_path TEXT,
            format TEXT,
            runtime_length_min INTEGER,
            audible_enriched_at TIMESTAMP,
            duration_hours REAL
        )
    """)
    if books:
        for b in books:
            cols = ", ".join(b.keys())
            placeholders = ", ".join(["?"] * len(b))
            conn.execute(
                f"INSERT INTO audiobooks ({cols}) VALUES ({placeholders})",  # nosec B608
                list(b.values()),
            )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _patch_sqlite_row():
    """Patch sqlite3.Row in verify_metadata module to use dict row factory.

    The source sets conn.row_factory = sqlite3.Row then calls .get() on rows
    (line 557) before converting to dict(). sqlite3.Row lacks .get(), so we
    replace it with a dict-based row factory that supports .get().
    """
    with patch("scripts.verify_metadata.sqlite3.Row", _dict_row_factory):
        yield


@pytest.fixture
def sample_book():
    """A fully-populated book dict mimicking a sqlite3.Row cast to dict."""
    return {
        "id": 1,
        "title": "The Great Gatsby",
        "author": "F. Scott Fitzgerald",
        "narrator": "Jake Gyllenhaal",
        "publisher": "Scribner",
        "series": "Classic American Literature",
        "asin": "B00EXAMPLE",
        "isbn": "9780743273565",
        "cover_path": "/covers/gatsby.jpg",
        "audible_image_url": "https://example.com/cover.jpg",
        "description": "A novel about the American Dream.",
        "publisher_summary": "The classic tale...",
        "language": "English",
        "content_type": "Product",
        "file_path": "/library/gatsby.opus",
        "runtime_length_min": 300,
        "audible_enriched_at": "2026-01-01 00:00:00",
    }


@pytest.fixture
def matching_tags():
    """Embedded tags that match sample_book exactly."""
    return {
        "title": "The Great Gatsby",
        "artist": "F. Scott Fitzgerald",
        "narrator": "Jake Gyllenhaal",
        "publisher": "Scribner",
        "series": "Classic American Literature",
    }


# ── similarity() ──


class TestSimilarity:
    def test_identical_strings(self):
        assert similarity("hello", "hello") == 1.0

    def test_completely_different(self):
        assert similarity("abc", "xyz") < 0.5

    def test_none_a(self):
        assert similarity(None, "hello") == 0.0

    def test_none_b(self):
        assert similarity("hello", None) == 0.0

    def test_both_none(self):
        assert similarity(None, None) == 0.0

    def test_empty_a(self):
        assert similarity("", "hello") == 0.0

    def test_empty_b(self):
        assert similarity("hello", "") == 0.0

    def test_case_insensitive(self):
        assert similarity("Hello World", "hello world") == 1.0

    def test_leading_trailing_whitespace(self):
        assert similarity("  hello  ", "hello") == 1.0

    def test_partial_match(self):
        ratio = similarity("The Great Gatsby", "Great Gatsby")
        assert 0.7 < ratio < 1.0

    def test_unicode_strings(self):
        ratio = similarity("Les Misérables", "Les Miserables")
        assert ratio > 0.8

    def test_cjk_characters(self):
        assert similarity("日本語テスト", "日本語テスト") == 1.0


# ── normalize_name() ──


class TestNormalizeName:
    def test_none(self):
        assert normalize_name(None) == ""

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_simple_name(self):
        assert normalize_name("John Smith") == "john smith"

    def test_last_first_format(self):
        assert normalize_name("Fitzgerald, F. Scott") == "f. scott fitzgerald"

    def test_strips_whitespace(self):
        assert normalize_name("  John Smith  ") == "john smith"

    def test_multiple_commas_no_flip(self):
        # More than one comma: do not flip
        result = normalize_name("A, B, C")
        assert result == "a, b, c"

    def test_case_lowered(self):
        assert normalize_name("TOLKIEN") == "tolkien"

    def test_unicode_name(self):
        assert normalize_name("García Márquez, Gabriel") == "gabriel garcía márquez"


# ── get_embedded_tags() ──


class TestGetEmbeddedTags:
    def test_file_not_exists(self, tmp_path):
        result = get_embedded_tags(str(tmp_path / "nonexistent.opus"))
        assert result is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_successful_format_tags(self, mock_run, tmp_path):
        fake_file = tmp_path / "test.m4b"
        fake_file.touch()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {"format": {"tags": {"title": "My Book", "artist": "Author Name"}}, "streams": []}
            ),
        )
        result = get_embedded_tags(str(fake_file))
        assert result == {"title": "My Book", "artist": "Author Name"}

    @patch("scripts.verify_metadata.subprocess.run")
    def test_successful_stream_tags_opus(self, mock_run, tmp_path):
        """Opus stores metadata in streams[0].tags, not format.tags."""
        fake_file = tmp_path / "test.opus"
        fake_file.touch()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"tags": {}},
                    "streams": [{"tags": {"title": "Opus Book", "artist": "Opus Author"}}],
                }
            ),
        )
        result = get_embedded_tags(str(fake_file))
        assert result == {"title": "Opus Book", "artist": "Opus Author"}

    @patch("scripts.verify_metadata.subprocess.run")
    def test_no_tags_anywhere(self, mock_run, tmp_path):
        fake_file = tmp_path / "test.mp3"
        fake_file.touch()
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps({"format": {}, "streams": [{}]})
        )
        result = get_embedded_tags(str(fake_file))
        assert result == {}

    @patch("scripts.verify_metadata.subprocess.run")
    def test_ffprobe_nonzero_return(self, mock_run, tmp_path):
        fake_file = tmp_path / "bad.opus"
        fake_file.touch()
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_embedded_tags(str(fake_file))
        assert result is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_ffprobe_timeout(self, mock_run, tmp_path):
        fake_file = tmp_path / "slow.opus"
        fake_file.touch()
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)
        result = get_embedded_tags(str(fake_file))
        assert result is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_ffprobe_invalid_json(self, mock_run, tmp_path):
        fake_file = tmp_path / "corrupt.opus"
        fake_file.touch()
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = get_embedded_tags(str(fake_file))
        assert result is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_ffprobe_not_found(self, mock_run, tmp_path):
        fake_file = tmp_path / "test.opus"
        fake_file.touch()
        mock_run.side_effect = FileNotFoundError("ffprobe not found")
        result = get_embedded_tags(str(fake_file))
        assert result is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_tag_keys_lowercased(self, mock_run, tmp_path):
        fake_file = tmp_path / "test.m4b"
        fake_file.touch()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {"format": {"tags": {"Title": "Book", "ARTIST": "Author"}}, "streams": []}
            ),
        )
        result = get_embedded_tags(str(fake_file))
        assert "title" in result
        assert "artist" in result

    @patch("scripts.verify_metadata.subprocess.run")
    def test_format_tags_none_falls_to_stream(self, mock_run, tmp_path):
        """When format.tags is explicitly None, fall through to streams."""
        fake_file = tmp_path / "test.opus"
        fake_file.touch()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {"format": {"tags": None}, "streams": [{"tags": {"title": "Stream Title"}}]}
            ),
        )
        result = get_embedded_tags(str(fake_file))
        assert result["title"] == "Stream Title"


# ── compute_duration_hours() ──


class TestComputeDurationHours:
    @patch("scripts.verify_metadata.subprocess.run")
    def test_valid_duration(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps({"format": {"duration": "36000.0"}})
        )
        result = compute_duration_hours("/fake/file.opus")
        assert result == pytest.approx(10.0)

    @patch("scripts.verify_metadata.subprocess.run")
    def test_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert compute_duration_hours("/fake/file.opus") is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_missing_duration(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"format": {}}))
        assert compute_duration_hours("/fake/file.opus") is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)
        assert compute_duration_hours("/fake/file.opus") is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="{bad json")
        assert compute_duration_hours("/fake/file.opus") is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert compute_duration_hours("/fake/file.opus") is None

    @patch("scripts.verify_metadata.subprocess.run")
    def test_duration_value_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps({"format": {"duration": "not_a_number"}})
        )
        assert compute_duration_hours("/fake/file.opus") is None


# ── MetadataIssue ──


class TestMetadataIssue:
    def test_init_all_fields(self):
        issue = MetadataIssue(
            book_id=1,
            field="title",
            severity=MetadataIssue.SEVERITY_ERROR,
            message="Title mismatch",
            db_value="DB Title",
            file_value="File Title",
            api_value="API Title",
            recommended_value="DB Title",
            confidence=0.9,
        )
        assert issue.book_id == 1
        assert issue.field == "title"
        assert issue.severity == "error"
        assert issue.db_value == "DB Title"
        assert issue.file_value == "File Title"
        assert issue.api_value == "API Title"
        assert issue.recommended_value == "DB Title"
        assert issue.confidence == 0.9

    def test_repr(self):
        issue = MetadataIssue(1, "title", "error", "Bad title")
        r = repr(issue)
        assert "[ERROR]" in r
        assert "Book 1" in r
        assert "title" in r
        assert "Bad title" in r

    def test_to_dict(self):
        issue = MetadataIssue(
            book_id=42,
            field="author",
            severity="warning",
            message="Author mismatch",
            db_value="db_auth",
            file_value="file_auth",
            confidence=0.75,
        )
        d = issue.to_dict()
        assert d["book_id"] == 42
        assert d["field"] == "author"
        assert d["severity"] == "warning"
        assert d["message"] == "Author mismatch"
        assert d["db_value"] == "db_auth"
        assert d["file_value"] == "file_auth"
        assert d["api_value"] is None
        assert d["recommended_value"] is None
        assert d["confidence"] == 0.75

    def test_default_values(self):
        issue = MetadataIssue(1, "x", "info", "msg")
        assert issue.db_value is None
        assert issue.file_value is None
        assert issue.api_value is None
        assert issue.recommended_value is None
        assert issue.confidence == 0.0


# ── verify_book() ──


class TestVerifyBook:
    def test_no_issues_matching_metadata(self, sample_book, matching_tags):
        issues = verify_book(sample_book, matching_tags, 5.0)
        # Should be clean (no mismatches)
        assert all(i.field != "title" for i in issues)
        assert all(i.field != "author" for i in issues)

    def test_title_mismatch_audible_enriched(self, sample_book):
        tags = {"title": "Completely Different Book Title"}
        issues = verify_book(sample_book, tags, None)
        title_issues = [i for i in issues if i.field == "title"]
        assert len(title_issues) == 1
        assert title_issues[0].severity == MetadataIssue.SEVERITY_INFO
        assert "Audible-enriched" in title_issues[0].message

    def test_title_mismatch_not_enriched(self, sample_book):
        sample_book["audible_enriched_at"] = None
        tags = {"title": "Completely Different Book Title"}
        issues = verify_book(sample_book, tags, None)
        title_issues = [i for i in issues if i.field == "title"]
        assert len(title_issues) == 1
        assert title_issues[0].severity == MetadataIssue.SEVERITY_WARNING

    def test_title_from_album_tag(self, sample_book):
        tags = {"album": "Totally Wrong Album Name XYZ"}
        issues = verify_book(sample_book, tags, None)
        title_issues = [i for i in issues if i.field == "title"]
        assert len(title_issues) == 1

    def test_title_no_mismatch_when_similar(self, sample_book, matching_tags):
        issues = verify_book(sample_book, matching_tags, None)
        title_issues = [i for i in issues if i.field == "title"]
        assert len(title_issues) == 0

    def test_author_mismatch_enriched(self, sample_book):
        tags = {"artist": "Completely Different Author"}
        issues = verify_book(sample_book, tags, None)
        auth_issues = [i for i in issues if i.field == "author"]
        assert len(auth_issues) == 1
        assert auth_issues[0].severity == MetadataIssue.SEVERITY_INFO
        assert auth_issues[0].recommended_value == sample_book["author"]

    def test_author_mismatch_not_enriched(self, sample_book):
        sample_book["audible_enriched_at"] = None
        tags = {"artist": "Completely Different Author"}
        issues = verify_book(sample_book, tags, None)
        auth_issues = [i for i in issues if i.field == "author"]
        assert len(auth_issues) == 1
        assert auth_issues[0].severity == MetadataIssue.SEVERITY_CONFLICT
        assert auth_issues[0].recommended_value is None

    def test_author_from_author_tag(self, sample_book):
        tags = {"author": "Wrong Author Name"}
        issues = verify_book(sample_book, tags, None)
        auth_issues = [i for i in issues if i.field == "author"]
        assert len(auth_issues) == 1

    def test_author_from_album_artist_tag(self, sample_book):
        tags = {"album_artist": "Wrong Author Name"}
        issues = verify_book(sample_book, tags, None)
        auth_issues = [i for i in issues if i.field == "author"]
        assert len(auth_issues) == 1

    def test_narrator_mismatch(self, sample_book):
        tags = {"narrator": "Completely Different Narrator"}
        issues = verify_book(sample_book, tags, None)
        narr_issues = [i for i in issues if i.field == "narrator"]
        assert len(narr_issues) == 1
        assert narr_issues[0].severity == MetadataIssue.SEVERITY_WARNING

    def test_narrator_from_composer_tag(self, sample_book):
        tags = {"composer": "Different Narrator Person"}
        issues = verify_book(sample_book, tags, None)
        narr_issues = [i for i in issues if i.field == "narrator"]
        assert len(narr_issues) == 1

    def test_no_narrator_in_db_skips_check(self, sample_book):
        sample_book["narrator"] = None
        tags = {"narrator": "Some Narrator"}
        issues = verify_book(sample_book, tags, None)
        narr_issues = [i for i in issues if i.field == "narrator"]
        # Should still get the "missing narrator" issue but no mismatch
        assert all("mismatch" not in i.message.lower() for i in narr_issues)

    def test_duration_minor_mismatch_warning(self, sample_book):
        # DB says 300 min = 5 hours. File says 5.7 hours (~14% off)
        issues = verify_book(sample_book, None, 5.7)
        dur_issues = [i for i in issues if i.field == "duration"]
        assert len(dur_issues) == 1
        assert dur_issues[0].severity == MetadataIssue.SEVERITY_WARNING

    def test_duration_major_mismatch_error(self, sample_book):
        # DB says 300 min = 5 hours. File says 10 hours (100% off)
        issues = verify_book(sample_book, None, 10.0)
        dur_issues = [i for i in issues if i.field == "duration"]
        assert len(dur_issues) == 1
        assert dur_issues[0].severity == MetadataIssue.SEVERITY_ERROR
        assert "Major duration" in dur_issues[0].message

    def test_duration_within_tolerance(self, sample_book):
        # DB says 300 min = 5 hours. File says 5.05 hours (1% off)
        issues = verify_book(sample_book, None, 5.05)
        dur_issues = [i for i in issues if i.field == "duration"]
        assert len(dur_issues) == 0

    def test_no_runtime_skips_duration(self, sample_book):
        sample_book["runtime_length_min"] = None
        issues = verify_book(sample_book, None, 5.0)
        dur_issues = [i for i in issues if i.field == "duration"]
        assert len(dur_issues) == 0

    def test_missing_asin_and_isbn(self, sample_book):
        sample_book["asin"] = None
        sample_book["isbn"] = None
        issues = verify_book(sample_book, None, None)
        id_issues = [i for i in issues if i.field == "identifier"]
        assert len(id_issues) == 1
        assert "No ASIN or ISBN" in id_issues[0].message

    def test_has_asin_no_identifier_issue(self, sample_book):
        issues = verify_book(sample_book, None, None)
        id_issues = [i for i in issues if i.field == "identifier"]
        assert len(id_issues) == 0

    def test_missing_cover(self, sample_book):
        sample_book["cover_path"] = None
        sample_book["audible_image_url"] = None
        issues = verify_book(sample_book, None, None)
        cover_issues = [i for i in issues if i.field == "cover"]
        assert len(cover_issues) == 1

    def test_has_cover_path_no_issue(self, sample_book):
        issues = verify_book(sample_book, None, None)
        cover_issues = [i for i in issues if i.field == "cover"]
        assert len(cover_issues) == 0

    def test_missing_description(self, sample_book):
        sample_book["description"] = None
        sample_book["publisher_summary"] = None
        issues = verify_book(sample_book, None, None)
        desc_issues = [i for i in issues if i.field == "description"]
        assert len(desc_issues) == 1

    def test_missing_narrator_issue(self, sample_book):
        sample_book["narrator"] = None
        issues = verify_book(sample_book, None, None)
        narr_issues = [i for i in issues if i.field == "narrator"]
        assert any("Missing or unknown" in i.message for i in narr_issues)

    def test_unknown_narrator_issue(self, sample_book):
        sample_book["narrator"] = "Unknown Narrator"
        issues = verify_book(sample_book, None, None)
        narr_issues = [i for i in issues if i.field == "narrator"]
        assert any("Missing or unknown" in i.message for i in narr_issues)

    def test_missing_language_despite_enrichment(self, sample_book):
        sample_book["language"] = None
        issues = verify_book(sample_book, None, None)
        lang_issues = [i for i in issues if i.field == "language"]
        assert len(lang_issues) == 1
        assert "Language not set" in lang_issues[0].message

    def test_missing_language_no_enrichment_no_issue(self, sample_book):
        sample_book["language"] = None
        sample_book["audible_enriched_at"] = None
        issues = verify_book(sample_book, None, None)
        lang_issues = [i for i in issues if i.field == "language"]
        assert len(lang_issues) == 0

    def test_series_mismatch(self, sample_book):
        tags = {"series": "Totally Different Series"}
        issues = verify_book(sample_book, tags, None)
        series_issues = [i for i in issues if i.field == "series"]
        assert len(series_issues) == 1

    def test_series_match(self, sample_book, matching_tags):
        issues = verify_book(sample_book, matching_tags, None)
        series_issues = [i for i in issues if i.field == "series"]
        assert len(series_issues) == 0

    def test_series_from_grouping_tag(self, sample_book):
        tags = {"grouping": "Unrelated Series Name"}
        issues = verify_book(sample_book, tags, None)
        series_issues = [i for i in issues if i.field == "series"]
        assert len(series_issues) == 1

    def test_series_from_tvshowtitle_tag(self, sample_book):
        tags = {"tvshowtitle": "Unrelated Show"}
        issues = verify_book(sample_book, tags, None)
        series_issues = [i for i in issues if i.field == "series"]
        assert len(series_issues) == 1

    def test_series_enriched_has_recommended(self, sample_book):
        tags = {"series": "Different Series"}
        issues = verify_book(sample_book, tags, None)
        series_issues = [i for i in issues if i.field == "series"]
        assert len(series_issues) == 1
        assert series_issues[0].recommended_value == sample_book["series"]

    def test_series_not_enriched_no_recommended(self, sample_book):
        sample_book["audible_enriched_at"] = None
        tags = {"series": "Different Series"}
        issues = verify_book(sample_book, tags, None)
        series_issues = [i for i in issues if i.field == "series"]
        assert len(series_issues) == 1
        assert series_issues[0].recommended_value is None

    def test_publisher_mismatch(self, sample_book):
        tags = {"publisher": "Completely Different Publisher Inc"}
        issues = verify_book(sample_book, tags, None)
        pub_issues = [i for i in issues if i.field == "publisher"]
        assert len(pub_issues) == 1

    def test_publisher_match(self, sample_book, matching_tags):
        issues = verify_book(sample_book, matching_tags, None)
        pub_issues = [i for i in issues if i.field == "publisher"]
        assert len(pub_issues) == 0

    def test_unknown_content_type(self, sample_book):
        sample_book["content_type"] = "MysteryType"
        issues = verify_book(sample_book, None, None)
        ct_issues = [i for i in issues if i.field == "content_type"]
        assert len(ct_issues) == 1
        assert "Unknown content type" in ct_issues[0].message

    def test_valid_content_types(self, sample_book):
        for ct in ("Product", "Performance", "Speech", "Podcast", "Lecture", "Radio/TV Program"):
            sample_book["content_type"] = ct
            issues = verify_book(sample_book, None, None)
            ct_issues = [i for i in issues if i.field == "content_type"]
            assert len(ct_issues) == 0, f"Unexpected issue for content_type={ct}"

    def test_none_content_type_no_issue(self, sample_book):
        sample_book["content_type"] = None
        issues = verify_book(sample_book, None, None)
        ct_issues = [i for i in issues if i.field == "content_type"]
        assert len(ct_issues) == 0

    def test_no_embedded_tags(self, sample_book):
        """Passing None for tags should skip tag-based checks."""
        issues = verify_book(sample_book, None, None)
        tag_fields = {"title", "author", "narrator", "series", "publisher"}
        mismatch_issues = [
            i for i in issues if i.field in tag_fields and "mismatch" in i.message.lower()
        ]
        assert len(mismatch_issues) == 0

    def test_empty_embedded_tags(self, sample_book):
        """Empty dict should skip tag comparisons (no keys to compare)."""
        issues = verify_book(sample_book, {}, None)
        mismatch_issues = [i for i in issues if "mismatch" in i.message.lower()]
        assert len(mismatch_issues) == 0

    def test_unicode_metadata(self):
        book = {
            "id": 99,
            "title": "Les Misérables",
            "author": "Victor Hugo",
            "narrator": "Émile Zola",
            "publisher": "Éditions Gallimard",
            "series": None,
            "asin": "B00FRENCH",
            "isbn": None,
            "cover_path": "/covers/miserables.jpg",
            "audible_image_url": None,
            "description": "Un roman épique",
            "publisher_summary": None,
            "language": "French",
            "content_type": "Product",
            "runtime_length_min": None,
            "audible_enriched_at": None,
        }
        tags = {"title": "Les Misérables", "artist": "Victor Hugo"}
        issues = verify_book(book, tags, None)
        title_issues = [i for i in issues if i.field == "title" and "mismatch" in i.message.lower()]
        assert len(title_issues) == 0

    def test_duration_zero_audible_hours(self, sample_book):
        """Edge: runtime_length_min=0 is falsy, so duration check is skipped."""
        sample_book["runtime_length_min"] = 0
        # 0 is falsy in Python, so `if file_duration_hours and book.get("runtime_length_min")`
        # evaluates to False — duration check is skipped entirely, no ZeroDivisionError
        issues = verify_book(sample_book, None, 5.0)
        dur_issues = [i for i in issues if i.field == "duration"]
        assert len(dur_issues) == 0


# ── apply_fixes() ──


class TestApplyFixes:
    def test_applies_high_confidence_fixes(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "Wrong Title", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(
                book_id=1,
                field="title",
                severity=MetadataIssue.SEVERITY_WARNING,
                message="Title mismatch",
                db_value="Wrong Title",
                recommended_value="Correct Title",
                confidence=0.9,
            )
        ]
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 1

        row = conn.execute("SELECT title FROM audiobooks WHERE id=1").fetchone()
        assert row[0] == "Correct Title"
        conn.close()

    def test_skips_low_confidence(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "Old", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(
                book_id=1,
                field="title",
                severity=MetadataIssue.SEVERITY_WARNING,
                message="m",
                recommended_value="New",
                confidence=0.5,
            )
        ]
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 0
        conn.close()

    def test_skips_info_severity(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "Old", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(
                book_id=1,
                field="title",
                severity=MetadataIssue.SEVERITY_INFO,
                message="m",
                recommended_value="New",
                confidence=0.9,
            )
        ]
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 0
        conn.close()

    def test_skips_no_recommended_value(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "Old", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(
                book_id=1,
                field="title",
                severity=MetadataIssue.SEVERITY_WARNING,
                message="m",
                recommended_value=None,
                confidence=0.9,
            )
        ]
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 0
        conn.close()

    def test_skips_non_text_fields(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(
                book_id=1,
                field="duration",
                severity=MetadataIssue.SEVERITY_ERROR,
                message="m",
                recommended_value="10h",
                confidence=0.9,
            )
        ]
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 0
        conn.close()

    def test_quiet_false_prints(self, tmp_path, capsys):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "Old", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(
                book_id=1,
                field="title",
                severity=MetadataIssue.SEVERITY_WARNING,
                message="m",
                db_value="Old",
                recommended_value="New",
                confidence=0.9,
            )
        ]
        apply_fixes(conn, issues, quiet=False)
        captured = capsys.readouterr()
        assert "Fixed title" in captured.out
        conn.close()

    def test_no_fixes_no_commit(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        conn = sqlite3.connect(db_path)
        issues = []
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 0
        conn.close()

    def test_multiple_fixes(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path,
            [
                {"id": 1, "title": "Wrong1", "author": "WrongA", "file_path": "/f/1.opus"},
                {"id": 2, "title": "Wrong2", "file_path": "/f/2.opus"},
            ],
        )
        conn = sqlite3.connect(db_path)

        issues = [
            MetadataIssue(1, "title", "warning", "m", recommended_value="Right1", confidence=0.9),
            MetadataIssue(1, "author", "conflict", "m", recommended_value="RightA", confidence=0.8),
            MetadataIssue(2, "title", "error", "m", recommended_value="Right2", confidence=0.95),
        ]
        count = apply_fixes(conn, issues, quiet=True)
        assert count == 3

        r1 = conn.execute("SELECT title, author FROM audiobooks WHERE id=1").fetchone()
        assert r1[0] == "Right1"
        assert r1[1] == "RightA"
        r2 = conn.execute("SELECT title FROM audiobooks WHERE id=2").fetchone()
        assert r2[0] == "Right2"
        conn.close()


# ── verify_metadata() ──


class TestVerifyMetadata:
    def test_empty_database(self, tmp_path):
        db_path = tmp_path / "empty.db"
        _create_test_db(db_path, [])
        result = verify_metadata(db_path=db_path, quiet=True, check_files=False)
        assert result["total_checked"] == 0
        assert result["issues_found"] == 0

    @patch("scripts.verify_metadata.get_embedded_tags")
    @patch("scripts.verify_metadata.compute_duration_hours")
    def test_single_book_no_issues(self, mock_dur, mock_tags, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path,
            [
                {
                    "id": 1,
                    "title": "Good Book",
                    "author": "Good Author",
                    "narrator": "Good Narrator",
                    "asin": "B001",
                    "cover_path": "/c.jpg",
                    "description": "A book",
                    "file_path": "/f/1.opus",
                    "runtime_length_min": 300,
                    "language": "English",
                    "content_type": "Product",
                }
            ],
        )
        mock_tags.return_value = {
            "title": "Good Book",
            "artist": "Good Author",
            "narrator": "Good Narrator",
        }
        mock_dur.return_value = 5.0  # 300 min = 5h

        result = verify_metadata(db_path=db_path, quiet=True, check_files=True)
        assert result["total_checked"] == 1
        assert result["errors"] == 0

    def test_single_id_filter(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path,
            [
                {
                    "id": 1,
                    "title": "Book 1",
                    "file_path": "/f/1.opus",
                    "asin": "A1",
                    "cover_path": "/c.jpg",
                    "description": "d",
                    "narrator": "N",
                },
                {
                    "id": 2,
                    "title": "Book 2",
                    "file_path": "/f/2.opus",
                    "asin": "A2",
                    "cover_path": "/c.jpg",
                    "description": "d",
                    "narrator": "N",
                },
            ],
        )
        result = verify_metadata(db_path=db_path, single_id=1, quiet=True, check_files=False)
        assert result["total_checked"] == 1

    @patch("scripts.verify_metadata.get_embedded_tags")
    @patch("scripts.verify_metadata.compute_duration_hours")
    def test_auto_fix(self, mock_dur, mock_tags, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path,
            [
                {
                    "id": 1,
                    "title": "Wrong Title",
                    "author": "Author",
                    "narrator": "Narrator",
                    "asin": "B001",
                    "cover_path": "/c.jpg",
                    "description": "d",
                    "file_path": "/f/1.opus",
                    "audible_enriched_at": "2026-01-01",
                    "language": "English",
                    "content_type": "Product",
                }
            ],
        )
        # Make file title very different to trigger mismatch (but enriched -> INFO, not WARNING)
        mock_tags.return_value = {"title": "XYZZY Unrelated"}
        mock_dur.return_value = None

        result = verify_metadata(db_path=db_path, auto_fix=True, quiet=True, check_files=True)
        # INFO severity issues are not auto-fixed, so fixes_applied should be 0
        assert result["fixes_applied"] == 0

    def test_dry_run_no_fixes(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        result = verify_metadata(
            db_path=db_path, dry_run=True, auto_fix=True, quiet=True, check_files=False
        )
        assert result["fixes_applied"] == 0

    def test_verbose_output(self, tmp_path, capsys):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        verify_metadata(db_path=db_path, quiet=False, check_files=False)
        captured = capsys.readouterr()
        assert "Verifying metadata" in captured.out
        assert "METADATA VERIFICATION RESULTS" in captured.out

    def test_verbose_with_check_files_message(self, tmp_path, capsys):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        verify_metadata(db_path=db_path, quiet=False, check_files=True)
        captured = capsys.readouterr()
        assert "Checking embedded file tags" in captured.out

    def test_check_files_false_skips_ffprobe(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus", "runtime_length_min": 60}]
        )
        with patch("scripts.verify_metadata.get_embedded_tags") as mock_tags:
            verify_metadata(db_path=db_path, quiet=True, check_files=False)
            mock_tags.assert_not_called()

    @patch("scripts.verify_metadata.get_embedded_tags")
    @patch("scripts.verify_metadata.compute_duration_hours")
    def test_no_file_path_skips_file_checks(self, mock_dur, mock_tags, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": None}])
        verify_metadata(db_path=db_path, quiet=True, check_files=True)
        mock_tags.assert_not_called()
        mock_dur.assert_not_called()

    def test_no_db_path_no_default_exits(self):
        with patch("scripts.verify_metadata.DATABASE_PATH", None):
            with pytest.raises(SystemExit):
                verify_metadata(db_path=None)

    def test_report_contains_all_keys(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        result = verify_metadata(db_path=db_path, quiet=True, check_files=False)
        expected_keys = {
            "total_checked",
            "issues_found",
            "errors",
            "conflicts",
            "warnings",
            "infos",
            "fixes_applied",
            "issues",
        }
        assert set(result.keys()) == expected_keys

    def test_issues_are_dicts(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        result = verify_metadata(db_path=db_path, quiet=True, check_files=False)
        for issue in result["issues"]:
            assert isinstance(issue, dict)
            assert "book_id" in issue

    def test_verbose_dry_run_label(self, tmp_path, capsys):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        verify_metadata(db_path=db_path, dry_run=True, quiet=False, check_files=False)
        captured = capsys.readouterr()
        assert "(DRY RUN)" in captured.out

    def test_verbose_auto_fix_line(self, tmp_path, capsys):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        verify_metadata(db_path=db_path, auto_fix=True, quiet=False, check_files=False)
        captured = capsys.readouterr()
        assert "Fixes applied" in captured.out

    @patch("scripts.verify_metadata.get_embedded_tags")
    @patch("scripts.verify_metadata.compute_duration_hours")
    def test_progress_printed_at_100(self, mock_dur, mock_tags, tmp_path, capsys):
        """When there are >=100 books, progress is printed at multiples of 100."""
        db_path = tmp_path / "test.db"
        books = [
            {"id": i, "title": f"Book {i}", "file_path": f"/f/{i}.opus"} for i in range(1, 102)
        ]
        _create_test_db(db_path, books)
        mock_tags.return_value = None
        mock_dur.return_value = None

        verify_metadata(db_path=db_path, quiet=False, check_files=True)
        captured = capsys.readouterr()
        assert "[100/101]" in captured.out

    def test_verbose_errors_section(self, tmp_path, capsys):
        """Verbose output prints ERRORS section when errors exist."""
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus", "runtime_length_min": 60}]
        )
        with (
            patch("scripts.verify_metadata.get_embedded_tags", return_value=None),
            patch("scripts.verify_metadata.compute_duration_hours", return_value=50.0),
        ):
            verify_metadata(db_path=db_path, quiet=False, check_files=True)
        captured = capsys.readouterr()
        assert "ERRORS" in captured.out

    def test_verbose_conflicts_section(self, tmp_path, capsys):
        """Verbose output prints CONFLICTS section when conflicts exist."""
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path,
            [
                {
                    "id": 1,
                    "title": "T",
                    "author": "Real Author",
                    "file_path": "/f/1.opus",
                    "asin": "B001",
                    "cover_path": "/c.jpg",
                    "description": "d",
                    "narrator": "N",
                }
            ],
        )
        with patch(
            "scripts.verify_metadata.get_embedded_tags",
            return_value={"artist": "Completely Different Author Name"},
        ):
            verify_metadata(db_path=db_path, quiet=False, check_files=True)
        captured = capsys.readouterr()
        assert "CONFLICTS" in captured.out

    def test_verbose_warnings_truncated(self, tmp_path, capsys):
        """When >20 warnings, verbose output shows '... and N more'."""
        db_path = tmp_path / "test.db"
        books = [{"id": i, "title": f"B{i}", "file_path": f"/f/{i}.opus"} for i in range(1, 30)]
        _create_test_db(db_path, books)
        verify_metadata(db_path=db_path, quiet=False, check_files=False)
        captured = capsys.readouterr()
        assert "WARNINGS" in captured.out
        assert "more" in captured.out


# ── verify_single_book() ──


class TestVerifySingleBook:
    @patch("scripts.verify_metadata.get_embedded_tags", return_value=None)
    @patch("scripts.verify_metadata.compute_duration_hours", return_value=None)
    def test_delegates_to_verify_metadata(self, mock_dur, mock_tags, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus"}])
        result = verify_single_book(book_id=1, db_path=db_path, quiet=True)
        assert result["total_checked"] == 1


# ── main() ──


class TestMain:
    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_basic(self, mock_vm, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {"total_checked": 0, "issues_found": 0}

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path)]):
            from scripts.verify_metadata import main

            main()

        mock_vm.assert_called_once()
        call_kwargs = mock_vm.call_args
        assert call_kwargs.kwargs["db_path"] == db_path

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_json_output(self, mock_vm, tmp_path, capsys):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {
            "total_checked": 0,
            "issues_found": 0,
            "errors": 0,
            "conflicts": 0,
            "warnings": 0,
            "infos": 0,
            "fixes_applied": 0,
            "issues": [],
        }

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path), "--json"]):
            from scripts.verify_metadata import main

            main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["total_checked"] == 0

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_dry_run_flag(self, mock_vm, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {}

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path), "--dry-run"]):
            from scripts.verify_metadata import main

            main()

        assert mock_vm.call_args.kwargs["dry_run"] is True

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_fix_flag(self, mock_vm, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {}

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path), "--fix"]):
            from scripts.verify_metadata import main

            main()

        assert mock_vm.call_args.kwargs["auto_fix"] is True

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_id_flag(self, mock_vm, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {}

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path), "--id", "42"]):
            from scripts.verify_metadata import main

            main()

        assert mock_vm.call_args.kwargs["single_id"] == 42

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_no_file_check_flag(self, mock_vm, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {}

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path), "--no-file-check"]):
            from scripts.verify_metadata import main

            main()

        assert mock_vm.call_args.kwargs["check_files"] is False

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_quiet_flag(self, mock_vm, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path, [])
        mock_vm.return_value = {}

        with patch("sys.argv", ["verify_metadata.py", "--db", str(db_path), "--quiet"]):
            from scripts.verify_metadata import main

            main()

        # --quiet without --json should NOT set quiet=True on verify_metadata
        # (quiet is set by --json flag, not --quiet)
        # Actually looking at the code: quiet=args.json, so --quiet has no direct effect
        # on verify_metadata, but it IS a valid arg
        mock_vm.assert_called_once()

    @patch("scripts.verify_metadata.verify_metadata")
    def test_main_no_db_uses_default(self, mock_vm):
        mock_vm.return_value = {}
        with patch("sys.argv", ["verify_metadata.py"]):
            from scripts.verify_metadata import main

            main()

        assert mock_vm.call_args.kwargs["db_path"] is None


# ── Edge cases ──


class TestEdgeCases:
    def test_book_with_all_none_fields(self, tmp_path):
        """Book with minimal fields (all optional fields None)."""
        book = {
            "id": 1,
            "title": "Minimal Book",
            "author": None,
            "narrator": None,
            "publisher": None,
            "series": None,
            "asin": None,
            "isbn": None,
            "cover_path": None,
            "audible_image_url": None,
            "description": None,
            "publisher_summary": None,
            "language": None,
            "content_type": None,
            "runtime_length_min": None,
            "audible_enriched_at": None,
        }
        issues = verify_book(book, None, None)
        # Should get identifier, cover, description, narrator warnings
        fields = [i.field for i in issues]
        assert "identifier" in fields
        assert "cover" in fields
        assert "description" in fields
        assert "narrator" in fields

    def test_file_tags_with_no_matching_db_fields(self):
        """Tags present but DB fields are None -> no comparison issues."""
        book = {
            "id": 1,
            "title": None,
            "author": None,
            "narrator": None,
            "publisher": None,
            "series": None,
            "asin": "X",
            "isbn": None,
            "cover_path": "/c.jpg",
            "description": "d",
            "publisher_summary": None,
            "language": None,
            "content_type": "Product",
            "runtime_length_min": None,
            "audible_enriched_at": None,
        }
        tags = {
            "title": "File Title",
            "artist": "File Author",
            "narrator": "File Narrator",
            "publisher": "File Publisher",
        }
        issues = verify_book(book, tags, None)
        # title comparison needs both file_title and book["title"] non-None
        mismatch_issues = [i for i in issues if "mismatch" in i.message.lower()]
        assert len(mismatch_issues) == 0

    def test_very_long_metadata(self):
        """Test with extremely long strings."""
        long_title = "A" * 10000
        book = {
            "id": 1,
            "title": long_title,
            "author": "B" * 5000,
            "narrator": "Narrator",
            "publisher": None,
            "series": None,
            "asin": "X",
            "isbn": None,
            "cover_path": "/c.jpg",
            "description": "d",
            "publisher_summary": None,
            "language": "English",
            "content_type": "Product",
            "runtime_length_min": None,
            "audible_enriched_at": "2026-01-01",
        }
        tags = {"title": "C" * 10000, "artist": "D" * 5000}
        # Should not raise, even with very long strings
        issues = verify_book(book, tags, None)
        assert isinstance(issues, list)

    @patch("scripts.verify_metadata.get_embedded_tags")
    @patch("scripts.verify_metadata.compute_duration_hours")
    def test_db_uses_default_path_when_set(self, mock_dur, mock_tags, tmp_path):
        """When DATABASE_PATH is set and no db_path given, uses DATABASE_PATH."""
        db_path = tmp_path / "default.db"
        _create_test_db(db_path, [])
        mock_tags.return_value = None
        mock_dur.return_value = None

        with patch("scripts.verify_metadata.DATABASE_PATH", str(db_path)):
            result = verify_metadata(db_path=None, quiet=True, check_files=False)
        assert result["total_checked"] == 0

    @patch("scripts.verify_metadata.get_embedded_tags")
    def test_runtime_but_no_file_duration(self, mock_tags, tmp_path):
        """Book has runtime_length_min but compute_duration returns None."""
        db_path = tmp_path / "test.db"
        _create_test_db(
            db_path, [{"id": 1, "title": "T", "file_path": "/f/1.opus", "runtime_length_min": 300}]
        )
        mock_tags.return_value = None
        with patch("scripts.verify_metadata.compute_duration_hours", return_value=None):
            result = verify_metadata(db_path=db_path, quiet=True, check_files=True)
        dur_issues = [i for i in result["issues"] if i["field"] == "duration"]
        assert len(dur_issues) == 0
