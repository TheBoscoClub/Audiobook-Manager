"""Speech-to-text provider interface and implementations."""

from .base import STTProvider, Transcript, WordTimestamp
from .deepl_stt import DeepLSTT
from .local_gpu_whisper import LocalGPUWhisperSTT
from .vastai_serverless import VastaiServerlessSTT
from .vastai_whisper import VastaiWhisperSTT
from .whisper_stt import WhisperSTT

__all__ = [
    "STTProvider",
    "Transcript",
    "WordTimestamp",
    "DeepLSTT",
    "LocalGPUWhisperSTT",
    "VastaiServerlessSTT",
    "VastaiWhisperSTT",
    "WhisperSTT",
]
