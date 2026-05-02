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
     would save the already-frozen scrollY=0 instead of the real position)
  4. Lock is idempotent — guarded by the data attribute check
  5. Restore uses scrollTo with behavior:auto (instant) — smooth would feel
     jumpy
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS = (REPO / "library" / "web-v2" / "js" / "library.js").read_text()


def test_lock_helpers_defined_as_static_methods():
    assert re.search(
        r"static\s+_lockBodyScroll\(\)\s*\{", JS
    ), "_lockBodyScroll static helper missing"
    assert re.search(
        r"static\s+_unlockBodyScrollIfLocked\(\)\s*\{", JS
    ), "_unlockBodyScrollIfLocked static helper missing"


def test_lock_uses_data_attribute_for_idempotency():
    """The lock helper must guard against double-lock via a body attribute.
    Double-lock would corrupt the saved scrollY (replace real position with
    the already-frozen 0)."""
    body = re.search(
        r"static\s+_lockBodyScroll\(\)\s*\{(.+?)\n  \}\n", JS, re.DOTALL
    )
    assert body, "could not extract _lockBodyScroll body"
    fn = body.group(1)
    assert "hasAttribute" in fn, (
        "lock must guard via hasAttribute() to be idempotent"
    )
    assert "_SCROLL_LOCK_ATTR" in fn, "lock must reference the attribute constant"


def test_unlock_no_op_when_not_locked():
    """Unlock must early-return if the body isn't locked — calling unlock
    on an unlocked body would otherwise reset scroll to 0."""
    body = re.search(
        r"static\s+_unlockBodyScrollIfLocked\(\)\s*\{(.+?)\n  \}\n",
        JS,
        re.DOTALL,
    )
    assert body
    fn = body.group(1)
    assert "if (saved === null) return" in fn or "saved == null" in fn, (
        "unlock must early-return if no lock attribute set"
    )


def test_unlock_restores_scroll_position():
    body = re.search(
        r"static\s+_unlockBodyScrollIfLocked\(\)\s*\{(.+?)\n  \}\n",
        JS,
        re.DOTALL,
    )
    fn = body.group(1)
    assert "scrollTo" in fn, "unlock must call scrollTo to restore position"
    assert 'behavior: "auto"' in fn or "behavior: 'auto'" in fn, (
        "scroll restore must be instant (auto), not smooth — smooth animates "
        "back to the saved position which feels jumpy"
    )


def test_show_book_detail_calls_lock():
    """showBookDetail must call _lockBodyScroll when mounting the modal."""
    detail_section = re.search(
        r"showBookDetail\(bookId\)\s*\{(.+?)\n  \}", JS, re.DOTALL
    )
    assert detail_section, "could not extract showBookDetail body"
    fn = detail_section.group(1)
    assert "AudiobookLibraryV2._lockBodyScroll()" in fn, (
        "showBookDetail must lock body scroll when opening modal"
    )


def test_close_paths_call_unlock():
    """Both close paths (backdrop click AND close button) must unlock."""
    detail_section = re.search(
        r"showBookDetail\(bookId\)\s*\{(.+?)\n  \}", JS, re.DOTALL
    )
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
    detail_section = re.search(
        r"showBookDetail\(bookId\)\s*\{(.+?)\n  \}", JS, re.DOTALL
    )
    assert detail_section, "could not extract showBookDetail body"
    fn = detail_section.group(1)

    cleanup_idx = fn.find('document.getElementById("book-detail-modal")')
    assert cleanup_idx >= 0, "cleanup of existing modal must exist in showBookDetail"
    unlock_idx = fn.find("_unlockBodyScrollIfLocked()", cleanup_idx)
    lock_idx = fn.find("_lockBodyScroll()", cleanup_idx)
    assert unlock_idx > cleanup_idx, (
        "unlock must be called after the existing-modal cleanup site"
    )
    assert lock_idx > unlock_idx, (
        "lock must come AFTER unlock — otherwise the double-lock corrupts "
        "the saved scrollY (new lock saves the already-frozen 0 instead of "
        "the user's real position)"
    )
