"""Local Whisper STT provider using faster-whisper.

Runs Whisper locally on CPU or GPU without any API keys.
Uses CTranslate2 for optimized inference (~4x faster than OpenAI whisper).

Install: pip install faster-whisper
"""

import logging
from pathlib import Path

from .base import STTProvider, Transcript, WordTimestamp

logger = logging.getLogger(__name__)

# Whisper supports 99 languages — same set as RunPod whisper_stt.py
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
}


class LocalWhisperSTT(STTProvider):
    """Local Whisper STT using faster-whisper (CTranslate2)."""

    is_local = True

    def __init__(self, model_size: str = "base", device: str = "auto"):
        """Initialize the local Whisper provider.

        Args:
            model_size: Whisper model size — "tiny", "base", "small",
                "medium", or "large-v3". Larger = more accurate but slower.
            device: "cpu", "cuda", or "auto" (auto-detects GPU).
        """
        self._model_size = model_size
        self._device = device
        self._model = None

    def _get_model(self):
        """Lazy-load the model on first use."""
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise RuntimeError(
                    "faster-whisper is not installed. "
                    "Install with: pip install faster-whisper"
                )

            device = self._device
            compute_type = "int8"
            if device == "auto":
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            if device == "cuda":
                compute_type = "float16"

            logger.info(
                "Loading Whisper model '%s' on %s (compute: %s)",
                self._model_size,
                device,
                compute_type,
            )
            self._model = WhisperModel(
                self._model_size, device=device, compute_type=compute_type
            )
        return self._model

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Transcribe an audio file using local Whisper."""
        model = self._get_model()

        logger.info("Transcribing %s (language=%s)", audio_path.name, language)
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
        )

        words = []
        for segment in segments:
            if segment.words:
                for w in segment.words:
                    words.append(
                        WordTimestamp(
                            word=w.word.strip(),
                            start_ms=int(w.start * 1000),
                            end_ms=int(w.end * 1000),
                        )
                    )

        duration_ms = int(info.duration * 1000) if info.duration else 0
        if words and not duration_ms:
            duration_ms = words[-1].end_ms

        logger.info(
            "Transcription complete: %d words, %.1f seconds",
            len(words),
            duration_ms / 1000,
        )

        return Transcript(
            words=words,
            language=language,
            provider=f"local-whisper-{self._model_size}",
            duration_ms=duration_ms,
        )

    def supports_language(self, language: str) -> bool:
        return language in WHISPER_LANGUAGES

    def usage_remaining(self) -> int | None:
        return None  # Local — unlimited

    @property
    def name(self) -> str:
        return f"local-whisper-{self._model_size}"
