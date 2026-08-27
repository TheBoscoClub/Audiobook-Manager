"""Regression guard for Audiobook-Manager-dwa.

The next-chapter pre-buffer logic in streaming-translate.js exists to keep
audio.play() inside iOS's user-gesture chain at chapter boundaries — without
it, Chinese auto-advance silently fails on Qing's iPhone Chrome and she has
to manually tap play at every chapter end.

These tests are STRUCTURAL — they assert the source code shape rather than
runtime behaviour. Runtime behaviour is verified by Qing on her actual iPhone
(visual UAT) because the iOS gesture-chain rule isn't reproducible on desktop
Chromium.

The shape we're locking down:
  1. The preload trigger constant exists with a sensible value
  2. maybePreloadNextChapter, clearPreload, detachPreloadListener all exist
  3. enterStreaming installs the timeupdate listener
  4. advanceChapter has a fast-path branch that consumes preloadedNextChapter
  5. enterIdle clears preload state (book switch must not splice cross-session data)

Any one of these missing means the auto-advance fix is silently broken.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS = (REPO / "library" / "web-v2" / "js" / "streaming-translate.js").read_text()


def test_preload_trigger_constant_present():
    """PRELOAD_TRIGGER_SEC must be defined with a reasonable value (5–30s)."""
    match = re.search(r"PRELOAD_TRIGGER_SEC\s*=\s*(\d+)", JS)
    assert match, "PRELOAD_TRIGGER_SEC constant missing — preload trigger has no threshold"
    seconds = int(match.group(1))
    assert 5 <= seconds <= 30, (
        f"PRELOAD_TRIGGER_SEC = {seconds}s is outside the 5–30s reasonable range. "
        "Too small means short chapters never trigger; too large wastes server work "
        "if the user seeks past the trigger zone repeatedly."
    )


def test_preload_helpers_defined():
    """All three helper functions must exist."""
    for name in ("maybePreloadNextChapter", "clearPreload", "detachPreloadListener"):
        assert re.search(rf"function\s+{re.escape(name)}\s*\(", JS), (
            f"Helper function `{name}` missing from streaming-translate.js. "
            "The pre-buffer feature requires all three (trigger, reset, cleanup)."
        )


def test_preload_state_variables_declared():
    """The three pre-buffer state vars must be declared at module scope."""
    for name in ("preloadedNextChapter", "preloadInProgress", "preloadTimeUpdateHandler"):
        assert re.search(rf"\bvar\s+{re.escape(name)}\b", JS), (
            f"State variable `{name}` not declared at module scope."
        )


def _function_body(name: str) -> str:
    """Return everything between `function NAME(...) {` and the next column-0 `}`.

    Function declarations in this file are top-level inside the IIFE wrapper,
    so their closing brace is at the start of a line with leading whitespace
    of exactly two spaces (one IIFE level deep). We anchor on `^  }$` to find
    the function-level close, which avoids matching nested `}` from inner
    blocks the way a lazy `(.*?)^\\s*\\}` would.
    """
    pattern = (
        r"^\s*function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{"
        r"(.*?)"
        r"^  \}$"
    )
    match = re.search(pattern, JS, re.DOTALL | re.MULTILINE)
    assert match, f"function {name} not found at IIFE-top-level (anchored on `^  }}$`)"
    return match.group(1)


def test_enter_streaming_installs_timeupdate_listener():
    """enterStreaming must wire maybePreloadNextChapter to the audio.timeupdate event."""
    body = _function_body("enterStreaming")
    assert 'addEventListener("timeupdate"' in body or "addEventListener('timeupdate'" in body, (
        "enterStreaming no longer registers a timeupdate listener — the preload "
        "trigger never fires without it."
    )
    assert "preloadTimeUpdateHandler" in body, (
        "enterStreaming doesn't reference preloadTimeUpdateHandler — the timeupdate "
        "listener tracking variable is detached from registration."
    )


def test_advance_chapter_has_fast_path():
    """advanceChapter must consume preloadedNextChapter when present (the fast path)."""
    body = _function_body("advanceChapter")
    assert "preloadedNextChapter" in body, (
        "advanceChapter no longer references preloadedNextChapter — the fast-path "
        "branch is gone, every chapter end will go back to the slow async POST "
        "and lose the iOS gesture chain."
    )
    # The fast path must call enterBuffering with the preloaded data and return early.
    assert re.search(
        r"preloadedNextChapter\s*&&.*?enterBuffering\s*\(",
        body,
        re.DOTALL,
    ), "advanceChapter doesn't call enterBuffering from the fast-path branch."


def test_enter_idle_clears_preload():
    """enterIdle must clear preload state — book switch can't splice old data into new session."""
    body = _function_body("enterIdle")
    assert "clearPreload" in body or "preloadedNextChapter = null" in body, (
        "enterIdle doesn't clear preload state — a book switch will leak the "
        "previous book's preloaded chapter into the new session."
    )
    assert "detachPreloadListener" in body or "removeEventListener" in body, (
        "enterIdle doesn't detach the timeupdate listener — stale callbacks "
        "will fire against a torn-down state machine."
    )
