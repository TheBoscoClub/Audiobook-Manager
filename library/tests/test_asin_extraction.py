"""Tests for multi-source ASIN extraction.

Sources checked in order:
1. chapters.json (existing behavior)
2. .voucher file (new — content_license.asin)
3. Source filename (new — {ASIN}_Title-*.aaxc pattern)
"""

import json
import sys
from pathlib import Path

import pytest

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
        book_dir = tmp_path / "Library" / "Author" / "Revenge Prey"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Revenge Prey.opus"
        opus_file.touch()

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
        book_dir = tmp_path / "Author" / "Title"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Title.opus"
        opus_file.touch()
        assert extract_asin(opus_file) is None

    def test_voucher_fallback_when_no_chapters_json(self, tmp_path):
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
        # B07EXAMPL1 is a valid 10-char Audible ASIN format (B + 9 alphanumeric)
        (sources_dir / "B07EXAMPL1_Some_Title-AAX_44_128.aaxc").touch()

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result == "B07EXAMPL1"

    def test_filename_asin_must_start_with_b_or_digit(self, tmp_path):
        book_dir = tmp_path / "Library" / "Author" / "Bad"
        book_dir.mkdir(parents=True)
        opus_file = book_dir / "Bad.opus"
        opus_file.touch()

        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
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

        chapters = {
            "content_metadata": {
                "content_reference": {"asin": "ASIN_FROM_C"}
            }
        }
        (book_dir / "chapters.json").write_text(json.dumps(chapters))

        sources_dir = tmp_path / "Sources"
        sources_dir.mkdir()
        voucher = {"content_license": {"asin": "ASIN_FROM_V"}}
        (sources_dir / "ASIN_FROM_V_Dual-AAX_44_128.voucher").write_text(
            json.dumps(voucher)
        )

        result = extract_asin(opus_file, sources_dir=sources_dir)
        assert result == "ASIN_FROM_C"
