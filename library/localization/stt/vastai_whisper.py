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
        # Support full URLs (e.g., RunPod proxy: https://pod-8000.proxy.runpod.net)
        if host.startswith("http://") or host.startswith("https://"):
            self._base_url = host.rstrip("/")
        else:
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
        result = self._call_transcribe_api(audio_path, language)

        words = _parse_word_timestamps(result)
        duration_ms = _extract_duration_ms(result, words)
        return Transcript(
            words=words,
            language=result.get("language", language),
            provider="vastai-whisper-large-v3",
            duration_ms=duration_ms,
        )

    def _call_transcribe_api(self, audio_path: Path, language: str) -> dict:
        """POST the audio file to the Whisper server and return its JSON."""
        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{self._base_url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f)},
                data={"language": language},
                timeout=(30, 300),  # (connect, read) — fail fast on dead tunnels
            )
        resp.raise_for_status()
        return resp.json()


def _extract_raw_words(result: dict) -> list[dict]:
    """Return flat list of word dicts, handling both top-level and nested shapes.

    faster-whisper / Vast.ai instances return top-level "words"; whisper.cpp
    servers return nested "segments[].words[]".
    """
    raw_words = result.get("words") or []
    if raw_words:
        return raw_words
    nested: list[dict] = []
    for seg in result.get("segments", []):
        nested.extend(seg.get("words", []))
    return nested


def _parse_word_timestamps(result: dict) -> list[WordTimestamp]:
    """Convert raw word dicts into WordTimestamp objects, dropping empties."""
    words: list[WordTimestamp] = []
    for w in _extract_raw_words(result):
        text = (w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        words.append(
            WordTimestamp(
                word=text,
                start_ms=int(float(w.get("start", 0)) * 1000),
                end_ms=int(float(w.get("end", 0)) * 1000),
            )
        )
    return words


def _extract_duration_ms(result: dict, words: list[WordTimestamp]) -> int:
    """Derive duration_ms, falling back to the last word's end_ms."""
    duration_ms = int(float(result.get("duration", 0)) * 1000)
    if not duration_ms and words:
        duration_ms = words[-1].end_ms
    return duration_ms
