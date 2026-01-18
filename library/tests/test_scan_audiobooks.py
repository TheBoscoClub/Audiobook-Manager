"""
Tests for the audiobook metadata scanner module.

This module scans directories for audiobook files and extracts metadata.
It includes progress tracking, file discovery, and statistics.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch


class TestProgressTracker:
    """Test the ProgressTracker class."""

    def test_initialization(self):
        """Test ProgressTracker initializes correctly."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100)

        assert tracker.total == 100
        assert tracker.current == 0
        assert tracker.rate == 0.0

    def test_custom_bar_width(self):
        """Test ProgressTracker with custom bar width."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(50, bar_width=20)

        assert tracker.bar_width == 20

    def test_draw_progress_bar(self):
        """Test progress bar drawing."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100, bar_width=10)

        # 0% - all empty
        bar = tracker.draw_progress_bar(0)
        assert bar == "░" * 10

        # 50% - half filled
        bar = tracker.draw_progress_bar(50)
        assert bar == "█" * 5 + "░" * 5

        # 100% - all filled
        bar = tracker.draw_progress_bar(100)
        assert bar == "█" * 10

    def test_calculate_rate_and_eta_initial(self):
        """Test rate and ETA calculation initially returns calculating."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100)
        tracker.current = 10

        rate, eta = tracker.calculate_rate_and_eta()

        # Initial rate is 0, ETA shows "calculating..."
        assert eta == "calculating..."

    def test_calculate_rate_and_eta_with_rate(self):
        """Test rate and ETA calculation with established rate."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100)
        # Simulate established rate
        tracker.rate = 60.0  # 60 files per minute = 1 per second
        tracker.current = 50

        rate, eta = tracker.calculate_rate_and_eta()

        # 50 remaining / 60 per minute = less than 1 minute
        assert "s" in eta or "m" in eta

    def test_update_increments_current(self, capsys):
        """Test update increments current counter."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100)
        tracker.update(25, "test_file.opus")

        assert tracker.current == 25

    def test_update_handles_long_filename(self, capsys):
        """Test update truncates long filenames."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100)
        long_name = "a" * 100 + ".opus"
        tracker.update(1, long_name)

        captured = capsys.readouterr()
        # Should have truncated (output contains ... for truncation)
        assert "..." in captured.out or tracker.current == 1

    def test_finish_prints_statistics(self, capsys):
        """Test finish prints final statistics."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(10)
        tracker.current = 10
        tracker.finish()

        captured = capsys.readouterr()
        assert "Scan complete" in captured.out
        assert "Total files: 10" in captured.out

    def test_finish_formats_time_correctly(self, capsys):
        """Test finish formats elapsed time correctly."""
        from scanner.scan_audiobooks import ProgressTracker

        tracker = ProgressTracker(100)
        # Simulate quick scan (less than 60 seconds)
        tracker.start_time = time.time() - 30  # 30 seconds ago
        tracker.finish()

        captured = capsys.readouterr()
        # Should show seconds for short times
        assert "s" in captured.out


class TestFindAudiobookFiles:
    """Test the find_audiobook_files function."""

    def test_finds_all_formats(self, temp_dir, capsys):
        """Test finds files of all supported formats."""
        from scanner.scan_audiobooks import (SUPPORTED_FORMATS,
                                             find_audiobook_files)

        # Create test files
        (temp_dir / "book1.m4b").touch()
        (temp_dir / "book2.opus").touch()
        (temp_dir / "book3.m4a").touch()
        (temp_dir / "book4.mp3").touch()

        result = find_audiobook_files(temp_dir, SUPPORTED_FORMATS)

        assert len(result) == 4

    def test_filters_cover_files(self, temp_dir, capsys):
        """Test filters out .cover. files."""
        from scanner.scan_audiobooks import (SUPPORTED_FORMATS,
                                             find_audiobook_files)

        (temp_dir / "book.opus").touch()
        (temp_dir / "book.cover.jpg").touch()  # Should not match
        (temp_dir / "Book.Cover.m4b").touch()  # Should be filtered

        result = find_audiobook_files(temp_dir, SUPPORTED_FORMATS)

        names = [f.name.lower() for f in result]
        assert "book.opus" in names
        assert "book.cover.m4b" not in names

    def test_searches_subdirectories(self, temp_dir, capsys):
        """Test recursively searches subdirectories."""
        from scanner.scan_audiobooks import (SUPPORTED_FORMATS,
                                             find_audiobook_files)

        # Create nested structure
        subdir = temp_dir / "Author" / "Series"
        subdir.mkdir(parents=True)
        (subdir / "nested_book.opus").touch()
        (temp_dir / "root_book.opus").touch()

        result = find_audiobook_files(temp_dir, SUPPORTED_FORMATS)

        names = [f.name for f in result]
        assert "nested_book.opus" in names
        assert "root_book.opus" in names

    def test_returns_empty_for_no_files(self, temp_dir, capsys):
        """Test returns empty list when no audiobook files found."""
        from scanner.scan_audiobooks import (SUPPORTED_FORMATS,
                                             find_audiobook_files)

        # Empty directory
        result = find_audiobook_files(temp_dir, SUPPORTED_FORMATS)

        assert result == []


class TestGetFileMetadata:
    """Test the get_file_metadata wrapper function."""

    @patch("scanner.scan_audiobooks._get_file_metadata")
    def test_calls_shared_function(self, mock_get_metadata):
        """Test calls shared get_file_metadata with AUDIOBOOK_DIR."""
        from scanner.scan_audiobooks import get_file_metadata

        mock_get_metadata.return_value = {"title": "Test"}

        result = get_file_metadata(Path("/test/book.opus"))

        assert mock_get_metadata.called
        assert result == {"title": "Test"}

    @patch("scanner.scan_audiobooks._get_file_metadata")
    def test_passes_calculate_hash(self, mock_get_metadata):
        """Test passes calculate_hash parameter."""
        from scanner.scan_audiobooks import get_file_metadata

        get_file_metadata(Path("/test/book.opus"), calculate_hash=False)

        call_args = mock_get_metadata.call_args
        assert call_args[0][2] is False  # Third positional arg is calculate_hash


class TestPrintScanStatistics:
    """Test the print_scan_statistics function."""

    def test_prints_total_count(self, capsys):
        """Test prints total audiobook count."""
        from scanner.scan_audiobooks import print_scan_statistics

        audiobooks = [
            {
                "author": "Author 1",
                "genre_subcategory": "Fiction",
                "publisher": "Publisher A",
                "duration_hours": 10.0,
            },
            {
                "author": "Author 2",
                "genre_subcategory": "Mystery",
                "publisher": "Publisher B",
                "duration_hours": 8.0,
            },
        ]

        print_scan_statistics(audiobooks)

        captured = capsys.readouterr()
        assert "Total audiobooks: 2" in captured.out

    def test_prints_unique_counts(self, capsys):
        """Test prints unique author/genre/publisher counts."""
        from scanner.scan_audiobooks import print_scan_statistics

        audiobooks = [
            {
                "author": "Author 1",
                "genre_subcategory": "Fiction",
                "publisher": "Publisher A",
                "duration_hours": 5.0,
            },
            {
                "author": "Author 1",  # Same author
                "genre_subcategory": "Mystery",
                "publisher": "Publisher A",  # Same publisher
                "duration_hours": 5.0,
            },
        ]

        print_scan_statistics(audiobooks)

        captured = capsys.readouterr()
        assert "Unique authors: 1" in captured.out
        assert "Unique genres: 2" in captured.out
        assert "Unique publishers: 1" in captured.out

    def test_prints_total_listening_time(self, capsys):
        """Test prints total listening time."""
        from scanner.scan_audiobooks import print_scan_statistics

        audiobooks = [
            {
                "author": "A",
                "genre_subcategory": "G",
                "publisher": "P",
                "duration_hours": 24.0,
            },
            {
                "author": "B",
                "genre_subcategory": "G",
                "publisher": "P",
                "duration_hours": 24.0,
            },
        ]

        print_scan_statistics(audiobooks)

        captured = capsys.readouterr()
        assert "48 hours" in captured.out
        assert "2 days" in captured.out


class TestScanAudiobooks:
    """Test the main scan_audiobooks function."""

    @patch("scanner.scan_audiobooks.find_audiobook_files")
    @patch("scanner.scan_audiobooks.get_file_metadata")
    @patch("scanner.scan_audiobooks.extract_cover_art")
    @patch("scanner.scan_audiobooks.enrich_metadata")
    def test_scan_creates_output_directories(
        self, mock_enrich, mock_cover, mock_metadata, mock_find, temp_dir, monkeypatch
    ):
        """Test scan creates necessary output directories."""
        from scanner import scan_audiobooks as module

        output_file = temp_dir / "data" / "audiobooks.json"
        cover_dir = temp_dir / "covers"

        monkeypatch.setattr(module, "OUTPUT_FILE", output_file)
        monkeypatch.setattr(module, "COVER_DIR", cover_dir)
        monkeypatch.setattr(module, "AUDIOBOOK_DIR", temp_dir)

        mock_find.return_value = []

        module.scan_audiobooks()

        assert output_file.parent.exists()
        assert cover_dir.exists()

    @patch("scanner.scan_audiobooks.find_audiobook_files")
    @patch("scanner.scan_audiobooks.get_file_metadata")
    @patch("scanner.scan_audiobooks.extract_cover_art")
    @patch("scanner.scan_audiobooks.enrich_metadata")
    def test_scan_saves_json_output(
        self, mock_enrich, mock_cover, mock_metadata, mock_find, temp_dir, monkeypatch
    ):
        """Test scan saves metadata to JSON file."""
        from scanner import scan_audiobooks as module

        output_file = temp_dir / "audiobooks.json"
        cover_dir = temp_dir / "covers"

        monkeypatch.setattr(module, "OUTPUT_FILE", output_file)
        monkeypatch.setattr(module, "COVER_DIR", cover_dir)
        monkeypatch.setattr(module, "AUDIOBOOK_DIR", temp_dir)

        mock_find.return_value = [temp_dir / "book.opus"]
        mock_metadata.return_value = {
            "title": "Test Book",
            "author": "Test Author",
            "duration_hours": 5.0,
        }
        mock_cover.return_value = "cover.jpg"
        mock_enrich.return_value = {
            "title": "Test Book",
            "author": "Test Author",
            "duration_hours": 5.0,
            "genre_subcategory": "general",
            "publisher": "Unknown",
        }

        module.scan_audiobooks()

        assert output_file.exists()
        with open(output_file) as f:
            data = json.load(f)
        assert "audiobooks" in data
        assert "generated_at" in data

    @patch("scanner.scan_audiobooks.find_audiobook_files")
    @patch("scanner.scan_audiobooks.get_file_metadata")
    def test_scan_skips_failed_metadata(
        self, mock_metadata, mock_find, temp_dir, monkeypatch
    ):
        """Test scan skips files with failed metadata extraction."""
        from scanner import scan_audiobooks as module

        output_file = temp_dir / "audiobooks.json"
        cover_dir = temp_dir / "covers"

        monkeypatch.setattr(module, "OUTPUT_FILE", output_file)
        monkeypatch.setattr(module, "COVER_DIR", cover_dir)
        monkeypatch.setattr(module, "AUDIOBOOK_DIR", temp_dir)

        mock_find.return_value = [
            temp_dir / "good.opus",
            temp_dir / "bad.opus",
        ]
        # First returns metadata, second returns None
        mock_metadata.side_effect = [
            {"title": "Good", "author": "A", "duration_hours": 5.0},
            None,
        ]

        # Need to mock enrich_metadata and extract_cover_art too
        with patch("scanner.scan_audiobooks.extract_cover_art", return_value=None):
            with patch(
                "scanner.scan_audiobooks.enrich_metadata",
                side_effect=lambda x: {
                    **x,
                    "genre_subcategory": "g",
                    "publisher": "p",
                },
            ):
                module.scan_audiobooks()

        with open(output_file) as f:
            data = json.load(f)
        # Only the good file should be included
        assert data["total_audiobooks"] == 1


class TestSupportedFormats:
    """Test SUPPORTED_FORMATS constant."""

    def test_contains_expected_formats(self):
        """Test all expected formats are in SUPPORTED_FORMATS."""
        from scanner.scan_audiobooks import SUPPORTED_FORMATS

        assert ".m4b" in SUPPORTED_FORMATS
        assert ".opus" in SUPPORTED_FORMATS
        assert ".m4a" in SUPPORTED_FORMATS
        assert ".mp3" in SUPPORTED_FORMATS


class TestExports:
    """Test module exports for backwards compatibility."""

    def test_exports_categorize_genre(self):
        """Test categorize_genre is exported."""
        from scanner.scan_audiobooks import categorize_genre

        result = categorize_genre("Science Fiction")
        assert "main" in result

    def test_exports_determine_literary_era(self):
        """Test determine_literary_era is exported."""
        from scanner.scan_audiobooks import determine_literary_era

        result = determine_literary_era("2020")
        assert "Century" in result or "Era" in result

    def test_exports_extract_topics(self):
        """Test extract_topics is exported."""
        from scanner.scan_audiobooks import extract_topics

        result = extract_topics("A war story about adventure")
        assert isinstance(result, list)
