"""
Unit tests for authentication UI components (Phase 4).

Tests cover:
- HTML page structure and required elements
- CSS file existence and structure
- Help tooltip system implementation
- Mobile responsiveness markers
- JavaScript auth state management functions
"""

import re
import sys
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
WEB_DIR = LIBRARY_DIR / "web-v2"
CSS_DIR = WEB_DIR / "css"
JS_DIR = WEB_DIR / "js"

sys.path.insert(0, str(LIBRARY_DIR))


class TestAuthHTMLPages:
    """Test authentication HTML page structure."""

    def test_login_page_exists(self):
        """Verify login.html exists."""
        login_path = WEB_DIR / "login.html"
        assert login_path.exists(), "login.html should exist"

    def test_register_page_exists(self):
        """Verify register.html exists."""
        register_path = WEB_DIR / "register.html"
        assert register_path.exists(), "register.html should exist"

    def test_401_error_page_exists(self):
        """Verify 401.html exists."""
        error_401 = WEB_DIR / "401.html"
        assert error_401.exists(), "401.html should exist"

    def test_403_error_page_exists(self):
        """Verify 403.html exists."""
        error_403 = WEB_DIR / "403.html"
        assert error_403.exists(), "403.html should exist"

    def test_login_page_has_viewport_meta(self):
        """Verify login page has mobile viewport meta tag."""
        login_content = (WEB_DIR / "login.html").read_text()
        assert 'name="viewport"' in login_content, "Login page should have viewport meta tag"
        assert "width=device-width" in login_content, "Viewport should set device-width"

    def test_register_page_has_viewport_meta(self):
        """Verify register page has mobile viewport meta tag."""
        register_content = (WEB_DIR / "register.html").read_text()
        assert 'name="viewport"' in register_content, "Register page should have viewport meta tag"

    def test_login_page_has_required_form_elements(self):
        """Verify login page has required form inputs."""
        login_content = (WEB_DIR / "login.html").read_text()

        # Check for username field
        assert 'id="username"' in login_content, "Login should have username input"
        assert 'name="username"' in login_content, "Username should have name attribute"

        # Check for TOTP code field
        assert 'id="code"' in login_content, "Login should have code input"
        assert 'inputmode="numeric"' in login_content, "Code field should have numeric inputmode"

        # Check for submit button
        assert 'type="submit"' in login_content, "Login should have submit button"

    def test_login_page_has_backup_code_form(self):
        """Verify login page has backup code recovery form."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'id="backup-form"' in login_content, "Should have backup code form"
        assert 'id="backup-code"' in login_content, "Should have backup code input"

    def test_register_page_has_multi_step_structure(self):
        """Verify register page has multi-step registration flow."""
        register_content = (WEB_DIR / "register.html").read_text()

        # Check for step containers
        assert 'id="step-request"' in register_content, "Should have request step"
        assert 'id="step-verify"' in register_content, "Should have verify step"
        assert 'id="step-totp"' in register_content, "Should have TOTP setup step"
        assert 'id="step-complete"' in register_content, "Should have complete step"

    def test_register_page_has_qr_container(self):
        """Verify register page has QR code container for TOTP setup."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'id="qr-code"' in register_content, "Should have QR code img element"
        assert 'id="totp-secret"' in register_content, "Should have TOTP secret display"
        assert 'id="backup-codes-list"' in register_content, "Should have backup codes list"

    def test_error_pages_have_return_links(self):
        """Verify error pages have navigation links."""
        error_401 = (WEB_DIR / "401.html").read_text()
        error_403 = (WEB_DIR / "403.html").read_text()

        assert 'href="login.html"' in error_401, "401 page should link to login"
        assert 'href="login.html"' in error_403, "403 page should link to login"
        assert 'href="index.html"' in error_403, "403 page should link to library"


class TestHelpTooltipSystem:
    """Test help tooltip implementation for layperson users."""

    def test_login_page_has_help_icons(self):
        """Verify login page has help icons for each field."""
        login_content = (WEB_DIR / "login.html").read_text()

        # Count help icons
        help_icon_count = login_content.count('class="help-icon"')
        assert help_icon_count >= 3, f"Login should have at least 3 help icons, found {help_icon_count}"

    def test_login_page_has_help_tooltips(self):
        """Verify login page has help tooltip content."""
        login_content = (WEB_DIR / "login.html").read_text()

        # Check for tooltip containers
        assert 'class="help-tooltip"' in login_content, "Should have help tooltips"
        assert 'class="help-content"' in login_content, "Should have help content sections"

        # Check for specific help IDs
        assert 'id="username-help"' in login_content, "Should have username help"
        assert 'id="code-help"' in login_content, "Should have code help"

    def test_register_page_has_help_icons(self):
        """Verify register page has help icons."""
        register_content = (WEB_DIR / "register.html").read_text()

        help_icon_count = register_content.count('class="help-icon"')
        assert help_icon_count >= 5, f"Register should have at least 5 help icons, found {help_icon_count}"

    def test_register_page_has_authenticator_help(self):
        """Verify register page explains authenticator apps."""
        register_content = (WEB_DIR / "register.html").read_text()

        # Check for authenticator explanation
        assert 'id="authenticator-help"' in register_content, "Should have authenticator help"
        assert "Google Authenticator" in register_content, "Should mention Google Authenticator"
        assert "Authy" in register_content, "Should mention Authy"

    def test_register_page_has_backup_codes_help(self):
        """Verify register page explains backup codes."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'id="backup-codes-help"' in register_content, "Should have backup codes help"

    def test_help_tooltips_have_layperson_language(self):
        """Verify help text uses simple, accessible language."""
        login_content = (WEB_DIR / "login.html").read_text()
        register_content = (WEB_DIR / "register.html").read_text()

        # Check for simple explanatory phrases
        assert "What is" in login_content, "Should use 'What is' explanations"
        assert "How do I" in register_content or "How to" in register_content, "Should have how-to guidance"

        # Check login help uses simple language
        assert "nickname" in login_content.lower(), "Should explain username simply"

    def test_help_icons_have_aria_labels(self):
        """Verify help icons have accessibility labels."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'aria-label="Help"' in login_content, "Help icons should have aria-label"

    def test_help_tooltips_have_data_help_attributes(self):
        """Verify help icons link to their tooltips via data attributes."""
        login_content = (WEB_DIR / "login.html").read_text()

        # Check for data-help attributes linking icons to tooltips
        assert 'data-help="username-help"' in login_content, "Username icon should link to tooltip"
        assert 'data-help="code-help"' in login_content, "Code icon should link to tooltip"


class TestAuthCSS:
    """Test authentication CSS files."""

    def test_auth_css_exists(self):
        """Verify auth.css exists."""
        auth_css = CSS_DIR / "auth.css"
        assert auth_css.exists(), "auth.css should exist"

    def test_help_tooltips_css_exists(self):
        """Verify help-tooltips.css exists."""
        help_css = CSS_DIR / "help-tooltips.css"
        assert help_css.exists(), "help-tooltips.css should exist"

    def test_auth_css_has_mobile_breakpoints(self):
        """Verify auth.css has mobile responsive styles."""
        auth_css_content = (CSS_DIR / "auth.css").read_text()

        # Check for media queries
        assert "@media" in auth_css_content, "Should have media queries"
        assert "max-width" in auth_css_content, "Should have max-width breakpoints"

    def test_help_tooltips_css_has_mobile_styles(self):
        """Verify help tooltips have mobile-specific styles."""
        help_css_content = (CSS_DIR / "help-tooltips.css").read_text()

        assert "@media" in help_css_content, "Should have media queries"
        assert "480px" in help_css_content, "Should have mobile breakpoint"

    def test_auth_css_has_user_menu_styles(self):
        """Verify auth.css has user menu component styles."""
        auth_css_content = (CSS_DIR / "auth.css").read_text()

        assert ".user-menu" in auth_css_content, "Should have user-menu class"
        assert ".user-button" in auth_css_content, "Should have user-button class"
        assert ".user-dropdown" in auth_css_content, "Should have user-dropdown class"

    def test_help_tooltips_css_has_icon_styles(self):
        """Verify help tooltips CSS styles the help icons."""
        help_css_content = (CSS_DIR / "help-tooltips.css").read_text()

        assert ".help-icon" in help_css_content, "Should have help-icon styles"
        assert ".help-tooltip" in help_css_content, "Should have help-tooltip styles"
        assert ".help-content" in help_css_content, "Should have help-content styles"

    def test_auth_css_links_in_html_pages(self):
        """Verify HTML pages include auth.css."""
        login_content = (WEB_DIR / "login.html").read_text()
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'href="css/auth.css"' in login_content, "Login should include auth.css"
        assert 'href="css/auth.css"' in register_content, "Register should include auth.css"

    def test_help_tooltips_css_links_in_html_pages(self):
        """Verify HTML pages include help-tooltips.css."""
        login_content = (WEB_DIR / "login.html").read_text()
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'href="css/help-tooltips.css"' in login_content, "Login should include help-tooltips.css"
        assert 'href="css/help-tooltips.css"' in register_content, "Register should include help-tooltips.css"


class TestAuthJavaScript:
    """Test authentication JavaScript functionality."""

    def test_library_js_exists(self):
        """Verify library.js exists."""
        library_js = JS_DIR / "library.js"
        assert library_js.exists(), "library.js should exist"

    def test_library_js_has_auth_state_management(self):
        """Verify library.js has auth state management."""
        library_js_content = (JS_DIR / "library.js").read_text()

        # Check for auth-related properties
        assert "this.user" in library_js_content, "Should track user state"
        assert "this.authEnabled" in library_js_content, "Should track auth enabled state"

    def test_library_js_has_check_auth_function(self):
        """Verify library.js has session checking function."""
        library_js_content = (JS_DIR / "library.js").read_text()

        assert "checkAuth" in library_js_content, "Should have checkAuth function"
        assert "/auth/session" in library_js_content, "Should call session endpoint"

    def test_library_js_has_logout_function(self):
        """Verify library.js has logout functionality."""
        library_js_content = (JS_DIR / "library.js").read_text()

        assert "logout" in library_js_content.lower(), "Should have logout functionality"
        assert "/auth/logout" in library_js_content, "Should call logout endpoint"

    def test_library_js_has_download_function(self):
        """Verify library.js has download functionality."""
        library_js_content = (JS_DIR / "library.js").read_text()

        assert "downloadAudiobook" in library_js_content, "Should have download function"
        assert "/download/" in library_js_content, "Should call download endpoint"

    def test_library_js_has_user_ui_update(self):
        """Verify library.js updates user UI."""
        library_js_content = (JS_DIR / "library.js").read_text()

        assert "updateUserUI" in library_js_content, "Should have UI update function"

    def test_login_page_has_tooltip_javascript(self):
        """Verify login page has tooltip toggle JavaScript."""
        login_content = (WEB_DIR / "login.html").read_text()

        # Check for tooltip handling code
        assert "help-icon" in login_content, "Should reference help-icon class"
        assert "addEventListener" in login_content, "Should add event listeners"
        assert "data-help" in login_content, "Should use data-help attributes"

    def test_register_page_has_tooltip_javascript(self):
        """Verify register page has tooltip toggle JavaScript."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert "help-icon" in register_content, "Should reference help-icon class"
        assert "addEventListener" in register_content, "Should add event listeners"

    def test_login_page_has_form_validation(self):
        """Verify login page validates form input."""
        login_content = (WEB_DIR / "login.html").read_text()

        # Check for basic validation
        assert "required" in login_content, "Form inputs should be required"
        assert "pattern" in login_content or "maxlength" in login_content, "Should have input constraints"

    def test_register_page_has_step_navigation(self):
        """Verify register page has step navigation logic."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert "showStep" in register_content, "Should have step navigation function"


class TestIndexPageAuthIntegration:
    """Test main index.html auth integration."""

    def test_index_page_includes_auth_css(self):
        """Verify index.html includes auth.css for user menu."""
        index_content = (WEB_DIR / "index.html").read_text()

        assert 'href="css/auth.css"' in index_content, "Index should include auth.css"

    def test_index_page_has_user_menu(self):
        """Verify index.html has user menu container."""
        index_content = (WEB_DIR / "index.html").read_text()

        assert 'id="user-menu"' in index_content, "Should have user menu container"
        assert 'id="user-menu-button"' in index_content, "Should have user menu button"

    def test_index_page_has_login_link(self):
        """Verify index.html has login link for unauthenticated users."""
        index_content = (WEB_DIR / "index.html").read_text()

        assert 'id="login-link"' in index_content, "Should have login link"
        assert 'href="login.html"' in index_content, "Login link should point to login.html"

    def test_index_page_has_logout_button(self):
        """Verify index.html has logout button in user menu."""
        index_content = (WEB_DIR / "index.html").read_text()

        assert 'id="logout-button"' in index_content, "Should have logout button"


class TestDownloadButtonIntegration:
    """Test download button in library UI."""

    def test_library_css_has_download_button_styles(self):
        """Verify library.css has download button styles."""
        library_css_content = (CSS_DIR / "library.css").read_text()

        assert ".btn-download" in library_css_content, "Should have download button styles"

    def test_library_css_download_button_mobile_styles(self):
        """Verify download button has mobile responsive styles."""
        library_css_content = (CSS_DIR / "library.css").read_text()

        # Check that download button is included in mobile styles
        mobile_section = library_css_content[library_css_content.find("@media"):]
        assert ".btn-download" in mobile_section, "Download button should have mobile styles"


class TestVerifyPage:
    """Test magic link verification landing page."""

    def test_verify_page_exists(self):
        """Verify verify.html exists."""
        verify_path = WEB_DIR / "verify.html"
        assert verify_path.exists(), "verify.html should exist"

    def test_verify_page_has_viewport_meta(self):
        """Verify verify page has mobile viewport meta tag."""
        verify_content = (WEB_DIR / "verify.html").read_text()
        assert 'name="viewport"' in verify_content, "Verify page should have viewport meta tag"

    def test_verify_page_has_token_form(self):
        """Verify page has manual token entry form."""
        verify_content = (WEB_DIR / "verify.html").read_text()

        assert 'id="manual-form"' in verify_content, "Should have manual entry form"
        assert 'id="manual-token"' in verify_content, "Should have manual token input field"
        assert 'type="submit"' in verify_content, "Should have submit button"

    def test_verify_page_has_state_containers(self):
        """Verify page has containers for different states."""
        verify_content = (WEB_DIR / "verify.html").read_text()

        assert 'id="state-verifying"' in verify_content, "Should have verifying state"
        assert 'id="state-success"' in verify_content, "Should have success state"
        assert 'id="state-error"' in verify_content, "Should have error state"
        assert 'id="state-manual"' in verify_content, "Should have manual entry state"

    def test_verify_page_handles_url_token(self):
        """Verify page JavaScript checks for token in URL."""
        verify_content = (WEB_DIR / "verify.html").read_text()

        assert "URLSearchParams" in verify_content, "Should parse URL parameters"
        assert ".get('token')" in verify_content or 'get("token")' in verify_content, "Should get token param"

    def test_verify_page_calls_correct_endpoint(self):
        """Verify page calls magic-link/verify endpoint."""
        verify_content = (WEB_DIR / "verify.html").read_text()

        assert "/auth/magic-link/verify" in verify_content, "Should call verify endpoint"

    def test_verify_page_includes_auth_css(self):
        """Verify page includes auth.css."""
        verify_content = (WEB_DIR / "verify.html").read_text()

        assert 'href="css/auth.css"' in verify_content, "Should include auth.css"

    def test_verify_page_has_art_deco_styling(self):
        """Verify page uses Art Deco styling classes."""
        verify_content = (WEB_DIR / "verify.html").read_text()

        assert 'class="auth-page"' in verify_content, "Should use auth-page class"
        assert 'class="auth-container"' in verify_content, "Should use auth-container class"


class TestMagicLinkOption:
    """Test magic link option in login page."""

    def test_login_page_has_magic_link_option(self):
        """Verify login page has magic link option."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'id="use-magic-link"' in login_content, "Should have magic link option"
        assert "Email me a link" in login_content, "Should have magic link text"

    def test_login_page_has_magic_link_form(self):
        """Verify login page has hidden magic link form."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'id="magic-link-form"' in login_content, "Should have magic link form"
        assert 'id="magic-username"' in login_content, "Should have magic link username input"

    def test_magic_link_form_calls_correct_endpoint(self):
        """Verify magic link form calls correct endpoint."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert "/auth/magic-link" in login_content, "Should call magic-link endpoint"

    def test_magic_link_has_help_tooltip(self):
        """Verify magic link option has help tooltip."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'data-help="magic-link-option-help"' in login_content or 'id="magic-link-help"' in login_content, \
            "Magic link should have help tooltip"

    def test_login_page_shows_success_message_for_magic_link(self):
        """Verify login page can show success message after magic link request."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'id="magic-link-success"' in login_content or "success-message-box" in login_content, \
            "Should have success message container"


class TestAuthMethodSelection:
    """Test authentication method selection in registration."""

    def test_register_page_has_auth_method_step(self):
        """Verify register page has auth method selection step."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'id="step-auth-method"' in register_content, "Should have auth method step"

    def test_register_page_has_totp_option(self):
        """Verify register page has TOTP authenticator option."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'id="method-totp"' in register_content or 'Authenticator App' in register_content, \
            "Should have TOTP option"

    def test_register_page_has_passkey_option(self):
        """Verify register page shows passkey option (even if disabled)."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert "Passkey" in register_content or "passkey" in register_content, \
            "Should mention passkey option"
        assert "Coming Soon" in register_content, \
            "Passkey should be marked as coming soon"

    def test_register_page_auth_method_selection_javascript(self):
        """Verify register page handles auth method selection."""
        register_content = (WEB_DIR / "register.html").read_text()

        # Check for auth method related JavaScript
        assert "authMethod" in register_content.lower() or "method" in register_content, \
            "Should have auth method handling"

    def test_register_pending_verification_state(self):
        """Verify register page tracks pending verification between steps."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert "pendingVerification" in register_content, \
            "Should track pending verification state"


class TestSecurityAttributes:
    """Test security-related attributes in auth pages."""

    def test_login_page_has_autocomplete_attributes(self):
        """Verify login form has proper autocomplete hints."""
        login_content = (WEB_DIR / "login.html").read_text()

        assert 'autocomplete="username"' in login_content, "Username should have autocomplete"
        assert 'autocomplete="one-time-code"' in login_content, "TOTP code should have one-time-code autocomplete"

    def test_register_page_disables_autocomplete_for_token(self):
        """Verify sensitive fields disable autocomplete."""
        register_content = (WEB_DIR / "register.html").read_text()

        assert 'autocomplete="off"' in register_content, "Verification token should disable autocomplete"

    def test_credentials_use_include(self):
        """Verify fetch calls include credentials for cookies."""
        login_content = (WEB_DIR / "login.html").read_text()
        library_js_content = (JS_DIR / "library.js").read_text()

        assert "credentials: 'include'" in login_content, "Login should include credentials"
        assert "credentials: 'include'" in library_js_content, "Library.js should include credentials"
