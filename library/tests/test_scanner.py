"""
Tests for the scanner module components.
Covers scan_audiobooks.py, find_missing_audiobooks.py, and create_priority_list.py
"""

import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Tests for translated/ chapter-artifact exclusion (Audiobook-Manager-2sw)
# =============================================================================


class TestCanonicalIteratorExclusions:
    """Regression guard for Audiobook-Manager-2sw — scanner ingested
    ``translated/`` chapter artifacts as standalone audiobook rows.

    ``scanner.utils.canonical.iter_canonical_audiobook_files`` is the single
    authoritative iterator (Audiobook-Manager-6cx) and MUST exclude:
    - ``.cover.<ext>`` cover-art sidecars
    - Anything under a ``translated/`` subdirectory
    """

    def test_excludes_translated_subdir(self):
        from scanner.utils.canonical import iter_canonical_audiobook_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book_dir = root / "Author" / "Book"
            book_dir.mkdir(parents=True)
            (book_dir / "Book.opus").touch()
            translated = book_dir / "translated"
            translated.mkdir()
            (translated / "Book.ch001.zh-Hans.opus").touch()

            result = list(iter_canonical_audiobook_files(root))
            names = [f.name for f in result]
            assert names == ["Book.opus"], (
                f"iterator should exclude translated/ chapter artifacts, got {names}"
            )

    def test_book_titled_Translated_not_excluded(self):
        """Defensive: a book whose title literally contains the word
        'Translated' (e.g. ``The Translated Soldier``) MUST still be
        ingested. Only directories NAMED ``translated`` are excluded —
        ``Path.parts`` membership, not substring match."""
        from scanner.utils.canonical import iter_canonical_audiobook_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Book directory whose name happens to contain "Translated"
            book = root / "Author" / "The Translated Soldier"
            book.mkdir(parents=True)
            (book / "The Translated Soldier.opus").touch()

            result = list(iter_canonical_audiobook_files(root))
            assert len(result) == 1
            assert result[0].name == "The Translated Soldier.opus"

    def test_50_chapter_translated_files_all_excluded(self):
        from scanner.utils.canonical import iter_canonical_audiobook_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "Author" / "Book"
            book.mkdir(parents=True)
            (book / "Book.opus").touch()
            translated = book / "translated"
            translated.mkdir()
            for i in range(50):
                (translated / f"Book.ch{i:03d}.zh-Hans.opus").touch()

            result = list(iter_canonical_audiobook_files(root))
            assert len(result) == 1
            assert result[0].name == "Book.opus"

    def test_excludes_cover_opus_alongside_translated(self):
        """Belt-and-suspenders — both filters applied together."""
        from scanner.utils.canonical import iter_canonical_audiobook_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "Author" / "Book"
            book.mkdir(parents=True)
            (book / "Book.opus").touch()
            (book / "Book.cover.opus").touch()
            translated = book / "translated"
            translated.mkdir()
            (translated / "Book.ch001.zh-Hans.opus").touch()

            result = list(iter_canonical_audiobook_files(root))
            assert len(result) == 1
            assert result[0].name == "Book.opus"

    def test_nonexistent_directory_yields_nothing(self):
        from scanner.utils.canonical import iter_canonical_audiobook_files

        result = list(iter_canonical_audiobook_files(Path("/nonexistent/path/xyz")))
        assert result == []

    def test_explicit_formats_restrict_extensions(self):
        from scanner.utils.canonical import iter_canonical_audiobook_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "Author" / "Book"
            book.mkdir(parents=True)
            (book / "Book.opus").touch()
            (book / "Book.m4b").touch()

            result = list(iter_canonical_audiobook_files(root, formats=[".opus"]))
            assert [f.name for f in result] == ["Book.opus"]


class TestConsumersUseCanonicalIterator:
    """Both ingest paths (incremental adder AND full rescan) must skip
    translated/ chapter artifacts and cover-art sidecars — verified through
    their public entry points now that both delegate to the canonical
    iterator (Audiobook-Manager-2sw / Audiobook-Manager-6cx).
    """

    def _make_library(self, root: Path) -> None:
        book = root / "Author" / "Book"
        book.mkdir(parents=True)
        (book / "Book.m4b").touch()
        (book / "Book.opus").touch()
        (book / "Book.cover.opus").touch()
        translated = book / "translated"
        translated.mkdir()
        (translated / "Book.ch001.zh-Hans.opus").touch()
        (translated / "Book.ch002.zh-Hans.opus").touch()

    def test_find_new_audiobooks_excludes_artifacts(self):
        from scanner.add_new_audiobooks import find_new_audiobooks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_library(root)

            result = find_new_audiobooks(root, set())
            assert {f.name for f in result} == {"Book.m4b", "Book.opus"}

    def test_find_audiobook_files_excludes_artifacts(self):
        from scanner.scan_audiobooks import find_audiobook_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_library(root)

            result = find_audiobook_files(root, [".m4b", ".opus"])
            assert {f.name for f in result} == {"Book.m4b", "Book.opus"}


# =============================================================================
# Tests for scan_audiobooks.py
# =============================================================================


class TestCalculateSha256:
    """Test the SHA-256 hash calculation function."""

    def test_calculate_sha256_simple_file(self, temp_dir):
        """Test hashing a simple file."""
        from common import calculate_sha256

        test_file = temp_dir / "test.txt"
        test_file.write_text("Hello, World!")

        result = calculate_sha256(test_file)

        # Verify it's a valid SHA-256 hash (64 hex characters)
        assert result is not None
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_calculate_sha256_consistent(self, temp_dir):
        """Test that same content produces same hash."""
        from common import calculate_sha256

        test_file = temp_dir / "test.txt"
        content = "Consistent content for hashing"
        test_file.write_text(content)

        hash1 = calculate_sha256(test_file)
        hash2 = calculate_sha256(test_file)

        assert hash1 == hash2

    def test_calculate_sha256_different_content(self, temp_dir):
        """Test that different content produces different hash."""
        from common import calculate_sha256

        file1 = temp_dir / "file1.txt"
        file2 = temp_dir / "file2.txt"
        file1.write_text("Content A")
        file2.write_text("Content B")

        hash1 = calculate_sha256(file1)
        hash2 = calculate_sha256(file2)

        assert hash1 != hash2

    def test_calculate_sha256_nonexistent_file(self, temp_dir):
        """Test hashing a file that doesn't exist."""
        from common import calculate_sha256

        nonexistent = temp_dir / "nonexistent.txt"
        result = calculate_sha256(nonexistent)

        assert result is None

    def test_calculate_sha256_empty_file(self, temp_dir):
        """Test hashing an empty file."""
        from common import calculate_sha256

        empty_file = temp_dir / "empty.txt"
        empty_file.write_text("")

        result = calculate_sha256(empty_file)

        # Empty file should still produce a valid hash
        assert result is not None
        assert len(result) == 64
        # SHA-256 of empty string
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected


class TestCategorizeGenre:
    """Test genre categorization function."""

    def test_categorize_mystery(self):
        """Test mystery genre categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Mystery & Thriller")

        assert result["main"] == "fiction"
        assert result["sub"] == "mystery & thriller"
        assert result["original"] == "Mystery & Thriller"

    def test_categorize_science_fiction(self):
        """Test sci-fi genre categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Science Fiction")

        assert result["main"] == "fiction"
        assert result["sub"] == "science fiction"

    def test_categorize_biography(self):
        """Test biography categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Biography")

        assert result["main"] == "non-fiction"
        assert result["sub"] == "biography & memoir"

    def test_categorize_history(self):
        """Test history categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("American History")

        assert result["main"] == "non-fiction"
        assert result["sub"] == "history"

    def test_categorize_unknown(self):
        """Test unknown genre falls back to uncategorized."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Completely Unknown Genre XYZ")

        assert result["main"] == "uncategorized"
        assert result["sub"] == "general"
        assert result["original"] == "Completely Unknown Genre XYZ"

    def test_categorize_case_insensitive(self):
        """Test that categorization is case-insensitive."""
        from scanner.scan_audiobooks import categorize_genre

        result1 = categorize_genre("MYSTERY")
        result2 = categorize_genre("mystery")
        result3 = categorize_genre("Mystery")

        assert result1["main"] == result2["main"] == result3["main"]
        assert result1["sub"] == result2["sub"] == result3["sub"]

    def test_categorize_fantasy(self):
        """Test fantasy genre categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Epic Fantasy")

        assert result["main"] == "fiction"
        assert result["sub"] == "fantasy"

    def test_categorize_horror(self):
        """Test horror genre categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Horror")

        assert result["main"] == "fiction"
        assert result["sub"] == "horror"

    def test_categorize_self_help(self):
        """Test self-help categorization."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Self-Help & Personal Development")

        assert result["main"] == "non-fiction"
        assert result["sub"] == "self-help"


class TestDetermineLiteraryEra:
    """Test literary era determination function."""

    def test_era_classical(self):
        """Test classical era (pre-1800)."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("1750")
        assert "Classical" in result

    def test_era_19th_century(self):
        """Test 19th century era."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("1850")
        assert "19th Century" in result

    def test_era_early_20th(self):
        """Test early 20th century era."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("1925")
        assert "Early 20th Century" in result

    def test_era_late_20th(self):
        """Test late 20th century era."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("1985")
        assert "Late 20th Century" in result

    def test_era_21st_early(self):
        """Test early 21st century era."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("2005")
        assert "21st Century" in result
        assert "Early" in result

    def test_era_21st_modern(self):
        """Test modern 21st century era."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("2015")
        assert "21st Century" in result
        assert "Modern" in result

    def test_era_contemporary(self):
        """Test contemporary era (2020+)."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("2023")
        assert "Contemporary" in result

    def test_era_empty_string(self):
        """Test empty year string."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("")
        assert "Unknown Era" in result

    def test_era_none(self):
        """Test None value."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era(None)  # type: ignore[arg-type]
        assert "Unknown Era" in result

    def test_era_invalid_format(self):
        """Test invalid year format."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("not-a-year")
        assert "Unknown Era" in result

    def test_era_full_date(self):
        """Test full date format (extracts year)."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("2020-05-15")
        assert "Contemporary" in result


class TestGetFileMetadata:
    """Test metadata extraction from audio files."""

    @patch("scanner.metadata_utils.subprocess.run")
    @patch("scanner.metadata_utils.calculate_sha256")
    def test_get_file_metadata_success(self, mock_hash, mock_run, temp_dir):
        """Test successful metadata extraction."""
        from scanner.scan_audiobooks import get_file_metadata

        # Create a test file
        test_file = temp_dir / "Library" / "Author Name" / "Book Title" / "audiobook.opus"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"fake audio content" * 1000)

        # Mock ffprobe output
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {
                        "duration": "36000",  # 10 hours
                        "tags": {
                            "title": "Test Book",
                            "artist": "Test Author",
                            "composer": "Test Narrator",
                            "genre": "Science Fiction",
                            "date": "2020",
                            "publisher": "Test Publisher",
                        },
                    }
                }
            ),
        )
        mock_hash.return_value = "abc123" * 10 + "abcd"

        # Patch AUDIOBOOK_DIR to our temp directory
        with patch("scanner.scan_audiobooks.AUDIOBOOK_DIR", temp_dir):
            result = get_file_metadata(test_file)

        assert result is not None
        assert result["title"] == "Test Book"
        assert result["author"] == "Test Author"
        assert result["narrator"] == "Test Narrator"
        assert result["genre"] == "Science Fiction"
        assert result["duration_hours"] == 10.0

    @patch("scanner.metadata_utils.subprocess.run")
    def test_get_file_metadata_ffprobe_failure(self, mock_run, temp_dir):
        """Test metadata extraction when ffprobe fails."""
        from scanner.scan_audiobooks import get_file_metadata

        test_file = temp_dir / "test.opus"
        test_file.write_bytes(b"fake")

        mock_run.return_value = MagicMock(returncode=1, stderr="Error reading file")

        with patch("scanner.scan_audiobooks.AUDIOBOOK_DIR", temp_dir):
            result = get_file_metadata(test_file)

        assert result is None

    @patch("scanner.metadata_utils.subprocess.run")
    @patch("scanner.metadata_utils.calculate_sha256")
    def test_get_file_metadata_missing_tags(self, mock_hash, mock_run, temp_dir):
        """Test metadata extraction with missing tags."""
        from scanner.scan_audiobooks import get_file_metadata

        test_file = temp_dir / "Library" / "Unknown" / "test.opus"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"fake audio content")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"format": {"duration": "3600", "tags": {}}}),  # 1 hour  # No tags
        )
        mock_hash.return_value = None

        with patch("scanner.scan_audiobooks.AUDIOBOOK_DIR", temp_dir):
            result = get_file_metadata(test_file)

        assert result is not None
        # Should use filename as title when no tags
        assert result["title"] == "test"
        # Should extract author from path structure
        assert result["author"] == "Unknown"
        assert result["narrator"] == "Unknown Narrator"
        assert result["publisher"] == "Unknown Publisher"


class TestExtractCoverArt:
    """Test cover art extraction."""

    @patch("scanner.metadata_utils.subprocess.run")
    def test_extract_cover_art_success(self, mock_run, temp_dir):
        """Test successful cover art extraction."""
        from scanner.metadata_utils import extract_cover_art

        test_file = temp_dir / "audiobook.opus"
        test_file.write_bytes(b"fake audio")
        output_dir = temp_dir / "covers"
        output_dir.mkdir()

        # Mock successful ffmpeg extraction
        def create_cover_file(*args, **kwargs):
            # Create the cover file that ffmpeg would create
            file_hash = hashlib.md5(str(test_file).encode(), usedforsecurity=False).hexdigest()
            cover_path = output_dir / f"{file_hash}.jpg"
            cover_path.write_bytes(b"fake jpeg")
            return MagicMock(returncode=0)

        mock_run.side_effect = create_cover_file

        result = extract_cover_art(test_file, output_dir)

        assert result is not None
        assert result.endswith(".jpg")

    @patch("scanner.metadata_utils.subprocess.run")
    def test_extract_cover_art_failure(self, mock_run, temp_dir):
        """Test cover art extraction when ffmpeg fails."""
        from scanner.metadata_utils import extract_cover_art

        test_file = temp_dir / "audiobook.opus"
        test_file.write_bytes(b"fake audio")
        output_dir = temp_dir / "covers"
        output_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=1)

        result = extract_cover_art(test_file, output_dir)

        assert result is None

    def test_extract_cover_art_already_exists(self, temp_dir):
        """Test that existing cover art is reused."""
        from scanner.metadata_utils import extract_cover_art

        test_file = temp_dir / "audiobook.opus"
        test_file.write_bytes(b"fake audio")
        output_dir = temp_dir / "covers"
        output_dir.mkdir()

        # Pre-create the cover file
        cover_hash = hashlib.md5(str(test_file).encode(), usedforsecurity=False).hexdigest()
        cover_path = output_dir / f"{cover_hash}.jpg"
        cover_path.write_bytes(b"existing cover")

        result = extract_cover_art(test_file, output_dir)

        assert result == cover_path.name


# =============================================================================
# Tests for find_missing_audiobooks.py
# =============================================================================


class TestFindCorruptedFiles:
    """Test finding corrupted/empty audiobook files."""

    def test_find_corrupted_empty_files(self, temp_dir, monkeypatch):
        """Test finding empty audiobook files."""
        # Create mock AUDIOBOOK_DIR structure
        library_dir = temp_dir / "Library"
        library_dir.mkdir()

        # Create an empty file (corrupted)
        empty_file = library_dir / "test-AAX_44_128.m4b"
        empty_file.write_bytes(b"")  # Empty file

        # Create a valid file (not corrupted)
        valid_file = library_dir / "valid.opus"
        valid_file.write_bytes(b"valid audio content")

        # Patch the config
        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)

        from scanner.find_missing_audiobooks import find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 1
        assert result[0]["filename"] == "test-AAX_44_128.m4b"
        # Title should have quality indicators removed
        assert "-AAX" not in result[0]["title"]

    def test_find_corrupted_no_empty_files(self, temp_dir, monkeypatch):
        """Test when no corrupted files exist."""
        library_dir = temp_dir / "Library"
        library_dir.mkdir()

        # Create valid files only
        valid_file = library_dir / "valid.opus"
        valid_file.write_bytes(b"valid audio content")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)

        from scanner.find_missing_audiobooks import find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 0

    def test_find_corrupted_multiple_formats(self, temp_dir, monkeypatch):
        """Test finding corrupted files across multiple formats."""
        library_dir = temp_dir / "Library"
        library_dir.mkdir()

        # Create empty files in different formats
        for ext in [".m4b", ".opus", ".mp3"]:
            empty_file = library_dir / f"empty{ext}"
            empty_file.write_bytes(b"")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)

        from scanner.find_missing_audiobooks import find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 3
        extensions = {r["extension"] for r in result}
        assert extensions == {".m4b", ".opus", ".mp3"}

    def test_find_corrupted_title_cleanup(self, temp_dir, monkeypatch):
        """Test that titles are properly cleaned up."""
        library_dir = temp_dir / "Library"
        library_dir.mkdir()

        empty_file = library_dir / "The_Great_Book-AAX_22_64.m4b"
        empty_file.write_bytes(b"")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)

        from scanner.find_missing_audiobooks import find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 1
        # Underscores should be replaced with spaces
        assert "_" not in result[0]["title"]
        # Quality indicator should be removed
        assert "AAX" not in result[0]["title"]


class TestFindMissingMain:
    """Test the main function of find_missing_audiobooks."""

    def test_main_no_corrupted(self, temp_dir, monkeypatch, capsys):
        """Test main when no corrupted files found."""
        library_dir = temp_dir / "Library"
        library_dir.mkdir()

        valid_file = library_dir / "valid.opus"
        valid_file.write_bytes(b"valid content")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)
        monkeypatch.chdir(temp_dir)

        from scanner.find_missing_audiobooks import main

        main()

        captured = capsys.readouterr()
        assert "No corrupted files found" in captured.out

    def test_main_with_corrupted(self, temp_dir, monkeypatch, capsys):
        """Test main when corrupted files are found."""
        library_dir = temp_dir / "Library"
        library_dir.mkdir()

        empty_file = library_dir / "corrupted.m4b"
        empty_file.write_bytes(b"")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)
        monkeypatch.setattr("scanner.find_missing_audiobooks.OUTPUT_CSV", temp_dir / "out.csv")
        monkeypatch.setattr("scanner.find_missing_audiobooks.OUTPUT_TXT", temp_dir / "out.txt")
        monkeypatch.chdir(temp_dir)

        from scanner.find_missing_audiobooks import main

        main()

        captured = capsys.readouterr()
        assert "corrupted/empty audiobook files" in captured.out
        # Check output files were created
        assert (temp_dir / "out.csv").exists()
        assert (temp_dir / "out.txt").exists()


class TestRemedyClassification:
    """Audiobook-Manager-aw9: findings are split by REMEDY, not filtered out.

    The report detects every zero-byte file — source media AND derived
    artifacts. What changed is that each row says which of two *different*
    actions fixes it. These tests pin both halves: the counts must not shrink
    (detection is unreduced) and each file must land in the right bucket.
    """

    @staticmethod
    def _build_mixed_tree(temp_dir):
        """Synthetic tree with one zero-byte file of each kind.

        Returns the AUDIOBOOK_DIR to scan. Contains:
          - a zero-byte SOURCE book            -> re-download
          - a zero-byte translated/ artifact   -> regenerate
          - a zero-byte cover-art sidecar      -> regenerate
          - a healthy book (must not be found at all)
        """
        library_dir = temp_dir / "Library"
        book_dir = library_dir / "Some Book"
        translated_dir = book_dir / "translated"
        translated_dir.mkdir(parents=True)

        (book_dir / "Some Book.m4b").write_bytes(b"")
        (translated_dir / "Some Book.ch001.zh-Hans.opus").write_bytes(b"")
        (book_dir / "Some Book.cover.opus").write_bytes(b"")
        (book_dir / "Healthy Book.opus").write_bytes(b"real audio bytes")

        return temp_dir

    def test_detection_is_not_reduced(self, temp_dir, monkeypatch):
        """All three damaged files are still reported — the healthy one is not.

        This is the guard on the operator decision: splitting the report must
        never become an excuse to stop detecting derived artifacts.
        """
        monkeypatch.setattr(
            "scanner.find_missing_audiobooks.AUDIOBOOK_DIR",
            self._build_mixed_tree(temp_dir),
        )

        from scanner.find_missing_audiobooks import find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 3, f"detection shrank: {[r['filename'] for r in result]}"
        assert {r["filename"] for r in result} == {
            "Some Book.m4b",
            "Some Book.ch001.zh-Hans.opus",
            "Some Book.cover.opus",
        }

    def test_each_file_lands_in_the_correct_remedy_bucket(self, temp_dir, monkeypatch):
        """Source media -> re-download; translated/ and cover art -> regenerate."""
        monkeypatch.setattr(
            "scanner.find_missing_audiobooks.AUDIOBOOK_DIR",
            self._build_mixed_tree(temp_dir),
        )

        from scanner.find_missing_audiobooks import (
            REMEDY_REDOWNLOAD,
            REMEDY_REGENERATE,
            find_corrupted_files,
        )

        by_name = {r["filename"]: r["remedy"] for r in find_corrupted_files()}

        assert by_name["Some Book.m4b"] == REMEDY_REDOWNLOAD
        assert by_name["Some Book.ch001.zh-Hans.opus"] == REMEDY_REGENERATE
        assert by_name["Some Book.cover.opus"] == REMEDY_REGENERATE

    def test_remedy_bucket_counts(self, temp_dir, monkeypatch):
        """One re-download, two regenerate — totalling every detected file."""
        monkeypatch.setattr(
            "scanner.find_missing_audiobooks.AUDIOBOOK_DIR",
            self._build_mixed_tree(temp_dir),
        )

        from scanner.find_missing_audiobooks import (
            REMEDY_REDOWNLOAD,
            REMEDY_REGENERATE,
            _group_by_remedy,
            find_corrupted_files,
        )

        corrupted = find_corrupted_files()
        by_remedy = _group_by_remedy(corrupted)

        assert len(by_remedy[REMEDY_REDOWNLOAD]) == 1
        assert len(by_remedy[REMEDY_REGENERATE]) == 2
        assert sum(len(v) for v in by_remedy.values()) == len(corrupted)

    def test_stray_aaxc_in_library_is_redownload(self, temp_dir, monkeypatch):
        """A zero-byte ``.aaxc`` original is source media, not a derived artifact."""
        library_dir = temp_dir / "Library" / "Some Book"
        library_dir.mkdir(parents=True)
        (library_dir / "Some Book.aaxc").write_bytes(b"")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)

        from scanner.find_missing_audiobooks import REMEDY_REDOWNLOAD, find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 1
        assert result[0]["remedy"] == REMEDY_REDOWNLOAD

    def test_book_titled_translated_is_not_misclassified(self, temp_dir, monkeypatch):
        """Only a directory NAMED ``translated`` marks an artifact as derived.

        Inherited from ``canonical.is_canonical_audiobook_file`` — asserted
        here so a future substring-match regression fails in this report too.
        """
        book_dir = temp_dir / "Library" / "The Translated Soldier"
        book_dir.mkdir(parents=True)
        (book_dir / "The Translated Soldier.m4b").write_bytes(b"")

        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)

        from scanner.find_missing_audiobooks import REMEDY_REDOWNLOAD, find_corrupted_files

        result = find_corrupted_files()

        assert len(result) == 1
        assert result[0]["remedy"] == REMEDY_REDOWNLOAD


class TestRemedyReportOutput:
    """The remedy split has to reach the operator, not just the data structure."""

    def test_csv_has_remedy_column_and_keeps_consumer_columns(self, temp_dir, monkeypatch):
        """``remedy`` is APPENDED — the columns create_priority_list.py reads survive."""
        library_dir = temp_dir / "Library" / "Some Book"
        translated_dir = library_dir / "translated"
        translated_dir.mkdir(parents=True)
        (library_dir / "Some Book.m4b").write_bytes(b"")
        (translated_dir / "Some Book.ch001.zh-Hans.opus").write_bytes(b"")

        out_csv = temp_dir / "out.csv"
        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)
        monkeypatch.setattr("scanner.find_missing_audiobooks.OUTPUT_CSV", out_csv)
        monkeypatch.setattr("scanner.find_missing_audiobooks.OUTPUT_TXT", temp_dir / "out.txt")
        monkeypatch.chdir(temp_dir)

        from scanner.find_missing_audiobooks import main

        main()

        with open(out_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        # Existing consumer columns unchanged, in their original order.
        assert fieldnames[:5] == ["title", "filename", "directory", "extension", "path"]
        assert fieldnames[5] == "remedy"

        remedies = {r["filename"]: r["remedy"] for r in rows}
        assert remedies["Some Book.m4b"] == "re-download"
        assert remedies["Some Book.ch001.zh-Hans.opus"] == "regenerate"

    def test_text_report_separates_the_two_remedies(self, temp_dir, monkeypatch, capsys):
        """The txt report no longer tells the operator to re-download an artifact."""
        library_dir = temp_dir / "Library" / "Some Book"
        translated_dir = library_dir / "translated"
        translated_dir.mkdir(parents=True)
        (library_dir / "Some Book.m4b").write_bytes(b"")
        (translated_dir / "Some Book.ch001.zh-Hans.opus").write_bytes(b"")

        out_txt = temp_dir / "out.txt"
        monkeypatch.setattr("scanner.find_missing_audiobooks.AUDIOBOOK_DIR", temp_dir)
        monkeypatch.setattr("scanner.find_missing_audiobooks.OUTPUT_CSV", temp_dir / "out.csv")
        monkeypatch.setattr("scanner.find_missing_audiobooks.OUTPUT_TXT", out_txt)
        monkeypatch.chdir(temp_dir)

        from scanner.find_missing_audiobooks import main

        main()

        content = out_txt.read_text()
        redownload_at = content.index("RE-DOWNLOAD —")
        regenerate_at = content.index("REGENERATE —")
        assert redownload_at < regenerate_at

        # Each file appears in its own section, not the other one.
        redownload_section = content[redownload_at:regenerate_at]
        regenerate_section = content[regenerate_at:]
        assert "Some Book.m4b" in redownload_section
        assert "Some Book.ch001.zh-Hans.opus" not in redownload_section
        assert "Some Book.ch001.zh-Hans.opus" in regenerate_section

        # The Audible instructions are scoped to the section they apply to.
        assert "Log in to your Audible account" in redownload_section
        assert "Log in to your Audible account" not in regenerate_section

        # Console output surfaces the split too.
        captured = capsys.readouterr()
        assert "Breakdown by remedy:" in captured.out


# =============================================================================
# Tests for create_priority_list.py
# =============================================================================


class TestCreatePriorityList:
    """Test priority list creation."""

    def test_create_priority_list_filters_covers(self, temp_dir, monkeypatch):
        """Test that cover files are filtered out."""
        # Create input CSV
        input_csv = temp_dir / "missing_audiobooks.csv"
        with open(input_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["title", "filename", "directory", "extension", "path"]
            )
            writer.writeheader()
            # Actual audiobook
            writer.writerow(
                {
                    "title": "Real Book",
                    "filename": "real_book.m4b",
                    "directory": "Sources",
                    "extension": ".m4b",
                    "path": "Sources/real_book.m4b",
                }
            )
            # Cover file (should be filtered)
            writer.writerow(
                {
                    "title": "Cover Image",
                    "filename": "book.cover.jpg",
                    "directory": "Sources",
                    "extension": ".jpg",
                    "path": "Sources/book.cover.jpg",
                }
            )

        output_txt = temp_dir / "priority.txt"

        monkeypatch.setattr("scanner.create_priority_list.INPUT_CSV", input_csv)
        monkeypatch.setattr("scanner.create_priority_list.OUTPUT_TXT", output_txt)

        from scanner.create_priority_list import main

        main()

        # Read output and verify only real audiobook is included
        content = output_txt.read_text()
        assert "Real Book" in content
        assert "Cover Image" not in content
        assert "1 audiobook" in content  # Should say 1 file

    def test_create_priority_list_empty_input(self, temp_dir, monkeypatch):
        """Test handling empty input."""
        input_csv = temp_dir / "missing_audiobooks.csv"
        with open(input_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["title", "filename", "directory", "extension", "path"]
            )
            writer.writeheader()
            # No data rows

        output_txt = temp_dir / "priority.txt"

        monkeypatch.setattr("scanner.create_priority_list.INPUT_CSV", input_csv)
        monkeypatch.setattr("scanner.create_priority_list.OUTPUT_TXT", output_txt)

        from scanner.create_priority_list import main

        main()

        content = output_txt.read_text()
        assert "0 audiobook" in content

    def test_create_priority_list_grouped_by_directory(self, temp_dir, monkeypatch):
        """Test that output is grouped by directory."""
        input_csv = temp_dir / "missing_audiobooks.csv"
        with open(input_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["title", "filename", "directory", "extension", "path"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "title": "Book A",
                    "filename": "book_a.m4b",
                    "directory": "Dir1",
                    "extension": ".m4b",
                    "path": "Dir1/book_a.m4b",
                }
            )
            writer.writerow(
                {
                    "title": "Book B",
                    "filename": "book_b.m4b",
                    "directory": "Dir2",
                    "extension": ".m4b",
                    "path": "Dir2/book_b.m4b",
                }
            )

        output_txt = temp_dir / "priority.txt"

        monkeypatch.setattr("scanner.create_priority_list.INPUT_CSV", input_csv)
        monkeypatch.setattr("scanner.create_priority_list.OUTPUT_TXT", output_txt)

        from scanner.create_priority_list import main

        main()

        content = output_txt.read_text()
        # Both directories should appear
        assert "DIRECTORY: Dir1" in content
        assert "DIRECTORY: Dir2" in content
