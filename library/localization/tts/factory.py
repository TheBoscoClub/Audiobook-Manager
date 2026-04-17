"""TTS provider factory — picks a backend based on localization config.

Explicit overrides (`provider_name` or `AUDIOBOOKS_TTS_PROVIDER`) always win
and raise on misconfiguration so the admin sees the real problem. Auto mode
is workload-aware and mirrors the STT selection policy: short/interactive
synthesis prefers edge-tts (no cold-start), long-form synthesis prefers a
GPU backend (XTTS on RunPod → Vast.ai) and falls through to edge-tts when
nothing remote is configured.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..fallback import with_local_fallback
from ..selection import WorkloadHint
from .base import TTSProvider
from .edge_tts_provider import EdgeTTSProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _remote_tts_candidates() -> list[TTSProvider]:
    """Return configured remote TTS providers in preferred order.

    RunPod XTTS is preferred because its serverless endpoint scales to
    zero. Vast.ai XTTS needs a pinned instance and is opt-in.
    """
    from .. import config as loc_config

    providers: list[TTSProvider] = []
    if loc_config.RUNPOD_API_KEY and loc_config.RUNPOD_XTTS_ENDPOINT:
        from .xtts import XTTSProvider

        providers.append(
            XTTSProvider(
                api_key=loc_config.RUNPOD_API_KEY, endpoint_id=loc_config.RUNPOD_XTTS_ENDPOINT
            )
        )
    if loc_config.VASTAI_XTTS_HOST:
        from .vastai_xtts import VastaiXTTSProvider

        providers.append(
            VastaiXTTSProvider(host=loc_config.VASTAI_XTTS_HOST, port=loc_config.VASTAI_XTTS_PORT)
        )
    return providers


def get_tts_provider(
    provider_name: str | None = None, workload: WorkloadHint = WorkloadHint.ANY
) -> TTSProvider:
    """Return a TTSProvider matching the configured backend.

    Explicit overrides — either ``provider_name`` or the
    ``AUDIOBOOKS_TTS_PROVIDER`` env var — always win and raise if the
    selected GPU backend is missing credentials. In auto mode, selection
    is workload-aware:

    - ``SHORT_CLIP`` → edge-tts (no cold-start, near-instant)
    - ``LONG_FORM`` → first configured GPU backend, else edge-tts
    - ``ANY`` → first configured GPU backend if any, else edge-tts

    Args:
        provider_name: Override — ``"edge-tts"``, ``"xtts-runpod"``,
            ``"xtts-vastai"``, or None/empty for auto mode.
        workload: Hint describing the work shape. Defaults to ``ANY``.

    Raises:
        ValueError: If an explicit GPU provider is selected but
            credentials are missing, or the name is unknown.
    """
    from .. import config as loc_config

    name = (provider_name or loc_config.TTS_PROVIDER or "").lower()

    explicit = _tts_by_explicit_name(name, loc_config)
    if explicit is not None:
        return explicit

    # Auto mode: workload-aware ordering.
    remote = _remote_tts_candidates()
    if workload is WorkloadHint.SHORT_CLIP or not remote:
        if not remote:
            logger.info("No remote TTS configured — using edge-tts")
        return EdgeTTSProvider()

    chosen = remote[0]
    logger.info("Auto TTS: selected %s (workload=%s)", chosen.name, workload.value)
    return chosen


def _tts_by_explicit_name(name: str, loc_config) -> TTSProvider | None:
    """Build a TTS provider for a named backend, or None for auto mode.
    Raises ValueError if the backend is known but unconfigured/unsupported.
    """
    if not name:
        return None
    if name in ("edge-tts", "edge"):
        return EdgeTTSProvider()
    if name in ("xtts", "xtts-runpod", "runpod"):
        from .xtts import XTTSProvider

        if not loc_config.RUNPOD_API_KEY:
            raise ValueError("xtts-runpod requires AUDIOBOOKS_RUNPOD_API_KEY in audiobooks.conf")
        if not loc_config.RUNPOD_XTTS_ENDPOINT:
            raise ValueError(
                "xtts-runpod requires AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT in audiobooks.conf"
            )
        return XTTSProvider(
            api_key=loc_config.RUNPOD_API_KEY, endpoint_id=loc_config.RUNPOD_XTTS_ENDPOINT
        )
    if name in ("xtts-vastai", "vastai", "vast"):
        from .vastai_xtts import VastaiXTTSProvider

        if not loc_config.VASTAI_XTTS_HOST:
            raise ValueError("xtts-vastai requires AUDIOBOOKS_VASTAI_XTTS_HOST in audiobooks.conf")
        return VastaiXTTSProvider(
            host=loc_config.VASTAI_XTTS_HOST, port=loc_config.VASTAI_XTTS_PORT
        )
    raise ValueError(f"Unknown TTS provider: {name}")


def synthesize_with_fallback(
    provider: TTSProvider, text: str, language: str, voice: str, output_path: Path
) -> Path:
    """Synthesize via ``provider``; on network failure, retry once via edge-tts.

    edge-tts is the always-available local fallback for TTS (no cold-start,
    no per-minute billing, no GPU). If the primary provider is already
    local-equivalent (``is_local=True``), network errors propagate.
    """
    return with_local_fallback(
        kind="TTS",
        provider_name=provider.name,
        is_local=provider.is_local,
        remote_call=lambda: provider.synthesize(text, language, voice, output_path),
        local_call=lambda: EdgeTTSProvider().synthesize(text, language, voice, output_path),
    )
