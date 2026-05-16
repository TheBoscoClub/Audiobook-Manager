"""Localization configuration — reads from environment and audiobooks.conf."""

import os
from pathlib import Path

from common_utils.secret_resolver import resolve_secret

DEFAULT_LOCALE = os.environ.get("AUDIOBOOKS_DEFAULT_LOCALE", "en")
SUPPORTED_LOCALES = os.environ.get("AUDIOBOOKS_SUPPORTED_LOCALES", "en,zh-Hans").split(",")

# STT provider: "deepl", "whisper", or "auto"
STT_PROVIDER = os.environ.get("AUDIOBOOKS_STT_PROVIDER", "auto")

# TTS provider: "edge-tts" or "xtts-runpod"
TTS_PROVIDER = os.environ.get("AUDIOBOOKS_TTS_PROVIDER", "edge-tts")
TTS_VOICE_ZH = os.environ.get("AUDIOBOOKS_TTS_VOICE_ZH", "zh-CN-XiaoxiaoNeural")

# API keys — resolved via env var OR *_FILE pointer. The pointer variant
# reads from a 0600 file referenced by AUDIOBOOKS_DEEPL_API_KEY_FILE /
# AUDIOBOOKS_RUNPOD_API_KEY_FILE so secrets can live outside audiobooks.conf.
DEEPL_API_KEY = resolve_secret("AUDIOBOOKS_DEEPL_API_KEY")
RUNPOD_API_KEY = resolve_secret("AUDIOBOOKS_RUNPOD_API_KEY")
RUNPOD_WHISPER_ENDPOINT = os.environ.get("AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT", "")
RUNPOD_XTTS_ENDPOINT = os.environ.get("AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT", "")

# Asymmetric-pool RunPod serverless endpoints. Streaming keeps min_workers=1
# (warm) for latency-critical per-segment inference; backlog keeps
# min_workers=0 (cold) for cheap batch work.
RUNPOD_STREAMING_WHISPER_ENDPOINT = os.environ.get(
    "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT", ""
)
RUNPOD_BACKLOG_WHISPER_ENDPOINT = os.environ.get("AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT", "")

# Local GPU Whisper service — host and port of the optional whisper-gpu
# systemd service (see extras/whisper-gpu/). Unset by default; installers
# who set up the service configure the reachable host/port themselves.
WHISPER_GPU_HOST = os.environ.get("AUDIOBOOKS_WHISPER_GPU_HOST", "")
WHISPER_GPU_PORT = int(os.environ.get("AUDIOBOOKS_WHISPER_GPU_PORT", "8765"))

# Douban Books API (access restricted since 2019)
DOUBAN_API_KEY = os.environ.get("AUDIOBOOKS_DOUBAN_API_KEY", "")


def validate_locale(locale: str) -> bool:
    """Check if a locale code is in the supported list."""
    return locale in SUPPORTED_LOCALES


def get_subtitle_dir(library_path: Path, book_folder: str) -> Path:
    """Return the subtitles directory for a book."""
    return library_path / book_folder / "subtitles"


def get_translated_audio_dir(library_path: Path, book_folder: str) -> Path:
    """Return the translated audio directory for a book."""
    return library_path / book_folder / "translated"
