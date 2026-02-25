"""Verify security headers allow same-origin iframe embedding."""

from pathlib import Path

CORE_PY = (
    Path(__file__).parent.parent / "backend" / "api_modular" / "core.py"
)


class TestSecurityHeadersForIframe:
    """Headers must allow same-origin framing for shell.html iframe architecture."""

    def test_x_frame_options_sameorigin(self):
        content = CORE_PY.read_text()
        # Must have SAMEORIGIN
        assert '"SAMEORIGIN"' in content, (
            "X-Frame-Options must be SAMEORIGIN (not DENY) for iframe shell"
        )
        # Must NOT have DENY for X-Frame-Options
        lines = content.split("\n")
        for line in lines:
            if "X-Frame-Options" in line:
                assert "DENY" not in line, (
                    "X-Frame-Options must not be DENY"
                )

    def test_csp_frame_ancestors_self(self):
        content = CORE_PY.read_text()
        assert "frame-ancestors 'self'" in content, (
            "CSP frame-ancestors must be 'self' (not 'none')"
        )
        assert "frame-ancestors 'none'" not in content, (
            "CSP frame-ancestors must not be 'none'"
        )

    def test_csp_frame_src_self(self):
        content = CORE_PY.read_text()
        assert "frame-src 'self'" in content, (
            "CSP must include frame-src 'self' to permit iframe element"
        )

    def test_other_security_headers_unchanged(self):
        """Verify we didn't accidentally remove other security headers."""
        content = CORE_PY.read_text()
        assert "X-Content-Type-Options" in content
        assert "nosniff" in content
        assert "Referrer-Policy" in content
        assert "Permissions-Policy" in content
