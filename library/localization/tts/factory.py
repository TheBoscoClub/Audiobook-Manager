"""TTS provider factory — picks a backend based on localization config.

The factory reads `AUDIOBOOKS_TTS_PROVIDER` (via `library.localization.config`)
and instantiates the matching provider with the right credentials. Falling
back to edge-tts when a GPU backend is configured but missing credentials
would hide configuration errors, so we raise instead — callers catch the
exception and log it so the admin sees the real problem.
"""

import logging

from .base import TTSProvider
from .edge_tts_provider import EdgeTTSProvider

logger = logging.getLogger(__name__)


def get_tts_provider(provider_name: str | None = None) -> TTSProvider:
    """Return a TTSProvider matching the configured backend.

    Args:
        provider_name: Override the configured provider. If None, reads
            AUDIOBOOKS_TTS_PROVIDER from the environment via config.

    Raises:
        ValueError: If a GPU provider is selected but credentials are missing.
    """
    from .. import config as loc_config

    name = (provider_name or loc_config.TTS_PROVIDER or "edge-tts").lower()

    if name in ("edge-tts", "edge", ""):
        return EdgeTTSProvider()

    if name in ("xtts", "xtts-runpod", "runpod"):
        from .xtts import XTTSProvider

        if not loc_config.RUNPOD_API_KEY:
            raise ValueError(
                "xtts-runpod requires AUDIOBOOKS_RUNPOD_API_KEY in audiobooks.conf"
            )
        if not loc_config.RUNPOD_XTTS_ENDPOINT:
            raise ValueError(
                "xtts-runpod requires AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT in audiobooks.conf"
            )
        return XTTSProvider(
            api_key=loc_config.RUNPOD_API_KEY,
            endpoint_id=loc_config.RUNPOD_XTTS_ENDPOINT,
        )

    if name in ("xtts-vastai", "vastai", "vast"):
        from .vastai_xtts import VastaiXTTSProvider

        if not loc_config.VASTAI_XTTS_HOST:
            raise ValueError(
                "xtts-vastai requires AUDIOBOOKS_VASTAI_XTTS_HOST in audiobooks.conf"
            )
        return VastaiXTTSProvider(
            host=loc_config.VASTAI_XTTS_HOST,
            port=loc_config.VASTAI_XTTS_PORT,
        )

    raise ValueError(f"Unknown TTS provider: {name}")
