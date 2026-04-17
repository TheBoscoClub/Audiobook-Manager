"""Tests for multi-author/narrator name parser."""

from backend.name_parser import (
    clean_name,
    generate_sort_name,
    has_role_suffix,
    is_brand_name,
    is_junk_name,
    normalize_for_dedup,
    parse_names,
    strip_credentials,
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
        assert generate_sort_name("Antoine de Saint-Exupéry") == "de Saint-Exupéry, Antoine"

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

    def test_credential_phd_stripped(self):
        assert generate_sort_name("Shari Y. Manning PhD") == "Manning, Shari Y."

    def test_credential_md_stripped(self):
        assert generate_sort_name("Blaise Aguirre MD") == "Aguirre, Blaise"

    def test_credential_md_dotted_stripped(self):
        assert generate_sort_name("Jeffrey M. Schwartz, M.D.") == "Schwartz, Jeffrey M."

    def test_credential_msw_stripped(self):
        assert generate_sort_name("Sheri Van Dijk MSW") == "Van Dijk, Sheri"

    def test_credential_psyd_stripped(self):
        assert generate_sort_name("Gillian Galen PsyD") == "Galen, Gillian"

    def test_credential_comma_phd_stripped(self):
        assert generate_sort_name("Tara Brach, PhD") == "Brach, Tara"

    def test_generational_iii_stripped(self):
        assert generate_sort_name("Robert S. Mueller III") == "Mueller, Robert S."

    def test_trailing_role_word_stripped(self):
        assert generate_sort_name("David Coward Translator") == "Coward, David"


class TestStripCredentials:
    """Test credential suffix stripping."""

    def test_phd(self):
        assert strip_credentials("Shari Y. Manning PhD") == "Shari Y. Manning"

    def test_md(self):
        assert strip_credentials("Blaise Aguirre MD") == "Blaise Aguirre"

    def test_md_dotted_comma(self):
        assert strip_credentials("Jeffrey M. Schwartz, M.D.") == "Jeffrey M. Schwartz"

    def test_msw(self):
        assert strip_credentials("Sheri Van Dijk MSW") == "Sheri Van Dijk"

    def test_comma_phd(self):
        assert strip_credentials("Tara Brach, PhD") == "Tara Brach"

    def test_iii(self):
        assert strip_credentials("Robert S. Mueller III") == "Robert S. Mueller"

    def test_no_credential(self):
        assert strip_credentials("Stephen King") == "Stephen King"

    def test_none(self):
        assert strip_credentials(None) is None

    def test_empty(self):
        assert strip_credentials("") == ""


class TestParseNames:
    """Test multi-name parsing with delimiter detection."""

    def test_single_author(self):
        assert parse_names("Stephen King") == ["Stephen King"]

    def test_semicolon_separated(self):
        assert parse_names("Stephen King; Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_and_separated(self):
        assert parse_names("Stephen King and Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_ampersand_separated(self):
        assert parse_names("Stephen King & Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_comma_multiple_authors(self):
        assert parse_names("Stephen King, Peter Straub") == ["Stephen King", "Peter Straub"]

    def test_comma_last_first_format(self):
        assert parse_names("King, Stephen") == ["Stephen King"]

    def test_comma_last_first_with_prefix(self):
        result = parse_names("de Saint-Exupéry, Antoine")
        assert result == ["Antoine de Saint-Exupéry"]

    def test_three_authors_semicolon(self):
        result = parse_names("Author One; Author Two; Author Three")
        assert result == ["Author One", "Author Two", "Author Three"]

    def test_strips_whitespace(self):
        assert parse_names("  Stephen King ;  Peter Straub  ") == ["Stephen King", "Peter Straub"]

    def test_empty_returns_empty_list(self):
        assert parse_names("") == []
        assert parse_names(None) == []

    def test_group_name_in_author_context_flagged(self):
        from backend.name_parser import is_group_name

        assert is_group_name("Full Cast") is True
        assert is_group_name("BBC Radio") is True
        assert is_group_name("Stephen King") is False

    def test_alternating_single_word_pairs(self):
        result = parse_names("King, Stephen, Straub, Peter")
        assert result == ["Stephen King", "Peter Straub"]

    def test_mixed_word_count_conservative(self):
        result = parse_names("de Saint-Exupéry, Antoine, Straub, Peter")
        assert len(result) >= 1

    def test_translator_comma_filtered(self):
        """'Frank Wynne, Translator' should not produce 'Translator' as a name."""
        result = parse_names("Georges Simenon, Frank Wynne, Translator")
        assert "Translator" not in result
        assert "Georges Simenon" in result
        assert "Frank Wynne" in result

    def test_trailing_more_filtered(self):
        """'more' from truncated Audible metadata should be filtered."""
        result = parse_names("Patrick O'Brian, Farley Mowat, more")
        assert "more" not in result
        assert "Patrick O'Brian" in result
        assert "Farley Mowat" in result

    def test_md_credential_filtered(self):
        """Standalone 'MD' should be filtered as junk."""
        result = parse_names("Rick Hanson, Richard Mendius, MD")
        assert "MD" not in result

    def test_credentials_stripped_from_parts(self):
        """Credentials should be stripped from name parts."""
        result = parse_names("Blaise Aguirre MD, Gillian Galen PsyD")
        assert "Blaise Aguirre" in result
        assert "Gillian Galen" in result

    def test_comma_phd_not_first_name(self):
        """'Tara Brach, PhD' should NOT become 'PhD Tara Brach'."""
        result = parse_names("Tara Brach, PhD")
        assert result == ["Tara Brach"]

    def test_comma_md_dotted_filtered(self):
        """'Jeffrey M. Schwartz, M.D., Rebecca Gladding, M.D., M.D.' cleanup."""
        result = parse_names("Jeffrey M. Schwartz, M.D., Rebecca Gladding, M.D., M.D.")
        assert "Jeffrey M. Schwartz" in result
        assert "Rebecca Gladding" in result
        assert "M.D." not in result

    def test_rick_hanson_comma_md(self):
        """'Rick Hanson, Richard Mendius, MD' should not have MD as name."""
        result = parse_names("Rick Hanson, Richard Mendius, MD")
        assert "Rick Hanson" in result
        assert "Richard Mendius" in result
        assert "MD" not in result

    def test_ph_d_comma_stripped(self):
        """'Rick Hanson, Ph.D.' should become just 'Rick Hanson'."""
        result = parse_names("Rick Hanson, Ph.D.")
        assert result == ["Rick Hanson"]


class TestCleanName:
    """Test individual name cleaning."""

    def test_strip_paren_role(self):
        assert clean_name("Anthea Bell (translator)") == "Anthea Bell"

    def test_strip_trailing_translator(self):
        assert clean_name("David Coward Translator") == "David Coward"

    def test_strip_phd(self):
        assert clean_name("Shari Y. Manning PhD") == "Shari Y. Manning"

    def test_strip_md_comma(self):
        assert clean_name("Jeffrey M. Schwartz, M.D.") == "Jeffrey M. Schwartz"

    def test_no_change_normal(self):
        assert clean_name("Stephen King") == "Stephen King"

    def test_empty(self):
        assert clean_name("") == ""

    def test_none(self):
        assert clean_name(None) == ""


class TestBrandDetection:
    """Test brand/publisher name detection."""

    def test_brand_keyword_publishing(self):
        assert is_brand_name("American Citizen Publishing") is True

    def test_brand_keyword_learning(self):
        assert is_brand_name("Earworms Learning") is True

    def test_brand_exact_name(self):
        assert is_brand_name("Aaptiv") is True

    def test_brand_movewith(self):
        assert is_brand_name("MoveWith") is True

    def test_person_name_not_brand(self):
        assert is_brand_name("Stephen King") is False

    def test_person_with_learning_in_name(self):
        assert is_brand_name("John Learning") is True

    def test_empty_not_brand(self):
        assert is_brand_name("") is False
        assert is_brand_name(None) is False

    def test_government_org(self):
        assert is_brand_name("Special Counsel's Office U.S. Department of Justice") is True

    def test_department_of(self):
        assert is_brand_name("Department of Defense") is True


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


class TestJunkNameDetection:
    """Test junk/standalone word detection."""

    def test_more(self):
        assert is_junk_name("more") is True

    def test_translator(self):
        assert is_junk_name("Translator") is True

    def test_md(self):
        assert is_junk_name("MD") is True

    def test_md_dotted(self):
        assert is_junk_name("M.D.") is True

    def test_phd(self):
        assert is_junk_name("PhD") is True

    def test_iii(self):
        assert is_junk_name("III") is True

    def test_real_name_not_junk(self):
        assert is_junk_name("Stephen King") is False

    def test_empty_is_junk(self):
        assert is_junk_name("") is True
        assert is_junk_name(None) is True


class TestNormalizeForDedup:
    """Test name normalization for deduplication."""

    def test_accent_removal(self):
        assert normalize_for_dedup("Miéville") == normalize_for_dedup("Mieville")

    def test_case_insensitive(self):
        assert normalize_for_dedup("Le Carré") == normalize_for_dedup("le Carré")

    def test_spacing_normalization(self):
        assert normalize_for_dedup("M. R. James") == normalize_for_dedup("M.R. James")

    def test_del_toro_case(self):
        assert normalize_for_dedup("Del Toro") == normalize_for_dedup("del Toro")

    def test_empty(self):
        assert normalize_for_dedup("") == ""
        assert normalize_for_dedup(None) == ""
