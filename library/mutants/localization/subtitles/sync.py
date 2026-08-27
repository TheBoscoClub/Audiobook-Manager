"""Align translated text to original audio timestamps.

Chinese translations are often shorter than English source text.
Each translated subtitle cue inherits the start/end time of its
source sentence, keeping subtitles synchronized with the original
audio narration.
"""

from ..stt.base import Transcript
from .vtt_generator import VTTCue


def align_translations(
    transcript: Transcript, translated_sentences: list[str], max_pause_ms: int = 800
) -> tuple[list[VTTCue], list[VTTCue]]:
    """Create aligned source and translated VTT cues from a transcript.

    Args:
        transcript: Word-level transcript from STT provider.
        translated_sentences: Translated text for each sentence
            (must match the number of sentences in the transcript).
        max_pause_ms: Pause threshold for sentence boundary detection.

    Returns:
        Tuple of (source_cues, translated_cues) with matching timestamps.
    """
    sentences = transcript.sentences(max_pause_ms)

    if len(sentences) != len(translated_sentences):
        raise ValueError(
            f"Sentence count mismatch: {len(sentences)} in transcript "
            f"vs {len(translated_sentences)} translations"
        )

    source_cues = []
    translated_cues = []

    for words, translated_text in zip(sentences, translated_sentences):
        start_ms = words[0].start_ms
        end_ms = words[-1].end_ms
        source_text = " ".join(w.word for w in words)

        source_cues.append(VTTCue(start_ms=start_ms, end_ms=end_ms, text=source_text))
        translated_cues.append(VTTCue(start_ms=start_ms, end_ms=end_ms, text=translated_text))

    return source_cues, translated_cues
