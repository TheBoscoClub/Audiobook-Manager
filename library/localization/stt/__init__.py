"""Speech-to-text provider interface and implementations."""

from .base import STTProvider, Transcript, WordTimestamp
from .local_gpu_whisper import LocalGPUWhisperSTT
from .whisper_stt import WhisperSTT

__all__ = [
    "STTProvider",
    "Transcript",
    "WordTimestamp",
    "LocalGPUWhisperSTT",
    "WhisperSTT",
]
