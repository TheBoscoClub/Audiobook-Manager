"""
Tests for proxy server hop-by-hop header filtering and path routing.

Validates RFC 2616 compliance for header filtering and correct proxy path
detection. Updated for the v8.4.2.0 WSGI refactor (Audiobook-Manager-nfx):
the former ``ReverseProxyHandler`` (http.server) is now ``ReverseProxyApp``
(WSGI under gunicorn) — path checks take the path as an argument and CORS
helpers take a headers mapping instead of reading handler instance state.
"""

# pyright: reportAttributeAccessIssue=false
# Reason: This test file dynamically constructs `types.ModuleType` mock
# config objects and assigns runtime attributes (AUDIOBOOKS_*, CORS_ORIGIN)
# to them. Pyright cannot statically know which attributes a ModuleType
# instance will have. File-level suppression is preferred to ~25
# per-line ignores on identical patterns.

import sys
from email.message import Message
from pathlib import Path
from typing import Any

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))


def _load_proxy_module():
    """Import (or reload) proxy_server with a mocked config module."""
    proxy_path = LIBRARY_DIR / "web-v2"
    if str(proxy_path) not in sys.path:
        sys.path.insert(0, str(proxy_path))
    # Must mock config before importing proxy_server
    import types

    mock_config: Any = types.ModuleType("config")
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
            import proxy_server  # noqa: F401  # type: ignore[import-not-found]
        return sys.modules["proxy_server"]
    finally:
        del sys.modules["config"]


def _make_headers(headers_dict):
    """Build a .get()-compatible headers mapping (as EnvironHeaders provides)."""
    msg = Message()
    for k, v in headers_dict.items():
        msg[k] = v
    return msg


class TestHopByHopHeaders:
    """Test the HOP_BY_HOP_HEADERS constant and filtering logic."""

    def _get_hop_by_hop_headers(self):
        """Import and return HOP_BY_HOP_HEADERS from proxy_server."""
        return _load_proxy_module().HOP_BY_HOP_HEADERS

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

    def _get_app(self):
        """Import and return the WSGI ReverseProxyApp instance."""
        return _load_proxy_module().app

    def test_proxy_prefixes_defined(self):
        """/api/, /auth/, and /streaming-audio/ must be in PROXY_PREFIXES."""
        prefixes = self._get_app().PROXY_PREFIXES
        assert "/api/" in prefixes, "Missing /api/ prefix"
        assert "/auth/" in prefixes, "Missing /auth/ prefix"
        assert "/streaming-audio/" in prefixes, "Missing /streaming-audio/ prefix"

    def test_is_proxy_path_true_for_streaming_audio(self):
        """_is_proxy_path returns True for /streaming-audio/* paths (MSE WebM segments)."""
        assert self._get_app()._is_proxy_path("/streaming-audio/117908/0/0/zh-Hans") is True

    def test_is_proxy_path_true_for_api(self):
        """_is_proxy_path returns True for /api/foo paths."""
        assert self._get_app()._is_proxy_path("/api/system/health") is True

    def test_is_proxy_path_false_for_static(self):
        """_is_proxy_path returns False for /static/foo or /index.html paths."""
        app = self._get_app()
        assert app._is_proxy_path("/static/style.css") is False
        assert app._is_proxy_path("/index.html") is False

    def test_root_path_not_proxied(self):
        """Root path '/' should NOT be proxied (it serves shell.html)."""
        assert self._get_app()._is_proxy_path("/") is False

    def test_path_without_trailing_slash_not_proxied(self):
        """Path '/api' without trailing slash should NOT match '/api/' prefix."""
        assert self._get_app()._is_proxy_path("/api") is False

    def test_uppercase_api_path_not_proxied(self):
        """Uppercase '/API/foo' should NOT match (prefix check is case-sensitive)."""
        assert self._get_app()._is_proxy_path("/API/foo") is False

    def test_prefix_substring_not_proxied(self):
        """Path '/api-data/foo' should NOT match '/api/' prefix."""
        assert self._get_app()._is_proxy_path("/api-data/foo") is False

    def test_auth_path_proxied(self):
        """Path '/auth/login' should be proxied."""
        assert self._get_app()._is_proxy_path("/auth/login") is True

    def test_covers_path_not_proxied(self):
        """Path '/covers/123.jpg' is served directly, not proxied to API."""
        assert self._get_app()._is_proxy_path("/covers/123.jpg") is False


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

    def _get_module(self, cors_origin="*"):
        proxy_mod = _load_proxy_module()
        proxy_mod.CORS_ORIGIN = cors_origin
        return proxy_mod

    def test_resolve_cors_origin_wildcard_with_origin_header_echoes(self):
        """When CORS_ORIGIN='*' and an Origin header is present, echo it."""
        mod = self._get_module(cors_origin="*")
        headers = _make_headers({"Origin": "https://example.thebosco.club"})
        assert mod.app._resolve_cors_origin(headers) == "https://example.thebosco.club"

    def test_resolve_cors_origin_wildcard_no_origin_header_keeps_wildcard(self):
        """When CORS_ORIGIN='*' and no Origin header, keep wildcard (non-browser caller)."""
        mod = self._get_module(cors_origin="*")
        assert mod.app._resolve_cors_origin(_make_headers({})) == "*"

    def test_resolve_cors_origin_specific_value_passes_through(self):
        """When CORS_ORIGIN is a specific value, it is used regardless of Origin."""
        mod = self._get_module(cors_origin="https://library.thebosco.club")
        headers = _make_headers({"Origin": "https://attacker.example"})
        assert mod.app._resolve_cors_origin(headers) == "https://library.thebosco.club"

    def test_cors_headers_emit_origin_credentials_and_vary(self):
        """_cors_headers must emit all three: Allow-Origin, Allow-Credentials, Vary."""
        mod = self._get_module(cors_origin="*")
        emitted = mod.app._cors_headers(_make_headers({"Origin": "https://library.thebosco.club"}))
        names = [n for n, _ in emitted]
        assert "Access-Control-Allow-Origin" in names
        assert "Access-Control-Allow-Credentials" in names
        assert "Vary" in names

    def test_cors_headers_credentials_is_true(self):
        """Allow-Credentials must literally be 'true' for browsers to expose response."""
        mod = self._get_module(cors_origin="*")
        emitted = mod.app._cors_headers(_make_headers({}))
        for name, value in emitted:
            if name == "Access-Control-Allow-Credentials":
                assert value == "true"
                return
        raise AssertionError("Access-Control-Allow-Credentials header missing")

    def test_cors_headers_no_wildcard_when_credentials_true(self):
        """Allow-Origin MUST NOT be '*' when Allow-Credentials=true (CORS spec).

        With Origin header present, the wildcard config must be replaced
        with the echoed origin. Browsers reject A-C-A-O='*' alongside
        A-C-A-C='true' as a security safeguard.
        """
        mod = self._get_module(cors_origin="*")
        emitted = mod.app._cors_headers(_make_headers({"Origin": "https://library.thebosco.club"}))
        for name, value in emitted:
            if name == "Access-Control-Allow-Origin":
                assert value != "*", (
                    "Allow-Origin='*' alongside Allow-Credentials=true is invalid per CORS spec"
                )
                assert value == "https://library.thebosco.club"
                return
        raise AssertionError("Access-Control-Allow-Origin header missing")

    def test_options_preflight_emits_credentials_header(self):
        """The CORS preflight (_handle_options) must emit Allow-Credentials
        together with Allow-Origin."""
        mod = self._get_module(cors_origin="*")
        recorded = {}

        def start_response(status, headers, exc_info=None):
            recorded["status"] = status
            recorded["headers"] = headers

        mod.app._handle_options(
            _make_headers({"Origin": "https://library.thebosco.club"}), start_response
        )
        names = [n for n, _ in recorded["headers"]]
        assert recorded["status"].startswith("204")
        # Both headers must be present
        assert "Access-Control-Allow-Origin" in names
        assert "Access-Control-Allow-Credentials" in names
        # Allow-Methods, Allow-Headers, Expose-Headers must still be present
        assert "Access-Control-Allow-Methods" in names
        assert "Access-Control-Allow-Headers" in names
        assert "Access-Control-Expose-Headers" in names


class TestCorsOriginCrlfRejection:
    """Test CRLF / control-character rejection in _resolve_cors_origin().

    Mitigates CodeQL #531 (py/http-response-splitting). A malicious caller
    can submit an Origin header containing CR/LF/NUL bytes; if echoed into
    the Access-Control-Allow-Origin response header without sanitization,
    this would let the attacker inject arbitrary headers (or a fake
    response body) into the proxy's HTTP response stream.

    The fix in _resolve_cors_origin() validates the Origin header via
    _origin_is_safe() and falls back to the configured CORS_ORIGIN when
    the value is unsafe.
    """

    def _get_module(self, cors_origin="*"):
        proxy_mod = _load_proxy_module()
        proxy_mod.CORS_ORIGIN = cors_origin
        return proxy_mod

    def test_origin_is_safe_helper_accepts_normal_origin(self):
        proxy_mod = self._get_module()
        assert proxy_mod._origin_is_safe("https://library.thebosco.club") is True

    def test_origin_is_safe_helper_rejects_empty(self):
        proxy_mod = self._get_module()
        assert proxy_mod._origin_is_safe("") is False

    def test_origin_is_safe_helper_rejects_crlf(self):
        proxy_mod = self._get_module()
        assert proxy_mod._origin_is_safe("http://evil.com\r\nX-Injected: 1") is False

    def test_origin_is_safe_helper_rejects_lf_only(self):
        proxy_mod = self._get_module()
        assert proxy_mod._origin_is_safe("http://evil.com\nX-Injected: 1") is False

    def test_origin_is_safe_helper_rejects_cr_only(self):
        proxy_mod = self._get_module()
        assert proxy_mod._origin_is_safe("http://evil.com\rX-Injected: 1") is False

    def test_origin_is_safe_helper_rejects_null_byte(self):
        proxy_mod = self._get_module()
        assert proxy_mod._origin_is_safe("http://evil.com\x00") is False

    def test_origin_is_safe_helper_rejects_other_control_chars(self):
        proxy_mod = self._get_module()
        # Tab (\x09), vertical tab (\x0b), form feed (\x0c), DEL (\x7f)
        for bad in ("\x09", "\x0b", "\x0c", "\x7f"):
            assert proxy_mod._origin_is_safe(f"http://evil.com{bad}foo") is False

    def test_origin_is_safe_helper_rejects_overlong(self):
        proxy_mod = self._get_module()
        # Anything beyond 256 chars is rejected as pathological
        assert proxy_mod._origin_is_safe("https://" + "a" * 300) is False

    def test_resolve_cors_origin_falls_back_when_origin_has_crlf(self):
        """Malicious Origin with CRLF must fall back to configured CORS_ORIGIN."""
        mod = self._get_module(cors_origin="*")
        headers = _make_headers({"Origin": "http://evil.com\r\nX-Injected: 1"})
        assert mod.app._resolve_cors_origin(headers) == "*"

    def test_resolve_cors_origin_falls_back_when_origin_has_null(self):
        mod = self._get_module(cors_origin="*")
        headers = _make_headers({"Origin": "http://evil.com\x00"})
        assert mod.app._resolve_cors_origin(headers) == "*"

    def test_cors_headers_do_not_echo_injected_header(self):
        """Crucial end-to-end test: the malicious Origin's injected header value
        must not appear anywhere in the emitted response headers."""
        mod = self._get_module(cors_origin="*")
        emitted = mod.app._cors_headers(
            _make_headers({"Origin": "http://evil.com\r\nX-Injected: 1"})
        )
        # The injected header name must not appear in any emitted header
        all_emitted = "\n".join(f"{n}: {v}" for n, v in emitted)
        assert "X-Injected" not in all_emitted
        # The Allow-Origin header must NOT contain the malicious value
        for name, value in emitted:
            if name == "Access-Control-Allow-Origin":
                assert "\r" not in value
                assert "\n" not in value
                assert "X-Injected" not in value
                # When the configured value is "*" and credentials are
                # paired, it's still a CORS-spec violation, but the test's
                # focus is preventing CRLF injection — the "*" fallback is
                # the safe baseline (no injected headers).
                assert value == "*"
                return
        raise AssertionError("Access-Control-Allow-Origin header missing")

    def test_resolve_cors_origin_falls_back_to_configured_specific_when_unsafe(self):
        """When CORS_ORIGIN is a specific allowlisted value and Origin is
        malicious, the configured value still wins (already covered by the
        existing wildcard-bypass logic, but explicitly covered for CRLF)."""
        mod = self._get_module(cors_origin="https://library.thebosco.club")
        headers = _make_headers({"Origin": "http://evil.com\r\nX-Injected: 1"})
        # Specific configured value short-circuits regardless of Origin
        assert mod.app._resolve_cors_origin(headers) == "https://library.thebosco.club"
