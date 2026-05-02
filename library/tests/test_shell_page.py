"""Verify shell.html structure for persistent player architecture."""

from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"
SHELL_HTML = WEB_DIR / "shell.html"


class TestShellPageExists:
    def test_shell_html_exists(self):
        assert SHELL_HTML.exists(), "shell.html must exist in web-v2/"

    def test_has_iframe(self):
        content = SHELL_HTML.read_text()
        assert 'id="content-frame"' in content, (
            "shell.html must have an iframe with id='content-frame'"
        )

    def test_iframe_default_src(self):
        content = SHELL_HTML.read_text()
        assert 'src="index.html' in content, "iframe default src must be index.html"

    def test_has_audio_element(self):
        content = SHELL_HTML.read_text()
        assert 'id="audio-element"' in content, "shell.html must contain the <audio> element"

    def test_has_player_bar(self):
        content = SHELL_HTML.read_text()
        assert 'id="shell-player"' in content, (
            "shell.html must have a player bar with id='shell-player'"
        )

    def test_player_bar_hidden_by_default(self):
        content = SHELL_HTML.read_text()
        # Find the shell-player element and verify it has hidden attribute nearby
        idx = content.find('id="shell-player"')
        # Check the opening tag (look backwards for < and forwards for >)
        tag_start = content.rfind("<", 0, idx)
        tag_end = content.find(">", idx)
        tag = content[tag_start : tag_end + 1]
        assert "hidden" in tag, "Player bar must be hidden by default"

    def test_has_shell_js(self):
        content = SHELL_HTML.read_text()
        assert "js/shell.js" in content, "shell.html must load shell.js"

    def test_has_shell_css(self):
        content = SHELL_HTML.read_text()
        assert "css/shell.css" in content, "shell.html must load shell.css"

    def test_no_library_js(self):
        """shell.html should NOT load library.js — that's for content pages."""
        content = SHELL_HTML.read_text()
        assert "library.js" not in content, (
            "shell.html must not load library.js (that belongs in iframe content)"
        )
