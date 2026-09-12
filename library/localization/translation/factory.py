"""Machine-translation provider selection.

The previous hosted-API integration was removed outright on 2026-09-11
(Audiobook-Manager-4uj) — the application must be provably unable to reach
that vendor, enforced by ``tests/test_source_guards.py``. The replacement
backend (Audiobook-Manager-r6h) is :class:`~.vllm_mt.VLLMTranslator`:
Qwen3-8B served by vLLM on a rented GPU node, reached through an SSH tunnel
(``scripts/gpu-node.sh``).

The backend is **burst-shaped, not always-on**: ``AUDIOBOOKS_MT_ENDPOINT``
is set only while a node session is up. With it unset — the normal state —
the factory returns ``None`` and callers degrade exactly as before: skip +
log for pipelines, 503 for endpoints, existing cached translations keep
serving.
"""

from __future__ import annotations

from .base import TranslationProvider


def _endpoint() -> str:
    """The vLLM endpoint URL, or empty when no node session is up.

    Read at call time (not import time) so a drain session can export the
    variable for its own lifetime without a process restart.
    """
    import os

    return os.environ.get("AUDIOBOOKS_MT_ENDPOINT", "").strip()


def get_translation_provider() -> TranslationProvider | None:
    """Return the configured machine-translation provider, or ``None``.

    ``None`` means new translations are unavailable; callers degrade the
    same way they always did when the backend was unconfigured (skip +
    log for pipelines, 503 for endpoints).
    """
    endpoint = _endpoint()
    if not endpoint:
        return None
    import os

    from ..config import TRANSLATION_DB_PATH
    from .vllm_mt import VLLMTranslator

    return VLLMTranslator(
        endpoint=endpoint,
        model=os.environ.get("AUDIOBOOKS_MT_MODEL", "Qwen/Qwen3-8B"),
        db_path=TRANSLATION_DB_PATH,
    )


def translation_provider_name() -> str | None:
    """Name of the configured provider, or ``None`` when none exists.

    Used by callers that record row provenance (``translator`` /
    ``translation_provider`` columns) at the moment a translation is
    persisted.
    """
    provider = get_translation_provider()
    return provider.name if provider is not None else None
