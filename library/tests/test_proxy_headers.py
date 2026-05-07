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
        """/api/, /auth/, and /streaming-audio/ must be in PROXY_PREFIXES."""
        handler_cls = self._get_handler_class()
        prefixes = handler_cls.PROXY_PREFIXES
        assert "/api/" in prefixes, "Missing /api/ prefix"
        assert "/auth/" in prefixes, "Missing /auth/ prefix"
        assert "/streaming-audio/" in prefixes, "Missing /streaming-audio/ prefix"

    def test_is_proxy_path_true_for_streaming_audio(self):
        """_is_proxy_path returns True for /streaming-audio/* paths (MSE WebM segments)."""
        handler_cls = self._get_handler_class()
        instance = object.__new__(handler_cls)
        instance.path = "/streaming-audio/117908/0/0/zh-Hans"
        assert instance._is_proxy_path() is True

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


class TestCorsHeaders:
    """Test that the CORS header pair is correctly emitted together.

    Both sides of the pair must be present whenever the proxy emits CORS
    headers — Access-Control-Allow-Origin AND Access-Control-Allow-
    Credentials: true. Since the web UI uses credentials:include for all
    fetch() calls, missing Allow-Credentials would silently break
    cross-origin scenarios. Wildcard Allow-Origin is invalid alongside
    Allow-Credentials per the CORS spec, so when the configured origin is
    "*" we echo the request Origin instead.
    """

    def _get_handler_class(self):
        proxy_path = LIBRARY_DIR / "web-v2"
        if str(proxy_path) not in sys.path:
            sys.path.insert(0, str(proxy_path))
        import types

        mock_config = types.ModuleType("config")
        mock_config.AUDIOBOOKS_API_PORT = 5001
        mock_config.AUDIOBOOKS_BIND_ADDRESS = "0.0.0.0"  # nosec B104  # test fixture
        mock_config.AUDIOBOOKS_CERTS = Path("/tmp/certs")  # nosec B108  # test fixture
        mock_config.AUDIOBOOKS_WEB_PORT = 8443
        mock_config.COVER_DIR = Path("/tmp/covers")  # nosec B108  # test fixture
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

    def _make_handler(self, headers_dict, cors_origin="*"):
        """Construct a handler stub that records send_header calls."""
        from email.message import Message

        # Force CORS_ORIGIN module-level constant for the test
        proxy_mod = sys.modules.get("proxy_server")
        if proxy_mod is not None:
            proxy_mod.CORS_ORIGIN = cors_origin

        handler_cls = self._get_handler_class()
        if proxy_mod is not None:
            proxy_mod.CORS_ORIGIN = cors_origin
        instance = object.__new__(handler_cls)

        msg = Message()
        for k, v in headers_dict.items():
            msg[k] = v
        instance.headers = msg

        instance._sent_headers = []

        def fake_send_header(name, value):
            instance._sent_headers.append((name, value))

        instance.send_header = fake_send_header  # type: ignore[assignment]
        return instance

    def test_resolve_cors_origin_wildcard_with_origin_header_echoes(self):
        """When CORS_ORIGIN='*' and an Origin header is present, echo it."""
        h = self._make_handler({"Origin": "https://example.thebosco.club"}, cors_origin="*")
        assert h._resolve_cors_origin() == "https://example.thebosco.club"

    def test_resolve_cors_origin_wildcard_no_origin_header_keeps_wildcard(self):
        """When CORS_ORIGIN='*' and no Origin header, keep wildcard (non-browser caller)."""
        h = self._make_handler({}, cors_origin="*")
        assert h._resolve_cors_origin() == "*"

    def test_resolve_cors_origin_specific_value_passes_through(self):
        """When CORS_ORIGIN is a specific value, it is used regardless of Origin."""
        h = self._make_handler(
            {"Origin": "https://attacker.example"}, cors_origin="https://library.thebosco.club"
        )
        assert h._resolve_cors_origin() == "https://library.thebosco.club"

    def test_send_cors_headers_emits_origin_credentials_and_vary(self):
        """_send_cors_headers must emit all three: Allow-Origin, Allow-Credentials, Vary."""
        h = self._make_handler({"Origin": "https://library.thebosco.club"}, cors_origin="*")
        h._send_cors_headers()
        names = [n for n, _ in h._sent_headers]
        assert "Access-Control-Allow-Origin" in names
        assert "Access-Control-Allow-Credentials" in names
        assert "Vary" in names

    def test_send_cors_headers_credentials_is_true(self):
        """Allow-Credentials must literally be 'true' for browsers to expose response."""
        h = self._make_handler({}, cors_origin="*")
        h._send_cors_headers()
        for name, value in h._sent_headers:
            if name == "Access-Control-Allow-Credentials":
                assert value == "true"
                return
        raise AssertionError("Access-Control-Allow-Credentials header missing")

    def test_send_cors_headers_no_wildcard_when_credentials_true(self):
        """Allow-Origin MUST NOT be '*' when Allow-Credentials=true (CORS spec).

        With Origin header present, the wildcard config must be replaced
        with the echoed origin. Browsers reject A-C-A-O='*' alongside
        A-C-A-C='true' as a security safeguard.
        """
        h = self._make_handler({"Origin": "https://library.thebosco.club"}, cors_origin="*")
        h._send_cors_headers()
        for name, value in h._sent_headers:
            if name == "Access-Control-Allow-Origin":
                assert value != "*", (
                    "Allow-Origin='*' alongside Allow-Credentials=true is invalid per CORS spec"
                )
                assert value == "https://library.thebosco.club"
                return
        raise AssertionError("Access-Control-Allow-Origin header missing")

    def test_do_options_emits_credentials_header(self):
        """The CORS preflight (do_OPTIONS) must emit Allow-Credentials together with Allow-Origin."""
        from unittest.mock import MagicMock

        h = self._make_handler({"Origin": "https://library.thebosco.club"}, cors_origin="*")
        h.send_response = MagicMock()
        h.end_headers = MagicMock()
        h.do_OPTIONS()
        names = [n for n, _ in h._sent_headers]
        # Both headers must be present
        assert "Access-Control-Allow-Origin" in names
        assert "Access-Control-Allow-Credentials" in names
        # Allow-Methods, Allow-Headers, Expose-Headers must still be present
        assert "Access-Control-Allow-Methods" in names
        assert "Access-Control-Allow-Headers" in names
        assert "Access-Control-Expose-Headers" in names
