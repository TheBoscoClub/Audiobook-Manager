"""Test that proxy_server detects WebSocket upgrade requests."""

import sys
from pathlib import Path

# proxy_server uses hyphenated directory; add to path manually
sys.path.insert(0, str(Path(__file__).parent.parent / "web-v2"))


def test_proxy_detects_websocket_upgrade_headers():
    """Verify the proxy recognizes WebSocket upgrade requests."""
    from proxy_server import is_websocket_upgrade  # type: ignore[import-not-found]

    class FakeHeaders:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}

        def get(self, key, default=None):
            return self._d.get(key.lower(), default)

    assert (
        is_websocket_upgrade(FakeHeaders({"Upgrade": "websocket", "Connection": "Upgrade"})) is True
    )

    assert is_websocket_upgrade(FakeHeaders({"Content-Type": "application/json"})) is False

    assert is_websocket_upgrade(FakeHeaders({"Upgrade": "h2c", "Connection": "Upgrade"})) is False


def test_wsgi_app_dispatches_upgrade_to_tunnel():
    """The WSGI app must route proxy-path WebSocket upgrades to the tunnel
    (v8.4.2.0 gunicorn refactor — parity with the old handler's do_GET)."""
    import io
    from unittest.mock import patch

    import proxy_server  # type: ignore[import-not-found]

    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": "/streaming-translate/ws",
        "PATH_INFO": "/streaming-translate/ws",
        "QUERY_STRING": "",
        "REMOTE_ADDR": "127.0.0.1",
        "wsgi.input": io.BytesIO(b""),
        "HTTP_UPGRADE": "websocket",
        "HTTP_CONNECTION": "Upgrade",
    }
    # /streaming-translate/ is NOT a proxy prefix — upgrade must not tunnel
    with patch.object(proxy_server.ReverseProxyApp, "_tunnel_websocket") as mock_tunnel:
        mock_tunnel.return_value = []
        proxy_server.app(environ, lambda *a, **k: None)
        assert mock_tunnel.call_count == 0

    # /api/ws IS a proxy prefix — upgrade must tunnel
    environ["RAW_URI"] = environ["PATH_INFO"] = "/api/streaming-translate/ws"
    with patch.object(proxy_server.ReverseProxyApp, "_tunnel_websocket") as mock_tunnel:
        mock_tunnel.return_value = []
        proxy_server.app(environ, lambda *a, **k: None)
        mock_tunnel.assert_called_once()
