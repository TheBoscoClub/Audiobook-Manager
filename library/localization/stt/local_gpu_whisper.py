"""Local GPU Whisper STT provider.

Calls the host's whisper-gpu.service over HTTP. The service runs on the
host where the AMD Radeon GPU lives; this provider can run anywhere
(VM, container, or host) as long as it can reach the service endpoint.

Architecturally identical to the RunPod/Vast.ai providers but with zero
latency and no billing — the GPU is local.
"""

import logging
from pathlib import Path

import requests

from .base import STTProvider, Transcript, WordTimestamp

logger = logging.getLogger(__name__)

WHISPER_LANGUAGES = {
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr",
    "pl", "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi",
    "he", "uk", "el", "ms", "cs", "ro", "da", "hu", "ta", "no",
    "th", "ur", "hr", "bg", "lt", "la", "mi", "ml", "cy", "sk",
    "te", "fa", "lv", "bn", "sr", "az", "sl", "kn", "et", "mk",
}


class LocalGPUWhisperSTT(STTProvider):
    """Local GPU Whisper via the host's whisper-gpu service."""

    def __init__(self, host: str = "192.168.122.1", port: int = 8765):
        self._base_url = f"http://{host}:{port}"

    @property
    def name(self) -> str:
        return "local-gpu-whisper"

    def supports_language(self, language: str) -> bool:
        return language.lower().split("-")[0] in WHISPER_LANGUAGES

    def usage_remaining(self) -> int | None:
        return None

    def is_available(self) -> bool:
        """Check if the whisper-gpu service is running and reachable."""
        try:
            resp = requests.get(f"{self._base_url}/health", timeout=3)
            return resp.ok and resp.json().get("status") == "ok"
        except (requests.ConnectionError, requests.Timeout):
            return False

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(
            "Transcribing %s via local GPU Whisper (lang=%s)",
            audio_path.name, language,
        )

        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{self._base_url}/transcribe",
                files={"file": (audio_path.name, f)},
                data={"language": language},
                timeout=1800,
            )

        resp.raise_for_status()
        result = resp.json()

        if "error" in result:
            raise RuntimeError(f"Whisper GPU service error: {result['error']}")

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

        logger.info(
            "Local GPU transcription complete: %d words, %.1fs (wall: %.1fs)",
            len(words), duration_ms / 1000,
            result.get("elapsed_seconds", 0),
        )

        return Transcript(
            words=words,
            language=result.get("language", language),
            provider="local-gpu-whisper-large-v3",
            duration_ms=duration_ms,
        )
