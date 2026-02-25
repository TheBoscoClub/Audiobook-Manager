"""Verify library.js delegates play to shell when in iframe."""

from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"
INDEX_HTML = Path(__file__).parent.parent / "web-v2" / "index.html"


class TestIframeBridge:

    def test_library_js_has_postmessage_play(self):
        """library.js must send postMessage to parent when playing."""
        content = LIBRARY_JS.read_text()
        assert "postMessage" in content, (
            "library.js must use postMessage to communicate with shell"
        )

    def test_library_js_detects_iframe(self):
        """library.js must detect if it's running inside an iframe."""
        content = LIBRARY_JS.read_text()
        assert "window.parent" in content or "self !== top" in content or "inIframe" in content

    def test_audio_element_not_in_index(self):
        """<audio> element should not be in index.html (moved to shell.html)."""
        content = INDEX_HTML.read_text()
        assert 'id="audio-element"' not in content, (
            "The <audio> element must be in shell.html, not index.html"
        )

    def test_player_overlay_not_in_index(self):
        """The old player overlay should not be in index.html (replaced by shell player bar)."""
        content = INDEX_HTML.read_text()
        assert 'id="audio-player"' not in content, (
            "The old audio-player overlay must be removed from index.html"
        )

    def test_audio_player_class_removed_from_library_js(self):
        """AudioPlayer class should not be in library.js (moved to shell.js)."""
        content = LIBRARY_JS.read_text()
        assert "class AudioPlayer" not in content, (
            "AudioPlayer class must be removed from library.js (now in shell.js)"
        )

    def test_playback_manager_removed_from_library_js(self):
        """PlaybackManager class should not be in library.js (moved to shell.js)."""
        content = LIBRARY_JS.read_text()
        assert "class PlaybackManager" not in content, (
            "PlaybackManager class must be removed from library.js (now in shell.js)"
        )

    def test_listens_for_player_state(self):
        """library.js should listen for playerState messages from shell."""
        content = LIBRARY_JS.read_text()
        assert "playerState" in content
