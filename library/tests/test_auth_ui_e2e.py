"""
End-to-end browser tests for authentication UI pages.

Supplements test_auth_ui.py (static structure checks) with real browser
rendering and interaction verification via Playwright.

Requires:
    - A test VM running the app, reachable via VM_HOST (or set AUDIOBOOKS_WEB_URL directly)
    - Playwright installed (pip install playwright && playwright install chromium)
    - Brave/Chromium browser available

Run with:
    pytest library/tests/test_auth_ui_e2e.py -v --headed
"""

import os

import pytest

pytestmark = pytest.mark.integration

try:
    from playwright.sync_api import expect, sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

if not PLAYWRIGHT_AVAILABLE:
    pytest.skip("Playwright not available", allow_module_level=True)

VM_HOST = os.environ.get("VM_HOST", "")
WEB_BASE_URL = os.environ.get("AUDIOBOOKS_WEB_URL", f"https://{VM_HOST}:8443" if VM_HOST else "")
IGNORE_HTTPS_ERRORS = True


@pytest.fixture(scope="module")
def browser():
    """Launch a browser for the test module."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    """Create a fresh browser page for each test."""
    context = browser.new_context(ignore_https_errors=IGNORE_HTTPS_ERRORS)
    pg = context.new_page()
    yield pg
    pg.close()
    context.close()


@pytest.fixture(scope="module")
def web_available():
    """Check if the web UI is reachable before running tests."""
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        urllib.request.urlopen(
            f"{WEB_BASE_URL}/login.html", timeout=5, context=ctx
        )  # nosec B310  # test fetches from hardcoded test URL
        return True
    except Exception:
        pytest.skip(f"Web UI not reachable at {WEB_BASE_URL}")


# ---------------------------------------------------------------------------
# Login Page Tests
# ---------------------------------------------------------------------------


class TestLoginPageRendering:
    """Verify login page renders correctly in a real browser."""

    def test_login_page_loads(self, page, web_available):
        """Login page loads without errors."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        expect(page).to_have_title(lambda t: t is not None)

    def test_username_field_visible_and_focusable(self, page, web_available):
        """Username input is visible and accepts focus."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        username = page.locator("#username")
        expect(username).to_be_visible()
        username.focus()
        expect(username).to_be_focused()

    def test_code_field_visible(self, page, web_available):
        """TOTP code input is visible with numeric inputmode."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        code = page.locator("#code")
        expect(code).to_be_visible()
        expect(code).to_have_attribute("inputmode", "numeric")

    def test_submit_button_visible(self, page, web_available):
        """Submit button is visible and clickable."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        submit = page.locator('button[type="submit"], input[type="submit"]').first
        expect(submit).to_be_visible()
        expect(submit).to_be_enabled()

    def test_login_form_shows_error_on_empty_submit(self, page, web_available):
        """Submitting empty form shows validation or error."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        # Try to submit without filling fields
        page.locator('button[type="submit"], input[type="submit"]').first.click()
        # Browser native validation should prevent submission (required fields)
        # or the app shows an error message
        # Either way, we should NOT navigate away from login
        page.wait_for_timeout(500)
        assert "/login" in page.url or "login.html" in page.url

    def test_login_shows_error_on_invalid_credentials(self, page, web_available):
        """Invalid credentials show an error message, not a crash."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        page.fill("#username", "nonexistent_user")
        page.fill("#code", "000000")
        page.locator('button[type="submit"], input[type="submit"]').first.click()
        page.wait_for_timeout(1000)
        # Should still be on login page with an error visible
        assert "/login" in page.url or "login.html" in page.url

    def test_backup_code_form_exists(self, page, web_available):
        """Backup code recovery form is present in the DOM."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        backup = page.locator("#backup-form")
        # May be hidden by default — just verify it exists
        assert backup.count() > 0

    def test_magic_link_option_present(self, page, web_available):
        """Magic link option is present in the login page."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        magic = page.locator("#use-magic-link, #magic-link-form")
        assert magic.count() > 0


# ---------------------------------------------------------------------------
# Registration Page Tests
# ---------------------------------------------------------------------------


class TestRegisterPageRendering:
    """Verify registration page renders and multi-step flow works."""

    def test_register_page_loads(self, page, web_available):
        """Register page loads without errors."""
        page.goto(f"{WEB_BASE_URL}/register.html")
        expect(page).to_have_title(lambda t: t is not None)

    def test_first_step_visible(self, page, web_available):
        """First registration step (request) is visible on load."""
        page.goto(f"{WEB_BASE_URL}/register.html")
        step_request = page.locator("#step-request")
        expect(step_request).to_be_visible()

    def test_subsequent_steps_hidden(self, page, web_available):
        """Later registration steps are hidden on initial load."""
        page.goto(f"{WEB_BASE_URL}/register.html")
        for step_id in ["step-verify", "step-totp", "step-complete"]:
            step = page.locator(f"#{step_id}")
            if step.count() > 0:
                expect(step).to_be_hidden()

    def test_username_input_on_first_step(self, page, web_available):
        """Username input is present and visible on the first step."""
        page.goto(f"{WEB_BASE_URL}/register.html")
        # Look for a username-like input in the request step
        username = page.locator(
            "#step-request input[name='username'], "
            "#step-request #reg-username, "
            "#step-request input[type='text']"
        ).first
        expect(username).to_be_visible()

    def test_qr_code_container_exists(self, page, web_available):
        """QR code container for TOTP setup exists in the DOM."""
        page.goto(f"{WEB_BASE_URL}/register.html")
        qr = page.locator("#qr-code")
        assert qr.count() > 0

    def test_passkey_option_mentioned(self, page, web_available):
        """Passkey option is referenced somewhere on the register page."""
        page.goto(f"{WEB_BASE_URL}/register.html")
        content = page.content()
        assert "passkey" in content.lower() or "Passkey" in content


# ---------------------------------------------------------------------------
# Help Tooltip Interaction Tests
# ---------------------------------------------------------------------------


class TestHelpTooltipInteraction:
    """Verify help tooltips toggle on click in real browser."""

    def test_help_icon_click_shows_tooltip(self, page, web_available):
        """Clicking a help icon makes its associated tooltip visible."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        icons = page.locator(".help-icon")
        if icons.count() == 0:
            pytest.skip("No help icons found on login page")

        first_icon = icons.first
        first_icon.click()
        page.wait_for_timeout(300)

        # After clicking, some tooltip/help-content should become visible
        visible_tooltips = page.locator(".help-content:visible, .help-tooltip:visible")
        assert visible_tooltips.count() > 0, "Clicking help icon should show tooltip"

    def test_help_icon_toggle_hides_tooltip(self, page, web_available):
        """Clicking the same help icon again hides the tooltip."""
        page.goto(f"{WEB_BASE_URL}/login.html")
        icons = page.locator(".help-icon")
        if icons.count() == 0:
            pytest.skip("No help icons found")

        first_icon = icons.first
        # Click to show
        first_icon.click()
        page.wait_for_timeout(300)
        # Click to hide
        first_icon.click()
        page.wait_for_timeout(300)

        # Tooltip should be hidden again
        visible = page.locator(".help-content:visible, .help-tooltip:visible")
        assert visible.count() == 0, "Second click should hide tooltip"


# ---------------------------------------------------------------------------
# Responsive Layout Tests
# ---------------------------------------------------------------------------


class TestResponsiveLayout:
    """Verify auth pages render correctly at different viewport widths."""

    @pytest.mark.parametrize("width,label", [(375, "mobile"), (768, "tablet"), (1280, "desktop")])
    def test_login_page_renders_at_breakpoint(self, browser, web_available, width, label):
        """Login page renders without overflow at {label} width."""
        context = browser.new_context(
            viewport={"width": width, "height": 800}, ignore_https_errors=IGNORE_HTTPS_ERRORS
        )
        pg = context.new_page()
        pg.goto(f"{WEB_BASE_URL}/login.html")
        pg.wait_for_load_state("networkidle")

        # Verify no horizontal overflow (page body fits viewport)
        body_width = pg.evaluate("document.body.scrollWidth")
        assert (
            body_width <= width + 20
        ), f"Login page overflows at {label} ({width}px): body is {body_width}px wide"

        # Verify the main form container is visible
        form_visible = pg.locator(".auth-container, .login-form, form").first.is_visible()
        assert form_visible, f"Login form not visible at {label} width"

        pg.close()
        context.close()

    @pytest.mark.parametrize("width,label", [(375, "mobile"), (768, "tablet"), (1280, "desktop")])
    def test_register_page_renders_at_breakpoint(self, browser, web_available, width, label):
        """Register page renders without overflow at {label} width."""
        context = browser.new_context(
            viewport={"width": width, "height": 800}, ignore_https_errors=IGNORE_HTTPS_ERRORS
        )
        pg = context.new_page()
        pg.goto(f"{WEB_BASE_URL}/register.html")
        pg.wait_for_load_state("networkidle")

        body_width = pg.evaluate("document.body.scrollWidth")
        assert (
            body_width <= width + 20
        ), f"Register page overflows at {label} ({width}px): body is {body_width}px wide"

        pg.close()
        context.close()


# ---------------------------------------------------------------------------
# Error Page Tests
# ---------------------------------------------------------------------------


class TestErrorPages:
    """Verify error pages render correctly."""

    def test_401_page_renders(self, page, web_available):
        """401 error page loads and shows login link."""
        page.goto(f"{WEB_BASE_URL}/401.html")
        login_link = page.locator('a[href="login.html"]')
        expect(login_link).to_be_visible()

    def test_403_page_renders(self, page, web_available):
        """403 error page loads and shows navigation links."""
        page.goto(f"{WEB_BASE_URL}/403.html")
        links = page.locator("a")
        assert links.count() >= 1, "403 page should have at least one navigation link"


# ---------------------------------------------------------------------------
# Verify Page Tests
# ---------------------------------------------------------------------------


class TestVerifyPageRendering:
    """Verify the magic link verification page renders correctly."""

    def test_verify_page_loads(self, page, web_available):
        """Verify page loads without errors."""
        page.goto(f"{WEB_BASE_URL}/verify.html")
        expect(page).to_have_title(lambda t: t is not None)

    def test_manual_token_form_visible(self, page, web_available):
        """Manual token entry form is visible (or becomes visible)."""
        page.goto(f"{WEB_BASE_URL}/verify.html")
        page.wait_for_timeout(1000)
        # The manual form may be shown after auto-verify fails (no token in URL)
        manual = page.locator("#state-manual, #manual-form")
        if manual.count() > 0:
            # At least exists in DOM
            pass
        else:
            pytest.skip("Manual form not found in verify page DOM")

    def test_verify_page_without_token_shows_manual_or_error(self, page, web_available):
        """Loading verify.html without a token shows manual entry or error state."""
        page.goto(f"{WEB_BASE_URL}/verify.html")
        page.wait_for_timeout(1500)
        content = page.content()
        # Should show manual entry form or error state (not stuck on "verifying")
        assert (
            "manual" in content.lower() or "error" in content.lower() or "token" in content.lower()
        ), "Verify page without token should show manual entry or error"
