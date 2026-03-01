"""Verify content page links work correctly within the iframe."""

import re
from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"

# Pages that load inside the iframe
CONTENT_PAGES = [
    "index.html",
    "utilities.html",
    "admin.html",
    "help.html",
    "about.html",
    "contact.html",
]
# Pages that must break out of iframe
AUTH_PAGES = ["login.html", "register.html", "claim.html", "verify.html"]


class TestContentPageLinks:
    def test_auth_links_have_target_top(self):
        """Links to auth pages from content pages must use target='_top'."""
        for page_name in CONTENT_PAGES:
            page_path = WEB_DIR / page_name
            if not page_path.exists():
                continue
            content = page_path.read_text()
            for auth_page in AUTH_PAGES:
                # Find <a> tags linking to auth pages
                links = re.findall(rf'<a[^>]*href="{auth_page}"[^>]*>', content)
                for link in links:
                    assert 'target="_top"' in link, (
                        f"{page_name}: link to {auth_page} must have target='_top': {link}"
                    )

    def test_js_redirects_to_auth_use_top(self):
        """JS redirects to login.html should use window.top.location."""
        for page_name in CONTENT_PAGES:
            page_path = WEB_DIR / page_name
            if not page_path.exists():
                continue
            content = page_path.read_text()
            # Find window.location.href = 'login.html' patterns (not window.top.location.href)
            # First count plain window.location redirects to login
            plain_redirects = re.findall(
                r"window\.location\.href\s*=\s*['\"]login\.html", content
            )
            top_redirects = re.findall(
                r"window\.top\.location\.href\s*=\s*['\"]login\.html", content
            )
            # Every redirect to login should use window.top
            plain_only = len(plain_redirects) - len(top_redirects)
            assert plain_only <= 0, (
                f"{page_name}: has {plain_only} JS redirect(s) to login.html using "
                f"window.location instead of window.top.location"
            )
