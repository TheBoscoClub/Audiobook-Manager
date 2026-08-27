"""RunPod Whisper large-v3 speech-to-text provider (fallback STT backend)."""

import logging
import time
from pathlib import Path

import requests

from .base import STTProvider, Transcript, WordTimestamp

logger = logging.getLogger(__name__)

# Whisper large-v3 supports 99 languages
WHISPER_LANGUAGES = {
    "en",
    "zh",
    "de",
    "es",
    "ru",
    "ko",
    "fr",
    "ja",
    "pt",
    "tr",
    "pl",
    "ca",
    "nl",
    "ar",
    "sv",
    "it",
    "id",
    "hi",
    "fi",
    "vi",
    "he",
    "uk",
    "el",
    "ms",
    "cs",
    "ro",
    "da",
    "hu",
    "ta",
    "no",
    "th",
    "ur",
    "hr",
    "bg",
    "lt",
    "la",
    "mi",
    "ml",
    "cy",
    "sk",
    "te",
    "fa",
    "lv",
    "bn",
    "sr",
    "az",
    "sl",
    "kn",
    "et",
    "mk",
    "br",
    "eu",
    "is",
    "hy",
    "ne",
    "mn",
    "bs",
    "kk",
    "sq",
    "sw",
    "gl",
    "mr",
    "pa",
    "si",
    "km",
    "sn",
    "yo",
    "so",
    "af",
    "oc",
    "ka",
    "be",
    "tg",
    "sd",
    "gu",
    "am",
    "yi",
    "lo",
    "uz",
    "fo",
    "ht",
    "ps",
    "tk",
    "nn",
    "mt",
    "sa",
    "lb",
    "my",
    "bo",
    "tl",
    "mg",
    "as",
    "tt",
    "haw",
    "ln",
    "ha",
    "ba",
    "jw",
    "su",
}

RUNPOD_API_URL = "https://api.runpod.ai/v2"


class WhisperSTT(STTProvider):
    """RunPod-hosted Whisper large-v3 for speech-to-text."""

    def __init__(self, api_key: str, endpoint_id: str):
        if not api_key:
            raise ValueError("RunPod API key is required")
        if not endpoint_id:
            raise ValueError("RunPod Whisper endpoint ID is required")
        self._api_key = api_key
        self._endpoint_id = endpoint_id

    @property
    def name(self) -> str:
        return "whisper"

    def supports_language(self, language: str) -> bool:
        return language.lower().split("-")[0] in WHISPER_LANGUAGES

    def usage_remaining(self) -> int | None:
        """RunPod is pay-per-use — no monthly cap."""
        return None

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Transcribe audio via RunPod Whisper serverless endpoint."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.supports_language(language):
            raise ValueError(f"Language '{language}' not supported by Whisper")

        logger.info("Transcribing %s via RunPod Whisper (lang=%s)", audio_path.name, language)

        import base64

        audio_b64 = base64.b64encode(audio_path.read_bytes()).decode()

        # Submit async job to RunPod
        run_url = f"{RUNPOD_API_URL}/{self._endpoint_id}/run"
        resp = requests.post(
            run_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "input": {
                    "audio_base64": audio_b64,
                    "language": language,
                    "word_timestamps": True,
                    "model": "large-v3",
                }
            },
            timeout=30,
        )
        resp.raise_for_status()
        job_id = resp.json()["id"]

        # Poll for completion
        status_url = f"{RUNPOD_API_URL}/{self._endpoint_id}/status/{job_id}"
        result = self._poll_job(status_url)

        words = []
        # RunPod worker-faster_whisper returns word timestamps as a flat
        # top-level array, not nested inside segments.
        word_list = result.get("word_timestamps", [])
        if not word_list:
            # Fallback: some workers nest words inside segments
            for segment in result.get("segments", []):
                word_list.extend(segment.get("words", []))

        for word_data in word_list:
            words.append(
                WordTimestamp(
                    word=word_data.get("word", "").strip(),
                    start_ms=int(word_data.get("start", 0) * 1000),
                    end_ms=int(word_data.get("end", 0) * 1000),
                )
            )

        # Duration from response or estimate from last word
        duration_ms = int(result.get("duration", 0) * 1000)
        if not duration_ms and words:
            duration_ms = words[-1].end_ms

        return Transcript(
            words=words, language=language, provider="whisper-large-v3", duration_ms=duration_ms
        )

    def _poll_job(self, status_url: str, max_wait: int = 600) -> dict:
        """Poll a RunPod job until completion or timeout."""
        start = time.monotonic()
        poll_interval: float = 2.0

        while time.monotonic() - start < max_wait:
            resp = requests.get(
                status_url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            if status == "COMPLETED":
                return data.get("output", {})
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"RunPod job {status}: {data.get('error', 'unknown')}")

            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 10)

        raise TimeoutError(f"RunPod job did not complete within {max_wait}s")
