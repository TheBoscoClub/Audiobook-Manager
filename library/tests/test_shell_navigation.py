"""Verify login redirects to shell.html and navigation works within iframe."""

from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"


class TestLoginRedirect:
    def test_login_redirects_to_shell(self):
        content = (WEB_DIR / "login.html").read_text()
        assert "shell.html" in content, (
            "login.html must redirect to shell.html after successful login"
        )

    def test_claim_redirects_to_shell(self):
        content = (WEB_DIR / "claim.html").read_text()
        assert "shell.html" in content

    def test_verify_redirects_to_shell(self):
        content = (WEB_DIR / "verify.html").read_text()
        assert "shell.html" in content
