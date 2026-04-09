"""End-to-end localization pipeline orchestrator.

Coordinates STT → Translation → VTT subtitle generation and
Translation → TTS audio generation for audiobook chapters.
"""

import logging
from pathlib import Path

from .config import STT_PROVIDER, DEEPL_API_KEY, RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT
from .stt.base import STTProvider
from .stt.deepl_stt import DeepLSTT
from .stt.whisper_stt import WhisperSTT
from .subtitles.sync import align_translations
from .subtitles.vtt_generator import generate_vtt
from .translation.deepl_translate import DeepLTranslator

logger = logging.getLogger(__name__)

# Minimum remaining minutes before switching from DeepL to Whisper
STT_MIN_REMAINING = 60


def get_stt_provider(provider_name: str = "") -> STTProvider:
    """Create an STT provider based on configuration.

    Args:
        provider_name: Override the configured provider ("deepl", "whisper", or "auto").

    Returns:
        An initialized STT provider instance.
    """
    name = provider_name or STT_PROVIDER

    if name == "whisper":
        return WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT)

    if name == "deepl":
        return DeepLSTT(DEEPL_API_KEY)

    # Auto mode: prefer DeepL if usage allows, else Whisper
    if DEEPL_API_KEY:
        deepl = DeepLSTT(DEEPL_API_KEY)
        remaining = deepl.usage_remaining()
        if remaining is None or remaining > STT_MIN_REMAINING:
            return deepl
        logger.info("DeepL STT has %d min remaining — routing to Whisper", remaining)

    if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
        return WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT)

    raise RuntimeError("No STT provider available — configure DEEPL or RUNPOD API keys")


def generate_subtitles(
    audio_path: Path,
    output_dir: Path,
    target_locale: str,
    source_lang: str = "en",
    chapter_name: str = "",
    stt_provider: STTProvider | None = None,
) -> tuple[Path, Path]:
    """Generate dual-language subtitles for a single audio file.

    Pipeline: STT → sentence detection → translation → VTT generation.

    Args:
        audio_path: Path to the source audio file.
        output_dir: Directory to write VTT files to.
        target_locale: Target translation locale (e.g., "zh-Hans").
        source_lang: Source audio language (default "en").
        chapter_name: Base name for output files (defaults to audio filename stem).
        stt_provider: STT provider to use (auto-selected if None).

    Returns:
        Tuple of (source_vtt_path, translated_vtt_path).
    """
    if not chapter_name:
        chapter_name = audio_path.stem

    provider = stt_provider or get_stt_provider()

    # Step 1: Transcribe audio
    logger.info("Step 1/3: Transcribing %s via %s", audio_path.name, provider.name)
    transcript = provider.transcribe(audio_path, language=source_lang)

    # Step 2: Translate sentences
    logger.info("Step 2/3: Translating %d sentences to %s", len(transcript.sentences()), target_locale)
    translator = DeepLTranslator(DEEPL_API_KEY)
    source_sentences = transcript.sentence_texts()
    translated_sentences = translator.translate(source_sentences, target_locale, source_lang.upper())

    # Step 3: Align and generate VTT files
    logger.info("Step 3/3: Generating VTT files")
    source_cues, translated_cues = align_translations(transcript, translated_sentences)

    source_vtt = generate_vtt(source_cues, output_dir / f"{chapter_name}.{source_lang}.vtt")
    translated_vtt = generate_vtt(translated_cues, output_dir / f"{chapter_name}.{target_locale}.vtt")

    logger.info("Subtitles generated: %s, %s", source_vtt.name, translated_vtt.name)
    return source_vtt, translated_vtt
