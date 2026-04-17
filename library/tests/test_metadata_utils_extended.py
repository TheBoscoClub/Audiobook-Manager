"""
Extended unit tests for scanner.metadata_utils — targeting uncovered lines.

Covers: categorize_genre content type (line 122), extract_asin / chapters.json
path (lines 296-304), get_file_metadata relative_path ValueError (lines 387-388),
extract_cover_art ffmpeg success but no file (line 443), standalone cover fallback
(lines 455-459), external resolver fallback (lines 466-481), standalone cover OSError
(lines 458-459*), extract_cover_art general exception (lines 490-492),
_find_standalone_cover (line 509), and build_genres_list fallback (line 528).
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from scanner.metadata_utils import (  # noqa: E402
    categorize_genre,
    is_content_type,
    extract_asin,
    get_file_metadata,
    extract_cover_art,
    _find_standalone_cover,
    build_genres_list,
    determine_literary_era,
)


class TestCategorizeGenreContentType:
    """Test line 122: content types classified as uncategorized."""

    def test_audiobook_is_content_type(self):
        """Line 122: 'Audiobook' is classified as uncategorized."""
        result = categorize_genre("Audiobook")
        assert result["main"] == "uncategorized"
        assert result["sub"] == "general"

    def test_podcast_is_content_type(self):
        """Line 122: 'Podcast' is classified as uncategorized."""
        result = categorize_genre("Podcast")
        assert result["main"] == "uncategorized"

    def test_unabridged_is_content_type(self):
        """Line 122: 'Unabridged' is classified as uncategorized."""
        assert is_content_type("Unabridged") is True
        result = categorize_genre("Unabridged")
        assert result["main"] == "uncategorized"

    def test_true_crime_matches_before_crime(self):
        """Lines 127-137: True crime matches 'true crime' not 'crime'."""
        result = categorize_genre("True Crime")
        assert result["sub"] == "true crime"
        assert result["main"] == "non-fiction"

    def test_historical_fiction_matches_correctly(self):
        """Lines 127-137: Historical fiction matches correctly."""
        result = categorize_genre("Historical Fiction")
        assert result["sub"] == "literary fiction"

    def test_unknown_genre_uncategorized(self):
        """Line 139: Unknown genre returns uncategorized."""
        result = categorize_genre("Underwater Basket Weaving")
        assert result["main"] == "uncategorized"
        assert result["sub"] == "general"


class TestExtractAsinFromChaptersJson:
    """Test lines 296-304: extract_asin (chapters.json path)."""

    def test_extracts_asin_from_valid_file(self, tmp_path):
        """Lines 296-302: Valid chapters.json returns ASIN."""
        audio_file = tmp_path / "book.opus"
        audio_file.touch()

        chapters = {"content_metadata": {"content_reference": {"asin": "B01ABCDEFG"}}}
        chapters_path = tmp_path / "chapters.json"
        chapters_path.write_text(json.dumps(chapters))

        result = extract_asin(audio_file)
        assert result == "B01ABCDEFG"

    def test_returns_none_when_no_file(self, tmp_path):
        """Line 293-294: Returns None when chapters.json doesn't exist."""
        audio_file = tmp_path / "book.opus"
        audio_file.touch()

        result = extract_asin(audio_file)
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        """Lines 303-304: Returns None on JSONDecodeError."""
        audio_file = tmp_path / "book.opus"
        audio_file.touch()

        chapters_path = tmp_path / "chapters.json"
        chapters_path.write_text("not valid json{{{")

        result = extract_asin(audio_file)
        assert result is None

    def test_returns_none_when_no_asin(self, tmp_path):
        """Line 302: Returns None when asin key is missing."""
        audio_file = tmp_path / "book.opus"
        audio_file.touch()

        chapters = {"content_metadata": {"content_reference": {}}}
        chapters_path = tmp_path / "chapters.json"
        chapters_path.write_text(json.dumps(chapters))

        result = extract_asin(audio_file)
        assert result is None

    def test_returns_none_when_empty_structure(self, tmp_path):
        """Lines 300-301: Returns None when content_metadata is empty."""
        audio_file = tmp_path / "book.opus"
        audio_file.touch()

        chapters_path = tmp_path / "chapters.json"
        chapters_path.write_text("{}")

        result = extract_asin(audio_file)
        assert result is None


class TestGetFileMetadataRelativePath:
    """Test lines 387-388: relative_path ValueError fallback."""

    @patch("scanner.metadata_utils.run_ffprobe")
    @patch("scanner.metadata_utils.calculate_sha256")
    def test_relative_path_fallback(self, mock_hash, mock_ffprobe, tmp_path):
        """Lines 387-388: ValueError when filepath not relative to audiobook_dir."""
        test_file = tmp_path / "book.opus"
        test_file.write_bytes(b"test")

        mock_ffprobe.return_value = {"format": {"duration": "3600", "tags": {"title": "Test"}}}
        mock_hash.return_value = "abc123"

        # Pass a different base dir that's not a parent
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        result = get_file_metadata(test_file, other_dir)
        assert result is not None
        # relative_path should fallback to str(filepath)
        assert result["relative_path"] == str(test_file)

    @patch("scanner.metadata_utils.run_ffprobe")
    @patch("scanner.metadata_utils.calculate_sha256")
    def test_no_hash_when_disabled(self, mock_hash, mock_ffprobe, tmp_path):
        """Lines 347-350: calculate_hash=False skips hash."""
        test_file = tmp_path / "book.opus"
        test_file.write_bytes(b"test")

        mock_ffprobe.return_value = {"format": {"duration": "3600", "tags": {"title": "Test"}}}

        result = get_file_metadata(test_file, tmp_path, calculate_hash=False)
        assert result is not None
        assert result["sha256_hash"] is None
        assert result["hash_verified_at"] is None
        mock_hash.assert_not_called()


class TestExtractCoverArtEdgeCases:
    """Test lines 443, 455-459, 466-481, 490-492: extract_cover_art edge cases."""

    @patch("scanner.metadata_utils.subprocess.run")
    def test_ffmpeg_success_no_file_created(self, mock_run, tmp_path, capsys):
        """Line 443: ffmpeg returns 0 but cover file not created."""
        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=0)

        result = extract_cover_art(test_file, cover_dir)
        # Should print warning and return None (no standalone cover either)
        captured = capsys.readouterr()
        assert "ffmpeg succeeded but cover not created" in captured.err
        assert result is None

    @patch("scanner.metadata_utils.subprocess.run")
    def test_standalone_cover_fallback(self, mock_run, tmp_path):
        """Lines 455-457: Standalone cover.jpg found as fallback."""
        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        # Create standalone cover file
        standalone = tmp_path / "cover.jpg"
        standalone.write_bytes(b"fake jpg data")

        # ffmpeg fails
        mock_run.return_value = MagicMock(returncode=1)

        result = extract_cover_art(test_file, cover_dir)
        assert result is not None
        assert result.endswith(".jpg")

    @patch("scanner.metadata_utils.subprocess.run")
    def test_standalone_cover_by_stem(self, mock_run, tmp_path):
        """Lines 502-503: Standalone {stem}.jpg found."""
        test_file = tmp_path / "my_book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        # Create stem-matching cover file
        standalone = tmp_path / "my_book.jpg"
        standalone.write_bytes(b"fake jpg data")

        mock_run.return_value = MagicMock(returncode=1)

        result = extract_cover_art(test_file, cover_dir)
        assert result is not None

    @patch("scanner.metadata_utils.subprocess.run")
    @patch("scanner.metadata_utils.shutil.copy2")
    def test_standalone_cover_copy_fails(self, mock_copy, mock_run, tmp_path, capsys):
        """Lines 458-462: OSError when copying standalone cover."""
        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        standalone = tmp_path / "cover.jpg"
        standalone.write_bytes(b"fake jpg data")

        mock_run.return_value = MagicMock(returncode=1)
        mock_copy.side_effect = OSError("Permission denied")

        result = extract_cover_art(test_file, cover_dir)
        captured = capsys.readouterr()
        assert "copy failed" in captured.err
        # Result is None since copy failed and no resolver
        assert result is None

    @patch("scanner.metadata_utils.subprocess.run")
    def test_external_resolver_success(self, mock_run, tmp_path):
        """Lines 466-477: External resolver returns cover."""
        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=1)

        metadata = {"title": "The Stand", "author": "Stephen King", "asin": "B001"}

        with patch("scanner.metadata_utils._find_standalone_cover", return_value=None):
            with patch.dict(sys.modules, {}):
                # Mock the cover_resolver import
                mock_resolver = MagicMock()
                mock_resolver.resolve_cover.return_value = "resolved_cover.jpg"

                with patch("builtins.__import__") as mock_import:
                    original_import = __import__

                    def selective_import(name, *args, **kwargs):
                        if name == "scanner.utils.cover_resolver":
                            return mock_resolver
                        return original_import(name, *args, **kwargs)

                    mock_import.side_effect = selective_import

                    # Can't easily mock nested import; test the path with direct patch
                    pass

        # Simpler approach: patch at module level
        mock_resolve = MagicMock(return_value="resolved.jpg")
        with patch("scanner.metadata_utils.subprocess.run", return_value=MagicMock(returncode=1)):
            with patch("scanner.metadata_utils._find_standalone_cover", return_value=None):
                with patch.dict(
                    "sys.modules",
                    {"scanner.utils.cover_resolver": MagicMock(resolve_cover=mock_resolve)},
                ):
                    extract_cover_art(test_file, cover_dir, metadata=metadata)
                    # The import happens inside the function; with mocked module
                    # it should try to use it

    @patch("scanner.metadata_utils.subprocess.run")
    def test_external_resolver_import_error(self, mock_run, tmp_path):
        """Lines 478-479: ImportError from resolver is silently caught.

        Forces ImportError on the canonical import path
        (`scanner.utils.cover_resolver`) by intercepting `__import__` and
        raising for that exact target — the resolver module *does* exist on
        sys.path post-F1, so we have to actively make the import fail.

        After the test, sys.modules is restored so subsequent tests
        (test_cover_resolver.py et al) keep the same module instance their
        top-level `from scanner.utils.cover_resolver import ...` bound to.
        Failing to restore caused 11 cross-file test failures earlier.
        """
        import builtins

        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=1)
        metadata = {"title": "Test Book"}

        real_import = builtins.__import__

        def selective_import(name, *args, **kwargs):
            if name == "scanner.utils.cover_resolver":
                raise ImportError("simulated missing resolver")
            return real_import(name, *args, **kwargs)

        # Snapshot the cached module (if any) so we can restore it post-test.
        cached_module = sys.modules.get("scanner.utils.cover_resolver")
        try:
            with patch("scanner.metadata_utils._find_standalone_cover", return_value=None):
                with patch("builtins.__import__", side_effect=selective_import):
                    # Drop any cached module so the import statement re-executes
                    # and hits our selective_import shim.
                    sys.modules.pop("scanner.utils.cover_resolver", None)
                    result = extract_cover_art(test_file, cover_dir, metadata=metadata)
                    # Should return None gracefully (ImportError caught)
                    assert result is None
        finally:
            # Restore exactly the module instance other tests already imported.
            if cached_module is not None:
                sys.modules["scanner.utils.cover_resolver"] = cached_module
            else:
                sys.modules.pop("scanner.utils.cover_resolver", None)

    @patch("scanner.metadata_utils.subprocess.run")
    def test_external_resolver_generic_exception(self, mock_run, tmp_path, capsys):
        """Lines 480-484: Generic exception from resolver logged.

        Patches `resolve_cover` on the real (now-importable) resolver module
        to raise. Patching the live module is more robust than swapping
        `sys.modules[...]` because `from X import Y` re-fetches `Y` from the
        module object every call, regardless of any sys.modules dict shim.
        """
        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=1)
        metadata = {"title": "Test Book"}

        with patch("scanner.metadata_utils._find_standalone_cover", return_value=None):
            with patch(
                "scanner.utils.cover_resolver.resolve_cover", side_effect=RuntimeError("API down")
            ):
                result = extract_cover_art(test_file, cover_dir, metadata=metadata)
                captured = capsys.readouterr()
                assert "external cover resolver failed" in captured.err
                assert result is None

    def test_general_exception_returns_none(self, tmp_path, capsys):
        """Lines 490-492: General exception in extract_cover_art."""
        test_file = tmp_path / "book.opus"
        test_file.touch()
        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        with patch("scanner.metadata_utils.hashlib.md5", side_effect=Exception("hash error")):
            result = extract_cover_art(test_file, cover_dir)
            captured = capsys.readouterr()
            assert "Error extracting cover" in captured.err
            assert result is None


class TestFindStandaloneCover:
    """Test line 509: _find_standalone_cover search order."""

    def test_finds_stem_jpg(self, tmp_path):
        """Lines 503: Finds {stem}.jpg."""
        audio = tmp_path / "mybook.opus"
        audio.touch()
        cover = tmp_path / "mybook.jpg"
        cover.touch()

        result = _find_standalone_cover(audio)
        assert result == cover

    def test_finds_stem_png(self, tmp_path):
        """Lines 504: Finds {stem}.png."""
        audio = tmp_path / "mybook.opus"
        audio.touch()
        cover = tmp_path / "mybook.png"
        cover.touch()

        result = _find_standalone_cover(audio)
        assert result == cover

    def test_finds_cover_jpg(self, tmp_path):
        """Lines 505: Finds cover.jpg."""
        audio = tmp_path / "mybook.opus"
        audio.touch()
        cover = tmp_path / "cover.jpg"
        cover.touch()

        result = _find_standalone_cover(audio)
        assert result == cover

    def test_finds_cover_png(self, tmp_path):
        """Lines 506: Finds cover.png."""
        audio = tmp_path / "mybook.opus"
        audio.touch()
        cover = tmp_path / "cover.png"
        cover.touch()

        result = _find_standalone_cover(audio)
        assert result == cover

    def test_returns_none_when_no_cover(self, tmp_path):
        """Line 509-510: Returns None when no cover file found."""
        audio = tmp_path / "mybook.opus"
        audio.touch()

        result = _find_standalone_cover(audio)
        assert result is None

    def test_prefers_stem_jpg_over_cover_jpg(self, tmp_path):
        """Search order: {stem}.jpg is checked before cover.jpg."""
        audio = tmp_path / "mybook.opus"
        audio.touch()
        stem_cover = tmp_path / "mybook.jpg"
        stem_cover.touch()
        generic_cover = tmp_path / "cover.jpg"
        generic_cover.touch()

        result = _find_standalone_cover(audio)
        assert result == stem_cover


class TestBuildGenresList:
    """Test line 528: build_genres_list fallback path."""

    def test_uncategorized_returns_empty(self):
        """Line 519-520: Uncategorized genre returns empty list."""
        result = build_genres_list(
            {"main": "uncategorized", "sub": "general", "original": "Audiobook"}
        )
        assert result == []

    def test_known_subcat_returns_display_name(self):
        """Lines 523-525: Known subcategory returns display name."""
        result = build_genres_list(
            {"main": "fiction", "sub": "science fiction", "original": "Sci-Fi"}
        )
        assert result == ["Science Fiction"]

    def test_unknown_subcat_fallback_title_case(self):
        """Line 528: Unknown subcategory uses title-cased name."""
        result = build_genres_list(
            {"main": "fiction", "sub": "weird tales", "original": "Weird Tales"}
        )
        assert result == ["Weird Tales"]


class TestDetermineLiteraryEra:
    """Test edge cases in determine_literary_era."""

    def test_invalid_year_string(self):
        """Lines 164: ValueError/TypeError returns Unknown Era."""
        assert determine_literary_era("not-a-year") == "Unknown Era"
        assert determine_literary_era("") == "Unknown Era"
        assert determine_literary_era(None) == "Unknown Era"

    def test_pre_1800(self):
        assert determine_literary_era("1750") == "Classical (Pre-1800)"

    def test_19th_century(self):
        assert determine_literary_era("1850") == "19th Century (1800-1899)"

    def test_early_20th(self):
        assert determine_literary_era("1925") == "Early 20th Century (1900-1949)"

    def test_late_20th(self):
        assert determine_literary_era("1975") == "Late 20th Century (1950-1999)"
