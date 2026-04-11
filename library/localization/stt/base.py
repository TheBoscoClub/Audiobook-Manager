"""Abstract base class for speech-to-text providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WordTimestamp:
    """A single word with its start and end time in milliseconds."""
    word: str
    start_ms: int
    end_ms: int


@dataclass
class Transcript:
    """A complete transcript with word-level timestamps."""
    words: list[WordTimestamp] = field(default_factory=list)
    language: str = "en"
    provider: str = ""
    duration_ms: int = 0

    def sentences(self, max_pause_ms: int = 800) -> list[list[WordTimestamp]]:
        """Group words into sentences using punctuation and pause detection.

        A sentence boundary is detected when:
        - The current word ends with sentence-ending punctuation (.!?)
        - There's a pause longer than max_pause_ms between words
        """
        if not self.words:
            return []

        sentences = []
        current: list[WordTimestamp] = []

        for i, word in enumerate(self.words):
            current.append(word)

            is_sentence_end = word.word.rstrip().endswith((".", "!", "?"))
            has_long_pause = (
                i + 1 < len(self.words)
                and self.words[i + 1].start_ms - word.end_ms > max_pause_ms
            )

            if is_sentence_end or has_long_pause:
                sentences.append(current)
                current = []

        if current:
            sentences.append(current)

        return sentences

    def sentence_texts(self, max_pause_ms: int = 800) -> list[str]:
        """Return sentences as plain text strings."""
        return [
            " ".join(w.word for w in sent)
            for sent in self.sentences(max_pause_ms)
        ]


class STTProvider(ABC):
    """Abstract speech-to-text provider."""

    #: True for in-process providers (no network I/O). Runtime fallback
    #: helpers use this to decide whether a network error should be
    #: retried against the local provider or re-raised.
    is_local: bool = False

    @abstractmethod
    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        """Transcribe an audio file and return word-level timestamps."""
        ...

    @abstractmethod
    def supports_language(self, language: str) -> bool:
        """Check if this provider supports the given language."""
        ...

    @abstractmethod
    def usage_remaining(self) -> int | None:
        """Minutes remaining in the current billing period, or None if unlimited."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string."""
        ...
