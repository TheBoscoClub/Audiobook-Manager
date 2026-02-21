"""Tests for admin audit UI in Back Office."""

import os

import pytest


class TestAuditSectionHTML:
    """Verify audit section exists in utilities.html."""

    @pytest.fixture()
    def utilities_html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "utilities.html")
        with open(path) as f:
            return f.read()

    def test_activity_section_exists(self, utilities_html):
        """Activity audit section should exist in utilities page."""
        assert 'id="activity-audit"' in utilities_html

    def test_activity_tab_exists(self, utilities_html):
        """Navigation tab for activity audit should exist."""
        assert 'data-section="activity"' in utilities_html

    def test_filter_controls_exist(self, utilities_html):
        """Should have filter controls for user, type, date."""
        assert 'id="activity-filter-type"' in utilities_html
        assert 'id="activity-filter-from"' in utilities_html
        assert 'id="activity-filter-to"' in utilities_html

    def test_stats_summary_exists(self, utilities_html):
        """Should show summary stats container."""
        assert 'id="activity-stats"' in utilities_html

    def test_stats_elements_exist(self, utilities_html):
        """Should have individual stat value elements."""
        assert 'id="audit-total-listens"' in utilities_html
        assert 'id="audit-total-downloads"' in utilities_html
        assert 'id="audit-active-users"' in utilities_html

    def test_activity_table_exists(self, utilities_html):
        """Should have an activity table container."""
        assert 'id="activity-table-body"' in utilities_html

    def test_pagination_controls_exist(self, utilities_html):
        """Should have pagination prev/next buttons."""
        assert 'id="activity-prev"' in utilities_html
        assert 'id="activity-next"' in utilities_html

    def test_page_info_exists(self, utilities_html):
        """Should have a page info display."""
        assert 'id="activity-page-info"' in utilities_html


class TestAuditSectionJS:
    """Verify audit JavaScript functions exist in utilities.js."""

    @pytest.fixture()
    def utilities_js(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "js", "utilities.js"
        )
        with open(path) as f:
            return f.read()

    def test_load_activity_audit_exists(self, utilities_js):
        """loadActivityAudit function should be defined."""
        assert "function loadActivityAudit" in utilities_js

    def test_load_activity_stats_exists(self, utilities_js):
        """loadActivityStats function should be defined."""
        assert "function loadActivityStats" in utilities_js

    def test_init_activity_section_exists(self, utilities_js):
        """initActivitySection function should be defined."""
        assert "function initActivitySection" in utilities_js

    def test_no_innerhtml_for_user_data(self, utilities_js):
        """Should not use innerHTML for dynamic content (XSS prevention)."""
        # Extract only the activity audit section code
        start = utilities_js.find("// Activity Audit")
        end = utilities_js.find("// ====", start + 1) if start != -1 else -1
        if start == -1:
            # Function-level check if section header not found
            start = utilities_js.find("function loadActivityAudit")
            end = utilities_js.find("\nfunction ", start + 50) if start != -1 else -1

        if start != -1 and end != -1:
            section = utilities_js[start:end]
            # innerHTML should not appear in user-data rendering paths
            # (Only setting empty '' or static HTML is acceptable)
            lines = section.split("\n")
            for line in lines:
                stripped = line.strip()
                if "innerHTML" in stripped:
                    # Allow clearing: innerHTML = '' or innerHTML = ""
                    assert "= ''" in stripped or '= ""' in stripped, (
                        f"innerHTML with user data found: {stripped}"
                    )


class TestAuditSectionCSS:
    """Verify audit CSS styles exist in utilities.css."""

    @pytest.fixture()
    def utilities_css(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "css", "utilities.css"
        )
        with open(path) as f:
            return f.read()

    def test_audit_stats_styles_exist(self, utilities_css):
        """Should have styles for audit stats cards."""
        assert ".audit-stats" in utilities_css

    def test_audit_filters_styles_exist(self, utilities_css):
        """Should have styles for audit filter bar."""
        assert ".audit-filters" in utilities_css

    def test_audit_table_styles_exist(self, utilities_css):
        """Should have styles for audit activity table."""
        assert ".audit-table" in utilities_css

    def test_audit_pagination_styles_exist(self, utilities_css):
        """Should have styles for audit pagination."""
        assert ".audit-pagination" in utilities_css
