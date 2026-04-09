"""Speech-to-text provider interface and implementations."""

from .base import STTProvider, Transcript, WordTimestamp
from .deepl_stt import DeepLSTT
from .local_whisper import LocalWhisperSTT
from .vastai_whisper import VastaiWhisperSTT
from .whisper_stt import WhisperSTT

__all__ = [
    "STTProvider", "Transcript", "WordTimestamp",
    "DeepLSTT", "LocalWhisperSTT", "VastaiWhisperSTT", "WhisperSTT",
]
