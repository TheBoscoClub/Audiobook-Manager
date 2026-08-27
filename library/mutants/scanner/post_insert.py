"""
Post-insert hook registry for audiobook ingest.

Replaces the hardcoded hook fan-out that previously lived in
``add_new_audiobooks._run_post_insert_hooks`` (Audiobook-Manager-6cx). With
the hardcoded pattern, adding a new post-insert feature meant remembering to
edit the fan-out function — the modular ``utilities_ops`` refactor lost the
hash-generation hook that way, a silent regression for ~3.5 months
(Audiobook-Manager-f5e).

With this registry, each hook self-registers via the ``register_post_insert``
decorator at import time of this module. ``run_post_insert_hooks`` iterates
the registry; it never needs editing when a hook is added or removed. A
regression test (``tests/test_post_insert_registry.py``) asserts every
registered hook fires on a synthetic insert.

All hooks are non-fatal: a hook failure is reported to stderr and must never
block or roll back the insert. Feature imports stay lazy (inside hook bodies)
so ingest does not pay import costs for features it may not need and so tests
can patch the feature modules directly.
"""

from __future__ import annotations

import contextvars
import sys
from pathlib import Path
from typing import Any, Callable

# A post-insert hook receives (audiobook_id, db_path).
PostInsertHook = Callable[[int, Path], Any]

_POST_INSERT_HOOKS: list[tuple[str, PostInsertHook]] = []

# Ambient verbosity for the current fan-out. Set by ``run_post_insert_hooks``
# and read by hooks that support quiet/verbose output (currently enrichment).
# Threaded via a ContextVar so the hook signature stays ``(audiobook_id,
# db_path)`` — the mover path (``import_single``) wants verbose enrichment
# (``quiet=False``); the bulk scanner path (``add_new_audiobooks``) keeps the
# quiet default.
_quiet_var: contextvars.ContextVar[bool] = contextvars.ContextVar("post_insert_quiet", default=True)

# Lazily-resolved feature callables (False = tried and unavailable)
_enrich_module = None
_verify_module = None


def register_post_insert(label: str) -> Callable[[PostInsertHook], PostInsertHook]:
    """Register a post-insert hook under a human-readable ``label``.

    The label is used in the non-fatal error message when the hook raises:
    ``⚠ {label} error (non-fatal): ...``. Hooks run in registration order.
    """

    def decorator(hook: PostInsertHook) -> PostInsertHook:
        _POST_INSERT_HOOKS.append((label, hook))
        return hook

    return decorator


def registered_post_insert_hooks() -> list[tuple[str, PostInsertHook]]:
    """Return a snapshot of the registered (label, hook) pairs."""
    return list(_POST_INSERT_HOOKS)


def run_post_insert_hooks(audiobook_id: int, db_path: Path, *, quiet: bool = True) -> None:
    """Run every registered post-insert hook for a newly-inserted audiobook.

    Each hook is isolated: an exception is reported to stderr and the
    remaining hooks still run. Hook failures never propagate to the caller.

    ``quiet`` controls the verbosity of hooks that support it (enrichment).
    It defaults to ``True`` (quiet) for the bulk scanner path; the mover path
    (``import_single``) passes ``quiet=False`` so per-book enrichment progress
    is logged inline. The flag is exposed to hooks via ``_quiet_var`` so the
    hook signature stays ``(audiobook_id, db_path)``.
    """
    if not audiobook_id:
        return
    token = _quiet_var.set(quiet)
    try:
        for label, hook in _POST_INSERT_HOOKS:
            try:
                hook(audiobook_id, db_path)
            except Exception as e:
                print(f"  ⚠ {label} error (non-fatal): {e}", file=sys.stderr)
    finally:
        _quiet_var.reset(token)


def _get_enrich_module() -> Callable[..., Any] | None:
    global _enrich_module
    if _enrich_module is None:
        try:
            from scripts.enrichment import enrich_book

            _enrich_module = enrich_book
        except ImportError:
            try:
                from scripts.enrich_single import enrich_book

                _enrich_module = enrich_book
            except ImportError:
                _enrich_module = False
    if not _enrich_module:
        return None
    return _enrich_module  # type: ignore[return-value]


def _get_verify_module() -> Callable[..., Any] | None:
    global _verify_module
    if _verify_module is None:
        try:
            from scripts.verify_metadata import verify_single_book

            _verify_module = verify_single_book
        except ImportError:
            _verify_module = False
    if not _verify_module:
        return None
    return _verify_module  # type: ignore[return-value]


@register_post_insert("Enrichment")
def _enrichment_hook(audiobook_id: int, db_path: Path) -> None:
    """Auto-enrich metadata for the new book (skips if no enricher installed)."""
    enrich_fn = _get_enrich_module()
    if enrich_fn:
        enrich_fn(book_id=audiobook_id, db_path=db_path, quiet=_quiet_var.get())


@register_post_insert("Verification")
def _verification_hook(audiobook_id: int, db_path: Path) -> None:
    """Verify (and auto-fix) metadata for the new book."""
    verify_fn = _get_verify_module()
    if verify_fn:
        verify_fn(book_id=audiobook_id, db_path=db_path, auto_fix=True, quiet=True)


@register_post_insert("Translation queue")
def _translation_queue_hook(audiobook_id: int, db_path: Path) -> None:
    """Enqueue the new book for translation into all configured locales."""
    from localization.queue import enqueue_book_all_locales

    enqueue_book_all_locales(audiobook_id)


@register_post_insert("Hash generation")
def _hash_generation_hook(audiobook_id: int, db_path: Path) -> None:
    """Populate ``sha256_hash`` for the new book.

    Restored in v8.3.10.1 — newly-ingested books were being inserted without
    ``sha256_hash`` because the per-file hash was only computed when
    ``calculate_hashes=True`` was passed to ``get_file_metadata``. If the
    metadata path already populated ``sha256_hash``, this is an idempotent
    no-op (the row's current hash is overwritten with the same value). See
    Audiobook-Manager-f5e.
    """
    from scripts.generate_hashes import generate_hash_for_book

    generate_hash_for_book(audiobook_id, db_path)


@register_post_insert("Original print year")
def _original_print_year_hook(audiobook_id: int, db_path: Path) -> None:
    """Populate ``original_publish_year`` from Open Library (v8.4.2.0).

    Looks up the book's FIRST print-publication year (OL
    ``first_publish_year``) — distinct from the Audible-sourced
    ``published_year``, which conflates book-publication and
    audiobook-release years. No confident OL match leaves the column NULL;
    the "Original Print Year" sort falls back to ``published_year`` via
    query-time COALESCE. Existing rows are backfilled by the operator-run
    ``scripts/original_print_year.py --backfill`` CLI (Audiobook-Manager-nfx).
    """
    from scripts.original_print_year import populate_original_publish_year

    populate_original_publish_year(audiobook_id, db_path=db_path, quiet=True)
