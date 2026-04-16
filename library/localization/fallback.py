"""Runtime fallback helpers for STT and TTS providers.

Remote GPU providers (Vast.ai, RunPod) can be unreachable when instances
aren't running or the host is misconfigured. The RunPod HTTPS proxy in
particular produces intermittent ConnectionError/HTTPError bursts even
when the underlying pod is fully healthy — a single failed request used
to trigger an immediate CPU fallback that then ran at ~1/40th the GPU
throughput for the rest of the chapter. We now retry the remote call a
few times with exponential backoff before giving up; only after all
retries fail do we fall back to local. Local provider failures are not
retried — the error is real and should propagate.
"""

import logging
import time
from typing import Callable, TypeVar

import requests

logger = logging.getLogger(__name__)

# Errors that indicate a remote provider is unreachable.
NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    requests.exceptions.RequestException,
    OSError,
    TimeoutError,
)

# Retry policy for transient remote provider failures (RunPod proxy hiccups,
# brief TLS handshakes, etc.) before falling back to the local provider.
REMOTE_MAX_ATTEMPTS = 4
REMOTE_BACKOFF_SECONDS = (2.0, 5.0, 15.0)  # len == REMOTE_MAX_ATTEMPTS - 1

T = TypeVar("T")


def with_local_fallback(
    kind: str,
    provider_name: str,
    is_local: bool,
    remote_call: Callable[[], T],
    local_call: Callable[[], T],
) -> T:
    """Run ``remote_call`` with retries; on repeated failure, run ``local_call``.

    Args:
        kind: "STT" or "TTS" — used in the log line only.
        provider_name: Provider identifier for log output.
        is_local: True if the primary provider is already the local one;
            in that case the fallback branch re-raises so the caller sees
            the real error rather than silently retrying.
        remote_call: Zero-arg thunk invoking the remote provider.
        local_call: Zero-arg thunk invoking the local provider.

    Returns:
        Whatever ``remote_call`` or ``local_call`` returns.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, REMOTE_MAX_ATTEMPTS + 1):
        try:
            return remote_call()
        except NETWORK_ERRORS as exc:
            last_exc = exc
            if is_local or attempt == REMOTE_MAX_ATTEMPTS:
                break
            delay = REMOTE_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "%s provider %s transient failure on attempt %d/%d (%s) — retrying in %.1fs",
                kind,
                provider_name,
                attempt,
                REMOTE_MAX_ATTEMPTS,
                exc.__class__.__name__,
                delay,
            )
            time.sleep(delay)

    if is_local:
        raise last_exc  # type: ignore[misc]
    logger.warning(
        "%s provider %s unreachable after %d attempts (%s) — falling back to local",
        kind,
        provider_name,
        REMOTE_MAX_ATTEMPTS,
        last_exc.__class__.__name__ if last_exc else "unknown",
    )
    return local_call()
