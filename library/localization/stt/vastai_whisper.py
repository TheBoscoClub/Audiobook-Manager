"""Vast.ai hosted Whisper large-v3 speech-to-text provider."""

import logging
from pathlib import Path

import requests

from .base import STTProvider, Transcript, WordTimestamp
from .whisper_stt import WHISPER_LANGUAGES

logger = logging.getLogger(__name__)


class VastaiWhisperSTT(STTProvider):
    """Vast.ai instance running faster-whisper with OpenAI-compatible API."""

    def __init__(self, host: str, port: int = 8000):
        if not host:
            raise ValueError("Vast.ai Whisper host address is required")
        self._base_url = f"http://{host}:{port}"

    @property
    def name(self) -> str:
        return "vastai-whisper"

    def supports_language(self, language: str) -> bool:
        return language.lower().split("-")[0] in WHISPER_LANGUAGES

    def usage_remaining(self) -> int | None:
        """Vast.ai is pay-per-use — no monthly cap."""
        return None

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Transcribe audio via Vast.ai faster-whisper server."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.supports_language(language):
            raise ValueError(f"Language '{language}' not supported by Whisper")

        logger.info("Transcribing %s via Vast.ai Whisper (lang=%s)", audio_path.name, language)

        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{self._base_url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f)},
                data={"language": language},
                timeout=600,
            )
        resp.raise_for_status()
        result = resp.json()

        words = []
        for w in result.get("words", []):
            words.append(WordTimestamp(
                word=w.get("word", "").strip(),
                start_ms=int(w.get("start", 0) * 1000),
                end_ms=int(w.get("end", 0) * 1000),
            ))

        duration_ms = int(result.get("duration", 0) * 1000)
        if not duration_ms and words:
            duration_ms = words[-1].end_ms

        return Transcript(
            words=words,
            language=result.get("language", language),
            provider="vastai-whisper-large-v3",
            duration_ms=duration_ms,
        )
