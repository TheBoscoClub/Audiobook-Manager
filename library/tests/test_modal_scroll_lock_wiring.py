"""Regression guard for Audiobook-Manager-v2z (book-detail modal scroll lock).

Bosco reported on 2026-05-02 that tapping a book cover in the main library
grid on his Pixel 10 XL Pro / Brave Android opened the book-detail modal
*outside* the visible viewport — he had to scroll a screen-height to find
it. Same modal opened from "My Library" worked correctly because that view
typically opens at the top.

Root cause: the modal IS `position: fixed` in `modals.css`, so it should
viewport-overlay regardless of scroll. But on Brave Android with its
dynamic bottom URL bar, the visual viewport can shift mid-mount and the
fixed modal lands in a position that's no longer visible. Standard fix is
to lock body scroll while the modal is open and restore on close — that
freezes the visual viewport too and eliminates the mid-mount shift.

These tests are STRUCTURAL — they assert the source code shape rather than
runtime behaviour. Runtime UI verification happens against Bosco/Qing's
actual phones (the headless test browser enforces a 500px minimum width and
doesn't reproduce the dynamic-toolbar interaction).

The shape we're locking down:
  1. AudiobookLibraryV2._lockBodyScroll / _unlockBodyScrollIfLocked exist
  2. Lock is applied when showBookDetail mounts the modal
  3. Unlock is called on backdrop click, close button, AND when an existing
     modal is removed before opening a new one (otherwise the new lock
     could overlap with the in-flight modal mount)
  4. Lock is idempotent — guarded by the data attribute check
  5. v8.3.10.5 round-7 (Audiobook-Manager-n9x): the lock body uses ONLY
     ``overflow: hidden`` on html+body — never ``position: fixed``. Brave
     Android treats ``position: fixed`` on body as creating a containing
     block for descendants' ``position: fixed``, contrary to CSS spec, so
     the modal's ``height: 100%`` resolved against body's tall content box
     instead of the viewport and the centered card landed below the
     screen. Removing ``position: fixed`` also removed the ``saved
     scrollY`` save/restore (``scrollTo``) logic — there is no scroll
     position to restore because body never moved.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS = (REPO / "library" / "web-v2" / "js" / "library.js").read_text()


def test_lock_helpers_defined_as_static_methods():
    assert re.search(r"static\s+_lockBodyScroll\(\)\s*\{", JS), (
        "_lockBodyScroll static helper missing"
    )
    assert re.search(r"static\s+_unlockBodyScrollIfLocked\(\)\s*\{", JS), (
        "_unlockBodyScrollIfLocked static helper missing"
    )


def test_lock_uses_data_attribute_for_idempotency():
    """The lock helper must guard against double-lock via a body attribute.
    Double-lock would corrupt the saved scrollY (replace real position with
    the already-frozen 0)."""
    body = re.search(r"static\s+_lockBodyScroll\(\)\s*\{(.+?)\n  \}\n", JS, re.DOTALL)
    assert body, "could not extract _lockBodyScroll body"
    fn = body.group(1)
    assert "hasAttribute" in fn, "lock must guard via hasAttribute() to be idempotent"
    assert "_SCROLL_LOCK_ATTR" in fn, "lock must reference the attribute constant"


def test_unlock_no_op_when_not_locked():
    """Unlock must early-return if the body isn't locked — calling unlock
    on an unlocked body would otherwise clear the inline overflow style on
    a body that never set it (a no-op-style write but pollutes attribute
    history). The guard is a ``hasAttribute`` check on the lock attribute.
    """
    body = re.search(
        r"static\s+_unlockBodyScrollIfLocked\(\)\s*\{(.+?)\n  \}\n",
        JS,
        re.DOTALL,
    )
    assert body
    fn = body.group(1)
    assert "hasAttribute" in fn, (
        "unlock must early-return when the lock attribute is absent — "
        "guard is a hasAttribute() check"
    )
    # The early return must come before the inline-style writes.
    hasattr_pos = fn.find("hasAttribute")
    return_pos = fn.find("return", hasattr_pos)
    style_pos = fn.find(".style.overflow")
    assert 0 <= return_pos < style_pos, (
        "early-return must precede inline-style writes (otherwise the guard does nothing)"
    )


def test_unlock_clears_overflow_styles():
    """Unlock must clear the inline ``overflow: hidden`` set by lock on
    both ``documentElement`` and ``body`` so the page scrolls again. The
    pre-v8.3.10.5-round-7 implementation also restored a saved scrollY
    via ``scrollTo``; that's gone because lock no longer sets
    ``position: fixed`` (and therefore body never moved). See module
    docstring for the Brave-Android rationale.
    """
    body = re.search(
        r"static\s+_unlockBodyScrollIfLocked\(\)\s*\{(.+?)\n  \}\n",
        JS,
        re.DOTALL,
    )
    assert body is not None
    fn = body.group(1)
    assert "documentElement.style.overflow" in fn, (
        "unlock must clear html element's inline overflow style"
    )
    assert "body.style.overflow" in fn, "unlock must clear body's inline overflow style"


def test_show_book_detail_calls_lock():
    """showBookDetail must call _lockBodyScroll when mounting the modal."""
    detail_section = re.search(r"showBookDetail\(bookId\)\s*\{(.+?)\n  \}", JS, re.DOTALL)
    assert detail_section, "could not extract showBookDetail body"
    fn = detail_section.group(1)
    assert "AudiobookLibraryV2._lockBodyScroll()" in fn, (
        "showBookDetail must lock body scroll when opening modal"
    )


def test_close_paths_call_unlock():
    """Both close paths (backdrop click AND close button) must unlock."""
    detail_section = re.search(r"showBookDetail\(bookId\)\s*\{(.+?)\n  \}", JS, re.DOTALL)
    assert detail_section is not None
    fn = detail_section.group(1)
    # Counts the unlock call sites — should appear at least twice within
    # showBookDetail (1: existing-modal cleanup, 2+: in _closeModal which
    # is shared between backdrop and close-button handlers).
    unlock_calls = fn.count("AudiobookLibraryV2._unlockBodyScrollIfLocked()")
    assert unlock_calls >= 2, (
        f"unlock must be called from both existing-modal-cleanup AND modal "
        f"close paths — found only {unlock_calls} call(s) in showBookDetail"
    )


def test_existing_modal_cleanup_unlocks_first():
    """When opening a new modal while an old one exists, the old lock must
    be released BEFORE the new lock is applied — otherwise the new lock
    saves the already-frozen scrollY=0 instead of the user's real position.
    """
    # Look for the cleanup-then-unlock-then-lock sequence
    # Extract the showBookDetail function body and check ordering within it.
    detail_section = re.search(r"showBookDetail\(bookId\)\s*\{(.+?)\n  \}", JS, re.DOTALL)
    assert detail_section, "could not extract showBookDetail body"
    fn = detail_section.group(1)

    cleanup_idx = fn.find('document.getElementById("book-detail-modal")')
    assert cleanup_idx >= 0, "cleanup of existing modal must exist in showBookDetail"
    unlock_idx = fn.find("_unlockBodyScrollIfLocked()", cleanup_idx)
    lock_idx = fn.find("_lockBodyScroll()", cleanup_idx)
    assert unlock_idx > cleanup_idx, "unlock must be called after the existing-modal cleanup site"
    assert lock_idx > unlock_idx, (
        "lock must come AFTER unlock — otherwise the double-lock corrupts "
        "the saved scrollY (new lock saves the already-frozen 0 instead of "
        "the user's real position)"
    )
