"""DeepL speech-to-text provider (primary STT backend)."""

import logging
from pathlib import Path

import requests

from .base import STTProvider, Transcript, WordTimestamp

logger = logging.getLogger(__name__)

# DeepL STT supports these languages
DEEPL_STT_LANGUAGES = {
    "en",
    "de",
    "fr",
    "es",
    "it",
    "pt",
    "nl",
    "pl",
    "ru",
    "ja",
    "zh",
    "ko",
    "ar",
    "bg",
    "cs",
    "da",
    "el",
    "et",
    "fi",
    "hu",
    "id",
    "lt",
    "lv",
    "nb",
    "ro",
    "sk",
    "sl",
    "sv",
    "tr",
    "uk",
}

DEEPL_API_URL = "https://api.deepl.com/v2"
DEEPL_FREE_API_URL = "https://api-free.deepl.com/v2"


class DeepLSTT(STTProvider):
    """DeepL speech-to-text using their transcription API."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("DeepL API key is required")
        self._api_key = api_key
        self._base_url = DEEPL_FREE_API_URL if api_key.endswith(":fx") else DEEPL_API_URL

    @property
    def name(self) -> str:
        return "deepl"

    def supports_language(self, language: str) -> bool:
        return language.lower().split("-")[0] in DEEPL_STT_LANGUAGES

    def usage_remaining(self) -> int | None:
        """Check remaining STT minutes via DeepL usage API."""
        try:
            resp = requests.get(
                f"{self._base_url}/usage",
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            character_count = data.get("character_count", 0)
            character_limit = data.get("character_limit", 0)
            # Approximate: 1 minute of audio ~ 150 words ~ 750 characters
            remaining_chars = character_limit - character_count
            return max(0, remaining_chars // 750)
        except Exception:
            logger.warning("Failed to check DeepL usage")
            return None

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Transcribe audio file via DeepL STT API."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.supports_language(language):
            raise ValueError(f"Language '{language}' not supported by DeepL STT")

        logger.info("Transcribing %s via DeepL STT (lang=%s)", audio_path.name, language)

        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{self._base_url}/transcribe",
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                files={"file": (audio_path.name, f, "audio/opus")},
                data={"source_lang": language.upper()},
                timeout=300,
            )
        resp.raise_for_status()
        result = resp.json()

        words = []
        for segment in result.get("segments", []):
            for word_data in segment.get("words", []):
                words.append(
                    WordTimestamp(
                        word=word_data["word"],
                        start_ms=int(word_data["start"] * 1000),
                        end_ms=int(word_data["end"] * 1000),
                    )
                )

        return Transcript(
            words=words,
            language=language,
            provider="deepl",
            duration_ms=int(result.get("duration", 0) * 1000),
        )
