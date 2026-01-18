"""
Tests for the common utility functions module.

The common module provides shared utilities used across the audiobook library:
- calculate_sha256: File hashing for integrity verification
- normalize_title: Title normalization for matching/deduplication
- sanitize_filename: Filename sanitization for safe file operations
"""


class TestCalculateSha256:
    """Test the calculate_sha256 function."""

    def test_hash_known_content(self, temp_dir):
        """Test SHA-256 hash of known content matches expected value."""
        from common import calculate_sha256

        test_file = temp_dir / "test.txt"
        test_file.write_bytes(b"hello world")

        # Known SHA-256 of "hello world"
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

        result = calculate_sha256(test_file)

        assert result == expected

    def test_hash_empty_file(self, temp_dir):
        """Test SHA-256 hash of empty file."""
        from common import calculate_sha256

        test_file = temp_dir / "empty.txt"
        test_file.write_bytes(b"")

        # Known SHA-256 of empty string
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

        result = calculate_sha256(test_file)

        assert result == expected

    def test_hash_binary_content(self, temp_dir):
        """Test SHA-256 hash of binary content."""
        from common import calculate_sha256

        test_file = temp_dir / "binary.bin"
        test_file.write_bytes(bytes(range(256)))

        result = calculate_sha256(test_file)

        assert result is not None
        assert len(result) == 64  # SHA-256 produces 64 hex chars

    def test_hash_nonexistent_file(self, temp_dir):
        """Test hashing non-existent file returns None."""
        from common import calculate_sha256

        result = calculate_sha256(temp_dir / "nonexistent.txt")

        assert result is None

    def test_hash_with_string_path(self, temp_dir):
        """Test function accepts string path as well as Path object."""
        from common import calculate_sha256

        test_file = temp_dir / "test.txt"
        test_file.write_bytes(b"test content")

        # Test with string path
        result = calculate_sha256(str(test_file))

        assert result is not None
        assert len(result) == 64

    def test_hash_large_file_chunked(self, temp_dir):
        """Test hashing a larger file uses chunking correctly."""
        from common import calculate_sha256

        test_file = temp_dir / "large.bin"
        # Create a 1MB file
        test_file.write_bytes(b"x" * (1024 * 1024))

        # Use small chunk size to test chunking
        result = calculate_sha256(test_file, chunk_size=1024)

        assert result is not None
        assert len(result) == 64


class TestNormalizeTitle:
    """Test the normalize_title function."""

    def test_empty_input(self):
        """Test empty string returns empty string."""
        from common import normalize_title

        assert normalize_title("") == ""

    def test_none_input(self):
        """Test None returns empty string."""
        from common import normalize_title

        assert normalize_title(None) == ""

    def test_simple_title(self):
        """Test simple title is lowercased."""
        from common import normalize_title

        assert normalize_title("The Great Gatsby") == "the great gatsby"

    def test_removes_unabridged_parentheses(self):
        """Test (Unabridged) suffix is removed."""
        from common import normalize_title

        result = normalize_title("Dune (Unabridged)")
        assert "unabridged" not in result
        assert result == "dune"

    def test_removes_unabridged_brackets(self):
        """Test [Unabridged] suffix is removed."""
        from common import normalize_title

        result = normalize_title("1984 [Unabridged]")
        assert result == "1984"

    def test_removes_tantor_suffix(self):
        """Test [Tantor] publisher suffix is removed."""
        from common import normalize_title

        result = normalize_title("The Hobbit [Tantor]")
        assert "tantor" not in result.lower()

    def test_removes_audible_suffix(self):
        """Test (Audible Audio Edition) suffix is removed."""
        from common import normalize_title

        result = normalize_title("Pride and Prejudice (Audible Audio Edition)")
        assert "audible" not in result.lower()

    def test_removes_novel_suffix(self):
        """Test ': A Novel' suffix is removed."""
        from common import normalize_title

        result = normalize_title("The Road: A Novel")
        assert "novel" not in result.lower()

    def test_removes_memoir_suffix(self):
        """Test ': A Memoir' suffix is removed."""
        from common import normalize_title

        result = normalize_title("Educated: A Memoir")
        assert "memoir" not in result.lower()

    def test_removes_punctuation(self):
        """Test punctuation is removed."""
        from common import normalize_title

        result = normalize_title("It's a Test! Really?")
        assert result == "its a test really"

    def test_collapses_whitespace(self):
        """Test multiple spaces are collapsed."""
        from common import normalize_title

        result = normalize_title("Title   with    spaces")
        assert "  " not in result
        assert result == "title with spaces"

    def test_case_insensitive_suffix_removal(self):
        """Test suffix removal is case-insensitive."""
        from common import normalize_title

        assert normalize_title("Book (UNABRIDGED)") == "book"
        assert normalize_title("Book [TANTOR]") == "book"

    def test_complex_title(self):
        """Test title with multiple suffixes and formatting."""
        from common import normalize_title

        # ": A Novel" suffix is removed, "(Unabridged)" is removed
        # But "Novel" as part of the title is kept
        title = "The Great Novel: A Novel (Unabridged)"
        result = normalize_title(title)

        # The suffix ": A Novel" is removed, leaving "The Great Novel"
        # which normalizes to "the great novel"
        assert result == "the great novel"
        assert "unabridged" not in result.lower()

    def test_removes_suffix_not_embedded(self):
        """Test that only suffix patterns are removed, not embedded words."""
        from common import normalize_title

        # ": A Novel" at end is a suffix and gets removed
        assert normalize_title("Story: A Novel") == "story"

        # But "Novel" in the middle of a title stays
        assert normalize_title("The Novel Approach") == "the novel approach"


class TestSanitizeFilename:
    """Test the sanitize_filename function."""

    def test_empty_input(self):
        """Test empty string returns 'Unknown'."""
        from common import sanitize_filename

        assert sanitize_filename("") == "Unknown"

    def test_none_input(self):
        """Test None returns 'Unknown'."""
        from common import sanitize_filename

        assert sanitize_filename(None) == "Unknown"

    def test_valid_filename_unchanged(self):
        """Test valid filename is returned unchanged."""
        from common import sanitize_filename

        assert sanitize_filename("valid_filename.txt") == "valid_filename.txt"

    def test_removes_invalid_chars(self):
        """Test invalid filename characters are removed."""
        from common import sanitize_filename

        # Test various invalid characters
        assert "<" not in sanitize_filename("file<name")
        assert ">" not in sanitize_filename("file>name")
        assert ":" not in sanitize_filename("file:name")
        assert '"' not in sanitize_filename('file"name')
        assert "/" not in sanitize_filename("file/name")
        assert "\\" not in sanitize_filename("file\\name")
        assert "|" not in sanitize_filename("file|name")
        assert "?" not in sanitize_filename("file?name")
        assert "*" not in sanitize_filename("file*name")

    def test_collapses_multiple_spaces(self):
        """Test multiple spaces are collapsed to single space."""
        from common import sanitize_filename

        result = sanitize_filename("file   with    spaces")
        assert "  " not in result

    def test_strips_whitespace_and_periods(self):
        """Test leading/trailing whitespace and periods are stripped."""
        from common import sanitize_filename

        assert sanitize_filename("  filename  ") == "filename"
        assert sanitize_filename("..filename..") == "filename"

    def test_max_length_truncation(self):
        """Test filename is truncated to max_length."""
        from common import sanitize_filename

        long_name = "a" * 300
        result = sanitize_filename(long_name, max_length=255)

        assert len(result) <= 255

    def test_custom_max_length(self):
        """Test custom max_length parameter."""
        from common import sanitize_filename

        long_name = "a" * 100
        result = sanitize_filename(long_name, max_length=50)

        assert len(result) == 50

    def test_returns_unknown_if_sanitized_to_empty(self):
        """Test returns 'Unknown' if all characters are invalid."""
        from common import sanitize_filename

        # All invalid characters should result in "Unknown"
        result = sanitize_filename('<>:"\\|?*')
        assert result == "Unknown"

    def test_preserves_valid_special_chars(self):
        """Test that hyphens, underscores, and periods are preserved."""
        from common import sanitize_filename

        result = sanitize_filename("file-name_test.txt")
        assert result == "file-name_test.txt"


class TestDefaultChunkSize:
    """Test the DEFAULT_CHUNK_SIZE constant."""

    def test_chunk_size_value(self):
        """Test DEFAULT_CHUNK_SIZE is 8MB."""
        from common import DEFAULT_CHUNK_SIZE

        assert DEFAULT_CHUNK_SIZE == 8 * 1024 * 1024
        assert DEFAULT_CHUNK_SIZE == 8388608
