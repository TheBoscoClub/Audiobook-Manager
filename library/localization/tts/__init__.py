"""Text-to-speech provider interface and implementations."""

from .base import TTSProvider, Voice
from .edge_tts_provider import EdgeTTSProvider
from .xtts import XTTSProvider

__all__ = ["TTSProvider", "Voice", "EdgeTTSProvider", "XTTSProvider"]
