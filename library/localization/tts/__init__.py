"""Text-to-speech provider interface and implementations."""

from .base import TTSProvider, Voice
from .edge_tts_provider import EdgeTTSProvider
from .factory import get_tts_provider
from .vastai_xtts import VastaiXTTSProvider
from .xtts import XTTSProvider

__all__ = [
    "TTSProvider",
    "Voice",
    "EdgeTTSProvider",
    "XTTSProvider",
    "VastaiXTTSProvider",
    "get_tts_provider",
]
