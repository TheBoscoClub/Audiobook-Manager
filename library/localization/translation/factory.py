"""Machine-translation provider selection.

There is currently NO machine-translation backend. The previous hosted-API
integration was removed outright on 2026-09-11 (Audiobook-Manager-4uj):
translating the remaining corpus through it was priced at thousands of
dollars against roughly $315 of burst GPU time, and the requirement that
replaced it is that the application be provably unable to reach that vendor
— enforced by ``tests/test_source_guards.py``.

What this means at runtime:

* Pipelines emit source-language output only and log the skip.
* API endpoints that would translate on demand return 503.
* Existing cached translations are untouched — they serve from the DB and
  the subtitle cache exactly as before.

A future backend (the measured plan is burst transcription + MT on a rented
GPU node) registers here: implement :class:`~.base.TranslationProvider` in a
sibling module and return an instance from :func:`get_translation_provider`.
"""

from __future__ import annotations

from .base import TranslationProvider


def get_translation_provider() -> TranslationProvider | None:
    """Return the configured machine-translation provider, or ``None``.

    ``None`` means new translations are unavailable; callers degrade the
    same way they always did when the backend was unconfigured (skip +
    log for pipelines, 503 for endpoints).
    """
    return None


def translation_provider_name() -> str | None:
    """Name of the configured provider, or ``None`` when none exists.

    Used by callers that record row provenance (``translator`` /
    ``translation_provider`` columns) at the moment a translation is
    persisted.
    """
    provider = get_translation_provider()
    return provider.name if provider is not None else None
