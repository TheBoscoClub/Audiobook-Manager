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
        assert (
            ".nav-link:not([hidden])" in content
        ), "layout.css should use .nav-link:not([hidden])"

    def test_layout_css_not_hidden_utilities_link(self):
        """.utilities-link:not([hidden]) must be in layout.css."""
        content = (CSS_DIR / "layout.css").read_text()
        assert (
            ".utilities-link:not([hidden])" in content
        ), "layout.css should use .utilities-link:not([hidden])"

    def test_layout_css_hover_not_hidden(self):
        """Hover rules must include :not([hidden]) to avoid styling hidden elements."""
        content = (CSS_DIR / "layout.css").read_text()
        assert (
            ".nav-link:not([hidden]):hover" in content
        ), "layout.css hover rule should include :not([hidden])"
        assert (
            ".utilities-link:not([hidden]):hover" in content
        ), "layout.css hover rule should include :not([hidden])"

    def test_responsive_css_not_hidden(self):
        """responsive.css touch rules must include :not([hidden])."""
        content = (CSS_DIR / "responsive.css").read_text()
        assert (
            ".nav-link:not([hidden])" in content
        ), "responsive.css should use .nav-link:not([hidden])"
        assert (
            ".utilities-link:not([hidden])" in content
        ), "responsive.css should use .utilities-link:not([hidden])"


class TestHeaderStructure:
    """Test header HTML structure with left/right nav groups."""

    def test_header_nav_left_exists(self):
        """index.html must have a header-nav-left container."""
        content = (WEB_DIR / "index.html").read_text()
        assert (
            "header-nav-left" in content
        ), "index.html should have header-nav-left class"

    def test_header_nav_right_exists(self):
        """index.html must have a header-nav-right container."""
        content = (WEB_DIR / "index.html").read_text()
        assert (
            "header-nav-right" in content
        ), "index.html should have header-nav-right class"

    def test_help_link_in_left_nav(self):
        """Help link must be inside the left nav section."""
        content = (WEB_DIR / "index.html").read_text()
        left_start = content.index("header-nav-left")
        right_start = content.index("header-nav-right")
        left_section = content[left_start:right_start]
        assert (
            'href="help.html"' in left_section
        ), "Help link should be inside header-nav-left"

    def test_backoffice_in_right_nav(self):
        """Back Office link must be inside the right nav section."""
        content = (WEB_DIR / "index.html").read_text()
        right_start = content.index("header-nav-right")
        right_section = content[right_start : right_start + 1200]
        assert (
            "admin-backoffice-link" in right_section
        ), "Back Office link should be inside header-nav-right"

    def test_account_button_in_shell_header(self):
        """Account button must be in shell.html header (moved from index.html in v7.4.0)."""
        content = (WEB_DIR / "shell.html").read_text()
        header_start = content.index("shell-header")
        header_section = content[header_start : header_start + 600]
        assert "my-account-btn" in header_section, "Account button should be inside shell-header"

    def test_login_link_in_right_nav(self):
        """Login link must be inside the right nav section."""
        content = (WEB_DIR / "index.html").read_text()
        right_start = content.index("header-nav-right")
        right_section = content[right_start : right_start + 400]
        assert (
            "login-link" in right_section
        ), "Login link should be inside header-nav-right"


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
