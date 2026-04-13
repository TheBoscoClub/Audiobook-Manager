"""Vast.ai XTTS v2 HTTP client — GPU-backed voice cloning via a self-hosted server.

The server is a thin FastAPI/Flask wrapper around Coqui XTTS v2 running on a
Vast.ai GPU instance. It exposes a single endpoint:

    POST /synthesize  {"text": "...", "language": "zh", "voice": "clone"}
         → audio/wav body

Configure via:
    AUDIOBOOKS_TTS_PROVIDER=xtts-vastai
    AUDIOBOOKS_VASTAI_XTTS_HOST=<host>
    AUDIOBOOKS_VASTAI_XTTS_PORT=8020
"""

import logging
from pathlib import Path

import requests

from .base import TTSProvider, Voice

logger = logging.getLogger(__name__)


class VastaiXTTSProvider(TTSProvider):
    """Self-hosted XTTS v2 on Vast.ai — same architecture as Vast Whisper STT."""

    def __init__(self, host: str, port: int = 8020):
        if not host:
            raise ValueError("Vast.ai XTTS host is required")
        self._base_url = f"http://{host}:{port}"

    @property
    def name(self) -> str:
        return "xtts-vastai"

    def requires_gpu(self) -> bool:
        return True

    def available_voices(self, language: str) -> list[Voice]:
        return [
            Voice(id="clone", name="Voice Clone", language=language, gender="neutral")
        ]

    def synthesize(
        self, text: str, language: str, voice: str, output_path: Path
    ) -> Path:
        """POST text to the Vast.ai XTTS server and write the returned audio."""
        lang_prefix = language.split("-")[0].lower()
        logger.info(
            "Synthesizing %d chars via Vast.ai XTTS (lang=%s)", len(text), lang_prefix
        )

        resp = requests.post(
            f"{self._base_url}/synthesize",
            json={"text": text, "language": lang_prefix, "voice": voice or "clone"},
            timeout=600,
        )
        resp.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        return output_path
