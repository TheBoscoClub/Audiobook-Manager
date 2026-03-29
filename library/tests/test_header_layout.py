"""
Tests for header layout changes: the :not([hidden]) CSS fix and
the left/right nav restructure.

Static analysis of layout.css, responsive.css, and index.html.
"""

from pathlib import Path

LIBRARY_DIR = Path(__file__).parent.parent
WEB_DIR = LIBRARY_DIR / "web-v2"
CSS_DIR = WEB_DIR / "css"


class TestHiddenAttributeFix:
    """Test that CSS selectors include :not([hidden]) to prevent
    display:flex from overriding the hidden attribute."""

    def test_layout_css_not_hidden_nav_link(self):
        """.nav-link:not([hidden]) must be in layout.css."""
        content = (CSS_DIR / "layout.css").read_text()
        assert ".nav-link:not([hidden])" in content, (
            "layout.css should use .nav-link:not([hidden])"
        )

    def test_layout_css_not_hidden_utilities_link(self):
        """.utilities-link:not([hidden]) must be in layout.css."""
        content = (CSS_DIR / "layout.css").read_text()
        assert ".utilities-link:not([hidden])" in content, (
            "layout.css should use .utilities-link:not([hidden])"
        )

    def test_layout_css_hover_not_hidden(self):
        """Hover rules must include :not([hidden]) to avoid styling hidden elements."""
        content = (CSS_DIR / "layout.css").read_text()
        assert ".nav-link:not([hidden]):hover" in content, (
            "layout.css hover rule should include :not([hidden])"
        )
        assert ".utilities-link:not([hidden]):hover" in content, (
            "layout.css hover rule should include :not([hidden])"
        )

    def test_responsive_css_not_hidden(self):
        """responsive.css touch rules must include :not([hidden])."""
        content = (CSS_DIR / "responsive.css").read_text()
        assert ".nav-link:not([hidden])" in content, (
            "responsive.css should use .nav-link:not([hidden])"
        )
        assert ".utilities-link:not([hidden])" in content, (
            "responsive.css should use .utilities-link:not([hidden])"
        )


class TestHeaderStructure:
    """Test header HTML structure (v8: shell header is canonical, hero is clean)."""

    def test_index_hero_has_no_nav_buttons(self):
        """v8: index.html hero should only have title and stats, no nav buttons."""
        content = (WEB_DIR / "index.html").read_text()
        assert "header-nav-left" not in content, (
            "v8: hero nav groups removed — auth handled by shell header"
        )
        assert "header-nav-right" not in content, (
            "v8: hero nav groups removed — auth handled by shell header"
        )

    def test_account_button_in_shell_header(self):
        """Account button must be in shell.html header."""
        content = (WEB_DIR / "shell.html").read_text()
        header_start = content.index("shell-header")
        header_section = content[header_start : header_start + 600]
        assert "my-account-btn" in header_section, (
            "Account button should be inside shell-header"
        )

    def test_account_button_not_in_index(self):
        """v8: account button only in shell header, not duplicated in index.html."""
        content = (WEB_DIR / "index.html").read_text()
        assert 'id="my-account-btn"' not in content, (
            "v8: account button should only be in shell.html, not index.html"
        )

    def test_shell_header_has_accessibility_btn(self):
        """Shell header should have accessibility panel toggle."""
        content = (WEB_DIR / "shell.html").read_text()
        assert "accessibility-btn" in content, (
            "Shell header should have accessibility button"
        )


class TestTutorialIntegration:
    """Test that tutorial assets are included in index.html."""

    def test_tutorial_css_included(self):
        """index.html must reference tutorial.css."""
        content = (WEB_DIR / "index.html").read_text()
        assert "tutorial.css" in content, "index.html should include tutorial.css"

    def test_tutorial_js_included(self):
        """index.html must reference tutorial.js."""
        content = (WEB_DIR / "index.html").read_text()
        assert "tutorial.js" in content, "index.html should include tutorial.js"
