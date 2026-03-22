"""
Tests for the help page (help.html) and help CSS.

Static analysis of file structure, accessibility, and content completeness.
Follows the same pattern as test_auth_ui.py — Path.read_text() assertions.
"""

from pathlib import Path


LIBRARY_DIR = Path(__file__).parent.parent
WEB_DIR = LIBRARY_DIR / "web-v2"
CSS_DIR = WEB_DIR / "css"


class TestHelpPageStructure:
    """Test help.html file structure and content."""

    def test_help_html_exists(self):
        """help.html must exist in web-v2/."""
        assert (WEB_DIR / "help.html").exists(), "help.html should exist"

    def test_has_doctype(self):
        """help.html must start with <!DOCTYPE html>."""
        content = (WEB_DIR / "help.html").read_text()
        assert content.strip().startswith("<!DOCTYPE html>"), (
            "help.html should start with DOCTYPE"
        )

    def test_includes_theme_css(self):
        """help.html must reference the Art Deco theme stylesheet."""
        content = (WEB_DIR / "help.html").read_text()
        assert "theme-art-deco.css" in content, (
            "help.html should include theme-art-deco.css"
        )

    def test_includes_help_css(self):
        """help.html must reference help.css."""
        content = (WEB_DIR / "help.html").read_text()
        assert "help.css" in content, "help.html should include help.css"

    def test_all_sections_present(self):
        """help.html must contain all 11 documented sections."""
        content = (WEB_DIR / "help.html").read_text()
        sections = [
            "getting-started",
            "browsing",
            "search-filter",
            "sorting",
            "collections",
            "player",
            "position-saving",
            "downloads",
            "profile",
            "notifications",
            "keyboard",
        ]
        for section_id in sections:
            assert f'id="{section_id}"' in content, f"Missing section id='{section_id}'"

    def test_start_tutorial_link(self):
        """help.html must contain a link to start the tutorial."""
        content = (WEB_DIR / "help.html").read_text()
        assert "index.html?tutorial=1" in content, (
            "help.html should link to index.html?tutorial=1"
        )

    def test_anchor_links_match_ids(self):
        """Every href='#section-*' in the TOC must have a matching id in the page."""
        import re

        content = (WEB_DIR / "help.html").read_text()
        # Find all anchor hrefs like href="#some-id"
        anchors = re.findall(r'href="#([a-z][\w-]*)"', content)
        for anchor in anchors:
            assert f'id="{anchor}"' in content, (
                f"Anchor href='#{anchor}' has no matching id='{anchor}'"
            )

    def test_accessibility_lang_attr(self):
        """help.html must have lang='en' on the html element."""
        content = (WEB_DIR / "help.html").read_text()
        assert '<html lang="en">' in content, "help.html should have <html lang='en'>"


class TestHelpCSS:
    """Test help.css file structure."""

    def test_help_css_exists(self):
        """help.css must exist in web-v2/css/."""
        assert (CSS_DIR / "help.css").exists(), "help.css should exist"

    def test_responsive_breakpoints(self):
        """help.css must contain 768px and 480px responsive breakpoints."""
        content = (CSS_DIR / "help.css").read_text()
        assert "768px" in content, "help.css should have 768px breakpoint"
        assert "480px" in content, "help.css should have 480px breakpoint"

    def test_uses_css_variables(self):
        """help.css must reference Art Deco theme CSS variables."""
        content = (CSS_DIR / "help.css").read_text()
        assert "--gold" in content, "help.css should use --gold variable"
        assert "--deco-charcoal" in content, (
            "help.css should use --deco-charcoal variable"
        )


class TestHelpNewFeatures:
    """Verify help.html covers per-user features added in v7.x."""

    def test_my_library_section(self):
        content = (WEB_DIR / "help.html").read_text()
        assert "My Library" in content

    def test_progress_tracking_content(self):
        content = (WEB_DIR / "help.html").read_text().lower()
        assert "progress" in content

    def test_download_history_content(self):
        content = (WEB_DIR / "help.html").read_text().lower()
        assert "download" in content

    def test_new_books_content(self):
        content = (WEB_DIR / "help.html").read_text()
        assert "New Books" in content or "new books" in content.lower()

    def test_tutorial_covers_my_library(self):
        """Tutorial JS must reference My Library tab."""
        content = (WEB_DIR / "js" / "tutorial.js").read_text()
        assert "my-library" in content.lower() or "My Library" in content
