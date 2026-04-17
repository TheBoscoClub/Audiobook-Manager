"""
Unit tests for auth.py email functions, localhost_only decorator,
first-user bootstrap, invite metadata parsing, and WebAuthn config
auto-derivation.

All tests use mocking — no VM or SMTP server required.
"""

import email
import json
import os
import smtplib
from unittest.mock import MagicMock, patch


def _decode_email_body(raw_msg: str) -> str:
    """Decode a MIME email message and return the combined text of all parts."""
    msg = email.message_from_string(raw_msg)
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(parts) if parts else raw_msg


# ---------------------------------------------------------------------------
# 1. localhost_only() decorator (lines 234-252)
# ---------------------------------------------------------------------------


class TestLocalhostOnly:
    """Tests for the localhost_only() decorator."""

    def test_allows_127_0_0_1(self, auth_app):
        """Request from 127.0.0.1 should pass through."""
        with auth_app.test_request_context("/test", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            result = dummy_view()
            assert result == "ok"

    def test_allows_ipv6_loopback(self, auth_app):
        """Request from ::1 should pass through."""
        with auth_app.test_request_context("/test", environ_base={"REMOTE_ADDR": "::1"}):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            result = dummy_view()
            assert result == "ok"

    def test_blocks_remote_addr(self, auth_app):
        """Request from a non-local IP should return 404."""
        with auth_app.test_request_context("/test", environ_base={"REMOTE_ADDR": "10.0.0.5"}):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            response, status = dummy_view()
            assert status == 404

    def test_x_forwarded_for_local(self, auth_app):
        """X-Forwarded-For with 127.0.0.1 should pass through."""
        with auth_app.test_request_context(
            "/test",
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
            headers={"X-Forwarded-For": "127.0.0.1"},
        ):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            result = dummy_view()
            assert result == "ok"

    def test_x_forwarded_for_remote(self, auth_app):
        """X-Forwarded-For with a remote IP should return 404."""
        with auth_app.test_request_context(
            "/test",
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
            headers={"X-Forwarded-For": "203.0.113.50, 10.0.0.1"},
        ):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            response, status = dummy_view()
            assert status == 404

    def test_x_forwarded_for_ipv6_loopback(self, auth_app):
        """X-Forwarded-For with ::1 should pass through."""
        with auth_app.test_request_context(
            "/test", environ_base={"REMOTE_ADDR": "192.168.1.1"}, headers={"X-Forwarded-For": "::1"}
        ):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            result = dummy_view()
            assert result == "ok"

    def test_error_response_hides_existence(self, auth_app):
        """404 response should include 'Access denied' to hide endpoint."""
        with auth_app.test_request_context("/test", environ_base={"REMOTE_ADDR": "8.8.8.8"}):
            from backend.api_modular.auth import localhost_only

            @localhost_only
            def dummy_view():
                return "ok"

            response, status = dummy_view()
            data = response.get_json()
            assert data["error"] == "Access denied"
            assert status == 404


# ---------------------------------------------------------------------------
# 2. First-user bootstrap TOTP (lines 1051-1073)
# ---------------------------------------------------------------------------


class TestFirstUserBootstrap:
    """Tests for the first-user-is-admin bootstrap path (lines 1049-1083).

    Uses patch.object on the auth module to mock UserRepository, etc.
    The route is /auth/register/start.
    """

    @staticmethod
    def _get_auth_module():
        """Get the actual auth module used by Flask's registered blueprint.

        Due to sys.path containing both library/ and library/backend/,
        the module may be loaded under two names: 'api_modular.auth'
        (used by create_app) and 'backend.api_modular.auth'. We need
        the one whose globals are bound to the view functions.
        """
        import sys as _sys

        # Prefer the short-path import used by create_app
        if "api_modular.auth" in _sys.modules:
            return _sys.modules["api_modular.auth"]
        import backend.api_modular.auth as auth_mod

        return auth_mod

    def test_first_user_becomes_admin_with_totp(self, auth_app):
        """When user_count=0, the first user is auto-created as admin
        with TOTP setup, QR code, and backup codes."""
        auth_mod = self._get_auth_module()
        mock_qr_png = b"\x89PNG_fake_qr"

        with (
            patch.object(auth_mod, "UserRepository") as MockUserRepo,
            patch.object(auth_mod, "AccessRequestRepository") as MockReqRepo,
            patch.object(
                auth_mod,
                "setup_totp",
                return_value=(
                    b"secret_bytes",
                    "BASE32SECRET",
                    "otpauth://totp/Library:bootstrap_user?secret=BASE32SECRET",
                ),
            ),
            patch.object(auth_mod, "generate_qr_code", return_value=mock_qr_png),
            patch.object(auth_mod, "base32_to_secret", return_value=b"decoded_secret"),
            patch.object(auth_mod, "BackupCodeRepository") as MockBackupRepo,
            patch.object(auth_mod, "User") as MockUser,
        ):
            mock_user_repo = MockUserRepo.return_value
            mock_user_repo.username_exists.return_value = False
            mock_user_repo.count.return_value = 0

            mock_req_repo = MockReqRepo.return_value
            mock_req_repo.has_any_request.return_value = False

            mock_user_instance = MagicMock()
            mock_user_instance.id = 1
            mock_user_instance.save.return_value = mock_user_instance
            MockUser.return_value = mock_user_instance

            mock_backup_repo = MockBackupRepo.return_value
            mock_backup_repo.create_codes_for_user.return_value = ["CODE1", "CODE2", "CODE3"]

            client = auth_app.test_client()
            resp = client.post("/auth/register/start", json={"username": "bootstrapuser"})
            data = resp.get_json()

            assert data["success"] is True
            assert data["first_user"] is True
            assert data["totp_secret"] == "BASE32SECRET"
            assert data["backup_codes"] == ["CODE1", "CODE2", "CODE3"]
            assert "totp_qr" in data
            assert "totp_uri" in data

            # Verify user was created as admin
            MockUser.assert_called_once()
            call_kwargs = MockUser.call_args
            assert call_kwargs[1]["is_admin"] is True
            assert call_kwargs[1]["can_download"] is True

    def test_first_user_qr_is_valid_base64(self, auth_app):
        """The QR code in the bootstrap response should be valid base64."""
        import base64

        auth_mod = self._get_auth_module()
        mock_qr_png = b"\x89PNG_test_data_12345"
        expected_b64 = base64.b64encode(mock_qr_png).decode("ascii")

        with (
            patch.object(auth_mod, "UserRepository") as MockUserRepo,
            patch.object(auth_mod, "AccessRequestRepository") as MockReqRepo,
            patch.object(auth_mod, "setup_totp", return_value=(b"secret", "B32", "otpauth://...")),
            patch.object(auth_mod, "generate_qr_code", return_value=mock_qr_png),
            patch.object(auth_mod, "base32_to_secret", return_value=b"decoded"),
            patch.object(auth_mod, "BackupCodeRepository") as MockBackupRepo,
            patch.object(auth_mod, "User") as MockUser,
        ):
            mock_user_repo = MockUserRepo.return_value
            mock_user_repo.username_exists.return_value = False
            mock_user_repo.count.return_value = 0

            mock_req_repo = MockReqRepo.return_value
            mock_req_repo.has_any_request.return_value = False

            mock_user_instance = MagicMock()
            mock_user_instance.id = 1
            mock_user_instance.save.return_value = mock_user_instance
            MockUser.return_value = mock_user_instance

            mock_backup_repo = MockBackupRepo.return_value
            mock_backup_repo.create_codes_for_user.return_value = ["C1"]

            client = auth_app.test_client()
            resp = client.post("/auth/register/start", json={"username": "qrbootstrap"})
            data = resp.get_json()
            assert data["first_user"] is True
            assert data["totp_qr"] == expected_b64


# ---------------------------------------------------------------------------
# 3. Invite metadata parsing (lines 1348-1358)
# ---------------------------------------------------------------------------


class TestInviteMetadataParsing:
    """Tests for invite metadata extraction from backup_codes_json."""

    def _parse_invite_metadata(self, backup_codes_json):
        """Simulate the invite metadata parsing logic from auth.py."""
        can_download = True
        if backup_codes_json:
            try:
                invite_meta = json.loads(backup_codes_json)
                if isinstance(invite_meta, dict) and invite_meta.get("invited"):
                    can_download = invite_meta.get("can_download", True)
            except json.JSONDecodeError, TypeError:
                pass
        return can_download

    def test_valid_json_can_download_true(self):
        """Valid JSON with invited.can_download=True."""
        meta = json.dumps({"invited": True, "can_download": True})
        assert self._parse_invite_metadata(meta) is True

    def test_valid_json_can_download_false(self):
        """Valid JSON with invited.can_download=False."""
        meta = json.dumps({"invited": True, "can_download": False})
        assert self._parse_invite_metadata(meta) is False

    def test_valid_json_no_can_download_key(self):
        """Valid JSON with invited but no can_download defaults to True."""
        meta = json.dumps({"invited": True})
        assert self._parse_invite_metadata(meta) is True

    def test_malformed_json(self):
        """Malformed JSON is silently ignored, defaults to True."""
        assert self._parse_invite_metadata("{bad json!!!") is True

    def test_no_invited_key(self):
        """JSON without 'invited' key defaults to True."""
        meta = json.dumps({"other_key": "value"})
        assert self._parse_invite_metadata(meta) is True

    def test_invited_false(self):
        """invited=False means the invited block is not used, defaults True."""
        meta = json.dumps({"invited": False, "can_download": False})
        assert self._parse_invite_metadata(meta) is True

    def test_none_backup_codes_json(self):
        """None backup_codes_json defaults to True."""
        assert self._parse_invite_metadata(None) is True

    def test_empty_string_backup_codes_json(self):
        """Empty string backup_codes_json defaults to True."""
        assert self._parse_invite_metadata("") is True

    def test_json_list_not_dict(self):
        """A JSON array (not dict) should default to True."""
        meta = json.dumps([1, 2, 3])
        assert self._parse_invite_metadata(meta) is True


# ---------------------------------------------------------------------------
# 4. WebAuthn config auto-derivation (lines 1941-1965)
# ---------------------------------------------------------------------------


class TestWebAuthnConfigDerivation:
    """Tests for get_webauthn_config() auto-discovery logic."""

    def _call_get_webauthn_config(self, env_overrides=None):
        """Call get_webauthn_config with mocked config."""
        env = {
            "WEBAUTHN_RP_ID": "",
            "WEBAUTHN_RP_NAME": "",
            "WEBAUTHN_ORIGIN": "",
            "AUDIOBOOKS_HOSTNAME": "",
            "AUDIOBOOKS_HTTPS_ENABLED": "true",
            "AUDIOBOOKS_WEB_PORT": "8443",
            "WEB_PORT": "8443",
        }
        if env_overrides:
            env.update(env_overrides)

        def mock_get_config(key, default=None):
            val = env.get(key, "")
            if val:
                return val
            return default

        with (
            patch("backend.api_modular.auth.get_webauthn_config.__module__", create=True),
            patch("socket.getfqdn", return_value="test-vm-cachyos"),
        ):
            from backend.api_modular.auth import get_webauthn_config

            # Patch the get_config that's imported inside the function
            with patch("config.get_config", side_effect=mock_get_config):
                return get_webauthn_config()

    def test_local_hostname_becomes_localhost(self):
        """A .local hostname should map RP ID to 'localhost'."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {"AUDIOBOOKS_HOSTNAME": "myserver.local"}
        )
        assert rp_id == "localhost"

    def test_single_label_hostname_becomes_localhost(self):
        """A single-label hostname (no dots) should map to 'localhost'."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {"AUDIOBOOKS_HOSTNAME": "test-vm-cachyos"}
        )
        assert rp_id == "localhost"

    def test_fqdn_used_as_rp_id(self):
        """A real FQDN should be used as-is for RP ID."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {"AUDIOBOOKS_HOSTNAME": "library.thebosco.club"}
        )
        assert rp_id == "library.thebosco.club"

    def test_origin_https_default_port(self):
        """HTTPS on port 443 should omit the port from origin."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {
                "AUDIOBOOKS_HOSTNAME": "library.thebosco.club",
                "AUDIOBOOKS_HTTPS_ENABLED": "true",
                "AUDIOBOOKS_WEB_PORT": "443",
            }
        )
        assert origin == "https://library.thebosco.club"

    def test_origin_https_custom_port(self):
        """HTTPS on non-443 port should include port in origin."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {
                "AUDIOBOOKS_HOSTNAME": "library.thebosco.club",
                "AUDIOBOOKS_HTTPS_ENABLED": "true",
                "AUDIOBOOKS_WEB_PORT": "8443",
            }
        )
        assert origin == "https://library.thebosco.club:8443"

    def test_localhost_always_includes_port(self):
        """Localhost origin always includes the port."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {"AUDIOBOOKS_HOSTNAME": "mybox.local", "AUDIOBOOKS_WEB_PORT": "8443"}
        )
        assert rp_id == "localhost"
        assert origin == "https://localhost:8443"

    def test_explicit_rp_id_overrides_auto(self):
        """Explicit WEBAUTHN_RP_ID takes priority over auto-detection."""
        rp_id, rp_name, origin = self._call_get_webauthn_config(
            {"WEBAUTHN_RP_ID": "custom.example.com"}
        )
        assert rp_id == "custom.example.com"

    def test_rp_name_default(self):
        """RP name defaults to 'The Library'."""
        rp_id, rp_name, origin = self._call_get_webauthn_config()
        assert rp_name == "The Library"

    def test_localdomain_suffix_becomes_localhost(self):
        """A .localdomain hostname should map to localhost."""
        rp_id, _, _ = self._call_get_webauthn_config({"AUDIOBOOKS_HOSTNAME": "server.localdomain"})
        assert rp_id == "localhost"

    def test_http_scheme_when_https_disabled(self):
        """When HTTPS is disabled, origin uses http scheme."""
        _, _, origin = self._call_get_webauthn_config(
            {
                "AUDIOBOOKS_HOSTNAME": "library.example.com",
                "AUDIOBOOKS_HTTPS_ENABLED": "false",
                "AUDIOBOOKS_WEB_PORT": "80",
            }
        )
        assert origin == "http://library.example.com"


# ---------------------------------------------------------------------------
# Helper: SMTP mock context manager
# ---------------------------------------------------------------------------


def _smtp_mock():
    """Create a patched smtplib.SMTP that works as a context manager."""
    mock_server = MagicMock()
    patcher = patch("smtplib.SMTP")
    mock_class = patcher.start()
    mock_class.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_class.return_value.__exit__ = MagicMock(return_value=False)
    return patcher, mock_class, mock_server


def _email_env():
    """Common SMTP environment variables for email tests."""
    return {
        "SMTP_HOST": "smtp.test.local",
        "SMTP_PORT": "587",
        "SMTP_USER": "testuser",
        "SMTP_PASS": "testpass",
        "SMTP_FROM": "library@test.local",
        "BASE_URL": "https://library.example.com",
    }


# ---------------------------------------------------------------------------
# 5. Send approval email (lines 2987-3210)
# ---------------------------------------------------------------------------


class TestSendApprovalEmail:
    """Tests for _send_approval_email()."""

    def test_successful_send(self, auth_app):
        """Approval email sends successfully and returns True."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_approval_email

                result = _send_approval_email("user@example.com", "alice")
                assert result is True
                mock_server.starttls.assert_called_once()
                mock_server.login.assert_called_once_with("testuser", "testpass")
                mock_server.sendmail.assert_called_once()
        finally:
            patcher.stop()

    def test_email_contains_username(self, auth_app):
        """Approval email body should contain the username."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_approval_email

                _send_approval_email("user@example.com", "bob")
                call_args = mock_server.sendmail.call_args
                msg_body = _decode_email_body(call_args[0][2])
                assert "bob" in msg_body
        finally:
            patcher.stop()

    def test_email_contains_claim_url(self, auth_app):
        """Approval email should contain the claim URL."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_approval_email

                _send_approval_email("user@example.com", "carol")
                call_args = mock_server.sendmail.call_args
                msg_body = _decode_email_body(call_args[0][2])
                assert "claim.html" in msg_body
                assert "carol" in msg_body
        finally:
            patcher.stop()

    def test_email_has_html_and_text_parts(self, auth_app):
        """Approval email should be multipart with HTML and plain text."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_approval_email

                _send_approval_email("user@example.com", "dave")
                call_args = mock_server.sendmail.call_args
                msg_body = call_args[0][2]
                assert "Content-Type: text/plain" in msg_body
                assert "Content-Type: text/html" in msg_body
        finally:
            patcher.stop()

    def test_email_contains_authenticator_links(self, auth_app):
        """Approval email should contain app store links for authenticators."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_approval_email

                _send_approval_email("user@example.com", "eve")
                call_args = mock_server.sendmail.call_args
                msg_body = _decode_email_body(call_args[0][2])
                assert "Google Authenticator" in msg_body
                assert "Aegis" in msg_body
                assert "FreeOTP" in msg_body
        finally:
            patcher.stop()

    def test_smtp_error_returns_false(self, auth_app):
        """SMTP failure should return False and log the error."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            mock_server.sendmail.side_effect = smtplib.SMTPException("Connection refused")
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_approval_email

                result = _send_approval_email("user@example.com", "frank")
                assert result is False
        finally:
            patcher.stop()

    def test_no_auth_when_no_credentials(self, auth_app):
        """When SMTP_USER and SMTP_PASS are empty, skip starttls/login."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            env = _email_env()
            env["SMTP_USER"] = ""
            env["SMTP_PASS"] = ""  # nosec B105 — deliberately empty to test missing-credential path
            with auth_app.test_request_context(), patch.dict(os.environ, env):
                from backend.api_modular.auth import _send_approval_email

                result = _send_approval_email("user@example.com", "grace")
                assert result is True
                mock_server.starttls.assert_not_called()
                mock_server.login.assert_not_called()
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# 6. Send denial email (lines 3221-3300)
# ---------------------------------------------------------------------------


class TestSendDenialEmail:
    """Tests for _send_denial_email()."""

    def test_denial_with_reason(self, auth_app):
        """Denial email with a specific reason."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_denial_email

                result = _send_denial_email("user@example.com", "alice", reason="Invitation only")
                assert result is True
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "Invitation only" in msg_body
        finally:
            patcher.stop()

    def test_denial_without_reason(self, auth_app):
        """Denial email without a reason uses default text."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_denial_email

                result = _send_denial_email("user@example.com", "bob")
                assert result is True
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "No specific reason was provided" in msg_body
        finally:
            patcher.stop()

    def test_denial_has_html_and_text(self, auth_app):
        """Denial email should have both HTML and plain text parts."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_denial_email

                _send_denial_email("user@example.com", "carol")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "Content-Type: text/plain" in msg_body
                assert "Content-Type: text/html" in msg_body
        finally:
            patcher.stop()

    def test_denial_contains_username(self, auth_app):
        """Denial email should contain the username."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_denial_email

                _send_denial_email("user@example.com", "uniquename123")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "uniquename123" in msg_body
        finally:
            patcher.stop()

    def test_denial_smtp_error(self, auth_app):
        """SMTP failure returns False."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            mock_server.sendmail.side_effect = OSError("Network unreachable")
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_denial_email

                result = _send_denial_email("user@example.com", "dave")
                assert result is False
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# 7. Send admin alert (lines 3478-3506)
# ---------------------------------------------------------------------------


class TestSendAdminAlert:
    """Tests for _send_admin_alert()."""

    def test_successful_alert(self, auth_app):
        """Admin alert sends successfully."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_admin_alert

                result = _send_admin_alert("alice", "Hello, I need help")
                assert result is True
                mock_server.sendmail.assert_called_once()
        finally:
            patcher.stop()

    def test_message_preview_in_body(self, auth_app):
        """Alert body should contain the message preview."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_admin_alert

                _send_admin_alert("bob", "My specific message")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "My specific message" in msg_body
        finally:
            patcher.stop()

    def test_long_message_gets_ellipsis(self, auth_app):
        """Messages >= 100 chars should have '...' appended."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_admin_alert

                long_msg = "A" * 100
                _send_admin_alert("carol", long_msg)
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "..." in msg_body
        finally:
            patcher.stop()

    def test_short_message_no_ellipsis(self, auth_app):
        """Messages < 100 chars should NOT have '...' in the preview line."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_admin_alert

                short_msg = "Short"
                _send_admin_alert("dave", short_msg)
                msg_body = mock_server.sendmail.call_args[0][2]
                # The preview line should be "Preview: Short" without "..."
                assert "Preview: Short\n" in msg_body

        finally:
            patcher.stop()

    def test_smtp_not_configured_skips(self, auth_app):
        """When SMTP_USER is empty, should skip and return False."""
        env = _email_env()
        env["SMTP_USER"] = ""
        with auth_app.test_request_context(), patch.dict(os.environ, env):
            from backend.api_modular.auth import _send_admin_alert

            result = _send_admin_alert("eve", "test message")
            assert result is False

    def test_smtp_error_returns_false(self, auth_app):
        """SMTP failure should return False."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            mock_server.sendmail.side_effect = ConnectionRefusedError()
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_admin_alert

                result = _send_admin_alert("frank", "test")
                assert result is False
        finally:
            patcher.stop()

    def test_admin_email_from_env(self, auth_app):
        """ADMIN_EMAIL env var should override SMTP_FROM as recipient."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            env = _email_env()
            env["ADMIN_EMAIL"] = "admin@custom.com"
            with auth_app.test_request_context(), patch.dict(os.environ, env):
                from backend.api_modular.auth import _send_admin_alert

                _send_admin_alert("grace", "test")
                call_args = mock_server.sendmail.call_args[0]
                assert call_args[1] == "admin@custom.com"
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# 8. Send reply email (lines 3818-3825)
# ---------------------------------------------------------------------------


class TestSendReplyEmail:
    """Tests for _send_reply_email()."""

    def test_successful_reply(self, auth_app):
        """Reply email sends successfully."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_reply_email

                result = _send_reply_email("user@example.com", "alice", "Thanks for your message!")
                assert result is True
                mock_server.sendmail.assert_called_once()
        finally:
            patcher.stop()

    def test_reply_contains_reply_text(self, auth_app):
        """Reply email body should contain the reply text."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_reply_email

                _send_reply_email("user@example.com", "bob", "Your issue is resolved now")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "Your issue is resolved now" in msg_body
        finally:
            patcher.stop()

    def test_reply_contains_username(self, auth_app):
        """Reply email should greet the user by name."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_reply_email

                _send_reply_email("user@example.com", "carol", "test reply")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "carol" in msg_body
        finally:
            patcher.stop()

    def test_reply_smtp_error(self, auth_app):
        """SMTP failure returns False."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            mock_server.sendmail.side_effect = smtplib.SMTPException("Auth failed")
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_reply_email

                result = _send_reply_email("user@example.com", "dave", "test")
                assert result is False
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# 9. Send invitation email (lines 4660-4665)
# ---------------------------------------------------------------------------


class TestSendInvitationEmail:
    """Tests for _send_invitation_email()."""

    def test_successful_send(self, auth_app):
        """Invitation email sends successfully."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_invitation_email

                result = _send_invitation_email("user@example.com", "alice", "ABCD-EFGH-IJKL-MNOP")
                assert result is True
        finally:
            patcher.stop()

    def test_claim_token_in_body(self, auth_app):
        """Invitation email should contain the claim token."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_invitation_email

                _send_invitation_email("user@example.com", "bob", "WXYZ-1234-ABCD-5678")
                msg_body = _decode_email_body(mock_server.sendmail.call_args[0][2])
                assert "WXYZ-1234-ABCD-5678" in msg_body
        finally:
            patcher.stop()

    def test_claim_url_in_body(self, auth_app):
        """Invitation email should contain the claim URL."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_invitation_email

                _send_invitation_email("user@example.com", "carol", "ABCD-EFGH-IJKL-MNOP")
                msg_body = _decode_email_body(mock_server.sendmail.call_args[0][2])
                assert "claim.html" in msg_body
                assert "library.example.com" in msg_body
        finally:
            patcher.stop()

    def test_invitation_has_html_and_text(self, auth_app):
        """Invitation email should have both HTML and text parts."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_invitation_email

                _send_invitation_email("user@example.com", "dave", "ABCD-EFGH-IJKL-MNOP")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "Content-Type: text/plain" in msg_body
                assert "Content-Type: text/html" in msg_body
        finally:
            patcher.stop()

    def test_invitation_smtp_error(self, auth_app):
        """SMTP failure returns False."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            mock_server.sendmail.side_effect = TimeoutError("Connection timed out")
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_invitation_email

                result = _send_invitation_email("user@example.com", "eve", "ABCD-EFGH-IJKL-MNOP")
                assert result is False
        finally:
            patcher.stop()

    def test_invitation_no_auth_without_credentials(self, auth_app):
        """Without SMTP credentials, skip starttls/login."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            env = _email_env()
            env["SMTP_USER"] = ""
            env["SMTP_PASS"] = ""  # nosec B105 — deliberately empty to test missing-credential path
            with auth_app.test_request_context(), patch.dict(os.environ, env):
                from backend.api_modular.auth import _send_invitation_email

                result = _send_invitation_email("user@example.com", "frank", "ABCD-EFGH-IJKL-MNOP")
                assert result is True
                mock_server.starttls.assert_not_called()
                mock_server.login.assert_not_called()
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# 10. Send activation email (lines 4783-4784)
# ---------------------------------------------------------------------------


class TestSendActivationEmail:
    """Tests for _send_activation_email()."""

    def test_successful_send(self, auth_app):
        """Activation email sends successfully."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_activation_email

                result = _send_activation_email("user@example.com", "alice", "tok123abc")
                assert result is True
        finally:
            patcher.stop()

    def test_activation_url_in_body(self, auth_app):
        """Activation email should contain the activation URL."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_activation_email

                _send_activation_email("user@example.com", "bob", "mytoken456")
                msg_body = _decode_email_body(mock_server.sendmail.call_args[0][2])
                assert "verify.html" in msg_body
                assert "mytoken456" in msg_body
                assert "activate=1" in msg_body
        finally:
            patcher.stop()

    def test_activation_has_html_and_text(self, auth_app):
        """Activation email should have both HTML and text parts."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_activation_email

                _send_activation_email("user@example.com", "carol", "tok789")
                msg_body = mock_server.sendmail.call_args[0][2]
                assert "Content-Type: text/plain" in msg_body
                assert "Content-Type: text/html" in msg_body
        finally:
            patcher.stop()

    def test_activation_contains_username(self, auth_app):
        """Activation email should contain the username."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_activation_email

                _send_activation_email("user@example.com", "uniqueuser99", "tok")
                msg_body = _decode_email_body(mock_server.sendmail.call_args[0][2])
                assert "uniqueuser99" in msg_body
        finally:
            patcher.stop()

    def test_activation_smtp_error(self, auth_app):
        """SMTP failure returns False."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            mock_server.sendmail.side_effect = smtplib.SMTPAuthenticationError(
                535, b"Bad credentials"
            )
            with auth_app.test_request_context(), patch.dict(os.environ, _email_env()):
                from backend.api_modular.auth import _send_activation_email

                result = _send_activation_email("user@example.com", "dave", "tok")
                assert result is False
        finally:
            patcher.stop()

    def test_activation_no_auth_without_credentials(self, auth_app):
        """Without SMTP credentials, skip starttls/login."""
        patcher, mock_smtp, mock_server = _smtp_mock()
        try:
            env = _email_env()
            env["SMTP_USER"] = ""
            env["SMTP_PASS"] = ""  # nosec B105 — deliberately empty to test missing-credential path
            with auth_app.test_request_context(), patch.dict(os.environ, env):
                from backend.api_modular.auth import _send_activation_email

                result = _send_activation_email("user@example.com", "eve", "tok")
                assert result is True
                mock_server.starttls.assert_not_called()
                mock_server.login.assert_not_called()
        finally:
            patcher.stop()
