"""End-to-end localization pipeline orchestrator.

Coordinates STT → Translation → VTT subtitle generation for audiobook
chapters. Provider selection is workload-aware and GPU-only: remote GPU
providers (Vast.ai, RunPod, local GPU service) handle all STT work.
There is no CPU fallback — if no GPU is reachable, the worker fails
loudly so fleet monitoring can detect and restart it.

Full-book generation splits the audio into chapters (via embedded
metadata or Audible sidecar) and transcribes each individually,
reporting progress between chapters so the frontend can show
"Chapter 3/42" style updates.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from .chapters import extract_chapters, split_chapter
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
from .stt.vastai_whisper import VastaiWhisperSTT
from .stt.whisper_stt import WhisperSTT
from .subtitles.sync import align_translations
from .subtitles.vtt_generator import VTTCue, generate_vtt

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


def _transcribe_with_fallback(
    provider: STTProvider, audio_path: Path, source_lang: str
) -> Transcript:
    """Transcribe via ``provider`` with retries; raises on exhausted retries."""
    return with_local_fallback(
        kind="STT",
        provider_name=provider.name,
        is_local=provider.is_local,
        remote_call=lambda: provider.transcribe(audio_path, language=source_lang),
    )


def _remote_stt_candidates() -> list[STTProvider]:
    """Return configured remote/network STT providers in preferred order.

    Vast.ai is first (dedicated instance, reliable throughput). RunPod
    is next (serverless, scales to zero — but frequently resource-constrained).
    Local GPU last: uses the host's AMD Radeon with ROCm but risks system
    instability under heavy Whisper loads.
    """
    providers: list[STTProvider] = []
    if VASTAI_WHISPER_HOST:
        providers.append(VastaiWhisperSTT(VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT))
    if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
        providers.append(WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT))
    if WHISPER_GPU_HOST:
        gpu_provider = LocalGPUWhisperSTT(WHISPER_GPU_HOST, WHISPER_GPU_PORT)
        if gpu_provider.is_available():
            providers.append(gpu_provider)
        else:
            logger.debug(
                "Local GPU Whisper service not reachable at %s:%d",
                WHISPER_GPU_HOST,
                WHISPER_GPU_PORT,
            )
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
        raise ValueError(
            "Local CPU Whisper has been removed. Use a GPU provider "
            "(vastai, whisper/runpod, local-gpu) or auto mode."
        )
    if name == "local-gpu":
        return LocalGPUWhisperSTT(WHISPER_GPU_HOST, WHISPER_GPU_PORT)
    if name == "whisper":
        if RUNPOD_API_KEY and RUNPOD_WHISPER_ENDPOINT:
            return WhisperSTT(RUNPOD_API_KEY, RUNPOD_WHISPER_ENDPOINT)
        raise ValueError(
            "RunPod Whisper requested but AUDIOBOOKS_RUNPOD_API_KEY / "
            "AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT not configured"
        )
    if name == "vastai":
        if VASTAI_WHISPER_HOST:
            return VastaiWhisperSTT(VASTAI_WHISPER_HOST, VASTAI_WHISPER_PORT)
        raise ValueError(
            "Vast.ai Whisper requested but AUDIOBOOKS_VASTAI_WHISPER_HOST "
            "not configured"
        )
    if name == "deepl":
        from .stt.deepl_stt import DeepLSTT

        return DeepLSTT(DEEPL_API_KEY)

    # Auto mode: workload-aware ordering — GPU only.
    remote = _remote_stt_candidates()
    if not remote:
        raise RuntimeError(
            "No STT provider configured. Set AUDIOBOOKS_VASTAI_WHISPER_HOST, "
            "AUDIOBOOKS_RUNPOD_API_KEY+ENDPOINT, or AUDIOBOOKS_WHISPER_GPU_HOST."
        )

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
            len(source_sentences),
            target_locale,
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

    logger.info(
        "Subtitles generated: %s%s",
        source_vtt.name,
        f", {translated_vtt.name}" if translated_vtt else "",
    )
    return source_vtt, translated_vtt


def _offset_cues(cues: list[VTTCue], offset_ms: int) -> list[VTTCue]:
    """Shift all cue timestamps by offset_ms to align with full-book timeline."""
    return [
        VTTCue(
            start_ms=c.start_ms + offset_ms, end_ms=c.end_ms + offset_ms, text=c.text
        )
        for c in cues
    ]


ChapterCompleteCallback = Callable[[int, Path, Path | None], None]


def generate_book_subtitles(
    audio_path: Path,
    output_dir: Path,
    target_locale: str,
    source_lang: str = "en",
    stt_provider: STTProvider | None = None,
    on_progress: ProgressCallback | None = None,
    on_chapter_complete: ChapterCompleteCallback | None = None,
    skip_chapters: set[int] | None = None,
) -> list[tuple[int, Path, Path | None]]:
    """Generate subtitles for an audiobook, chapter by chapter.

    Splits the audio into chapters, transcribes each individually, and
    produces per-chapter VTT files. Fires ``on_chapter_complete`` after
    each chapter so callers can persist results incrementally (e.g.,
    write to DB so the player shows subtitles while later chapters are
    still transcribing).

    Args:
        audio_path: Path to the full audiobook file.
        output_dir: Directory for VTT output files.
        target_locale: Target translation locale (e.g., "zh-Hans").
        source_lang: Source audio language (default "en").
        stt_provider: STT provider (auto-selected if None).
        on_progress: Called with (chapter_index, total_chapters, chapter_title)
            before each chapter starts transcription.
        on_chapter_complete: Called with (chapter_index, source_vtt,
            translated_vtt_or_None) after each chapter's VTTs are written.
        skip_chapters: Set of chapter indices to skip (already generated).

    Returns:
        List of (chapter_index, source_vtt, translated_vtt_or_None) tuples.
        If no chapters are found, falls back to single-file processing and
        returns a single entry with chapter_index=0.
    """
    chapters = extract_chapters(audio_path)
    if not chapters:
        if skip_chapters and 0 in skip_chapters:
            logger.info("Single-file subtitles already exist — nothing to do")
            return []
        logger.info("No chapter data found — processing as single file")
        src, tr = generate_subtitles(
            audio_path,
            output_dir,
            target_locale,
            source_lang,
            stt_provider=stt_provider,
        )
        return [(0, src, tr)]

    provider = stt_provider or get_stt_provider(workload=WorkloadHint.LONG_FORM)
    total = len(chapters)
    results: list[tuple[int, Path, Path | None]] = []

    for chapter in chapters:
        if skip_chapters and chapter.index in skip_chapters:
            logger.info(
                "Chapter %d/%d: %s — already generated, skipping",
                chapter.index + 1,
                total,
                chapter.title,
            )
            continue

        if on_progress:
            on_progress(chapter.index, total, chapter.title)

        logger.info(
            "Chapter %d/%d: %s (%.1f min)",
            chapter.index + 1,
            total,
            chapter.title,
            chapter.duration_ms / 60_000,
        )

        chapter_file: Path | None = None
        try:
            chapter_file = split_chapter(audio_path, chapter)

            transcript = _transcribe_with_fallback(
                provider,
                chapter_file,
                source_lang,
            )
            source_sentences = transcript.sentence_texts()
            if not source_sentences:
                logger.warning(
                    "No speech in chapter %d (%s) — skipping",
                    chapter.index,
                    chapter.title,
                )
                continue

            source_cues, translated_cues = align_translations(
                transcript,
                source_sentences,
            )
            source_cues = _offset_cues(source_cues, chapter.start_ms)

            safe_title = "".join(
                c if c.isalnum() or c in "- _" else "_" for c in chapter.title
            ).strip("_")[:50]
            chapter_stem = f"ch{chapter.index:03d}_{safe_title}"

            source_vtt = generate_vtt(
                source_cues,
                output_dir / f"{chapter_stem}.{source_lang}.vtt",
            )

            translated_vtt = None
            if DEEPL_API_KEY and target_locale != source_lang:
                from .translation.deepl_translate import DeepLTranslator

                translator = DeepLTranslator(DEEPL_API_KEY)
                translated_texts = translator.translate(
                    source_sentences,
                    target_locale,
                    source_lang.upper(),
                )
                _, tr_cues = align_translations(transcript, translated_texts)
                tr_cues = _offset_cues(tr_cues, chapter.start_ms)
                translated_vtt = generate_vtt(
                    tr_cues,
                    output_dir / f"{chapter_stem}.{target_locale}.vtt",
                )

            results.append((chapter.index, source_vtt, translated_vtt))
            if on_chapter_complete:
                on_chapter_complete(chapter.index, source_vtt, translated_vtt)

        finally:
            if chapter_file and chapter_file.exists():
                chapter_file.unlink(missing_ok=True)

    logger.info(
        "Book subtitles complete: %d/%d chapters processed",
        len(results),
        total,
    )
    return results
