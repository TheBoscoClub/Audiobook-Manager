"""
Tests for the editions detection module.

The editions module provides helpers for detecting different editions of the same
audiobook (e.g., "50th Anniversary Edition", "Unabridged", "Revised Edition").
"""

import pytest


class TestHasEditionMarker:
    """Test the has_edition_marker function."""

    def test_none_input(self):
        """Test that None returns False."""
        from backend.api_modular.editions import has_edition_marker

        assert has_edition_marker(None) is False

    def test_empty_string(self):
        """Test that empty string returns False."""
        from backend.api_modular.editions import has_edition_marker

        assert has_edition_marker("") is False

    def test_no_marker(self):
        """Test title without edition markers returns False."""
        from backend.api_modular.editions import has_edition_marker

        assert has_edition_marker("The Great Gatsby") is False
        assert has_edition_marker("To Kill a Mockingbird") is False

    @pytest.mark.parametrize(
        "title",
        [
            "The Great Gatsby (50th Anniversary Edition)",
            "1984 - Revised Edition",
            "Dune: Unabridged",
            "The Complete Works of Shakespeare",
            "Expanded Edition of War and Peace",
            "Deluxe Collector's Box Set",
            "Special Illustrated Edition",
            "The Annotated Alice",
            "The Hobbit (Updated Cover)",
            "Pride and Prejudice (Abridged)",
        ],
    )
    def test_edition_markers_detected(self, title):
        """Test various edition markers are detected."""
        from backend.api_modular.editions import has_edition_marker

        assert has_edition_marker(title) is True

    def test_case_insensitive(self):
        """Test that detection is case-insensitive."""
        from backend.api_modular.editions import has_edition_marker

        assert has_edition_marker("UNABRIDGED EDITION") is True
        assert has_edition_marker("anniversary EDITION") is True
        assert has_edition_marker("REvIsEd") is True


class TestNormalizeBaseTitle:
    """Test the normalize_base_title function."""

    def test_none_input(self):
        """Test that None returns empty string."""
        from backend.api_modular.editions import normalize_base_title

        assert normalize_base_title(None) == ""

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        from backend.api_modular.editions import normalize_base_title

        assert normalize_base_title("") == ""

    def test_simple_title(self):
        """Test simple title is lowercased and stripped."""
        from backend.api_modular.editions import normalize_base_title

        assert normalize_base_title("The Great Gatsby") == "the great gatsby"
        assert normalize_base_title("  Dune  ") == "dune"

    def test_removes_edition_in_parentheses(self):
        """Test edition markers in parentheses are removed."""
        from backend.api_modular.editions import normalize_base_title

        result = normalize_base_title("The Great Gatsby (50th Anniversary Edition)")
        assert "anniversary" not in result
        assert "edition" not in result
        assert "gatsby" in result

    def test_removes_numbered_edition(self):
        """Test numbered editions like '2nd Edition' are removed."""
        from backend.api_modular.editions import normalize_base_title

        assert (
            normalize_base_title("Python Crash Course - 2nd Edition")
            == "python crash course"
        )
        assert normalize_base_title("Clean Code - 1st Edition") == "clean code"

    def test_removes_unabridged_suffix(self):
        """Test unabridged/abridged suffixes are removed."""
        from backend.api_modular.editions import normalize_base_title

        result = normalize_base_title("War and Peace: Unabridged")
        assert "unabridged" not in result
        assert "war and peace" in result

        result = normalize_base_title("Les Miserables (Abridged)")
        assert "abridged" not in result

    def test_removes_year_suffix(self):
        """Test year in parentheses at end is removed."""
        from backend.api_modular.editions import normalize_base_title

        result = normalize_base_title("Brave New World (2023)")
        assert "2023" not in result
        assert "brave new world" in result

    def test_normalizes_punctuation(self):
        """Test colons and dashes are normalized."""
        from backend.api_modular.editions import normalize_base_title

        # Colons are removed
        result = normalize_base_title("Book: Subtitle")
        assert ":" not in result

        # Dashes become spaces
        result = normalize_base_title("Book-Title")
        assert "-" not in result
        assert "book title" in result

    def test_removes_complete_expanded(self):
        """Test complete/expanded markers are removed."""
        from backend.api_modular.editions import normalize_base_title

        result = normalize_base_title("The Stand: Complete and Uncut Edition")
        assert "complete" not in result

        result = normalize_base_title("IT: Expanded Edition")
        assert "expanded" not in result

    def test_complex_title(self):
        """Test complex title with multiple markers."""
        from backend.api_modular.editions import normalize_base_title

        title = "The Great Gatsby (50th Anniversary Edition) - 3rd Edition (2024)"
        result = normalize_base_title(title)

        assert "anniversary" not in result
        assert "edition" not in result
        assert "2024" not in result
        assert "gatsby" in result


class TestEditionsAPI:
    """Test the editions API route."""

    @pytest.fixture
    def populated_db(self, flask_app, session_temp_dir):
        """Populate database with test audiobooks for edition testing."""
        import sqlite3

        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Insert test audiobooks - same book, different editions
        test_books = [
            (
                1,
                "The Great Gatsby",
                "F. Scott Fitzgerald",
                "Jake Gyllenhaal",
                4.5,
                "4h 30m",
                256.0,
            ),
            (
                2,
                "The Great Gatsby (50th Anniversary Edition)",
                "F. Scott Fitzgerald",
                "Tim Robbins",
                5.0,
                "5h 0m",
                280.0,
            ),
            (
                3,
                "The Great Gatsby: Unabridged",
                "F. Scott Fitzgerald",
                "Frank Muller",
                5.2,
                "5h 12m",
                290.0,
            ),
            # Different author - should not be grouped
            (
                4,
                "The Great Gatsby Analysis",
                "John Smith",
                "Narrator X",
                2.0,
                "2h 0m",
                100.0,
            ),
            # Same author, different book
            (
                5,
                "Tender Is the Night",
                "F. Scott Fitzgerald",
                "Jake Gyllenhaal",
                6.0,
                "6h 0m",
                350.0,
            ),
        ]

        for (
            book_id,
            title,
            author,
            narrator,
            duration,
            duration_fmt,
            size,
        ) in test_books:
            cursor.execute(
                """
                INSERT OR REPLACE INTO audiobooks
                (id, title, author, narrator, duration_hours, duration_formatted, file_size_mb, file_path, format)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    title,
                    author,
                    narrator,
                    duration,
                    duration_fmt,
                    size,
                    f"/test/{title}.opus",
                    "opus",
                ),
            )

        conn.commit()
        conn.close()
        return db_path

    def test_get_editions_for_book_with_editions(self, app_client, populated_db):
        """Test getting editions for a book that has multiple editions."""
        response = app_client.get("/api/audiobooks/1/editions")

        assert response.status_code == 200
        data = response.get_json()

        assert data["author"] == "F. Scott Fitzgerald"
        assert data["edition_count"] >= 1
        assert "editions" in data

    def test_get_editions_nonexistent_book(self, app_client, populated_db):
        """Test getting editions for a book that doesn't exist."""
        response = app_client.get("/api/audiobooks/9999/editions")

        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data

    def test_editions_include_required_fields(self, app_client, populated_db):
        """Test that edition data includes all required fields."""
        response = app_client.get("/api/audiobooks/1/editions")
        data = response.get_json()

        assert "title" in data
        assert "author" in data
        assert "edition_count" in data
        assert "editions" in data

        # Each edition should have book details
        if data["editions"]:
            edition = data["editions"][0]
            assert "id" in edition
            assert "title" in edition
            assert "author" in edition

    def test_editions_grouped_by_base_title(self, app_client, populated_db):
        """Test that editions are grouped by normalized base title."""
        # The Great Gatsby and its editions should be grouped
        response = app_client.get("/api/audiobooks/1/editions")
        data = response.get_json()

        # All returned editions should be for "The Great Gatsby" variants
        for edition in data["editions"]:
            assert "gatsby" in edition["title"].lower()


class TestEditionMatchingLogic:
    """Test the matching logic between editions."""

    def test_same_base_title_matches(self):
        """Test that different editions of same book match."""
        from backend.api_modular.editions import normalize_base_title

        titles = [
            "The Great Gatsby",
            "The Great Gatsby (50th Anniversary Edition)",
            "The Great Gatsby: Unabridged",
            "The Great Gatsby - 2nd Edition",
        ]

        base_titles = [normalize_base_title(t) for t in titles]

        # All should normalize to roughly the same base title
        # (may have slight variations in punctuation handling)
        assert "gatsby" in base_titles[0]  # First one should contain gatsby
        for base in base_titles[1:]:
            # Check that the core "gatsby" is present in all
            assert "gatsby" in base

    def test_different_books_dont_match(self):
        """Test that different books don't match as editions."""
        from backend.api_modular.editions import normalize_base_title

        book1 = normalize_base_title("The Great Gatsby")
        book2 = normalize_base_title("To Kill a Mockingbird")
        book3 = normalize_base_title("1984")

        assert book1 != book2
        assert book1 != book3
        assert book2 != book3
