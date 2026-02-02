"""
Integration tests for the full authentication lifecycle.

Tests the complete auth flow for each auth method against the VM API:
  Admin login → Register user → Admin approve → Claim credentials → Login → Verify session

Runs against: http://192.168.122.100:5001

Required:
  - test-vm-cachyos running with audiobook API
  - testadmin account (TOTP, admin)
  - SKIP_VM_DEPLOY=1 to skip auto-deploy (optional)

Auth methods tested:
  - TOTP: Full lifecycle with pyotp code generation
  - Passkey: Full lifecycle with software WebAuthn authenticator
  - FIDO2: Full lifecycle with hardware YubiKey (or software fallback)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pyotp
import pytest
import requests

pytestmark = pytest.mark.integration

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from tests.helpers.software_authenticator import SoftwareAuthenticator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VM_HOST = os.environ.get("VM_HOST", "192.168.122.100")
VM_API_PORT = int(os.environ.get("VM_API_PORT", "5001"))
API_BASE = f"http://{VM_HOST}:{VM_API_PORT}"

# testadmin TOTP secret (reset via audiobook-user totp-reset on VM)
ADMIN_USERNAME = "testadmin"
ADMIN_TOTP_SECRET = os.environ.get(
    "ADMIN_TOTP_SECRET", "W2GGPH7KH2WL2PN22SGW62WQMJOABGZS"
)

# WebAuthn origin must match the VM's WEBAUTHN_ORIGIN config
WEBAUTHN_ORIGIN = os.environ.get("WEBAUTHN_ORIGIN", "https://localhost:9090")

# Test user names
TOTP_USER = "totptest1"
PASSKEY_USER = "pkeytest1"
FIDO2_USER = "fidotest1"

# Timeout for HTTP requests
TIMEOUT = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def api(path: str) -> str:
    """Build a full API URL."""
    return f"{API_BASE}{path}"


def _fix_secure_cookie(session: requests.Session, response: requests.Response) -> None:
    """Re-add the session cookie without the Secure flag.

    The server sets Secure on the cookie even over HTTP, which prevents
    requests.Session from sending it on subsequent HTTP requests.
    We extract the token and re-add it as a non-Secure cookie.
    """
    token = response.cookies.get("audiobooks_session")
    if token:
        session.cookies.set(
            "audiobooks_session",
            token,
            domain=VM_HOST,
            path="/",
        )


def admin_login(session: requests.Session) -> dict:
    """Log in as testadmin and return the response JSON."""
    code = pyotp.TOTP(ADMIN_TOTP_SECRET).now()
    resp = session.post(
        api("/auth/login"),
        json={"username": ADMIN_USERNAME, "code": code},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    assert data["success"] is True, f"Admin login failed: {data}"
    _fix_secure_cookie(session, resp)
    return data


def cleanup_user(session: requests.Session, username: str) -> None:
    """Delete a test user if they exist. Requires admin session."""
    resp = session.get(api("/auth/admin/users"), timeout=TIMEOUT)
    if resp.status_code != 200:
        return
    users = resp.json().get("users", [])
    for u in users:
        if u["username"] == username:
            session.delete(
                api(f"/auth/admin/users/{u['id']}"), timeout=TIMEOUT
            )
            break


def cleanup_pending_request(session: requests.Session, username: str) -> None:
    """Deny any access request for username (any status). Requires admin session.

    Also cleans up via SSH if the API doesn't expose a delete endpoint,
    by denying pending requests or deleting the row directly.
    """
    resp = session.get(api("/auth/admin/access-requests"), timeout=TIMEOUT)
    if resp.status_code != 200:
        return
    for req in resp.json().get("requests", []):
        if req.get("username") == username:
            if req.get("status") == "pending":
                session.post(
                    api(f"/auth/admin/access-requests/{req['id']}/deny"),
                    json={"reason": "test cleanup"},
                    timeout=TIMEOUT,
                )
            # For approved/denied requests still in DB, we need direct DB cleanup
            _cleanup_access_request_db(username)


SSH_KEY = os.path.expanduser("~/.claude/ssh/id_ed25519")
SSH_USER = "claude"


def _cleanup_access_request_db(username: str) -> None:
    """Delete an access request directly from the VM database via SSH."""
    script = f"""\
import sys
sys.path.insert(0, '/opt/audiobooks/library')
from auth.database import AuthDatabase
db = AuthDatabase(db_path='/var/lib/audiobooks/auth.db', key_path='/etc/audiobooks/auth.key')
db.initialize()
with db.connection() as conn:
    conn.execute('DELETE FROM access_requests WHERE username = ?', ('{username}',))
"""
    try:
        subprocess.run(
            [
                "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
                f"{SSH_USER}@{VM_HOST}",
                "sudo /opt/audiobooks/library/venv/bin/python3",
            ],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        pass


def register_and_approve(
    admin_session: requests.Session, username: str
) -> str:
    """Register a user and approve their access request.

    Returns the claim_token (formatted, with dashes).
    """
    # Register
    resp = requests.post(
        api("/auth/register/start"),
        json={"username": username},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    assert data["success"] is True, f"Registration failed: {data}"
    claim_token = data["claim_token"]
    request_id = data["request_id"]

    # Admin approve
    resp = admin_session.post(
        api(f"/auth/admin/access-requests/{request_id}/approve"),
        json={"message": "Approved for testing"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    assert resp.json().get("success") is True

    return claim_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_available():
    """Skip the entire module if the VM API is unreachable."""
    try:
        resp = requests.get(
            api("/api/system/version"), timeout=5
        )
        # 200 = OK, 401/403 = auth required but API is up
        if resp.status_code not in (200, 401, 403):
            pytest.skip(f"VM API returned {resp.status_code}")
    except requests.exceptions.ConnectionError:
        pytest.skip("VM API unreachable")


@pytest.fixture(scope="module")
def admin_session(api_available):
    """Module-scoped admin session with cleanup of test users."""
    s = requests.Session()
    admin_login(s)

    # Pre-cleanup: remove any leftover test users and access requests
    for username in (TOTP_USER, PASSKEY_USER, FIDO2_USER):
        cleanup_user(s, username)
        cleanup_pending_request(s, username)
        _cleanup_access_request_db(username)

    yield s

    # Post-cleanup
    # Re-login in case session expired during tests
    try:
        admin_login(s)
    except Exception:
        pass
    for username in (TOTP_USER, PASSKEY_USER, FIDO2_USER):
        cleanup_user(s, username)
        _cleanup_access_request_db(username)

    s.close()


# ---------------------------------------------------------------------------
# Test: Admin Login
# ---------------------------------------------------------------------------


class TestAdminLogin:
    """Verify the testadmin account can log in with TOTP."""

    def test_admin_totp_login(self, api_available):
        """Login as testadmin using TOTP, verify session."""
        s = requests.Session()
        data = admin_login(s)
        assert data["user"]["username"] == ADMIN_USERNAME
        assert data["user"]["is_admin"] is True

        # Verify session
        me = s.get(api("/auth/me"), timeout=TIMEOUT)
        assert me.status_code == 200
        assert me.json()["user"]["username"] == ADMIN_USERNAME
        s.close()


# ---------------------------------------------------------------------------
# Test: TOTP User Lifecycle
# ---------------------------------------------------------------------------


class TestTOTPUserLifecycle:
    """Full lifecycle: register → approve → claim TOTP → login → verify."""

    def test_full_totp_lifecycle(self, admin_session):
        """End-to-end TOTP user lifecycle."""
        # Step 1: Register
        claim_token = register_and_approve(admin_session, TOTP_USER)

        # Step 2: Claim TOTP credentials
        resp = requests.post(
            api("/auth/register/claim"),
            json={"username": TOTP_USER, "claim_token": claim_token},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim failed: {resp.text}"
        claim_data = resp.json()
        assert claim_data["success"] is True
        assert "totp_secret" in claim_data
        assert "backup_codes" in claim_data

        totp_secret = claim_data["totp_secret"]

        # Step 3: Login with TOTP
        code = pyotp.TOTP(totp_secret).now()
        user_session = requests.Session()
        resp = user_session.post(
            api("/auth/login"),
            json={"username": TOTP_USER, "code": code},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        _fix_secure_cookie(user_session, resp)
        login_data = resp.json()
        assert login_data["success"] is True
        assert login_data["user"]["username"] == TOTP_USER

        # Step 4: Verify session
        me = user_session.get(api("/auth/me"), timeout=TIMEOUT)
        assert me.status_code == 200
        assert me.json()["user"]["username"] == TOTP_USER
        assert me.json()["user"]["is_admin"] is False

        user_session.close()

        print(f"\n  TOTP lifecycle complete for {TOTP_USER}")


# ---------------------------------------------------------------------------
# Test: Passkey User Lifecycle
# ---------------------------------------------------------------------------


class TestPasskeyUserLifecycle:
    """Full lifecycle: register → approve → claim passkey → login → verify."""

    def test_full_passkey_lifecycle(self, admin_session):
        """End-to-end passkey user lifecycle using software authenticator."""
        authenticator = SoftwareAuthenticator()

        # Step 1: Register and approve
        claim_token = register_and_approve(admin_session, PASSKEY_USER)

        # Step 2: Begin WebAuthn claim
        resp = requests.post(
            api("/auth/register/claim/webauthn/begin"),
            json={
                "username": PASSKEY_USER,
                "claim_token": claim_token,
                "auth_type": "passkey",
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim begin failed: {resp.text}"
        begin_data = resp.json()
        assert "options" in begin_data
        assert "challenge" in begin_data

        options = begin_data["options"]
        if isinstance(options, str):
            options = json.loads(options)
        challenge_b64 = begin_data["challenge"]

        # Step 3: Create credential with software authenticator
        credential_response = authenticator.make_credential(
            options, WEBAUTHN_ORIGIN
        )

        # Step 4: Complete WebAuthn claim
        resp = requests.post(
            api("/auth/register/claim/webauthn/complete"),
            json={
                "username": PASSKEY_USER,
                "claim_token": claim_token,
                "credential": credential_response,
                "challenge": challenge_b64,
                "auth_type": "passkey",
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim complete failed: {resp.text}"
        complete_data = resp.json()
        assert complete_data["success"] is True
        assert "backup_codes" in complete_data

        # Step 5: Login — begin authentication
        resp = requests.post(
            api("/auth/login/webauthn/begin"),
            json={"username": PASSKEY_USER},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login begin failed: {resp.text}"
        login_begin = resp.json()
        login_options = login_begin["options"]
        if isinstance(login_options, str):
            login_options = json.loads(login_options)
        login_challenge = login_begin["challenge"]

        # Step 6: Sign assertion with software authenticator
        assertion_response = authenticator.get_assertion(
            login_options, WEBAUTHN_ORIGIN
        )

        # Step 7: Complete login
        user_session = requests.Session()
        resp = user_session.post(
            api("/auth/login/webauthn/complete"),
            json={
                "username": PASSKEY_USER,
                "credential": assertion_response,
                "challenge": login_challenge,
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login complete failed: {resp.text}"
        _fix_secure_cookie(user_session, resp)
        login_data = resp.json()
        assert login_data["success"] is True
        assert login_data["user"]["username"] == PASSKEY_USER

        # Step 8: Verify session
        me = user_session.get(api("/auth/me"), timeout=TIMEOUT)
        assert me.status_code == 200
        assert me.json()["user"]["username"] == PASSKEY_USER

        user_session.close()

        print(f"\n  Passkey lifecycle complete for {PASSKEY_USER}")


# ---------------------------------------------------------------------------
# Test: FIDO2 User Lifecycle
# ---------------------------------------------------------------------------

# Detect hardware YubiKey at module level
# Set FIDO2_SOFTWARE=1 to force software authenticator even with YubiKey present
_FIDO2_HARDWARE = False
if not os.environ.get("FIDO2_SOFTWARE", ""):
    try:
        from fido2.hid import CtapHidDevice

        _fido2_devices = list(CtapHidDevice.list_devices())
        _FIDO2_HARDWARE = len(_fido2_devices) > 0
    except Exception:
        pass


class TestFIDO2UserLifecycle:
    """Full lifecycle: register → approve → claim FIDO2 → login → verify.

    Uses hardware YubiKey if detected, otherwise falls back to software authenticator.
    """

    @pytest.mark.hardware
    def test_full_fido2_lifecycle(self, admin_session):
        """End-to-end FIDO2 user lifecycle (requires --hardware flag)."""
        if _FIDO2_HARDWARE:
            self._run_hardware_fido2(admin_session)
        else:
            self._run_software_fido2(admin_session)

    def _run_software_fido2(self, admin_session):
        """FIDO2 lifecycle using software authenticator (CI fallback)."""
        authenticator = SoftwareAuthenticator()

        # Step 1: Register and approve
        claim_token = register_and_approve(admin_session, FIDO2_USER)

        # Step 2: Begin WebAuthn claim
        resp = requests.post(
            api("/auth/register/claim/webauthn/begin"),
            json={
                "username": FIDO2_USER,
                "claim_token": claim_token,
                "auth_type": "fido2",
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim begin failed: {resp.text}"
        begin_data = resp.json()
        options = begin_data["options"]
        if isinstance(options, str):
            options = json.loads(options)
        challenge_b64 = begin_data["challenge"]

        # Step 3: Create credential
        credential_response = authenticator.make_credential(
            options, WEBAUTHN_ORIGIN
        )

        # Step 4: Complete claim
        resp = requests.post(
            api("/auth/register/claim/webauthn/complete"),
            json={
                "username": FIDO2_USER,
                "claim_token": claim_token,
                "credential": credential_response,
                "challenge": challenge_b64,
                "auth_type": "fido2",
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim complete failed: {resp.text}"
        assert resp.json()["success"] is True

        # Step 5: Login begin
        resp = requests.post(
            api("/auth/login/webauthn/begin"),
            json={"username": FIDO2_USER},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login begin failed: {resp.text}"
        login_begin = resp.json()
        login_options = login_begin["options"]
        if isinstance(login_options, str):
            login_options = json.loads(login_options)
        login_challenge = login_begin["challenge"]

        # Step 6: Sign assertion
        assertion_response = authenticator.get_assertion(
            login_options, WEBAUTHN_ORIGIN
        )

        # Step 7: Complete login
        user_session = requests.Session()
        resp = user_session.post(
            api("/auth/login/webauthn/complete"),
            json={
                "username": FIDO2_USER,
                "credential": assertion_response,
                "challenge": login_challenge,
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login complete failed: {resp.text}"
        _fix_secure_cookie(user_session, resp)
        assert resp.json()["success"] is True
        assert resp.json()["user"]["username"] == FIDO2_USER

        # Step 8: Verify session
        me = user_session.get(api("/auth/me"), timeout=TIMEOUT)
        assert me.status_code == 200
        assert me.json()["user"]["username"] == FIDO2_USER

        user_session.close()

        print(f"\n  FIDO2 lifecycle complete for {FIDO2_USER} (software fallback)")

    def _run_hardware_fido2(self, admin_session):
        """FIDO2 lifecycle using hardware YubiKey."""
        from fido2.client import (
            DefaultClientDataCollector,
            Fido2Client,
            UserInteraction,
        )
        from fido2.hid import CtapHidDevice

        class CliInteraction(UserInteraction):
            """Prompts user to touch the YubiKey."""

            def prompt_up(self):
                print("\n  >>> Touch your YubiKey now... <<<")

            def request_pin(self, permissions, rd_id):
                return "3141"

            def request_uv(self, permissions, rd_id):
                print("\n  >>> Verify on YubiKey... <<<")
                return True

        device = next(CtapHidDevice.list_devices())
        collector = DefaultClientDataCollector(WEBAUTHN_ORIGIN)
        client = Fido2Client(
            device,
            collector,
            user_interaction=CliInteraction(),
        )

        # Step 1: Register and approve
        claim_token = register_and_approve(admin_session, FIDO2_USER)

        # Step 2: Begin WebAuthn claim
        resp = requests.post(
            api("/auth/register/claim/webauthn/begin"),
            json={
                "username": FIDO2_USER,
                "claim_token": claim_token,
                "auth_type": "fido2",
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim begin failed: {resp.text}"
        begin_data = resp.json()
        options = begin_data["options"]
        if isinstance(options, str):
            options = json.loads(options)
        challenge_b64 = begin_data["challenge"]

        # Build the creation options that Fido2Client expects
        # The py_webauthn options are JSON-serialized; we need to pass them
        # to the FIDO2 client in the correct format
        creation_result = client.make_credential(
            _parse_creation_options(options)
        )

        # Build credential response matching the server's expected format
        credential_response = _fido2_creation_to_dict(creation_result)

        # Step 4: Complete claim
        resp = requests.post(
            api("/auth/register/claim/webauthn/complete"),
            json={
                "username": FIDO2_USER,
                "claim_token": claim_token,
                "credential": credential_response,
                "challenge": challenge_b64,
                "auth_type": "fido2",
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Claim complete failed: {resp.text}"
        assert resp.json()["success"] is True

        # Step 5: Login begin
        resp = requests.post(
            api("/auth/login/webauthn/begin"),
            json={"username": FIDO2_USER},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login begin failed: {resp.text}"
        login_begin = resp.json()
        login_options = login_begin["options"]
        if isinstance(login_options, str):
            login_options = json.loads(login_options)
        login_challenge = login_begin["challenge"]

        # Step 6: Authenticate with YubiKey
        assertion_result = client.get_assertion(
            _parse_request_options(login_options)
        )

        # Build assertion response
        assertion_response = _fido2_assertion_to_dict(assertion_result)

        # Step 7: Complete login
        user_session = requests.Session()
        resp = user_session.post(
            api("/auth/login/webauthn/complete"),
            json={
                "username": FIDO2_USER,
                "credential": assertion_response,
                "challenge": login_challenge,
            },
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Login complete failed: {resp.text}"
        _fix_secure_cookie(user_session, resp)
        assert resp.json()["success"] is True

        # Step 8: Verify session
        me = user_session.get(api("/auth/me"), timeout=TIMEOUT)
        assert me.status_code == 200
        assert me.json()["user"]["username"] == FIDO2_USER

        user_session.close()

        print(f"\n  FIDO2 lifecycle complete for {FIDO2_USER} (hardware YubiKey)")


# ---------------------------------------------------------------------------
# Hardware FIDO2 helpers
# ---------------------------------------------------------------------------


def _b64url_encode_hw(data: bytes) -> str:
    """Base64url encode without padding (for hardware path)."""
    from base64 import urlsafe_b64encode
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode_hw(s: str) -> bytes:
    """Base64url decode with padding restoration."""
    from base64 import urlsafe_b64decode
    s += "=" * (4 - len(s) % 4)
    return urlsafe_b64decode(s)


def _parse_creation_options(options: dict):
    """Convert server JSON options to fido2.webauthn PublicKeyCredentialCreationOptions."""
    from fido2.webauthn import (
        PublicKeyCredentialCreationOptions,
        PublicKeyCredentialRpEntity,
        PublicKeyCredentialUserEntity,
        PublicKeyCredentialParameters,
        PublicKeyCredentialType,
    )

    rp = PublicKeyCredentialRpEntity(
        id=options["rp"]["id"],
        name=options["rp"]["name"],
    )

    user_data = options["user"]
    user = PublicKeyCredentialUserEntity(
        id=_b64url_decode_hw(user_data["id"]),
        name=user_data["name"],
        display_name=user_data.get("displayName", user_data["name"]),
    )

    pub_key_params = []
    for p in options.get("pubKeyCredParams", []):
        pub_key_params.append(
            PublicKeyCredentialParameters(
                type=PublicKeyCredentialType.PUBLIC_KEY,
                alg=p["alg"],
            )
        )

    challenge = _b64url_decode_hw(options["challenge"])

    return PublicKeyCredentialCreationOptions(
        rp=rp,
        user=user,
        challenge=challenge,
        pub_key_cred_params=pub_key_params,
        timeout=options.get("timeout"),
    )


def _parse_request_options(options: dict):
    """Convert server JSON options to fido2.webauthn PublicKeyCredentialRequestOptions."""
    from fido2.webauthn import (
        PublicKeyCredentialRequestOptions,
        PublicKeyCredentialDescriptor,
        PublicKeyCredentialType,
    )

    challenge = _b64url_decode_hw(options["challenge"])

    allow_credentials = []
    for ac in options.get("allowCredentials", []):
        allow_credentials.append(
            PublicKeyCredentialDescriptor(
                type=PublicKeyCredentialType.PUBLIC_KEY,
                id=_b64url_decode_hw(ac["id"]),
            )
        )

    return PublicKeyCredentialRequestOptions(
        challenge=challenge,
        timeout=options.get("timeout"),
        rp_id=options.get("rpId"),
        allow_credentials=allow_credentials,
    )


def _fido2_creation_to_dict(result) -> dict:
    """Convert fido2 make_credential result to server-expected dict."""
    attestation = result.response.attestation_object
    client_data = result.response.client_data

    # The credential ID is in the attestation object's auth data
    auth_data = attestation.auth_data
    credential_id = auth_data.credential_data.credential_id

    return {
        "id": _b64url_encode_hw(credential_id),
        "rawId": _b64url_encode_hw(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64url_encode_hw(bytes(client_data)),
            "attestationObject": _b64url_encode_hw(bytes(attestation)),
        },
        "authenticatorAttachment": "cross-platform",
    }


def _fido2_assertion_to_dict(result) -> dict:
    """Convert fido2 get_assertion result to server-expected dict."""
    auth_response = result.get_response(0)
    assertion = auth_response.response

    return {
        "id": _b64url_encode_hw(auth_response.raw_id),
        "rawId": _b64url_encode_hw(auth_response.raw_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64url_encode_hw(bytes(assertion.client_data)),
            "authenticatorData": _b64url_encode_hw(
                bytes(assertion.authenticator_data)
            ),
            "signature": _b64url_encode_hw(assertion.signature),
        },
        "authenticatorAttachment": "cross-platform",
    }
