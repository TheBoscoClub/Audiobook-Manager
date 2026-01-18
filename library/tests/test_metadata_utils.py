"""
Tests for scanner metadata utilities module.

This module provides metadata extraction functions for audiobook files,
including author/narrator extraction, topic detection, and ffprobe integration.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestExtractTopics:
    """Test the extract_topics function."""

    def test_extracts_war_topic(self):
        """Test extracts war-related topics."""
        from scanner.metadata_utils import extract_topics

        description = "A gripping tale of battle and military conflict."
        result = extract_topics(description)

        assert "war" in result

    def test_extracts_adventure_topic(self):
        """Test extracts adventure-related topics."""
        from scanner.metadata_utils import extract_topics

        description = "An epic journey and quest across uncharted lands."
        result = extract_topics(description)

        assert "adventure" in result

    def test_extracts_technology_topic(self):
        """Test extracts technology-related topics."""
        from scanner.metadata_utils import extract_topics

        description = "A story about artificial intelligence and computer systems."
        result = extract_topics(description)

        assert "technology" in result

    def test_extracts_multiple_topics(self):
        """Test extracts multiple topics from description."""
        from scanner.metadata_utils import extract_topics

        description = "A war story about family and society in political turmoil."
        result = extract_topics(description)

        assert len(result) >= 2

    def test_returns_general_when_no_matches(self):
        """Test returns 'general' when no topics match."""
        from scanner.metadata_utils import extract_topics

        description = "A simple story with no specific themes."
        result = extract_topics(description)

        assert result == ["general"]

    def test_case_insensitive(self):
        """Test topic extraction is case-insensitive."""
        from scanner.metadata_utils import extract_topics

        description = "MILITARY conflict and ADVENTURE"
        result = extract_topics(description)

        assert "war" in result or "adventure" in result


class TestExtractAuthorFromPath:
    """Test the extract_author_from_path function."""

    def test_extracts_author_from_standard_path(self):
        """Test extracts author from standard Library structure."""
        from scanner.metadata_utils import extract_author_from_path

        path = Path("/raid0/Audiobooks/Library/Stephen King/The Stand/book.opus")
        result = extract_author_from_path(path)

        assert result == "Stephen King"

    def test_returns_none_when_no_library(self):
        """Test returns None when 'Library' not in path."""
        from scanner.metadata_utils import extract_author_from_path

        path = Path("/raid0/Audiobooks/Random/Author/book.opus")
        result = extract_author_from_path(path)

        assert result is None

    def test_returns_none_when_path_too_short(self):
        """Test returns None when path ends at Library."""
        from scanner.metadata_utils import extract_author_from_path

        path = Path("/raid0/Library")
        result = extract_author_from_path(path)

        assert result is None

    def test_skips_audiobook_folder(self):
        """Test skips 'Audiobook' folder and uses next level."""
        from scanner.metadata_utils import extract_author_from_path

        path = Path("/raid0/Library/Audiobook/Stephen King/book.opus")
        result = extract_author_from_path(path)

        assert result == "Stephen King"

    def test_returns_none_when_audiobook_folder_is_last(self):
        """Test returns None when Audiobook folder has no children."""
        from scanner.metadata_utils import extract_author_from_path

        path = Path("/raid0/Library/Audiobook")
        result = extract_author_from_path(path)

        assert result is None


class TestExtractNarratorFromTags:
    """Test the extract_narrator_from_tags function."""

    def test_extracts_narrator_tag(self):
        """Test extracts narrator from 'narrator' tag."""
        from scanner.metadata_utils import extract_narrator_from_tags

        tags = {"narrator": "Frank Muller"}
        result = extract_narrator_from_tags(tags)

        assert result == "Frank Muller"

    def test_extracts_from_composer_tag(self):
        """Test extracts narrator from 'composer' tag."""
        from scanner.metadata_utils import extract_narrator_from_tags

        tags = {"composer": "Tim Robbins"}
        result = extract_narrator_from_tags(tags)

        assert result == "Tim Robbins"

    def test_extracts_from_performer_tag(self):
        """Test extracts narrator from 'performer' tag."""
        from scanner.metadata_utils import extract_narrator_from_tags

        tags = {"performer": "Scott Brick"}
        result = extract_narrator_from_tags(tags)

        assert result == "Scott Brick"

    def test_skips_if_same_as_author(self):
        """Test skips narrator field if same as author."""
        from scanner.metadata_utils import extract_narrator_from_tags

        tags = {"narrator": "Stephen King", "performer": "Will Patton"}
        result = extract_narrator_from_tags(tags, author="Stephen King")

        # Should skip narrator (same as author) and use performer
        assert result == "Will Patton"

    def test_returns_unknown_when_no_tags(self):
        """Test returns 'Unknown Narrator' when no matching tags."""
        from scanner.metadata_utils import extract_narrator_from_tags

        tags = {}
        result = extract_narrator_from_tags(tags)

        assert result == "Unknown Narrator"

    def test_case_insensitive_author_comparison(self):
        """Test author comparison is case-insensitive."""
        from scanner.metadata_utils import extract_narrator_from_tags

        tags = {"narrator": "STEPHEN KING", "composer": "Frank Muller"}
        result = extract_narrator_from_tags(tags, author="stephen king")

        assert result == "Frank Muller"


class TestRunFfprobe:
    """Test the run_ffprobe function."""

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_parsed_json(self, mock_run):
        """Test returns parsed JSON on success."""
        from scanner.metadata_utils import run_ffprobe

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"format": {"duration": "3600"}}',
        )

        result = run_ffprobe(Path("/test/book.opus"))

        assert result == {"format": {"duration": "3600"}}

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run, capsys):
        """Test returns None when ffprobe exits with error."""
        from scanner.metadata_utils import run_ffprobe

        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="File not found",
        )

        result = run_ffprobe(Path("/test/missing.opus"))

        assert result is None

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run, capsys):
        """Test returns None when ffprobe times out."""
        from scanner.metadata_utils import run_ffprobe

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

        result = run_ffprobe(Path("/test/large.opus"))

        assert result is None

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_none_on_invalid_json(self, mock_run, capsys):
        """Test returns None when ffprobe returns invalid JSON."""
        from scanner.metadata_utils import run_ffprobe

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json{",
        )

        result = run_ffprobe(Path("/test/book.opus"))

        assert result is None


class TestGetFileMetadata:
    """Test the get_file_metadata function."""

    @patch("scanner.metadata_utils.run_ffprobe")
    @patch("scanner.metadata_utils.calculate_sha256")
    def test_returns_none_when_ffprobe_fails(self, mock_hash, mock_ffprobe):
        """Test returns None when ffprobe returns None."""
        from scanner.metadata_utils import get_file_metadata

        mock_ffprobe.return_value = None

        result = get_file_metadata(Path("/test/book.opus"), Path("/test"))

        assert result is None

    @patch("scanner.metadata_utils.run_ffprobe")
    @patch("scanner.metadata_utils.calculate_sha256")
    def test_returns_metadata_dict(self, mock_hash, mock_ffprobe, temp_dir):
        """Test returns complete metadata dictionary."""
        from scanner.metadata_utils import get_file_metadata

        # Create a test file
        test_file = temp_dir / "book.opus"
        test_file.write_bytes(b"test content")

        mock_ffprobe.return_value = {
            "format": {
                "duration": "3600",
                "tags": {
                    "title": "Test Book",
                    "artist": "Test Author",
                    "narrator": "Test Narrator",
                },
            },
        }
        mock_hash.return_value = "abc123"

        result = get_file_metadata(test_file, temp_dir)

        assert result is not None
        assert result["title"] == "Test Book"
        assert result["author"] == "Test Author"
        assert result["sha256_hash"] == "abc123"

    @patch("scanner.metadata_utils.run_ffprobe")
    def test_handles_exception_gracefully(self, mock_ffprobe, capsys):
        """Test handles exceptions and returns None."""
        from scanner.metadata_utils import get_file_metadata

        mock_ffprobe.side_effect = Exception("Unexpected error")

        result = get_file_metadata(Path("/test/book.opus"), Path("/test"))

        assert result is None


class TestExtractCoverArt:
    """Test the extract_cover_art function."""

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_cover_filename_on_success(self, mock_run, temp_dir):
        """Test returns cover filename when extraction succeeds."""
        from scanner.metadata_utils import extract_cover_art

        # Create test file
        test_file = temp_dir / "book.opus"
        test_file.touch()
        cover_dir = temp_dir / "covers"
        cover_dir.mkdir()

        # Simulate successful extraction
        def create_cover(*args, **kwargs):
            # Get the output path from command
            cmd = args[0]
            output_path = Path(cmd[-1])
            output_path.touch()
            return MagicMock(returncode=0)

        mock_run.side_effect = create_cover

        result = extract_cover_art(test_file, cover_dir)

        assert result is not None
        assert result.endswith(".jpg")

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_none_on_failure(self, mock_run, temp_dir):
        """Test returns None when ffmpeg fails."""
        from scanner.metadata_utils import extract_cover_art

        test_file = temp_dir / "book.opus"
        test_file.touch()
        cover_dir = temp_dir / "covers"
        cover_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=1)

        result = extract_cover_art(test_file, cover_dir)

        assert result is None

    @patch("scanner.metadata_utils.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run, temp_dir):
        """Test returns None when ffmpeg times out."""
        from scanner.metadata_utils import extract_cover_art

        test_file = temp_dir / "book.opus"
        test_file.touch()
        cover_dir = temp_dir / "covers"
        cover_dir.mkdir()

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

        result = extract_cover_art(test_file, cover_dir)

        assert result is None

    def test_returns_existing_cover(self, temp_dir):
        """Test returns existing cover without re-extracting."""
        import hashlib

        from scanner.metadata_utils import extract_cover_art

        test_file = temp_dir / "book.opus"
        test_file.touch()
        cover_dir = temp_dir / "covers"
        cover_dir.mkdir()

        # Pre-create the cover file
        file_hash = hashlib.md5(
            str(test_file).encode(), usedforsecurity=False
        ).hexdigest()
        existing_cover = cover_dir / f"{file_hash}.jpg"
        existing_cover.touch()

        # Should return existing cover without calling ffmpeg
        with patch("scanner.metadata_utils.subprocess.run") as mock_run:
            result = extract_cover_art(test_file, cover_dir)
            mock_run.assert_not_called()

        assert result == existing_cover.name


class TestEnrichMetadata:
    """Test the enrich_metadata function."""

    def test_adds_genre_category(self):
        """Test adds genre categorization fields."""
        from scanner.metadata_utils import enrich_metadata

        metadata = {
            "genre": "Science Fiction",
            "year": "2020",
            "description": "A sci-fi story",
        }

        result = enrich_metadata(metadata)

        assert "genre_category" in result
        assert "genre_subcategory" in result
        assert "genre_original" in result

    def test_adds_literary_era(self):
        """Test adds literary era field."""
        from scanner.metadata_utils import enrich_metadata

        metadata = {"genre": "Fiction", "year": "2020", "description": "A modern story"}

        result = enrich_metadata(metadata)

        assert "literary_era" in result
        assert (
            "Contemporary" in result["literary_era"]
            or "Modern" in result["literary_era"]
        )

    def test_adds_topics(self):
        """Test adds topics from description."""
        from scanner.metadata_utils import enrich_metadata

        metadata = {
            "genre": "Fiction",
            "year": "2020",
            "description": "A war story about military conflict",
        }

        result = enrich_metadata(metadata)

        assert "topics" in result
        assert isinstance(result["topics"], list)

    def test_handles_missing_fields(self):
        """Test handles missing optional fields."""
        from scanner.metadata_utils import enrich_metadata

        metadata = {}  # Empty metadata

        result = enrich_metadata(metadata)

        # Should not raise, and should have default values
        assert "genre_category" in result
        assert "literary_era" in result
        assert "topics" in result


class TestTopicKeywords:
    """Test the TOPIC_KEYWORDS constant."""

    def test_contains_expected_topics(self):
        """Test TOPIC_KEYWORDS contains expected topic categories."""
        from scanner.metadata_utils import TOPIC_KEYWORDS

        assert "war" in TOPIC_KEYWORDS
        assert "adventure" in TOPIC_KEYWORDS
        assert "technology" in TOPIC_KEYWORDS
        assert "family" in TOPIC_KEYWORDS
        assert "politics" in TOPIC_KEYWORDS


class TestGenreTaxonomy:
    """Test the GENRE_TAXONOMY constant."""

    def test_contains_fiction_and_nonfiction(self):
        """Test GENRE_TAXONOMY has fiction and non-fiction categories."""
        from scanner.metadata_utils import GENRE_TAXONOMY

        assert "fiction" in GENRE_TAXONOMY
        assert "non-fiction" in GENRE_TAXONOMY

    def test_fiction_has_subcategories(self):
        """Test fiction has expected subcategories."""
        from scanner.metadata_utils import GENRE_TAXONOMY

        fiction = GENRE_TAXONOMY["fiction"]
        assert "mystery & thriller" in fiction
        assert "science fiction" in fiction
        assert "fantasy" in fiction
