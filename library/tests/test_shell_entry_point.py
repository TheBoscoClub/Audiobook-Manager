"""Verify proxy servers redirect root to shell.html for persistent player."""

from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"


class TestProxyRootRedirect:
    """Both proxy servers must redirect / to /shell.html."""

    def test_proxy_server_redirects_root(self):
        content = (WEB_DIR / "proxy_server.py").read_text()
        assert 'self.path == "/"' in content, (
            "proxy_server.py must check for root path"
        )
        assert "/shell.html" in content, (
            "proxy_server.py must redirect to /shell.html"
        )

    def test_https_server_redirects_root(self):
        content = (WEB_DIR / "https_server.py").read_text()
        assert 'self.path == "/"' in content, (
            "https_server.py must check for root path"
        )
        assert "/shell.html" in content, (
            "https_server.py must redirect to /shell.html"
        )

    def test_no_index_html_redirect(self):
        """Proxy must NOT redirect /index.html — iframe loads it directly."""
        content = (WEB_DIR / "proxy_server.py").read_text()
        # The redirect condition should only match "/" not "/index.html"
        assert 'self.path == "/index.html"' not in content, (
            "proxy must not redirect /index.html — the iframe loads it"
        )
