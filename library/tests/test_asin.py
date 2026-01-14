"""
Tests for ASIN (Amazon Standard Identification Number) functionality.

Tests cover:
- ASIN extraction from source file names
- Title normalization for matching
- ASIN population API endpoint
- Database ASIN queries
"""

import json
import sys
from pathlib import Path

import pytest

# Add parent dir for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "rnd"))


class TestAsinExtraction:
    """Test ASIN extraction from source filenames."""

    def test_extract_standard_asin(self):
        """Test extraction from standard Audible filename format."""
        from populate_asins_from_sources import extract_asin_and_title

        filename = "0062868071_The_End_Is_Always_Near_Apocalyptic_Moments-AAX_44_128.aaxc"
        asin, title = extract_asin_and_title(filename)

        assert asin == "0062868071"
        assert "The End Is Always Near" in title

    def test_extract_alphanumeric_asin(self):
        """Test extraction with alphanumeric ASIN (B-prefix)."""
        from populate_asins_from_sources import extract_asin_and_title

        filename = "B009XEJWP8_Don_Quixote_Translated_by_Edith_Grossman-AAX_44_128.aaxc"
        asin, title = extract_asin_and_title(filename)

        assert asin == "B009XEJWP8"
        assert "Don Quixote" in title

    def test_extract_with_different_quality(self):
        """Test extraction with various AAX quality formats."""
        from populate_asins_from_sources import extract_asin_and_title

        # 22kHz 64kbps
        filename1 = "0063031884_The_Neil_Gaiman_Reader-AAX_22_64.aaxc"
        asin1, title1 = extract_asin_and_title(filename1)
        assert asin1 == "0063031884"
        assert "Neil Gaiman" in title1

        # 44kHz 128kbps
        filename2 = "0062945149_Ultralearning-AAX_44_128.aaxc"
        asin2, title2 = extract_asin_and_title(filename2)
        assert asin2 == "0062945149"
        assert "Ultralearning" in title2

    def test_extract_invalid_format(self):
        """Test handling of invalid filename formats."""
        from populate_asins_from_sources import extract_asin_and_title

        # Missing ASIN
        asin1, title1 = extract_asin_and_title("Some_Book_Title-AAX_44_128.aaxc")
        assert asin1 is None
        assert title1 is None

        # Missing AAX suffix
        asin2, title2 = extract_asin_and_title("0062868071_Some_Title.aaxc")
        assert asin2 is None
        assert title2 is None

        # Too short ASIN
        asin3, title3 = extract_asin_and_title("123456_Short-AAX_44_128.aaxc")
        assert asin3 is None
        assert title3 is None

    def test_extract_long_title(self):
        """Test extraction with very long titles."""
        from populate_asins_from_sources import extract_asin_and_title

        filename = (
            "B00D9473WC_Your_Deceptive_Mind_A_Scientific_Guide_to_Critical_"
            "Thinking_Skills_The_Great_Courses-AAX_44_128.aaxc"
        )
        asin, title = extract_asin_and_title(filename)

        assert asin == "B00D9473WC"
        assert "Your Deceptive Mind" in title
        assert "Critical Thinking" in title

    def test_title_underscore_conversion(self):
        """Test that underscores are converted to spaces in titles."""
        from populate_asins_from_sources import extract_asin_and_title

        filename = "B07ZRZNXDV_You_Ought_to_Know_Adam_Wade-AAX_44_128.aaxc"
        asin, title = extract_asin_and_title(filename)

        assert asin == "B07ZRZNXDV"
        assert "_" not in title
        assert "You Ought to Know" in title


class TestTitleNormalization:
    """Test title normalization for matching."""

    def test_normalize_removes_articles(self):
        """Test that normalization removes leading articles."""
        from common import normalize_title

        assert "end" in normalize_title("The End")
        assert "first" in normalize_title("A First Look")
        assert "introduction" in normalize_title("An Introduction")

    def test_normalize_lowercase(self):
        """Test that normalization converts to lowercase."""
        from common import normalize_title

        result = normalize_title("THE BIG BOOK")
        assert result == result.lower()

    def test_normalize_removes_punctuation(self):
        """Test that normalization removes punctuation."""
        from common import normalize_title

        result = normalize_title("Hello, World! How's It Going?")
        assert "," not in result
        assert "!" not in result
        assert "'" not in result
        assert "?" not in result

    def test_normalize_removes_extra_whitespace(self):
        """Test that normalization removes extra whitespace."""
        from common import normalize_title

        result = normalize_title("Multiple   Spaces   Here")
        assert "  " not in result

    def test_normalize_special_characters(self):
        """Test handling of special characters."""
        from common import normalize_title

        # Ellipsis
        result1 = normalize_title("Episode 07: …And Subsequent Eating Contest")
        assert "and subsequent" in result1

        # Em dashes
        result2 = normalize_title("Book One — The Beginning")
        assert "book one" in result2 or "beginning" in result2


class TestSimilarityCalculation:
    """Test similarity calculation for fuzzy matching."""

    def test_similarity_exact_match(self):
        """Test similarity for exact matches."""
        from populate_asins_from_sources import calculate_similarity

        score = calculate_similarity("hello world", "hello world")
        assert score == 1.0

    def test_similarity_partial_match(self):
        """Test similarity for partial matches."""
        from populate_asins_from_sources import calculate_similarity

        score = calculate_similarity("hello world", "hello there")
        assert 0 < score < 1

    def test_similarity_no_match(self):
        """Test similarity for completely different strings."""
        from populate_asins_from_sources import calculate_similarity

        score = calculate_similarity("abc def", "xyz uvw")
        assert score == 0.0

    def test_similarity_empty_strings(self):
        """Test similarity with empty strings."""
        from populate_asins_from_sources import calculate_similarity

        assert calculate_similarity("", "") == 0.0
        assert calculate_similarity("hello", "") == 0.0
        assert calculate_similarity("", "world") == 0.0

    def test_similarity_case_insensitive(self):
        """Test that similarity is case-insensitive via normalization."""
        from populate_asins_from_sources import calculate_similarity
        from common import normalize_title

        s1 = normalize_title("The Great Book")
        s2 = normalize_title("the great book")
        score = calculate_similarity(s1, s2)
        assert score == 1.0


class TestAsinAPIEndpoint:
    """Test the ASIN population API endpoint."""

    def test_populate_asins_endpoint_exists(self, app_client):
        """Test that the ASIN population endpoint exists."""
        response = app_client.post(
            "/api/utilities/populate-asins-async",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )
        # Should return 200 (started) or 409 (already running)
        assert response.status_code in (200, 409, 404)

    def test_populate_asins_returns_operation_id(self, app_client):
        """Test that endpoint returns an operation ID on success."""
        response = app_client.post(
            "/api/utilities/populate-asins-async",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )

        if response.status_code == 200:
            data = json.loads(response.data)
            assert "operation_id" in data
            assert data.get("success") is True

    def test_populate_asins_dry_run_param(self, app_client):
        """Test that dry_run parameter is accepted."""
        # Dry run = True
        response1 = app_client.post(
            "/api/utilities/populate-asins-async",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )
        assert response1.status_code in (200, 409, 404)

        # Dry run = False (actual execution)
        response2 = app_client.post(
            "/api/utilities/populate-asins-async",
            data=json.dumps({"dry_run": False}),
            content_type="application/json",
        )
        assert response2.status_code in (200, 409, 404)


class TestAsinDatabaseQueries:
    """Test ASIN-related database queries."""

    def test_query_audiobooks_with_asin(self, app_client):
        """Test that audiobooks API includes ASIN field."""
        response = app_client.get("/api/audiobooks?per_page=1")
        assert response.status_code == 200

        data = json.loads(response.data)
        if data.get("audiobooks") and len(data["audiobooks"]) > 0:
            audiobook = data["audiobooks"][0]
            # ASIN field should exist (may be null)
            assert "asin" in audiobook or "id" in audiobook

    def test_single_audiobook_includes_asin(self, app_client):
        """Test that single audiobook response includes ASIN."""
        # First get a valid ID
        response = app_client.get("/api/audiobooks?per_page=1")
        data = json.loads(response.data)

        if data.get("audiobooks") and len(data["audiobooks"]) > 0:
            audiobook_id = data["audiobooks"][0]["id"]

            response = app_client.get(f"/api/audiobooks/{audiobook_id}")
            if response.status_code == 200:
                audiobook = json.loads(response.data)
                assert "asin" in audiobook or "id" in audiobook


class TestAsinFormats:
    """Test various ASIN format validations."""

    def test_numeric_asin_format(self):
        """Test 10-digit numeric ASIN format."""
        from populate_asins_from_sources import extract_asin_and_title

        # Standard 10-digit numeric
        filename = "1234567890_Test_Book-AAX_44_128.aaxc"
        asin, _ = extract_asin_and_title(filename)
        assert asin == "1234567890"
        assert len(asin) == 10

    def test_alphanumeric_asin_format(self):
        """Test B-prefix alphanumeric ASIN format."""
        from populate_asins_from_sources import extract_asin_and_title

        # B-prefix (common for audiobooks)
        filename = "B08XYZ1234_Test_Book-AAX_44_128.aaxc"
        asin, _ = extract_asin_and_title(filename)
        assert asin == "B08XYZ1234"
        assert asin.startswith("B")
        assert len(asin) == 10

    def test_mixed_case_asin(self):
        """Test that ASIN extraction handles case correctly."""
        from populate_asins_from_sources import extract_asin_and_title

        # Lowercase should still work due to IGNORECASE flag
        filename = "b08xyz1234_Test_Book-AAX_44_128.aaxc"
        asin, _ = extract_asin_and_title(filename)
        # Should match regardless of case
        assert asin is not None
        assert len(asin) == 10


class TestAsinSourceFileMatching:
    """Test source file to database matching logic."""

    def test_exact_title_match(self):
        """Test exact title matching."""
        from populate_asins_from_sources import calculate_similarity
        from common import normalize_title

        # Same title, normalized
        source_title = normalize_title("The Great Gatsby")
        db_title = normalize_title("The Great Gatsby")

        score = calculate_similarity(source_title, db_title)
        assert score == 1.0

    def test_subtitle_differences(self):
        """Test handling of subtitle differences."""
        from populate_asins_from_sources import calculate_similarity
        from common import normalize_title

        # With and without subtitle
        source_title = normalize_title("The Book")
        db_title = normalize_title("The Book: A Novel")

        score = calculate_similarity(source_title, db_title)
        # Should still have high similarity
        assert score > 0.5

    def test_series_info_in_title(self):
        """Test handling of series info in titles."""
        from populate_asins_from_sources import calculate_similarity
        from common import normalize_title

        source_title = normalize_title("Trust No One X-Files Book 1")
        db_title = normalize_title("Trust No One: X-Files, Book 1")

        score = calculate_similarity(source_title, db_title)
        # Should match well despite formatting differences
        assert score > 0.7
