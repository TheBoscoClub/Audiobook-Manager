"""Verify playback fetch calls include credentials for auth cookie.

PlaybackManager methods (savePositionToAPI, getPositionFromAPI, flushToAPI) moved
from library.js to shell.js as part of the ShellPlayer class. loadMyLibrary remains
in library.js.
"""

import re
from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"
SHELL_JS = Path(__file__).parent.parent / "web-v2" / "js" / "shell.js"


def _extract_fetch_block(
    content: str, method_name: str, source_name: str = "source"
) -> str:
    """Extract the body of a method from a JS file by name."""
    pattern = rf"(async\s+)?{method_name}\s*\([^)]*\)\s*\{{"
    match = re.search(pattern, content)
    if not match:
        raise ValueError(f"Method {method_name} not found in {source_name}")
    start = match.start()
    depth = 0
    for i, ch in enumerate(content[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    raise ValueError(f"Could not find end of method {method_name}")


class TestPlaybackManagerCredentials:
    """Session cookie must be sent with all position API calls (now in shell.js)."""

    def test_save_position_has_credentials(self):
        content = SHELL_JS.read_text()
        block = _extract_fetch_block(content, "savePositionToAPI", "shell.js")
        assert "credentials" in block, (
            "savePositionToAPI must include credentials: 'include' "
            "in its fetch options so the session cookie is sent"
        )

    def test_get_position_has_credentials(self):
        content = SHELL_JS.read_text()
        block = _extract_fetch_block(content, "getPositionFromAPI", "shell.js")
        assert "credentials" in block, (
            "getPositionFromAPI must include credentials: 'include' "
            "in its fetch options so the session cookie is sent"
        )

    def test_flush_to_api_calls_save_position(self):
        """flushToAPI delegates to savePositionToAPI, so it inherits credentials."""
        content = SHELL_JS.read_text()
        block = _extract_fetch_block(content, "flushToAPI", "shell.js")
        assert "savePositionToAPI" in block

    def test_load_my_library_has_credentials(self):
        """loadMyLibrary should already have credentials (sanity check)."""
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "loadMyLibrary", "library.js")
        assert "credentials" in block
