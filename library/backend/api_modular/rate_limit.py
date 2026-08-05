"""Failed-authentication throttling for the /auth/* credential endpoints.

Every credential this app accepts is short and enumerable: a TOTP code is six
digits (10^6), a backup code is a handful of characters. Without a limiter the
only thing standing between an attacker and a valid code is request
throughput — a six-digit TOTP with an unthrottled endpoint falls in minutes,
and the 30-second step means only the codes valid *right now* need trying.

Design notes
------------
- **Per (client address, username)**, not per address alone. Keying on the
  address alone lets one attacker lock every account out from a single IP;
  keying on the username alone lets anyone lock out a known user. Both
  dimensions together throttle the guess without handing over a denial of
  service.
- **Sliding window**: failures older than ``window_seconds`` fall out, so a
  user who mistypes a code today is not one mistake away from lockout a week
  later.
- **Success resets** the counter for that key immediately.
- **In-process and worker-local, by design.** ``audiobook-api.service`` runs
  gunicorn with a hard 1-worker constraint (its WebSocket connection manager
  keeps in-memory state), so one process sees every auth request and the
  counters are in fact global for this deployment. If that constraint is ever
  lifted, an attacker gets N× the budget where N is the worker count — at
  which point this needs to move to the auth database or a shared store.
  Nothing here silently degrades: the limiter keeps working, the budget just
  multiplies.
- **Fails closed on lockout, open on absence.** An unknown key is allowed; a
  locked key is refused with 429 + ``Retry-After``.
"""

import logging
import threading
import time
from collections import deque
from functools import wraps
from typing import Any, Callable, Deque, Optional

from flask import current_app, jsonify, request

logger = logging.getLogger(__name__)

# Defaults. Overridable per-app via the Flask config keys named below, so a
# deployment can loosen them without editing code (and tests can tighten them).
DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_SECONDS = 300  # 5 minutes of history counts toward the limit
DEFAULT_LOCKOUT_SECONDS = 300  # 5 minutes locked once the limit is reached

CONFIG_MAX_FAILURES = "AUTH_RATE_LIMIT_MAX_FAILURES"
CONFIG_WINDOW_SECONDS = "AUTH_RATE_LIMIT_WINDOW_SECONDS"
CONFIG_LOCKOUT_SECONDS = "AUTH_RATE_LIMIT_LOCKOUT_SECONDS"

# Response codes that count as a failed credential attempt. 400 is excluded:
# it means malformed input, not a wrong guess, and counting it would let a
# client lock itself out by sending junk.
FAILURE_STATUS_CODES = frozenset({401, 403})


class SlidingWindowLimiter:
    """Thread-safe sliding-window failure counter with lockout.

    Timestamps come from ``time.monotonic()`` so a system clock change (NTP
    step, DST, manual set) can neither extend nor cancel a lockout.
    """

    def __init__(
        self,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        lockout_seconds: int = DEFAULT_LOCKOUT_SECONDS,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, Deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    # -- internals -----------------------------------------------------

    def _prune(self, key: str, now: float) -> Deque[float]:
        """Drop out-of-window failures for a key. Caller holds the lock."""
        stamps = self._failures.get(key)
        if stamps is None:
            stamps = deque()
            self._failures[key] = stamps
        cutoff = now - self.window_seconds
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        return stamps

    def _forget_if_idle(self, key: str, stamps: Deque[float]) -> None:
        """Release memory for keys with nothing left to remember."""
        if not stamps and key not in self._locked_until:
            self._failures.pop(key, None)

    # -- public API ----------------------------------------------------

    def retry_after(self, key: str) -> Optional[int]:
        """Seconds until ``key`` may try again, or None if it may try now."""
        now = time.monotonic()
        with self._lock:
            locked_until = self._locked_until.get(key)
            if locked_until is None:
                return None
            if locked_until <= now:
                # Lockout served: clear it and the failures that caused it, so
                # the window genuinely expires instead of re-locking instantly.
                del self._locked_until[key]
                self._failures.pop(key, None)
                return None
            return max(1, int(locked_until - now))

    def record_failure(self, key: str) -> bool:
        """Register a failed attempt. Returns True if this triggered lockout."""
        now = time.monotonic()
        with self._lock:
            stamps = self._prune(key, now)
            stamps.append(now)
            if len(stamps) >= self.max_failures:
                self._locked_until[key] = now + self.lockout_seconds
                stamps.clear()
                return True
            return False

    def reset(self, key: str) -> None:
        """Forget everything about a key (called on a successful auth)."""
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def failure_count(self, key: str) -> int:
        """Current in-window failure count (diagnostics and tests)."""
        now = time.monotonic()
        with self._lock:
            stamps = self._prune(key, now)
            count = len(stamps)
            self._forget_if_idle(key, stamps)
            return count

    def clear(self) -> None:
        """Drop all state. Test-support; never called by request handling."""
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


# Module-level limiter shared by every decorated endpoint, so an attacker
# cannot get a fresh budget by rotating between /auth/login and the WebAuthn
# or backup-code verifiers. The endpoint name IS part of the key, but the
# window and lockout policy are one object.
_limiter = SlidingWindowLimiter()


def get_limiter() -> SlidingWindowLimiter:
    """Return the process-wide limiter."""
    return _limiter


def _configured(name: str, default: int) -> int:
    try:
        return int(current_app.config.get(name, default))
    except (RuntimeError, TypeError, ValueError):  # fmt: skip
        return default


def _apply_config() -> None:
    """Sync the limiter with the active app's config, if it overrides."""
    _limiter.max_failures = _configured(CONFIG_MAX_FAILURES, DEFAULT_MAX_FAILURES)
    _limiter.window_seconds = _configured(CONFIG_WINDOW_SECONDS, DEFAULT_WINDOW_SECONDS)
    _limiter.lockout_seconds = _configured(CONFIG_LOCKOUT_SECONDS, DEFAULT_LOCKOUT_SECONDS)


def _request_username() -> str:
    """Best-effort username from the request body, lowercased.

    Missing or unparseable bodies collapse to ``-``: the attempt still counts,
    it just shares a bucket with other body-less attempts from that address.
    """
    try:
        data = request.get_json(silent=True) or {}
    except Exception:  # noqa: BLE001 — body parsing must never break the gate
        return "-"
    if not isinstance(data, dict):
        return "-"
    for field in ("username", "user", "email"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()[:128]
    return "-"


def _rate_limit_key(scope: str) -> str:
    # Imported lazily: auth_shared imports this module's decorator, so a
    # top-level import here would close an import cycle.
    from .auth_shared import effective_client_address

    return f"{scope}|{effective_client_address()}|{_request_username()}"


def _too_many_requests(retry_after: int) -> Any:
    response = jsonify(
        {
            "error": "Too many failed attempts",
            "message": f"Try again in {retry_after} seconds.",
            "retry_after": retry_after,
        }
    )
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response


def _status_of(result: Any) -> Optional[int]:
    """Extract the HTTP status from any Flask view return shape."""
    if isinstance(result, tuple):
        for item in result[1:]:
            if isinstance(item, int):
                return item
        return 200
    status = getattr(result, "status_code", None)
    return status if isinstance(status, int) else None


def auth_rate_limited(scope: str) -> Callable:
    """Throttle repeated credential failures on an auth endpoint.

    ``scope`` names the endpoint family in the key (so /auth/login failures
    and WebAuthn failures are counted separately) while sharing one policy.
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated(*args: Any, **kwargs: Any) -> Any:
            _apply_config()
            key = _rate_limit_key(scope)

            retry_after = _limiter.retry_after(key)
            if retry_after is not None:
                logger.warning("Auth rate limit: refused locked key for scope=%s", scope)
                return _too_many_requests(retry_after)

            result = f(*args, **kwargs)

            status = _status_of(result)
            if status in FAILURE_STATUS_CODES:
                if _limiter.record_failure(key):
                    logger.warning(
                        "Auth rate limit: locked out after %d failures (scope=%s)",
                        _limiter.max_failures,
                        scope,
                    )
            elif status is not None and 200 <= status < 300:
                _limiter.reset(key)
            return result

        return decorated

    return decorator
