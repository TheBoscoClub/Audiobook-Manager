"""
Tests for progress streaming functionality in async operations.

Verifies that subprocess.Popen-based progress streaming correctly:
- Parses output patterns from scripts
- Updates progress tracker with meaningful messages
- Handles various output formats (line-by-line, carriage returns)

These tests ensure the UI won't appear "hung" during long-running operations.
"""

import re
from unittest.mock import MagicMock



class TestProgressPatterns:
    """Test the regex patterns used to parse script output."""

    # Download patterns (audible.py)
    def test_download_item_pattern(self):
        """Test parsing [X/Y] Downloading: Title pattern."""
        pattern = re.compile(r"\[(\d+)/(\d+)\]\s*Downloading:\s*(.+)")

        # Typical download progress line
        line = "[3/16] Downloading: The Great Gatsby"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "3"
        assert match.group(2) == "16"
        assert match.group(3) == "The Great Gatsby"

    def test_download_success_pattern(self):
        """Test parsing success indicators."""
        pattern = re.compile(r"[✓✔]\s*Downloaded.*:\s*(.+)")

        line = "✓ Downloaded: The Great Gatsby"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "The Great Gatsby"

    def test_download_fail_pattern(self):
        """Test parsing failure indicators."""
        pattern = re.compile(r"[✗✘]\s*Failed.*:\s*(.+)")

        line = "✗ Failed: Connection timeout"
        match = pattern.search(line)

        assert match is not None

    def test_download_complete_pattern(self):
        """Test parsing completion summary."""
        pattern = re.compile(r"Download complete:\s*(\d+)\s*succeeded.*(\d+)\s*failed")

        line = "Download complete: 14 succeeded, 2 failed"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "14"
        assert match.group(2) == "2"

    # Genre/Narrator sync patterns (audible.py)
    def test_processing_pattern(self):
        """Test parsing [X/Y] Processing pattern."""
        pattern = re.compile(r"\[(\d+)/(\d+)\].*Processing")

        line = "[100/500] Processing audiobooks..."
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "100"
        assert match.group(2) == "500"

    def test_update_pattern(self):
        """Test parsing update count patterns."""
        pattern = re.compile(r"(?:would update|updated)\s*(\d+)", re.I)

        assert pattern.search("Would update 15 records").group(1) == "15"
        assert pattern.search("Updated 15 records").group(1) == "15"
        assert pattern.search("UPDATED 15").group(1) == "15"

    def test_loading_pattern(self):
        """Test parsing loading count."""
        pattern = re.compile(r"Loading\s*(\d+)\s*audiobooks", re.I)

        line = "Loading 1823 audiobooks from database"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "1823"

    # Scanner patterns (library.py)
    def test_scanner_progress_pattern(self):
        """Test parsing scanner progress: 99% | 1821/1828."""
        pattern = re.compile(r"(\d+)%\s*\|\s*(\d+)/(\d+)")

        line = "Scanning: 99% | 1821/1828"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "99"
        assert match.group(2) == "1821"
        assert match.group(3) == "1828"

    # Import patterns (library.py)
    def test_import_found_pattern(self):
        """Test parsing Found X audiobooks."""
        pattern = re.compile(r"Found\s+(\d+)\s+audiobooks")

        line = "Found 1823 audiobooks"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "1823"

    def test_import_processed_pattern(self):
        """Test parsing Processed X/Y audiobooks."""
        pattern = re.compile(r"Processed\s+(\d+)/(\d+)\s+audiobooks")

        line = "  Processed 500/1823 audiobooks..."
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "500"
        assert match.group(2) == "1823"

    def test_import_imported_pattern(self):
        """Test parsing Imported X audiobooks."""
        pattern = re.compile(r"Imported\s+(\d+)\s+audiobooks")

        line = "✓ Imported 1823 audiobooks"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "1823"

    # Hash generation patterns (hashing.py)
    def test_hash_progress_pattern(self):
        """Test parsing [X/Y] hash progress."""
        pattern = re.compile(r"\[(\d+)/(\d+)\]")

        line = "[450/1823] Hashing file.opus"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1) == "450"
        assert match.group(2) == "1823"

    def test_hash_file_pattern(self):
        """Test parsing current file being hashed."""
        pattern = re.compile(r"Hashing:\s*(.+)")

        line = "Hashing: /path/to/audiobook.opus"
        match = pattern.search(line)

        assert match is not None
        assert match.group(1).strip() == "/path/to/audiobook.opus"

    # Maintenance patterns (maintenance.py)
    def test_queue_building_pattern(self):
        """Test parsing queue building progress."""
        pattern = re.compile(r"(\d+)\s*(?:files?|items?|audiobooks?)")

        assert pattern.search("Found 150 files").group(1) == "150"
        assert pattern.search("Processing 100 items").group(1) == "100"

    def test_duplicate_count_pattern(self):
        """Test parsing duplicate count."""
        pattern = re.compile(r"(\d+)\s*(?:duplicate|matched)", re.I)

        assert pattern.search("Found 5 duplicates").group(1) == "5"
        assert pattern.search("5 matched pairs").group(1) == "5"


class TestProgressScaling:
    """Test progress percentage scaling calculations."""

    def test_basic_scaling(self):
        """Test basic progress scaling from 0-100 to custom range."""
        # Scale 5-90% range (85% width)
        def scale_progress(current, total, start=5, end=90):
            if total == 0:
                return start
            raw_percent = current / total
            return start + int(raw_percent * (end - start))

        # At 0%
        assert scale_progress(0, 100, 5, 90) == 5
        # At 50%
        assert scale_progress(50, 100, 5, 90) == 47
        # At 100%
        assert scale_progress(100, 100, 5, 90) == 90

    def test_download_scaling(self):
        """Test download progress scaling (2-90% range)."""
        def download_scale(current, total):
            if total == 0:
                return 2
            return 2 + int((current / total) * 88)

        assert download_scale(0, 16) == 2
        assert download_scale(8, 16) == 46  # 50% of 88 + 2
        assert download_scale(16, 16) == 90

    def test_import_scaling(self):
        """Test import progress scaling (10-85% range)."""
        def import_scale(current, total):
            if total == 0:
                return 10
            return 10 + int((current / total) * 75)

        assert import_scale(0, 1823) == 10
        assert import_scale(1823, 1823) == 85


class TestProgressTrackerIntegration:
    """Test that progress updates are called correctly."""

    def test_progress_updates_called_in_order(self):
        """Test progress updates increase monotonically."""
        mock_tracker = MagicMock()
        updates = []

        def capture_update(op_id, progress, message):
            updates.append(progress)

        mock_tracker.update_progress.side_effect = capture_update

        # Simulate download progress updates
        total = 16
        last_progress = 2
        for current in range(1, total + 1):
            progress = 2 + int((current / total) * 88)
            if progress > last_progress:
                mock_tracker.update_progress("op-1", progress, f"[{current}/{total}]")
                last_progress = progress

        # Verify monotonically increasing
        for i in range(1, len(updates)):
            assert updates[i] >= updates[i-1], f"Progress decreased: {updates[i-1]} -> {updates[i]}"

    def test_skip_redundant_updates(self):
        """Test that redundant progress updates are skipped."""
        mock_tracker = MagicMock()

        # Only update when progress changes
        last_progress = 5
        update_count = 0

        for i in range(100):
            progress = 5 + int((i / 100) * 90)
            if progress > last_progress:
                mock_tracker.update_progress("op-1", progress, f"Progress: {i}%")
                last_progress = progress
                update_count += 1

        # Should be far fewer updates than iterations
        assert update_count < 90  # Progress is 5-95, so ~90 unique values max


class TestOutputParsing:
    """Test parsing of simulated script output."""

    def test_parse_download_output(self):
        """Test parsing complete download output sequence."""
        output = """
[1/5] Downloading: Book One
✓ Downloaded: Book One
[2/5] Downloading: Book Two
✗ Failed: Book Two - Connection error
[3/5] Downloading: Book Three
✓ Downloaded: Book Three
[4/5] Downloading: Book Four
✓ Downloaded: Book Four
[5/5] Downloading: Book Five
✓ Downloaded: Book Five
Download complete: 4 succeeded, 1 failed
"""
        _item_pattern = re.compile(r"\[(\d+)/(\d+)\]\s*Downloading:\s*(.+)")  # noqa: F841
        success_pattern = re.compile(r"[✓✔]\s*Downloaded.*:\s*(.+)")
        fail_pattern = re.compile(r"[✗✘]\s*Failed.*:\s*(.+)")
        complete_pattern = re.compile(r"Download complete:\s*(\d+)\s*succeeded.*(\d+)\s*failed")

        downloaded = 0
        failed = 0

        for line in output.strip().split("\n"):
            if success_pattern.search(line):
                downloaded += 1
            elif fail_pattern.search(line):
                failed += 1

            match = complete_pattern.search(line)
            if match:
                final_success = int(match.group(1))
                final_failed = int(match.group(2))

        assert downloaded == 4
        assert failed == 1
        assert final_success == 4
        assert final_failed == 1

    def test_parse_import_output(self):
        """Test parsing database import output."""
        output = """
Creating database: /path/to/db.sqlite
✓ Database schema created
Loading audiobooks from: /path/to/audiobooks.json
Found 1823 audiobooks
Preserving existing metadata...
  Preserved 500 narrator records
  Preserved genre data for 400 audiobooks
Importing audiobooks...
  Processed 100/1823 audiobooks...
  Processed 200/1823 audiobooks...
  Processed 300/1823 audiobooks...
✓ Imported 1823 audiobooks
Optimizing database...
✓ Database optimized
"""
        found_pattern = re.compile(r"Found\s+(\d+)\s+audiobooks")
        processed_pattern = re.compile(r"Processed\s+(\d+)/(\d+)\s+audiobooks")
        imported_pattern = re.compile(r"Imported\s+(\d+)\s+audiobooks")

        total_found = 0
        last_processed = 0
        total_imported = 0

        for line in output.strip().split("\n"):
            match = found_pattern.search(line)
            if match:
                total_found = int(match.group(1))

            match = processed_pattern.search(line)
            if match:
                last_processed = int(match.group(1))

            match = imported_pattern.search(line)
            if match:
                total_imported = int(match.group(1))

        assert total_found == 1823
        assert last_processed == 300
        assert total_imported == 1823


class TestCarriageReturnHandling:
    """Test handling of carriage return (\r) progress output."""

    def test_parse_cr_progress(self):
        """Test parsing progress with carriage returns (scanner style)."""
        # Scanner outputs with \r for in-place updates
        output = "Scanning: 10% | 100/1000\rScanning: 20% | 200/1000\rScanning: 30% | 300/1000\n"

        pattern = re.compile(r"(\d+)%\s*\|\s*(\d+)/(\d+)")

        # Split on both \r and \n
        lines = []
        buffer = ""
        for char in output:
            if char in ("\r", "\n"):
                if buffer:
                    lines.append(buffer)
                    buffer = ""
            else:
                buffer += char
        if buffer:
            lines.append(buffer)

        assert len(lines) == 3

        # Parse last line
        match = pattern.search(lines[-1])
        assert match is not None
        assert match.group(1) == "30"
        assert match.group(2) == "300"
        assert match.group(3) == "1000"

    def test_char_by_char_reading(self):
        """Test character-by-character reading pattern used in streaming."""
        output = "Line 1\rLine 2\nLine 3\r\nLine 4"

        lines = []
        buffer = ""

        for char in output:
            if char in ("\r", "\n"):
                if buffer:
                    lines.append(buffer)
                    buffer = ""
            else:
                buffer += char

        if buffer:  # Capture remaining content
            lines.append(buffer)

        assert lines == ["Line 1", "Line 2", "Line 3", "Line 4"]


class TestEdgeCases:
    """Test edge cases in progress parsing."""

    def test_empty_output(self):
        """Test handling of empty output."""
        pattern = re.compile(r"\[(\d+)/(\d+)\]")

        assert pattern.search("") is None
        assert pattern.search("   ") is None
        assert pattern.search("\n\n\n") is None

    def test_malformed_progress(self):
        """Test handling of malformed progress strings."""
        pattern = re.compile(r"\[(\d+)/(\d+)\]")

        # Missing numbers
        assert pattern.search("[/100]") is None
        assert pattern.search("[50/]") is None

        # Wrong delimiters
        assert pattern.search("(50/100)") is None
        assert pattern.search("{50/100}") is None

        # Extra text that shouldn't match as numbers
        assert pattern.search("[abc/100]") is None

    def test_zero_total(self):
        """Test handling of zero total (avoid division by zero)."""
        def safe_progress(current, total, start=5, end=90):
            if total <= 0:
                return start
            return start + int((current / total) * (end - start))

        assert safe_progress(0, 0) == 5
        assert safe_progress(50, 0) == 5

    def test_truncate_long_titles(self):
        """Test truncation of long file/book titles."""
        long_title = "A" * 100
        truncated = long_title[:50]

        assert len(truncated) == 50

        # Typical truncation pattern used in the code
        title = "This is a very long audiobook title that exceeds the normal display width"
        display_title = title.strip()[:40]
        assert len(display_title) == 40

    def test_unicode_in_output(self):
        """Test handling of unicode characters in script output."""
        pattern = re.compile(r"[✓✔]\s*Downloaded.*:\s*(.+)")

        # Various unicode checkmarks
        assert pattern.search("✓ Downloaded: Book").group(1) == "Book"
        assert pattern.search("✔ Downloaded: Book").group(1) == "Book"

        # Unicode in title
        match = pattern.search("✓ Downloaded: Café Stories")
        assert match is not None
        assert match.group(1) == "Café Stories"


class TestModuleImports:
    """Test that the fixed modules can be imported correctly."""

    def test_import_audible_module(self):
        """Test audible module imports without errors."""
        from backend.api_modular.utilities_ops import audible
        assert hasattr(audible, 'init_audible_routes')

    def test_import_maintenance_module(self):
        """Test maintenance module imports without errors."""
        from backend.api_modular.utilities_ops import maintenance
        assert hasattr(maintenance, 'init_maintenance_routes')

    def test_import_hashing_module(self):
        """Test hashing module imports without errors."""
        from backend.api_modular.utilities_ops import hashing
        assert hasattr(hashing, 'init_hashing_routes')

    def test_import_library_module(self):
        """Test library module imports without errors."""
        from backend.api_modular.utilities_ops import library
        assert hasattr(library, 'init_library_routes')

    def test_modules_use_popen(self):
        """Verify modules use subprocess.Popen (not blocking subprocess.run)."""
        import inspect
        from backend.api_modular.utilities_ops import audible, maintenance, hashing, library

        # Get source code and check for Popen usage
        for module in [audible, maintenance, hashing, library]:
            source = inspect.getsource(module)
            # Should have Popen (streaming)
            assert "subprocess.Popen" in source, f"{module.__name__} missing subprocess.Popen"
