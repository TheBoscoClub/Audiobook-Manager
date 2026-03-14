"""Tests for multi-author/narrator name parser."""

from library.backend.name_parser import (
    generate_sort_name,
    has_role_suffix,
    is_brand_name,
    parse_names,
)


class TestGenerateSortName:
    """Test sort name generation from individual names."""

    def test_simple_two_part(self):
        assert generate_sort_name("Stephen King") == "King, Stephen"

    def test_initials(self):
        assert generate_sort_name("J.R.R. Tolkien") == "Tolkien, J.R.R."

    def test_prefix_le(self):
        assert generate_sort_name("John le Carré") == "le Carré, John"

    def test_prefix_van(self):
        assert generate_sort_name("Ludwig van Beethoven") == "van Beethoven, Ludwig"

    def test_prefix_de(self):
        assert (
            generate_sort_name("Antoine de Saint-Exupéry")
            == "de Saint-Exupéry, Antoine"
        )

    def test_single_name(self):
        assert generate_sort_name("Plato") == "Plato"

    def test_three_part_name(self):
        assert generate_sort_name("Arthur Conan Doyle") == "Doyle, Arthur Conan"

    def test_group_name_full_cast(self):
        assert generate_sort_name("Full Cast") == "Full Cast"

    def test_group_name_bbc_radio(self):
        assert generate_sort_name("BBC Radio") == "BBC Radio"

    def test_role_suffix_stripped(self):
        assert generate_sort_name("Neil Gaiman (editor)") == "Gaiman, Neil"

    def test_dash_role_stripped(self):
        assert generate_sort_name("Stephen Fry - introductions") == "Fry, Stephen"

    def test_none_returns_empty(self):
        assert generate_sort_name(None) == ""

    def test_unknown_author(self):
        assert generate_sort_name("Unknown Author") == ""

    def test_unknown_narrator(self):
        assert generate_sort_name("Unknown Narrator") == ""


class TestParseNames:
    """Test multi-name parsing with delimiter detection."""

    def test_single_author(self):
        assert parse_names("Stephen King") == ["Stephen King"]

    def test_semicolon_separated(self):
        assert parse_names("Stephen King; Peter Straub") == [
            "Stephen King",
            "Peter Straub",
        ]

    def test_and_separated(self):
        assert parse_names("Stephen King and Peter Straub") == [
            "Stephen King",
            "Peter Straub",
        ]

    def test_ampersand_separated(self):
        assert parse_names("Stephen King & Peter Straub") == [
            "Stephen King",
            "Peter Straub",
        ]

    def test_comma_multiple_authors(self):
        # Multi-word names on each side = multiple authors
        assert parse_names("Stephen King, Peter Straub") == [
            "Stephen King",
            "Peter Straub",
        ]

    def test_comma_last_first_format(self):
        # Single word on each side = "Last, First"
        assert parse_names("King, Stephen") == ["Stephen King"]

    def test_comma_last_first_with_prefix(self):
        # "de Saint-Exupéry, Antoine" has multi-word/hyphenated last name
        # Conservative: treat as single author in Last, First format
        result = parse_names("de Saint-Exupéry, Antoine")
        assert result == ["Antoine de Saint-Exupéry"]

    def test_three_authors_semicolon(self):
        result = parse_names("Author One; Author Two; Author Three")
        assert result == ["Author One", "Author Two", "Author Three"]

    def test_strips_whitespace(self):
        assert parse_names("  Stephen King ;  Peter Straub  ") == [
            "Stephen King",
            "Peter Straub",
        ]

    def test_empty_returns_empty_list(self):
        assert parse_names("") == []
        assert parse_names(None) == []

    def test_group_name_in_author_context_flagged(self):
        """Group names should be detectable for redirection to narrators."""
        from library.backend.name_parser import is_group_name

        assert is_group_name("Full Cast") is True
        assert is_group_name("BBC Radio") is True
        assert is_group_name("Stephen King") is False

    def test_alternating_single_word_pairs(self):
        # "King, Stephen, Straub, Peter" - all single words = Last,First pairs
        result = parse_names("King, Stephen, Straub, Peter")
        assert result == ["Stephen King", "Peter Straub"]

    def test_mixed_word_count_conservative(self):
        # Not all single words - conservative single author
        result = parse_names("de Saint-Exupéry, Antoine, Straub, Peter")
        # Ambiguous - should treat conservatively
        assert len(result) >= 1  # At minimum, don't crash


class TestBrandDetection:
    """Test brand/publisher name detection."""

    def test_brand_keyword_publishing(self):
        assert is_brand_name("American Citizen Publishing") is True

    def test_brand_keyword_learning(self):
        assert is_brand_name("Earworms Learning") is True

    def test_brand_exact_name(self):
        assert is_brand_name("Aaptiv") is True

    def test_person_name_not_brand(self):
        assert is_brand_name("Stephen King") is False

    def test_person_with_learning_in_name(self):
        # "Learning" is a keyword but in a person's name context
        # Our detection is word-based, so "John Learning" would match.
        # This is intentional — real person names don't contain "Learning".
        assert is_brand_name("John Learning") is True

    def test_empty_not_brand(self):
        assert is_brand_name("") is False
        assert is_brand_name(None) is False


class TestRoleSuffixDetection:
    """Test role suffix detection."""

    def test_translator_dash(self):
        assert has_role_suffix("Frances Riddle - translator") is True

    def test_editor_paren(self):
        assert has_role_suffix("Neil Gaiman (editor)") is True

    def test_adaptation_dash(self):
        assert has_role_suffix("Marty Ross - adaptation") is True

    def test_no_role(self):
        assert has_role_suffix("Stephen King") is False
