"""Verify PlaybackManager fetch calls include credentials for auth cookie."""

import re
from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"


def _extract_fetch_block(content: str, method_name: str) -> str:
    """Extract the body of a method from library.js by name."""
    pattern = rf"(async\s+)?{method_name}\s*\([^)]*\)\s*\{{"
    match = re.search(pattern, content)
    if not match:
        raise ValueError(f"Method {method_name} not found in library.js")
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
    """Session cookie must be sent with all position API calls."""

    def test_save_position_has_credentials(self):
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "savePositionToAPI")
        assert "credentials" in block, (
            "savePositionToAPI must include credentials: 'include' "
            "in its fetch options so the session cookie is sent"
        )

    def test_get_position_has_credentials(self):
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "getPositionFromAPI")
        assert "credentials" in block, (
            "getPositionFromAPI must include credentials: 'include' "
            "in its fetch options so the session cookie is sent"
        )

    def test_flush_to_api_calls_save_position(self):
        """flushToAPI delegates to savePositionToAPI, so it inherits credentials."""
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "flushToAPI")
        assert "savePositionToAPI" in block

    def test_load_my_library_has_credentials(self):
        """loadMyLibrary should already have credentials (sanity check)."""
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "loadMyLibrary")
        assert "credentials" in block
