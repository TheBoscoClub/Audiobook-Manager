"""Trust-boundary tests for the proxy → Flask client-address handoff.

Two layers cooperate to decide whether a request counts as "localhost" for
``@localhost_only`` / ``@admin_or_localhost``:

1. ``proxy_server.ReverseProxyApp._collect_proxy_headers`` — drops any
   client-supplied ``X-Forwarded-For`` / ``X-Real-IP`` / ``X-Forwarded-Proto``
   / ``Host`` and re-authors them from the address it actually observed.
2. ``auth_shared.effective_client_address`` — consults those headers only when
   the request reached Flask from a loopback peer (i.e. from that proxy).

Before this pair existed, either layer alone was bypassable: the proxy
forwarded the client's ``X-Forwarded-For`` verbatim and the decorators read
its left-most entry, so ``X-Forwarded-For: 127.0.0.1`` from anywhere on the
network granted access to localhost-gated admin endpoints.
"""

import sys
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from common import is_loopback_address  # noqa: E402

WEB_V2_DIR = LIBRARY_DIR / "web-v2"


def _proxy_module():
    """Import proxy_server (config is already importable via LIBRARY_DIR)."""
    if str(WEB_V2_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_V2_DIR))
    import proxy_server as ps  # type: ignore[import-not-found]

    return ps


proxy_server = _proxy_module()


def _headers(mapping):
    """Build an EnvironHeaders view from plain header names."""
    environ = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in mapping.items()}
    return proxy_server.EnvironHeaders(environ)


# ============================================================
# 1. is_loopback_address() — the single canonical predicate
# ============================================================


class TestIsLoopbackAddress:
    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1", "localhost", "LOCALHOST", " ::1 "],
    )
    def test_loopback_forms_accepted(self, address):
        assert is_loopback_address(address) is True

    @pytest.mark.parametrize(
        "address",
        ["10.0.0.5", "203.0.113.50", "192.168.1.1", "2001:db8::1", "", "   ", None, "not-an-ip"],
    )
    def test_non_loopback_and_garbage_rejected(self, address):
        """Fails closed — unparseable input must never read as loopback."""
        assert is_loopback_address(address) is False


# ============================================================
# 2. Proxy layer: forwarded headers are authored, not relayed
# ============================================================


class TestProxyAuthorsForwardingHeaders:
    def test_client_supplied_forwarding_headers_are_dropped(self):
        environ = {"REMOTE_ADDR": "203.0.113.50"}
        headers = _headers(
            {
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
                "X-Forwarded-Proto": "http",
                "Host": "evil.example.com",
            }
        )
        forwarded = proxy_server.app._collect_proxy_headers(environ, headers)
        assert forwarded["X-Forwarded-For"] == "203.0.113.50"
        assert forwarded["X-Real-IP"] == "203.0.113.50"
        assert forwarded["X-Forwarded-Proto"] == "https"
        assert forwarded["Host"] == f"127.0.0.1:{proxy_server.API_PORT}"

    def test_direct_client_address_is_the_socket_peer(self):
        forwarded = proxy_server.app._collect_proxy_headers(
            {"REMOTE_ADDR": "10.0.0.5"}, _headers({})
        )
        assert forwarded["X-Forwarded-For"] == "10.0.0.5"
        assert forwarded["X-Real-IP"] == "10.0.0.5"

    def test_genuine_localhost_call_is_forwarded_as_localhost(self):
        forwarded = proxy_server.app._collect_proxy_headers(
            {"REMOTE_ADDR": "127.0.0.1"}, _headers({})
        )
        assert forwarded["X-Real-IP"] == "127.0.0.1"

    def test_trusted_front_door_contributes_its_own_observation(self):
        """Caddy (loopback peer) appends the address it saw — use that entry.

        Caddy APPENDS to any inbound X-Forwarded-For, so the left-most entry
        is whatever the internet client sent and the right-most is Caddy's
        own observation. Reading the left-most entry was the original bug.
        """
        environ = {"REMOTE_ADDR": "127.0.0.1"}
        headers = _headers({"X-Forwarded-For": "127.0.0.1, 203.0.113.50"})
        forwarded = proxy_server.app._collect_proxy_headers(environ, headers)
        assert forwarded["X-Real-IP"] == "203.0.113.50"

    def test_client_headers_still_forwarded(self):
        headers = _headers({"Cookie": "audiobooks_session=abc", "Range": "bytes=0-99"})
        forwarded = proxy_server.app._collect_proxy_headers({"REMOTE_ADDR": "127.0.0.1"}, headers)
        assert forwarded["Cookie"] == "audiobooks_session=abc"
        assert forwarded["Range"] == "bytes=0-99"

    def test_crlf_in_forwarded_address_is_stripped(self):
        environ = {"REMOTE_ADDR": "127.0.0.1"}
        headers = _headers({"X-Forwarded-For": "1.2.3.4\r\nX-Admin: yes"})
        forwarded = proxy_server.app._collect_proxy_headers(environ, headers)
        assert "\r" not in forwarded["X-Forwarded-For"]
        assert "\n" not in forwarded["X-Forwarded-For"]


# ============================================================
# 3. Flask layer: both localhost decorators, four scenarios each
# ============================================================


def _decorated(decorator):
    @decorator
    def dummy_view():
        return "ok"

    return dummy_view


def _status_of(result):
    """Return the HTTP status of a view result ('ok' means it ran)."""
    return 200 if result == "ok" else result[1]


# (label, REMOTE_ADDR, request headers, expected status)
_TRUST_CASES = [
    ("direct localhost", "127.0.0.1", {}, 200),
    ("proxied localhost-origin", "127.0.0.1", {"X-Real-IP": "127.0.0.1"}, 200),
    ("proxied remote", "127.0.0.1", {"X-Real-IP": "203.0.113.50"}, 404),
    (
        "proxied remote, spoofed XFF",
        "127.0.0.1",
        {"X-Real-IP": "203.0.113.50", "X-Forwarded-For": "127.0.0.1"},
        404,
    ),
    ("direct remote", "10.0.0.5", {}, 404),
    ("direct remote, spoofed XFF", "10.0.0.5", {"X-Forwarded-For": "127.0.0.1"}, 404),
    ("direct remote, spoofed X-Real-IP", "10.0.0.5", {"X-Real-IP": "127.0.0.1"}, 404),
]


class TestLocalhostOnlyTrustBoundary:
    @pytest.mark.parametrize("label,remote_addr,headers,expected", _TRUST_CASES)
    def test_localhost_only(self, auth_app, label, remote_addr, headers, expected):
        from backend.api_modular.auth import localhost_only

        with auth_app.test_request_context(
            "/test", environ_base={"REMOTE_ADDR": remote_addr}, headers=headers
        ):
            assert _status_of(_decorated(localhost_only)()) == expected, label

    @pytest.mark.parametrize("label,remote_addr,headers,expected", _TRUST_CASES)
    def test_admin_or_localhost_standalone_mode(
        self, auth_app, label, remote_addr, headers, expected
    ):
        """AUTH_ENABLED=false is the mode where the address gate applies."""
        from backend.api_modular.auth import admin_or_localhost

        previous = auth_app.config.get("AUTH_ENABLED", False)
        auth_app.config["AUTH_ENABLED"] = False
        try:
            with auth_app.test_request_context(
                "/test", environ_base={"REMOTE_ADDR": remote_addr}, headers=headers
            ):
                assert _status_of(_decorated(admin_or_localhost)()) == expected, label
        finally:
            auth_app.config["AUTH_ENABLED"] = previous

    def test_admin_or_localhost_requires_admin_when_auth_enabled(self, auth_app):
        """With AUTH_ENABLED=true the address is never consulted at all."""
        from backend.api_modular.auth import admin_or_localhost

        previous = auth_app.config.get("AUTH_ENABLED", False)
        auth_app.config["AUTH_ENABLED"] = True
        try:
            with auth_app.test_request_context(
                "/test",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
                headers={"X-Real-IP": "127.0.0.1"},
            ):
                assert _status_of(_decorated(admin_or_localhost)()) == 401
        finally:
            auth_app.config["AUTH_ENABLED"] = previous


class TestEffectiveClientAddress:
    def test_x_real_ip_beats_forged_forwarded_chain(self, auth_app):
        from backend.api_modular.auth_shared import effective_client_address

        with auth_app.test_request_context(
            "/test",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Real-IP": "203.0.113.50", "X-Forwarded-For": "127.0.0.1, 127.0.0.1"},
        ):
            assert effective_client_address() == "203.0.113.50"

    def test_headers_ignored_entirely_for_non_loopback_peer(self, auth_app):
        from backend.api_modular.auth_shared import effective_client_address

        with auth_app.test_request_context(
            "/test",
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
            headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-For": "127.0.0.1"},
        ):
            assert effective_client_address() == "10.0.0.5"


# ============================================================
# The local maintenance door is never forwarded (bd Audiobook-Manager-bd38.1)
# ============================================================


def _recorder(reached: list):
    """A stand-in handler that notes it was called and returns an empty body."""

    def handler(*args, **kwargs):
        reached.append(args)
        return [b""]

    return handler


class TestLocalOnlyPathsAreNotForwarded:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/local/maintenance/status",
            "/api/local/",
            "/api/local",
            "/api/%6cocal/maintenance/status",  # percent-encoded "l"
            "/api//local/maintenance/status",
            "/api/local/maintenance/status?x=1",
            "/api/./local/maintenance/status",
            "/api/x/../local/maintenance/status",
        ],
    )
    @pytest.mark.parametrize("method", ["GET", "POST"])
    def test_refused_without_reaching_the_api(self, path, method, monkeypatch):
        app = proxy_server.ReverseProxyApp()
        reached: list = []
        monkeypatch.setattr(app, "proxy_to_api", _recorder(reached), raising=True)
        monkeypatch.setattr(app, "_tunnel_websocket", _recorder(reached), raising=True)
        raw_path, _, query = path.partition("?")
        environ = {
            "REQUEST_METHOD": method,
            "RAW_URI": path,
            "PATH_INFO": raw_path,
            "QUERY_STRING": query,
            "REMOTE_ADDR": "192.0.2.5",
            "HTTP_UPGRADE": "websocket",
            "HTTP_CONNECTION": "Upgrade",
        }
        seen = {}

        def start_response(status, headers):
            seen["status"] = status

        app(environ, start_response)
        assert seen["status"].startswith("404")
        assert reached == []

    def test_ordinary_api_paths_still_forward(self, monkeypatch):
        app = proxy_server.ReverseProxyApp()
        reached: list = []
        monkeypatch.setattr(app, "proxy_to_api", _recorder(reached), raising=True)
        environ = {
            "REQUEST_METHOD": "GET",
            "RAW_URI": "/api/localization/strings",
            "PATH_INFO": "/api/localization/strings",
            "QUERY_STRING": "",
            "REMOTE_ADDR": "192.0.2.5",
        }
        app(environ, lambda status, headers: None)
        assert len(reached) == 1


# ============================================================
# The WebSocket tunnel re-authors forwarding headers too (bd Audiobook-Manager-ur75.1)
# ============================================================


class TestWebSocketTunnelForwardingHeaders:
    """The tunnel copies client headers to the API. Forwarding headers must not
    be among them: the API trusts X-Real-IP / X-Forwarded-For from a loopback
    peer, and the tunnel's peer address IS loopback."""

    def _upgrade_request(self, client_headers, remote_addr="203.0.113.9"):
        environ = {
            "REQUEST_METHOD": "POST",
            "RAW_URI": "/api/translate/segment-complete",
            "PATH_INFO": "/api/translate/segment-complete",
            "QUERY_STRING": "",
            "REMOTE_ADDR": remote_addr,
            "HTTP_UPGRADE": "websocket",
            "HTTP_CONNECTION": "Upgrade",
        }
        for name, value in client_headers.items():
            environ["HTTP_" + name.upper().replace("-", "_")] = value
        raw = proxy_server.app._build_ws_upgrade_request(
            environ, proxy_server.EnvironHeaders(environ)
        )
        lines = raw.decode("latin-1").split("\r\n")
        sent: dict[str, list[str]] = {}
        for line in lines[1:]:
            if line:
                name, _, value = line.partition(": ")
                sent.setdefault(name.lower(), []).append(value)
        return lines[0], sent

    @pytest.mark.parametrize(
        "forged",
        [
            {"X-Real-IP": "127.0.0.1"},
            {"X-Forwarded-For": "127.0.0.1"},
            {"X-Forwarded-For": "127.0.0.1, 198.51.100.7", "X-Real-IP": "::1"},
            {"Forwarded": "for=127.0.0.1"},
        ],
    )
    def test_forged_forwarding_headers_are_replaced(self, forged):
        _, sent = self._upgrade_request(forged)
        assert sent["x-real-ip"] == ["203.0.113.9"]
        assert sent["x-forwarded-for"] == ["203.0.113.9"]
        assert "forwarded" not in sent

    def test_a_client_that_sends_none_still_gets_them_written(self):
        # With no forwarding header at all the API would fall back to the
        # tunnel's own loopback peer address and call the client "localhost".
        _, sent = self._upgrade_request({})
        assert sent["x-real-ip"] == ["203.0.113.9"]
        assert sent["x-forwarded-for"] == ["203.0.113.9"]
        assert sent["x-forwarded-proto"] == ["https"]

    def test_the_upgrade_headers_and_cookie_still_pass(self):
        request_line, sent = self._upgrade_request({"Cookie": "audiobooks_session=abc"})
        assert request_line == "POST /api/translate/segment-complete HTTP/1.1"
        assert sent["upgrade"] == ["websocket"]
        assert sent["connection"] == ["Upgrade"]
        assert sent["cookie"] == ["audiobooks_session=abc"]
