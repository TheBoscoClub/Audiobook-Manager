# library/tests/test_docs_position_sync.py
"""Verify position sync documentation reflects per-user local-only system."""

import os
import pytest


class TestPositionSyncDocs:
    @pytest.fixture
    def position_sync_md(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docs", "POSITION_SYNC.md"
        )
        with open(path) as f:
            return f.read()

    def test_no_audible_sync_docs(self, position_sync_md):
        """Audible sync should not be documented as a current feature."""
        assert "sync with audible" not in position_sync_md.lower()
        assert "audible cloud" not in position_sync_md.lower()

    def test_per_user_documented(self, position_sync_md):
        assert (
            "per-user" in position_sync_md.lower()
            or "per user" in position_sync_md.lower()
        )

    def test_local_only_documented(self, position_sync_md):
        assert "local" in position_sync_md.lower()
