"""End-to-end localization pipeline orchestrator.

Coordinates STT → Translation → VTT subtitle generation for audiobook
chapters. Provider selection is workload-aware: short/interactive work
prefers local providers (no cold-start, no billing minimums), while
long-form work (chapters, full books) prefers remote GPU for throughput.
Runtime network errors fall back to local once per request via the
shared :mod:`library.localization.fallback` helper.
"""

import logging
from pathlib import Path

from .config import (
    DEEPL_API_KEY,
    RUNPOD_API_KEY,
    RUNPOD_WHISPER_ENDPOINT,
    STT_PROVIDER,
    VASTAI_WHISPER_HOST,
    VASTAI_WHISPER_PORT,
    WHISPER_GPU_HOST,
    WHISPER_GPU_PORT,
)
from .fallback import with_local_fallback
from .selection import WorkloadHint
from .stt.base import STTProvider, Transcript
from .stt.local_gpu_whisper import LocalGPUWhisperSTT
from .stt.local_whisper import LocalWhisperSTT
from .stt.vastai_whisper import VastaiWhisperSTT
from .stt.whisper_stt import WhisperSTT
from .subtitles.sync import align_translations
from .subtitles.vtt_generator import generate_vtt

logger = logging.getLogger(__name__)


def _transcribe_with_fallback(
    provider: STTProvider, audio_path: Path, source_lang: str
) -> Transcript:
    """Transcribe via ``provider``; on network failure, retry once locally."""
    return with_local_fallback(
        kind="STT",
        provider_name=provider.name,
        is_local=provider.is_local,
        remote_call=lambda: provider.transcribe(audio_path, language=source_lang),
        local_call=lambda: LocalWhisperSTT().transcribe(audio_path, language=source_lang),
    )


def _remote_stt_candidates() -> list[STTProvider]:
    """Return configured remote/network STT providers in preferred order.

    Local GPU is first: zero latency, no billing, uses the host's AMD
    Radeon with ROCm. Only included if the whisper-gpu service is
    reachable (avoids blocking on a downed service).

    RunPod is next (serverless, scales to zero). Vast.ai last (requires
    a pinned instance).
    """
    providers: list[STTProvider] = []
    if WHISPER_GPU_HOST:
        gpu_provider = LocalGPUWhisperSTT(WHISPER_GPU_HOST, WHISPER_GPU_PORT)
        if gpu_provider.is_available():
            providers.append(gpu_provider)
        else:
            logger.debug("Local GPU Whisper service not reachable at %s:%d",
                         WHISPER_GPU_HOST, WHISPER_GPU_PORT)
    if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
        providers.append(WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT))
    if VASTAI_WHISPER_HOST:
        providers.append(VastaiWhisperSTT(VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT))
    return providers


def get_stt_provider(
    provider_name: str = "",
    workload: WorkloadHint = WorkloadHint.ANY,
) -> STTProvider:
    """Pick an STT provider based on configuration and workload shape.

    Explicit overrides (``provider_name`` or the ``STT_PROVIDER`` env var)
    always win. In auto mode, selection is workload-aware:

    - ``SHORT_CLIP`` → local first (no cold-start, no per-minute billing)
    - ``LONG_FORM`` → remote GPU first (RunPod serverless → Vast.ai)
    - ``ANY`` → remote if configured, else local

    DeepL STT is intentionally NOT in the auto chain: its transcribe
    endpoint rejects payloads above ~100 MB, and audiobooks are routinely
    200–500 MB. Callers who need it must opt in via ``provider_name="deepl"``.

    Args:
        provider_name: Override — ``"local"``, ``"whisper"`` (RunPod),
            ``"vastai"``, ``"deepl"``, or empty for auto mode.
        workload: Hint describing the work shape. Defaults to ``ANY``.

    Returns:
        An initialized STT provider instance.
    """
    name = (provider_name or STT_PROVIDER or "").lower()

    if name == "local":
        return LocalWhisperSTT()
    if name == "local-gpu":
        return LocalGPUWhisperSTT(WHISPER_GPU_HOST, WHISPER_GPU_PORT)
    if name == "whisper":
        if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
            return WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT)
        logger.warning("RunPod Whisper requested but not configured — using local")
        return LocalWhisperSTT()
    if name == "vastai":
        if VASTAI_WHISPER_HOST:
            return VastaiWhisperSTT(VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT)
        logger.warning("Vast.ai Whisper requested but not configured — using local")
        return LocalWhisperSTT()
    if name == "deepl":
        from .stt.deepl_stt import DeepLSTT
        return DeepLSTT(DEEPL_API_KEY)

    # Auto mode: workload-aware ordering.
    remote = _remote_stt_candidates()

    if workload is WorkloadHint.SHORT_CLIP or not remote:
        if not remote:
            logger.info("No remote STT configured — using local Whisper")
        return LocalWhisperSTT()

    # LONG_FORM or ANY with remote available → prefer the first remote.
    chosen = remote[0]
    logger.info("Auto STT: selected %s (workload=%s)", chosen.name, workload.value)
    return chosen


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

    # Step 1: Transcribe audio (with one-shot local fallback on network errors)
    logger.info("Step 1/3: Transcribing %s via %s", audio_path.name, provider.name)
    transcript = _transcribe_with_fallback(provider, audio_path, source_lang)

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
