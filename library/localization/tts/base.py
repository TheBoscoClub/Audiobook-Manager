"""Abstract base class for text-to-speech providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Voice:
    """A TTS voice descriptor."""
    id: str
    name: str
    language: str
    gender: str  # "female" or "male"


class TTSProvider(ABC):
    """Abstract text-to-speech provider."""

    #: True for providers that do not require network I/O to a GPU host.
    #: edge-tts still hits Microsoft's cloud but is effectively "local"
    #: from the audiobooks-host perspective — it has no cold-start, no
    #: per-minute billing, and is the always-available fallback.
    is_local: bool = False

    @abstractmethod
    def synthesize(self, text: str, language: str, voice: str, output_path: Path) -> Path:
        """Generate audio file from text. Returns path to the generated file."""
        ...

    @abstractmethod
    def available_voices(self, language: str) -> list[Voice]:
        """List available voices for a language."""
        ...

    @abstractmethod
    def requires_gpu(self) -> bool:
        """Whether this provider requires a GPU."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string."""
        ...
