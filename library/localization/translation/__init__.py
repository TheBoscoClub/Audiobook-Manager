"""Machine translation: provider interface, factory, and translation memory.

No concrete provider currently ships — ``get_translation_provider()``
returns ``None`` and callers degrade to source-language output. See
``factory.py`` for the background and how a future backend registers.
"""

from .base import TranslationProvider, TranslationUnavailableError
from .factory import get_translation_provider, translation_provider_name

__all__ = [
    "TranslationProvider",
    "TranslationUnavailableError",
    "get_translation_provider",
    "translation_provider_name",
]
