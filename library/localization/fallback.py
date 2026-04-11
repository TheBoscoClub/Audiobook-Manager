"""Runtime fallback helpers for STT and TTS providers.

Remote GPU providers (Vast.ai, RunPod) can be unreachable when instances
aren't running or the host is misconfigured. Rather than surfacing the
connection error to the user, fall back once to a local provider for the
current request. Local provider failures are not retried — the error is
real and should propagate.
"""

import logging
from typing import Callable, TypeVar

import requests

logger = logging.getLogger(__name__)

# Errors that indicate a remote provider is unreachable.
NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    requests.exceptions.RequestException,
    OSError,
    TimeoutError,
)

T = TypeVar("T")


def with_local_fallback(
    kind: str,
    provider_name: str,
    is_local: bool,
    remote_call: Callable[[], T],
    local_call: Callable[[], T],
) -> T:
    """Run ``remote_call``; on network failure, log and run ``local_call`` once.

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
    try:
        return remote_call()
    except NETWORK_ERRORS as exc:
        if is_local:
            raise
        logger.warning(
            "%s provider %s unreachable (%s) — falling back to local",
            kind,
            provider_name,
            exc.__class__.__name__,
        )
        return local_call()
