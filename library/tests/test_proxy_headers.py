"""
Tests for proxy server hop-by-hop header filtering and path routing.

Validates RFC 2616 compliance for header filtering and correct proxy path detection.
"""

import sys
from pathlib import Path

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))


class TestHopByHopHeaders:
    """Test the HOP_BY_HOP_HEADERS constant and filtering logic."""

    def _get_hop_by_hop_headers(self):
        """Import and return HOP_BY_HOP_HEADERS from proxy_server."""
        # proxy_server lives in web-v2/, not in the normal backend package
        proxy_path = LIBRARY_DIR / "web-v2"
        if str(proxy_path) not in sys.path:
            sys.path.insert(0, str(proxy_path))
        # Must mock config before importing proxy_server
        import types

        mock_config = types.ModuleType("config")
        mock_config.AUDIOBOOKS_API_PORT = 5001
        mock_config.AUDIOBOOKS_BIND_ADDRESS = "0.0.0.0"  # nosec B104  # test fixture binds localhost/test network
        mock_config.AUDIOBOOKS_CERTS = Path("/tmp/certs")  # nosec B108  # test fixture path
        mock_config.AUDIOBOOKS_WEB_PORT = 8443
        mock_config.COVER_DIR = Path("/tmp/covers")  # nosec B108  # test fixture path
        sys.modules["config"] = mock_config
        try:
            import importlib

            if "proxy_server" in sys.modules:
                importlib.reload(sys.modules["proxy_server"])
            else:
                import proxy_server  # noqa: F401
            return sys.modules["proxy_server"].HOP_BY_HOP_HEADERS
        finally:
            del sys.modules["config"]

    def test_hop_by_hop_headers_defined(self):
        """HOP_BY_HOP_HEADERS set contains all 8 RFC 2616 Section 13.5.1 headers."""
        headers = self._get_hop_by_hop_headers()
        expected = {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
        }
        assert headers == expected, f"Missing or extra headers: {headers ^ expected}"

    def test_hop_by_hop_headers_are_lowercase(self):
        """All hop-by-hop header names must be lowercase for
        case-insensitive matching."""
        headers = self._get_hop_by_hop_headers()
        for h in headers:
            assert h == h.lower(), f"Header '{h}' is not lowercase"

    def test_non_hop_by_hop_not_in_set(self):
        """Common pass-through headers must NOT be in the hop-by-hop set."""
        headers = self._get_hop_by_hop_headers()
        passthrough = ["content-type", "x-custom", "set-cookie", "content-length"]
        for h in passthrough:
            assert h not in headers, f"'{h}' should not be a hop-by-hop header"


class TestProxyPrefixes:
    """Test PROXY_PREFIXES and _is_proxy_path logic."""

    def _get_handler_class(self):
        """Import and return ReverseProxyHandler class."""
        proxy_path = LIBRARY_DIR / "web-v2"
        if str(proxy_path) not in sys.path:
            sys.path.insert(0, str(proxy_path))
        import types

        mock_config = types.ModuleType("config")
        mock_config.AUDIOBOOKS_API_PORT = 5001
        mock_config.AUDIOBOOKS_BIND_ADDRESS = "0.0.0.0"  # nosec B104  # test fixture binds localhost/test network
        mock_config.AUDIOBOOKS_CERTS = Path("/tmp/certs")  # nosec B108  # test fixture path
        mock_config.AUDIOBOOKS_WEB_PORT = 8443
        mock_config.COVER_DIR = Path("/tmp/covers")  # nosec B108  # test fixture path
        sys.modules["config"] = mock_config
        try:
            import importlib

            if "proxy_server" in sys.modules:
                importlib.reload(sys.modules["proxy_server"])
            else:
                import proxy_server  # noqa: F401
            return sys.modules["proxy_server"].ReverseProxyHandler
        finally:
            del sys.modules["config"]

    def test_proxy_prefixes_defined(self):
        """/api/ and /auth/ must be in PROXY_PREFIXES."""
        handler_cls = self._get_handler_class()
        prefixes = handler_cls.PROXY_PREFIXES
        assert "/api/" in prefixes, "Missing /api/ prefix"
        assert "/auth/" in prefixes, "Missing /auth/ prefix"

    def test_is_proxy_path_true_for_api(self):
        """_is_proxy_path returns True for /api/foo paths."""
        handler_cls = self._get_handler_class()
        # Create a minimal mock instance with just the .path attribute
        instance = object.__new__(handler_cls)
        instance.path = "/api/system/health"
        assert instance._is_proxy_path() is True

    def test_is_proxy_path_false_for_static(self):
        """_is_proxy_path returns False for /static/foo or /index.html paths."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/static/style.css"
        assert instance._is_proxy_path() is False

        instance.path = "/index.html"
        assert instance._is_proxy_path() is False

    def test_root_path_not_proxied(self):
        """Root path '/' should NOT be proxied (it serves shell.html)."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/"
        assert instance._is_proxy_path() is False

    def test_path_without_trailing_slash_not_proxied(self):
        """Path '/api' without trailing slash should NOT match '/api/' prefix."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/api"
        assert instance._is_proxy_path() is False

    def test_uppercase_api_path_not_proxied(self):
        """Uppercase '/API/foo' should NOT match (prefix check is case-sensitive)."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/API/foo"
        assert instance._is_proxy_path() is False

    def test_prefix_substring_not_proxied(self):
        """Path '/api-data/foo' should NOT match '/api/' prefix."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/api-data/foo"
        assert instance._is_proxy_path() is False

    def test_auth_path_proxied(self):
        """Path '/auth/login' should be proxied."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/auth/login"
        assert instance._is_proxy_path() is True

    def test_covers_path_not_proxied(self):
        """Path '/covers/123.jpg' is served directly, not proxied to API."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/covers/123.jpg"
        assert instance._is_proxy_path() is False
