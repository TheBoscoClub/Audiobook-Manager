"""Provider-agnostic streaming-inference GPU health probe.

Canonical implementation of the per-provider health check used by both
the live API path
(:func:`library.backend.api_modular.streaming_translate._probe_stt_warmth`,
which adds 60-second caching and a tuple-shape adaptation for the
buffer-fill threshold) and the timer-driven translation monitor
(:func:`library.translation_monitor.probe.probe_gpu_instance_health`,
which transforms the result into the
``{"providers": {...}, "any_healthy": ..., "stub": False}`` shape used
by operator-facing health logging).

Two provider families are recognized today — RunPod serverless on
``api.runpod.ai`` and Vast.ai serverless on ``run.vast.ai`` — both
expose a RunPod-compatible ``GET /v2/<endpoint>/health`` returning
``{"workers": {"ready": N, "running": M, ...}}``. Adding a new
provider family is a one-line addition to ``_PROVIDERS``.

This module is provider-agnostic at the call-site level — callers that
inspect ``any_healthy`` work identically regardless of which provider
families are configured. Pessimistic by design: "unknown" maps to
"unhealthy", not "healthy". The pre-v8.3.10.5 stub returned
``any_healthy=True`` even when nothing was checked, which masked the
2026-05-04 prod incident where the worker was alive but starving the
user's chapter for 10+ minutes.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Trusted-host probe targets. Adding a new entry without verifying the
# host's TLS/CA chain is a security regression (the urllib.request
# invocations below intentionally trust these base URLs).
_PROVIDERS: tuple[tuple[str, str, str, str], ...] = (
    # (provider_name, api_key_env, endpoint_id_env, base_url)
    (
        "runpod",
        "AUDIOBOOKS_RUNPOD_API_KEY",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT",
        "https://api.runpod.ai",
    ),
    (
        "vastai",
        "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY",
        "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT",
        "https://run.vast.ai",
    ),
)

PROBE_TIMEOUT_SEC = 3


def _probe_one_provider(name: str, api_key: str, endpoint: str, base_url: str) -> dict | None:
    """Probe a single ``(api_key, endpoint)`` pair for streaming worker health.

    Returns ``None`` if the provider is unconfigured (either credential
    missing); otherwise returns a dict with ``name``, ``ready``, and
    ``endpoint_id`` keys. ``ready`` is ``0`` if the probe fails for any
    reason (network, timeout, malformed response) — failure is *not*
    raised so the caller can iterate multiple providers and aggregate.
    """
    if not api_key or not endpoint:
        return None
    url = f"{base_url}/v2/{endpoint}/health"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})  # noqa: S310
    entry: dict = {"name": name, "ready": 0, "endpoint_id": endpoint}
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SEC) as resp:  # nosec B310 — trusted provider hosts  # noqa: S310
            payload = json.loads(resp.read().decode())
        workers = payload.get("workers", {}) or {}
        entry["ready"] = int(workers.get("ready", 0))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        logger.debug("%s warmth probe failed: %s", name, e)
    return entry


def probe_all_streaming_providers() -> dict:
    """Iterate every configured streaming provider, return aggregate health.

    Returns a dict with two keys:

    ``providers`` — list of ``{"name", "ready", "endpoint_id"}`` dicts,
    one per provider whose env-var pair is configured. Order matches
    :data:`_PROVIDERS` (RunPod first, Vast.ai second) for determinism.
    Unconfigured providers are absent — *not* present with ready=0.

    ``any_healthy`` — True iff at least one configured provider has
    ``ready > 0``. False when no providers are configured, every probe
    returned 0 ready workers, or every probe failed.
    """
    providers: list[dict] = []
    for name, api_key_env, endpoint_env, base_url in _PROVIDERS:
        entry = _probe_one_provider(
            name=name,
            api_key=os.environ.get(api_key_env, ""),
            endpoint=os.environ.get(endpoint_env, ""),
            base_url=base_url,
        )
        if entry is not None:
            providers.append(entry)
    any_healthy = any(p["ready"] > 0 for p in providers)
    return {"providers": providers, "any_healthy": any_healthy}
