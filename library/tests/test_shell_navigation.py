"""Verify auth pages redirect to / (canonical URL) after successful login."""

from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"


class TestLoginRedirect:
    def test_login_redirects_to_root(self):
        content = (WEB_DIR / "login.html").read_text()
        assert "window.location.href = '/'" in content, (
            "login.html must redirect to / after successful login"
        )
        assert "window.location.href = 'shell.html'" not in content, (
            "login.html must NOT redirect to shell.html directly"
        )

    def test_claim_redirects_to_root(self):
        content = (WEB_DIR / "claim.html").read_text()
        assert "window.location.href = '/'" in content
        assert "window.location.href = 'shell.html'" not in content

    def test_verify_redirects_to_root(self):
        content = (WEB_DIR / "verify.html").read_text()
        assert "window.location.href = '/'" in content
        assert "window.location.href = 'shell.html'" not in content

    def test_no_shell_html_links(self):
        """No auth page should contain href='shell.html' navigation links."""
        for page in ("login.html", "verify.html", "claim.html"):
            content = (WEB_DIR / page).read_text()
            assert 'href="shell.html"' not in content, (
                f"{page} must not link to shell.html — use / instead"
            )
            assert "href='shell.html'" not in content, (
                f"{page} must not link to shell.html — use / instead"
            )
