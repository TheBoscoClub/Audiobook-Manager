"""Tests for failed-authentication throttling on the /auth/* credential endpoints.

Before this existed, ``/auth/login`` accepted unlimited guesses at a six-digit
TOTP code. The whole keyspace is 10^6 and only the ~2 codes valid in the
current window need trying, so an unthrottled endpoint is not a hard target —
it is a few minutes of HTTP.

Covers the limiter in isolation (window, lockout, reset, expiry, key
separation) and end-to-end through the real Flask routes.
"""

import sys
import time
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))

from api_modular.rate_limit import (  # noqa: E402
    CONFIG_LOCKOUT_SECONDS,
    CONFIG_MAX_FAILURES,
    CONFIG_WINDOW_SECONDS,
    DEFAULT_MAX_FAILURES,
    SlidingWindowLimiter,
    get_limiter,
)
from auth import AuthType, User  # noqa: E402
from auth.totp import TOTPAuthenticator, setup_totp  # noqa: E402

WRONG_CODE = "000000"


@pytest.fixture(autouse=True)
def clean_limiter():
    """Every test starts with an empty limiter and leaves one behind."""
    get_limiter().clear()
    yield
    get_limiter().clear()


# ──────────────────────────────────────────────────────────────────────
# SlidingWindowLimiter in isolation
# ──────────────────────────────────────────────────────────────────────


class TestSlidingWindowLimiter:
    def test_allows_until_the_limit(self):
        limiter = SlidingWindowLimiter(max_failures=3, window_seconds=60, lockout_seconds=60)
        assert limiter.retry_after("k") is None
        assert limiter.record_failure("k") is False
        assert limiter.record_failure("k") is False
        assert limiter.retry_after("k") is None, "still under the limit"

    def test_locks_at_the_limit(self):
        limiter = SlidingWindowLimiter(max_failures=3, window_seconds=60, lockout_seconds=60)
        for _ in range(3):
            limiter.record_failure("k")
        retry_after = limiter.retry_after("k")
        assert retry_after is not None and 0 < retry_after <= 60

    def test_success_resets_the_counter(self):
        limiter = SlidingWindowLimiter(max_failures=3, window_seconds=60, lockout_seconds=60)
        limiter.record_failure("k")
        limiter.record_failure("k")
        limiter.reset("k")
        assert limiter.failure_count("k") == 0
        assert limiter.record_failure("k") is False, "counter restarted from zero"

    def test_lockout_expires(self):
        limiter = SlidingWindowLimiter(max_failures=2, window_seconds=60, lockout_seconds=0)
        limiter.record_failure("k")
        limiter.record_failure("k")
        time.sleep(0.01)
        assert limiter.retry_after("k") is None, "lockout must lapse"
        assert limiter.failure_count("k") == 0, "the failures that caused it lapse too"

    def test_window_expiry_drops_old_failures(self):
        limiter = SlidingWindowLimiter(max_failures=3, window_seconds=0, lockout_seconds=60)
        limiter.record_failure("k")
        time.sleep(0.01)
        assert limiter.failure_count("k") == 0, "out-of-window failures must fall out"

    def test_keys_are_independent(self):
        limiter = SlidingWindowLimiter(max_failures=2, window_seconds=60, lockout_seconds=60)
        limiter.record_failure("a")
        limiter.record_failure("a")
        assert limiter.retry_after("a") is not None
        assert limiter.retry_after("b") is None, "one key's lockout must not leak to another"

    def test_lockout_survives_a_clock_step(self, monkeypatch):
        """Lockout is measured on the monotonic clock, not wall time."""
        limiter = SlidingWindowLimiter(max_failures=1, window_seconds=60, lockout_seconds=60)
        limiter.record_failure("k")
        monkeypatch.setattr("time.time", lambda: 0)
        assert limiter.retry_after("k") is not None


# ──────────────────────────────────────────────────────────────────────
# End-to-end through the real /auth routes
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def throttled_user(auth_db):
    secret, _, _ = setup_totp("throttleuser")
    user = User(
        username="throttleuser",
        auth_type=AuthType.TOTP,
        auth_credential=secret,
        can_download=False,
        is_admin=False,
    )
    user.save(auth_db)
    assert user.id is not None
    return user, secret


@pytest.fixture
def tight_limits(auth_app):
    """Three strikes, short lockout — keeps the test fast and explicit."""
    previous = {
        key: auth_app.config.get(key)
        for key in (CONFIG_MAX_FAILURES, CONFIG_WINDOW_SECONDS, CONFIG_LOCKOUT_SECONDS)
    }
    auth_app.config[CONFIG_MAX_FAILURES] = 3
    auth_app.config[CONFIG_WINDOW_SECONDS] = 60
    auth_app.config[CONFIG_LOCKOUT_SECONDS] = 60
    yield
    for key, value in previous.items():
        if value is None:
            auth_app.config.pop(key, None)
        else:
            auth_app.config[key] = value


def _bad_login(client, username):
    return client.post("/auth/login", json={"username": username, "code": WRONG_CODE})


class TestLoginThrottling:
    def test_wrong_codes_lock_out_after_n_failures(self, auth_app, throttled_user, tight_limits):
        user, _secret = throttled_user
        client = auth_app.test_client()

        for attempt in range(3):
            r = _bad_login(client, user.username)
            assert r.status_code == 401, f"attempt {attempt} should be a plain rejection"

        r = _bad_login(client, user.username)
        assert r.status_code == 429, "the 4th attempt must be refused by the limiter"
        assert r.headers.get("Retry-After"), "429 must carry Retry-After"
        assert r.get_json()["retry_after"] > 0

    def test_locked_out_client_cannot_use_a_correct_code(
        self, auth_app, auth_db, throttled_user, tight_limits
    ):
        """The lockout is what makes brute force stop paying — a correct guess
        arriving while locked must not be honoured."""
        user, secret = throttled_user
        client = auth_app.test_client()
        for _ in range(3):
            _bad_login(client, user.username)

        code = TOTPAuthenticator(secret).current_code()
        r = client.post("/auth/login", json={"username": user.username, "code": code})
        assert r.status_code == 429

    def test_success_before_the_limit_resets_the_counter(
        self, auth_app, auth_db, throttled_user, tight_limits
    ):
        user, secret = throttled_user
        client = auth_app.test_client()
        with auth_db.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))

        _bad_login(client, user.username)
        _bad_login(client, user.username)

        code = TOTPAuthenticator(secret).current_code()
        r = client.post("/auth/login", json={"username": user.username, "code": code})
        assert r.status_code == 200, f"login should still succeed: {r.get_json()}"

        # Counter is back to zero: a full fresh budget of failures follows.
        for attempt in range(3):
            assert _bad_login(client, user.username).status_code == 401, attempt

    def test_lockout_lapses_after_the_window(self, auth_app, throttled_user):
        client = auth_app.test_client()
        user, _secret = throttled_user
        auth_app.config[CONFIG_MAX_FAILURES] = 2
        auth_app.config[CONFIG_WINDOW_SECONDS] = 60
        auth_app.config[CONFIG_LOCKOUT_SECONDS] = 0
        try:
            for _ in range(2):
                _bad_login(client, user.username)
            time.sleep(0.01)
            assert _bad_login(client, user.username).status_code == 401, (
                "a zero-second lockout must lapse immediately, not persist"
            )
        finally:
            for key in (CONFIG_MAX_FAILURES, CONFIG_WINDOW_SECONDS, CONFIG_LOCKOUT_SECONDS):
                auth_app.config.pop(key, None)

    def test_other_usernames_are_not_locked_out(self, auth_app, throttled_user, tight_limits):
        """Keying includes the username, so one target's lockout is not a
        denial of service against every other account from that address."""
        user, _secret = throttled_user
        client = auth_app.test_client()
        for _ in range(3):
            _bad_login(client, user.username)
        assert _bad_login(client, user.username).status_code == 429

        r = _bad_login(client, "someoneelse")
        assert r.status_code == 401, "an unrelated username must not inherit the lockout"

    def test_malformed_body_does_not_count_as_a_failure(
        self, auth_app, throttled_user, tight_limits
    ):
        """400s are bad input, not wrong guesses — counting them would let a
        client lock itself out with junk."""
        client = auth_app.test_client()
        for _ in range(6):
            r = client.post("/auth/login", json={})
            assert r.status_code == 400
        user, _secret = throttled_user
        assert _bad_login(client, user.username).status_code == 401


class TestDecoratorCoverage:
    """Every endpoint that verifies a secret must carry the limiter."""

    @pytest.mark.parametrize(
        "module,route",
        [
            ("auth.py", "/login"),
            ("auth_webauthn.py", "/login/webauthn/complete"),
            ("auth_recovery.py", "/recover/backup-code"),
        ],
    )
    def test_route_is_rate_limited(self, module, route):
        source = (LIBRARY_DIR / "backend" / "api_modular" / module).read_text()
        marker = f'@auth_bp.route("{route}"'
        index = source.index(marker)
        block = source[index : index + 300]
        assert "@auth_rate_limited(" in block, f"{route} in {module} is not rate limited"

    def test_defaults_are_a_meaningful_budget(self):
        assert DEFAULT_MAX_FAILURES <= 10, (
            "a generous default defeats the purpose — a six-digit code needs "
            "the attempt budget to be small"
        )
