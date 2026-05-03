"""Verify shell.js contains required player and messaging logic."""

from pathlib import Path

SHELL_JS = Path(__file__).parent.parent / "web-v2" / "js" / "shell.js"


class TestShellJS:
    def test_shell_js_exists(self):
        assert SHELL_JS.exists(), "shell.js must exist in web-v2/js/"

    def test_has_message_listener(self):
        content = SHELL_JS.read_text()
        assert "addEventListener" in content
        assert "'message'" in content or '"message"' in content

    def test_handles_play_message(self):
        content = SHELL_JS.read_text()
        assert "'play'" in content or '"play"' in content

    def test_handles_pause_message(self):
        content = SHELL_JS.read_text()
        assert "'pause'" in content or '"pause"' in content

    def test_handles_seek_message(self):
        content = SHELL_JS.read_text()
        assert "'seek'" in content or '"seek"' in content

    def test_origin_validation(self):
        """Messages must validate origin to prevent cross-origin attacks."""
        content = SHELL_JS.read_text()
        assert "origin" in content

    def test_sends_player_state(self):
        content = SHELL_JS.read_text()
        assert "playerState" in content

    def test_has_credentials_on_api_calls(self):
        """Any fetch calls in shell.js must include credentials. Comments
        and string-literal references to `fetch(` (e.g. inline docs of the
        gesture-activation rationale in playBook) are stripped before
        counting so they don't false-positive."""
        import re

        content = SHELL_JS.read_text()
        if "fetch(" not in content:
            return
        # Strip block comments, line comments, and string literals (preserve
        # length so error messages stay actionable).
        stripped = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), content, flags=re.DOTALL)
        stripped = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), stripped)

        def _blank(m):
            return m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1]

        stripped = re.sub(r'"(?:\\.|[^"\\\n])*"', _blank, stripped)
        stripped = re.sub(r"'(?:\\.|[^'\\\n])*'", _blank, stripped)
        stripped = re.sub(r"`(?:\\.|[^`\\])*`", _blank, stripped, flags=re.DOTALL)

        fetches = len(re.findall(r"fetch\(", stripped))
        creds = len(re.findall(r"credentials\s*:\s*['\"]include['\"]", content))
        assert creds >= fetches, (
            f"Found {fetches} fetch calls but only {creds} with credentials"
        )

    def test_has_media_session(self):
        content = SHELL_JS.read_text()
        assert "mediaSession" in content

    def test_saves_position_with_credentials(self):
        content = SHELL_JS.read_text()
        assert "credentials" in content
