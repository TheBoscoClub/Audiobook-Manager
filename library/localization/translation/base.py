"""Machine-translation provider interface.

Mirrors the STT (``stt/base.py``) and TTS (``tts/base.py``) provider
layers: an abstract contract here, concrete backends registered in
``factory.py``. No backend currently ships — see the factory module for
why, and what that means at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationUnavailableError(RuntimeError):
    """The translation backend could not be reached or refused the request.

    Raised only when the caller passes strict=True. Callers that PERSIST the
    result (subtitle files, DB rows) must be strict: silently storing the
    English source as though it were a translation is unrecoverable, because
    nothing downstream can tell it apart from a real one.
    """


class TranslationProvider(ABC):
    """Abstract machine-translation provider.

    Implementations translate batches of source-language strings into a
    target locale. Degradation is signalled, never silently passed through:
    a backend that returns source text unchanged must set :attr:`degraded`
    and honour ``strict=True`` by raising
    :class:`TranslationUnavailableError` instead.
    """

    #: True once any batch came back untranslated (backend down, refused, …).
    degraded: bool = False
    #: Count of texts returned untranslated across the provider's lifetime.
    degraded_texts: int = 0

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string — recorded as row provenance in the
        ``translator`` / ``translation_provider`` DB columns."""

    @abstractmethod
    def translate(
        self,
        texts: list[str],
        target_locale: str,
        source_lang: str = "EN",
        strict: bool = False,
    ) -> list[str]:
        """Translate a batch of texts to the target locale.

        Must return exactly ``len(texts)`` results, aligned to the input.
        ``strict=True`` — mandatory for any caller that writes the result to
        disk or to the database — makes backend failure raise
        :class:`TranslationUnavailableError` rather than returning source
        text.
        """

    def translate_one(
        self, text: str, target_locale: str, source_lang: str = "EN", strict: bool = False
    ) -> str:
        """Translate a single string."""
        results = self.translate([text], target_locale, source_lang, strict=strict)
        return results[0] if results else text
