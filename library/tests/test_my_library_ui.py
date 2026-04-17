"""
Tests for My Library tab UI components.

Validates:
- HTML tab elements exist in index.html
- CSS progress bar classes exist in components.css
- CSS tab bar classes exist in layout.css
- JS tab methods exist in library.js
"""

import os

import pytest

# ============================================================
# HTML Tests
# ============================================================


class TestMyLibraryTabHTML:
    """Verify tab bar HTML elements exist in index.html."""

    @pytest.fixture
    def index_html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "index.html")
        with open(path) as f:
            return f.read()

    def test_my_library_tab_exists(self, index_html):
        assert 'data-tab="my-library"' in index_html

    def test_browse_all_tab_exists(self, index_html):
        assert 'data-tab="browse"' in index_html

    def test_tab_container_exists(self, index_html):
        assert 'class="library-tabs"' in index_html

    def test_tab_container_id(self, index_html):
        assert 'id="library-tabs"' in index_html

    def test_tab_container_hidden_by_default(self, index_html):
        """Tab bar should be hidden by default (shown by JS when user is logged in)."""
        assert 'id="library-tabs"' in index_html
        # Find the line containing library-tabs and verify display:none
        for line in index_html.split("\n"):
            if 'id="library-tabs"' in line:
                assert "display: none" in line or 'style="display:none"' in line
                break

    def test_browse_tab_has_active_class(self, index_html):
        """Browse All should be the default active tab."""
        assert 'class="tab-btn active" data-tab="browse"' in index_html

    def test_my_library_tab_button_class(self, index_html):
        """My Library tab should have tab-btn class."""
        assert 'class="tab-btn" data-tab="my-library"' in index_html

    def test_tab_bar_between_search_and_grid(self, index_html):
        """Tab bar should appear after search section and before the books grid."""
        search_pos = index_html.find("search-section")
        tabs_pos = index_html.find("library-tabs")
        grid_pos = index_html.find("books-grid")
        assert search_pos < tabs_pos < grid_pos


# ============================================================
# CSS Tests
# ============================================================


class TestProgressBarCSS:
    """Verify progress bar CSS classes exist in components.css."""

    @pytest.fixture
    def components_css(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "css", "components.css")
        with open(path) as f:
            return f.read()

    def test_progress_bar_class_exists(self, components_css):
        assert ".book-progress" in components_css

    def test_progress_bar_fill_class(self, components_css):
        assert ".book-progress-fill" in components_css

    def test_progress_bar_text_class(self, components_css):
        assert ".book-progress-text" in components_css


class TestTabBarCSS:
    """Verify tab bar CSS classes exist in layout.css."""

    @pytest.fixture
    def layout_css(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "css", "layout.css")
        with open(path) as f:
            return f.read()

    def test_library_tabs_class_exists(self, layout_css):
        assert ".library-tabs" in layout_css

    def test_tab_btn_class_exists(self, layout_css):
        assert ".tab-btn" in layout_css

    def test_tab_btn_active_class_exists(self, layout_css):
        assert ".tab-btn.active" in layout_css

    def test_gold_accent_on_active_tab(self, layout_css):
        """Active tab should use gold color accent."""
        # Find the .tab-btn.active rule and check it uses gold
        in_active = False
        for line in layout_css.split("\n"):
            if ".tab-btn.active" in line:
                in_active = True
            if in_active:
                if "gold" in line.lower() or "#D4AF37" in line or "#d4af37" in line:
                    return  # Found gold reference
                if "}" in line:
                    in_active = False
        # Check CSS variables referencing gold
        assert "var(--gold" in layout_css


# ============================================================
# JS Tests
# ============================================================


class TestMyLibraryJS:
    """Verify My Library tab JS methods exist in library.js."""

    @pytest.fixture
    def library_js(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "js", "library.js")
        with open(path) as f:
            return f.read()

    def test_init_tabs_method_exists(self, library_js):
        assert "initTabs" in library_js

    def test_switch_tab_method_exists(self, library_js):
        assert "switchTab" in library_js

    def test_load_my_library_method_exists(self, library_js):
        assert "loadMyLibrary" in library_js

    def test_build_my_library_card_element_method_exists(self, library_js):
        assert "buildMyLibraryCardElement" in library_js

    def test_init_calls_init_tabs(self, library_js):
        """The init() method should call initTabs()."""
        assert "this.initTabs()" in library_js

    def test_switch_tab_handles_browse(self, library_js):
        """switchTab should handle 'browse' tab."""
        assert "'browse'" in library_js or '"browse"' in library_js

    def test_switch_tab_handles_my_library(self, library_js):
        """switchTab should handle 'my-library' tab."""
        assert "'my-library'" in library_js or '"my-library"' in library_js

    def test_fetches_user_library_endpoint(self, library_js):
        """Should fetch from /api/user/library endpoint."""
        assert "/user/library" in library_js

    def test_current_tab_state_tracked(self, library_js):
        """Should track current tab in state."""
        assert "currentTab" in library_js

    def test_my_library_card_shows_progress(self, library_js):
        """My Library card renderer should include progress bar elements."""
        assert "book-progress-fill" in library_js

    def test_format_duration_for_progress(self, library_js):
        """Should have logic to format duration for progress display."""
        assert "formatDuration" in library_js or "formatTime" in library_js
