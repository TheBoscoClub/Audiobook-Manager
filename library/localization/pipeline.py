"""End-to-end localization pipeline orchestrator.

Coordinates STT → Translation → VTT subtitle generation and
Translation → TTS audio generation for audiobook chapters.
"""

import logging
from pathlib import Path

from .config import (
    STT_PROVIDER, DEEPL_API_KEY, RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT,
    VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT,
)
from .stt.base import STTProvider
from .stt.deepl_stt import DeepLSTT
from .stt.local_whisper import LocalWhisperSTT
from .stt.vastai_whisper import VastaiWhisperSTT
from .stt.whisper_stt import WhisperSTT
from .subtitles.sync import align_translations
from .subtitles.vtt_generator import generate_vtt

logger = logging.getLogger(__name__)

# Minimum remaining minutes before switching from DeepL to Whisper
STT_MIN_REMAINING = 60


def get_stt_provider(provider_name: str = "") -> STTProvider:
    """Create an STT provider based on configuration.

    Provider priority (auto mode):
        1. Vast.ai Whisper (if host configured — direct GPU instance)
        2. RunPod Whisper (if API key and endpoint set — serverless)
        3. Local Whisper via faster-whisper (always available as fallback)
        4. DeepL STT — last resort only; audiobooks typically exceed its
           upload size limit (HTTP 413), so it is not useful for full books

    Args:
        provider_name: Override — "deepl", "whisper", "vastai", "local", or "auto".

    Returns:
        An initialized STT provider instance.
    """
    name = provider_name or STT_PROVIDER

    if name == "local":
        return LocalWhisperSTT()

    if name == "vastai":
        if VASTAI_WHISPER_HOST:
            return VastaiWhisperSTT(VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT)
        logger.warning("Vast.ai Whisper requested but not configured — falling back to local")
        return LocalWhisperSTT()

    if name == "whisper":
        if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
            return WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT)
        logger.warning("RunPod Whisper requested but not configured — falling back to local")
        return LocalWhisperSTT()

    if name == "deepl":
        return DeepLSTT(DEEPL_API_KEY)

    # Auto mode: prefer Whisper providers (no upload-size limit), DeepL last.
    # DeepL's transcribe endpoint rejects payloads above ~100 MB, and full
    # audiobooks are routinely 200-500 MB, so DeepL is almost never viable
    # for this use case — it's kept only as a last resort.
    if VASTAI_WHISPER_HOST:
        return VastaiWhisperSTT(VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT)

    if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
        return WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT)

    if DEEPL_API_KEY:
        deepl = DeepLSTT(DEEPL_API_KEY)
        remaining = deepl.usage_remaining()
        if remaining is None or remaining > STT_MIN_REMAINING:
            logger.warning(
                "Falling back to DeepL STT — may fail with 413 on audiobooks >100MB"
            )
            return deepl

    # Final fallback: local Whisper (no API keys needed)
    logger.info("No cloud STT provider configured — using local Whisper")
    return LocalWhisperSTT()


def generate_subtitles(
    audio_path: Path,
    output_dir: Path,
    target_locale: str,
    source_lang: str = "en",
    chapter_name: str = "",
    stt_provider: STTProvider | None = None,
) -> tuple[Path, Path | None]:
    """Generate subtitles for a single audio file.

    Pipeline: STT → sentence detection → (translation if API key) → VTT.

    If a DeepL API key is configured, generates dual-language VTTs
    (source + translated). Without a key, generates source-language
    subtitles only.

    Args:
        audio_path: Path to the source audio file.
        output_dir: Directory to write VTT files to.
        target_locale: Target translation locale (e.g., "zh-Hans").
        source_lang: Source audio language (default "en").
        chapter_name: Base name for output files (defaults to audio filename stem).
        stt_provider: STT provider to use (auto-selected if None).

    Returns:
        Tuple of (source_vtt_path, translated_vtt_path_or_None).
    """
    if not chapter_name:
        chapter_name = audio_path.stem

    provider = stt_provider or get_stt_provider()

    # Step 1: Transcribe audio
    logger.info("Step 1/3: Transcribing %s via %s", audio_path.name, provider.name)
    transcript = provider.transcribe(audio_path, language=source_lang)

    source_sentences = transcript.sentence_texts()
    if not source_sentences:
        raise ValueError(f"No speech detected in {audio_path.name}")

    # Step 2: Translate sentences (if DeepL key available and target != source)
    translated_vtt = None
    if DEEPL_API_KEY and target_locale != source_lang:
        logger.info(
            "Step 2/3: Translating %d sentences to %s",
            len(source_sentences), target_locale,
        )
        from .translation.deepl_translate import DeepLTranslator
        translator = DeepLTranslator(DEEPL_API_KEY)
        translated_sentences = translator.translate(
            source_sentences, target_locale, source_lang.upper()
        )

        # Align and generate both VTTs
        logger.info("Step 3/3: Generating dual-language VTT files")
        source_cues, translated_cues = align_translations(
            transcript, translated_sentences
        )
        translated_vtt = generate_vtt(
            translated_cues,
            output_dir / f"{chapter_name}.{target_locale}.vtt",
        )
    else:
        if not DEEPL_API_KEY:
            logger.info("Step 2/3: Skipping translation (no DeepL API key)")
        logger.info("Step 3/3: Generating source-language VTT file")
        source_cues, _ = align_translations(
            transcript,
            source_sentences,
        )

    source_vtt = generate_vtt(
        source_cues, output_dir / f"{chapter_name}.{source_lang}.vtt"
    )

    logger.info("Subtitles generated: %s%s",
                source_vtt.name,
                f", {translated_vtt.name}" if translated_vtt else "")
    return source_vtt, translated_vtt
