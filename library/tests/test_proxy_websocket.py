"""Test that proxy_server detects WebSocket upgrade requests."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# proxy_server uses hyphenated directory; add to path manually
sys.path.insert(0, str(Path(__file__).parent.parent / "web-v2"))


def test_proxy_detects_websocket_upgrade_headers():
    """Verify the proxy recognizes WebSocket upgrade requests."""
    from proxy_server import is_websocket_upgrade

    class FakeHeaders:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}
        def get(self, key, default=None):
            return self._d.get(key.lower(), default)

    assert is_websocket_upgrade(FakeHeaders({
        "Upgrade": "websocket", "Connection": "Upgrade"
    })) is True

    assert is_websocket_upgrade(FakeHeaders({
        "Content-Type": "application/json"
    })) is False

    assert is_websocket_upgrade(FakeHeaders({
        "Upgrade": "h2c", "Connection": "Upgrade"
    })) is False
