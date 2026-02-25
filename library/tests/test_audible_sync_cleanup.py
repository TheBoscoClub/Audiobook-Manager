"""Verify all Audible sync frontend code has been removed."""

from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"


class TestAudibleSyncRemoval:
    """Audible sync was removed from the backend. Frontend remnants must go too."""

    def test_no_audible_sync_method(self):
        content = LIBRARY_JS.read_text()
        assert "syncWithAudible" not in content, (
            "syncWithAudible method should be removed from library.js"
        )

    def test_no_audible_sync_timer(self):
        content = LIBRARY_JS.read_text()
        assert "audibleSyncInterval" not in content
        assert "audibleSyncDelayMs" not in content
        assert "startAudibleSyncTimer" not in content
        assert "stopAudibleSyncTimer" not in content

    def test_no_audible_sync_references(self):
        """No references to 'Audible sync' should remain in library.js."""
        content = LIBRARY_JS.read_text()
        assert "Audible sync" not in content
        assert "Audible service" not in content
