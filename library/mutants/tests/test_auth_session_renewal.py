"""
Tests for rolling renewal of persistent ("remember me") session cookies.

Browsers clamp stored cookies to 400 days regardless of Max-Age, so
"remember me" issues a 400-day cookie and rolling renewal re-issues it on
authenticated activity once the session is past half its life — an active
user never expires, a dormant one still dies at the horizon.

Covers:
- Near-expiry sessions renewed on activity (fresh cookie + advanced
  server-side ``expires_at`` horizon)
- Far-from-expiry sessions NOT renewed (no per-request Set-Cookie churn)
- Past-expiry sessions rejected, invalidated, and NOT renewed
- Legacy persistent rows (``expires_at`` NULL) backfilled on first use
- Non-persistent sessions never renewed
- Renewed cookie value passes the ``_SAFE_SESSION_TOKEN_RE`` allowlist
- ``/auth/session/restore`` enforcing the horizon and advancing it
- ``SessionRepository.cleanup_stale`` purging expired persistent rows
- ``Session.touch`` writing SQLite-comparable timestamps
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add library + backend directories to path (mirrors conftest.py)
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))

# Short-path module — the same auth_shared instance the conftest auth_app
# uses (its create_app comes from ``api_modular``, not ``backend.api_modular``)
from api_modular.auth_shared import (  # noqa: E402
    _SAFE_SESSION_TOKEN_RE,
    SESSION_DURATION_REMEMBER,
    SESSION_RENEWAL_THRESHOLD,
    set_session_cookie,
)
from auth import AuthType, Session, SessionRepository, User  # noqa: E402
from auth.totp import TOTPAuthenticator, setup_totp  # noqa: E402

COOKIE_NAME = "audiobooks_session"


# ──────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def renewal_user(auth_db):
    """Dedicated TOTP user so session manipulation can't race other tests."""
    secret, _, _ = setup_totp("renewaluser")
    user = User(
        username="renewaluser",
        auth_type=AuthType.TOTP,
        auth_credential=secret,
        can_download=False,
        is_admin=False,
    )
    user.save(auth_db)
    assert user.id is not None
    return user, secret


@pytest.fixture(scope="module")
def db_session_user(auth_db):
    """Second dedicated user for direct-repository session tests."""
    user = User(
        username="renewaldbuser",
        auth_type=AuthType.TOTP,
        auth_credential=b"testsecret",
        can_download=False,
        is_admin=False,
    )
    user.save(auth_db)
    assert user.id is not None
    return user


def _login(auth_app, auth_db, user, secret, remember_me=True):
    """Fresh client logged in as the given user. Clears prior sessions first."""
    with auth_db.connection() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))
    client = auth_app.test_client()
    code = TOTPAuthenticator(secret).current_code()
    r = client.post(
        "/auth/login",
        json={"username": user.username, "code": code, "remember_me": remember_me},
    )
    assert r.status_code == 200, f"login failed: {r.get_json()}"
    return client


def _set_expires(auth_db, user_id, dt):
    """Set the session expiry horizon for a user's session row."""
    value = dt.replace(microsecond=0).isoformat(sep=" ") if dt is not None else None
    with auth_db.connection() as conn:
        conn.execute("UPDATE sessions SET expires_at = ? WHERE user_id = ?", (value, user_id))


def _get_expires(auth_db, user_id):
    """Return the session expires_at for a user as datetime (or None)."""
    with auth_db.connection() as conn:
        cur = conn.execute("SELECT expires_at FROM sessions WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
    if row is None:
        return "NO_ROW"
    return datetime.fromisoformat(row[0]) if row[0] else None


def _session_set_cookies(response):
    """Extract audiobooks_session Set-Cookie headers from a response."""
    return [h for h in response.headers.getlist("Set-Cookie") if h.startswith(f"{COOKIE_NAME}=")]


# ──────────────────────────────────────────────────────────────────────
# Rolling renewal via authenticated requests
# ──────────────────────────────────────────────────────────────────────


class TestRollingRenewal:
    """Rolling renewal on ordinary authenticated requests."""

    def test_near_expiry_session_renewed_on_activity(self, auth_app, auth_db, renewal_user):
        """A persistent session inside the renewal window gets a fresh cookie."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        token = client.get_cookie(COOKIE_NAME).value

        # 10 days of life left — well inside the renewal threshold
        _set_expires(auth_db, user.id, datetime.now() + timedelta(days=10))

        r = client.get("/auth/check")
        assert r.status_code == 200
        assert r.get_json()["authenticated"] is True

        renewed = _session_set_cookies(r)
        assert renewed, "expected a renewal Set-Cookie on the response"
        assert f"Max-Age={SESSION_DURATION_REMEMBER}" in renewed[0]

        # Same token re-issued (no rotation)
        assert client.get_cookie(COOKIE_NAME).value == token

        # Server-side horizon advanced alongside the cookie
        expires = _get_expires(auth_db, user.id)
        assert expires is not None and expires != "NO_ROW"
        assert expires > datetime.now() + timedelta(days=399)

    def test_far_from_expiry_session_not_renewed(self, auth_app, auth_db, renewal_user):
        """A persistent session with plenty of life left is left alone."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)

        horizon = datetime.now().replace(microsecond=0) + timedelta(days=300)
        _set_expires(auth_db, user.id, horizon)

        r = client.get("/auth/check")
        assert r.status_code == 200
        assert r.get_json()["authenticated"] is True
        assert not _session_set_cookies(r), "no renewal expected above the threshold"
        assert _get_expires(auth_db, user.id) == horizon

    def test_past_expiry_session_not_renewed(self, auth_app, auth_db, renewal_user):
        """A session past its horizon is rejected and invalidated, not renewed."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        _set_expires(auth_db, user.id, datetime.now() - timedelta(days=1))

        r = client.get("/auth/check")
        assert r.status_code == 200
        assert r.get_json()["authenticated"] is False
        assert not _session_set_cookies(r), "expired session must not be renewed"
        assert _get_expires(auth_db, user.id) == "NO_ROW", "expired row must be invalidated"

    def test_past_expiry_session_rejected_on_protected_route(self, auth_app, auth_db, renewal_user):
        """Protected routes 401 once the horizon has passed."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        _set_expires(auth_db, user.id, datetime.now() - timedelta(days=1))

        r = client.get("/auth/me")
        assert r.status_code == 401
        assert not _session_set_cookies(r)

    def test_legacy_null_expires_backfilled_on_first_use(self, auth_app, auth_db, renewal_user):
        """Persistent rows without expires_at (pre-renewal) are backfilled.

        New sessions now carry a horizon from creation, so the NULL state has
        to be forced here — it only occurs on rows written by releases before
        ``Session.create_for_user`` set ``expires_at``. The backfill path stays
        because those rows are still in live databases.
        """
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        _set_expires(auth_db, user.id, None)  # simulate a pre-8.4.2.0 row
        assert _get_expires(auth_db, user.id) is None

        r = client.get("/auth/check")
        assert r.get_json()["authenticated"] is True
        assert _session_set_cookies(r), "NULL-horizon row should renew immediately"
        expires = _get_expires(auth_db, user.id)
        assert expires is not None and expires != "NO_ROW"
        assert expires > datetime.now() + timedelta(days=399)

    def test_new_persistent_session_has_horizon_at_creation(self, auth_app, auth_db, renewal_user):
        """Login writes expires_at immediately — never NULL.

        A NULL horizon makes ``Session.is_valid()`` unconditionally true (it
        short-circuits on ``if self.expires_at``), i.e. the session never
        expires, and makes the renewal hook treat every request as overdue.
        """
        user, secret = renewal_user
        _login(auth_app, auth_db, user, secret)
        expires = _get_expires(auth_db, user.id)
        assert expires not in (None, "NO_ROW"), "persistent session created without a horizon"
        assert expires > datetime.now() + timedelta(days=399)

    def test_new_non_persistent_session_has_horizon_at_creation(
        self, auth_app, auth_db, renewal_user
    ):
        """Ordinary sessions get a horizon too, just a much shorter one."""
        user, secret = renewal_user
        _login(auth_app, auth_db, user, secret, remember_me=False)
        expires = _get_expires(auth_db, user.id)
        assert expires not in (None, "NO_ROW"), "session created without a horizon"
        assert datetime.now() < expires < datetime.now() + timedelta(days=31)

    def test_non_persistent_session_not_renewed(self, auth_app, auth_db, renewal_user):
        """Plain (non-remember) sessions never get the persistent renewal."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret, remember_me=False)
        before = _get_expires(auth_db, user.id)

        r = client.get("/auth/check")
        assert r.get_json()["authenticated"] is True
        assert not _session_set_cookies(r)
        assert _get_expires(auth_db, user.id) == before, "horizon must not advance"

    def test_renewed_cookie_token_passes_safe_regex(self, auth_app, auth_db, renewal_user):
        """The renewed value goes through the _SAFE_SESSION_TOKEN_RE gate."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        original = client.get_cookie(COOKIE_NAME).value
        _set_expires(auth_db, user.id, datetime.now() + timedelta(days=10))

        r = client.get("/auth/check")
        assert _session_set_cookies(r)
        renewed = client.get_cookie(COOKIE_NAME).value
        assert renewed == original
        assert _SAFE_SESSION_TOKEN_RE.match(renewed), "renewed token must pass the allowlist"

        # And the shared cookie writer still rejects malformed tokens, so a
        # tainted value can never ride the renewal path into a header
        from flask import Response

        with pytest.raises(ValueError):
            set_session_cookie(Response(), "bad\r\ntoken", remember_me=True)


# ──────────────────────────────────────────────────────────────────────
# /auth/session/restore honors and advances the horizon
# ──────────────────────────────────────────────────────────────────────


class TestRestoreHorizon:
    """Session restore endpoint interaction with the expiry horizon."""

    def test_expired_persistent_session_restore_rejected(self, auth_app, auth_db, db_session_user):
        """Restore must not resurrect a session past its horizon."""
        user = db_session_user
        session, raw_token = Session.create_for_user(
            auth_db, user.id, user_agent="pytest", ip_address="127.0.0.1", remember_me=True
        )
        _set_expires(auth_db, user.id, datetime.now() - timedelta(days=1))

        client = auth_app.test_client()
        r = client.post("/auth/session/restore", json={"token": raw_token})
        assert r.status_code == 401
        assert "expired" in r.get_json()["error"].lower()
        assert SessionRepository(auth_db).get_by_token(raw_token) is None

    def test_restore_advances_horizon_with_cookie(self, auth_app, auth_db, db_session_user):
        """A successful restore re-issues the cookie AND advances expires_at."""
        user = db_session_user
        session, raw_token = Session.create_for_user(
            auth_db, user.id, user_agent="pytest", ip_address="127.0.0.1", remember_me=True
        )

        client = auth_app.test_client()
        r = client.post("/auth/session/restore", json={"token": raw_token})
        assert r.status_code == 200, r.get_json()
        cookies = _session_set_cookies(r)
        assert cookies and f"Max-Age={SESSION_DURATION_REMEMBER}" in cookies[0]
        expires = _get_expires(auth_db, user.id)
        assert expires is not None and expires != "NO_ROW"
        assert expires > datetime.now() + timedelta(days=399)


# ──────────────────────────────────────────────────────────────────────
# Server-side cleanup respects the horizon
# ──────────────────────────────────────────────────────────────────────


class TestCleanupAndTimestamps:
    """cleanup_stale + timestamp-format regressions."""

    def test_cleanup_purges_expired_persistent_sessions(self, auth_db, db_session_user):
        """Expired persistent rows are unreachable garbage — cleanup removes them."""
        user = db_session_user
        _, raw_token = Session.create_for_user(
            auth_db, user.id, user_agent="pytest", ip_address="127.0.0.1", remember_me=True
        )
        _set_expires(auth_db, user.id, datetime.now() - timedelta(days=2))

        removed = SessionRepository(auth_db).cleanup_stale()
        assert removed >= 1
        assert SessionRepository(auth_db).get_by_token(raw_token) is None

    def test_cleanup_keeps_unexpired_persistent_sessions(self, auth_db, db_session_user):
        """Persistent rows inside their horizon (or with none) survive cleanup."""
        user = db_session_user
        _, raw_token = Session.create_for_user(
            auth_db, user.id, user_agent="pytest", ip_address="127.0.0.1", remember_me=True
        )
        _set_expires(auth_db, user.id, datetime.now() + timedelta(days=100))

        SessionRepository(auth_db).cleanup_stale()
        assert SessionRepository(auth_db).get_by_token(raw_token) is not None

        # NULL horizon (legacy row) also survives — inactivity never kills it
        _set_expires(auth_db, user.id, None)
        SessionRepository(auth_db).cleanup_stale()
        assert SessionRepository(auth_db).get_by_token(raw_token) is not None

    def test_touch_writes_sqlite_compatible_timestamp(self, auth_db, db_session_user):
        """touch() must write space-separated timestamps for SQL comparisons."""
        user = db_session_user
        session, _ = Session.create_for_user(
            auth_db, user.id, user_agent="pytest", ip_address="127.0.0.1"
        )
        session.touch(auth_db)
        with auth_db.connection() as conn:
            cur = conn.execute("SELECT last_seen FROM sessions WHERE id = ?", (session.id,))
            raw = cur.fetchone()[0]
        assert "T" not in raw, f"touch() wrote non-SQLite-comparable timestamp: {raw!r}"

    def test_cleanup_removes_stale_touched_sessions_any_format(self, auth_db, db_session_user):
        """Stale non-persistent rows are removed whether last_seen used ' ' or 'T'.

        Regression: touch() used to write 'T'-separated isoformat, which
        sorts after the space-format threshold, so same-day stale sessions
        were never cleaned. datetime() normalization makes both formats work.
        """
        user = db_session_user
        old = datetime.now() - timedelta(hours=3)
        for stamp in (old.isoformat(sep=" ", timespec="seconds"), old.isoformat()):
            _, raw_token = Session.create_for_user(
                auth_db, user.id, user_agent="pytest", ip_address="127.0.0.1"
            )
            with auth_db.connection() as conn:
                conn.execute(
                    "UPDATE sessions SET last_seen = ? WHERE user_id = ?", (stamp, user.id)
                )
            SessionRepository(auth_db).cleanup_stale()
            assert SessionRepository(auth_db).get_by_token(raw_token) is None, (
                f"stale session with last_seen={stamp!r} survived cleanup"
            )


# ──────────────────────────────────────────────────────────────────────
# Constants sanity
# ──────────────────────────────────────────────────────────────────────


class TestDurationConstants:
    """The advertised durations must be browser-honest."""

    def test_remember_duration_within_browser_clamp(self):
        """Max-Age must not exceed the 400-day clamp browsers enforce."""
        assert SESSION_DURATION_REMEMBER == 400 * 24 * 60 * 60

    def test_renewal_threshold_is_half_life(self):
        assert SESSION_RENEWAL_THRESHOLD == SESSION_DURATION_REMEMBER // 2


# ──────────────────────────────────────────────────────────────────────
# Logout vs. the renewal hook
# ──────────────────────────────────────────────────────────────────────


class TestLogoutIsNotRenewed:
    """``/auth/logout`` must not hand back the session it just destroyed.

    The renewal hook is registered ``after_app_request``, so it also runs on
    the logout response. It saw ``g._current_session`` (populated while
    authenticating the logout call), judged the persistent session overdue,
    and appended a second ``Set-Cookie`` re-issuing the same token after the
    clearing one — leaving the browser signed in.
    """

    def test_logout_clears_cookie_and_does_not_reissue(self, auth_app, auth_db, renewal_user):
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        # Force the state that made the hook fire: a persistent session the
        # hook considers overdue.
        _set_expires(auth_db, user.id, None)

        r = client.post("/auth/logout")
        assert r.status_code == 200
        cookies = _session_set_cookies(r)
        assert cookies, "logout must send a clearing Set-Cookie"
        assert len(cookies) == 1, f"logout re-issued the session cookie: {cookies}"
        clearing = cookies[0]
        assert "Expires=Thu, 01 Jan 1970" in clearing or "Max-Age=0" in clearing, (
            f"logout cookie is not a clearing cookie: {clearing}"
        )

    def test_logout_near_expiry_persistent_session_not_renewed(
        self, auth_app, auth_db, renewal_user
    ):
        """Same guarantee when the horizon exists but is inside the window."""
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        _set_expires(auth_db, user.id, datetime.now() + timedelta(days=10))

        r = client.post("/auth/logout")
        assert r.status_code == 200
        assert len(_session_set_cookies(r)) == 1

    def test_logout_deletes_the_session_row(self, auth_app, auth_db, renewal_user):
        user, secret = renewal_user
        client = _login(auth_app, auth_db, user, secret)
        _set_expires(auth_db, user.id, None)

        client.post("/auth/logout")
        assert _get_expires(auth_db, user.id) == "NO_ROW"

    def test_session_marks_itself_invalidated(self, auth_db, db_session_user):
        """The hook's guard: an in-memory Session knows its row is gone."""
        session, _token = Session.create_for_user(auth_db, db_session_user.ensured_id)
        assert session.invalidated is False
        session.invalidate(auth_db)
        assert session.invalidated is True
