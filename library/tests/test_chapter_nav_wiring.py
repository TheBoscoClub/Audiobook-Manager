"""Regression guard for Audiobook-Manager-9by + 6ub — chapter-level navigation.

Pattern A: single button each, double-tap-back convention.
- ⏮ Skip-back: tap mid-chapter restarts current chapter; within 3s of start
  jumps to previous chapter (Apple Books / Audible / Pocket Casts UX).
- ⏭ Skip-forward: jumps to next chapter.

Buttons are display:none in the HTML and revealed in playBook() whenever
chapter boundaries are available via ANY of three pathways:
  1. The new /api/audiobooks/<id>/chapters endpoint (Audiobook-Manager-6ub) —
     covers the ~90% of the library that plays via the English single-stream
     /stream/<id> path.
  2. Active streaming MSE pipeline (one chapter per MSE chain).
  3. Cached translatedEntries (one URL per pre-translated chapter).

Tests are STRUCTURAL — runtime UI behaviour gets verified by Qing on her
iPhone Chrome / Safari and Bosco on desktop Brave. Structural assertions
catch the kinds of regressions that would silently break the buttons:
missing wiring, missing public-API entry points, hardcoded chapter +1 in
places that should be parameterised, accidental hide-by-default removal.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHELL_HTML = (REPO / "library" / "web-v2" / "shell.html").read_text()
SHELL_JS = (REPO / "library" / "web-v2" / "js" / "shell.js").read_text()
STREAMING_JS = (REPO / "library" / "web-v2" / "js" / "streaming-translate.js").read_text()
EN_JSON = json.loads((REPO / "library" / "locales" / "en.json").read_text())
ZH_JSON = json.loads((REPO / "library" / "locales" / "zh-Hans.json").read_text())


# ── HTML markup ──


def test_skip_back_chapter_button_present():
    """The skip-back-chapter button must exist with a stable id."""
    assert 'id="sp-skip-back-chapter"' in SHELL_HTML, (
        "sp-skip-back-chapter button missing from shell.html — chapter "
        "navigation has no entry point"
    )


def test_skip_forward_chapter_button_present():
    """The skip-forward-chapter button must exist with a stable id."""
    assert 'id="sp-skip-forward-chapter"' in SHELL_HTML, (
        "sp-skip-forward-chapter button missing from shell.html"
    )


def test_chapter_buttons_hidden_by_default():
    """Buttons are hidden in HTML — playBook() reveals them per-mode. Without
    this, English single-stream playback (no chapter info) would show
    non-functional chapter buttons."""
    for btn in ("sp-skip-back-chapter", "sp-skip-forward-chapter"):
        # Find the button tag and check it has display:none inline
        match = re.search(rf'<button[^>]*id="{btn}"[^>]*>', SHELL_HTML)
        assert match, f"{btn} button tag not found"
        tag = match.group(0)
        assert "display:none" in tag, (
            f"{btn} must be hidden by default — playBook() reveals it only "
            f"when chapter nav is meaningful"
        )


# ── shell.js wiring ──


def test_shell_has_skip_back_handler():
    """Click handler installed for skip-back-chapter."""
    assert re.search(
        r'getElementById\("sp-skip-back-chapter"\)\.addEventListener\("click"',
        SHELL_JS,
    ), "Click handler for sp-skip-back-chapter not wired"


def test_shell_has_skip_forward_handler():
    """Click handler installed for skip-forward-chapter."""
    assert re.search(
        r'getElementById\("sp-skip-forward-chapter"\)\.addEventListener\("click"',
        SHELL_JS,
    ), "Click handler for sp-skip-forward-chapter not wired"


def test_shell_skip_methods_defined():
    """ShellPlayer methods _skipBackChapter and _skipForwardChapter exist."""
    assert "_skipBackChapter()" in SHELL_JS, "_skipBackChapter method missing"
    assert "_skipForwardChapter()" in SHELL_JS, "_skipForwardChapter method missing"


def test_shell_skip_back_uses_double_tap_threshold():
    """Skip-back uses RESTART_THRESHOLD_SEC for the within-3s previous-chapter
    fall-through. Without this constant the standard audiobook double-tap UX
    pattern would be broken."""
    assert re.search(r"RESTART_THRESHOLD_SEC\s*=\s*\d+", SHELL_JS), (
        "RESTART_THRESHOLD_SEC missing — skip-back can't distinguish restart-current vs prev-chapter"
    )


def test_shell_visibility_helper_defined():
    """playBook delegates chapter-button visibility to the
    _applyChapterButtonVisibility helper, which is also called after the
    chapters fetch resolves. Without the helper, buttons would only update
    at playBook entry — before the chapters API responds — so the EN
    single-stream path would never light them up. (Audiobook-Manager-6ub)"""
    assert "_applyChapterButtonVisibility(" in SHELL_JS, (
        "_applyChapterButtonVisibility helper missing — visibility cannot "
        "be recomputed after the async /api/audiobooks/<id>/chapters fetch "
        "resolves"
    )


def test_shell_visibility_includes_chapters_array():
    """Visibility decision must consider this.chapters.length, not just
    streaming/translated state. This is the core of the 6ub fix — without
    it the buttons stay hidden on the EN single-stream path even when
    chapter boundaries are available."""
    assert re.search(
        r"this\.chapters\s*&&\s*this\.chapters\.length\s*>\s*0",
        SHELL_JS,
    ), (
        "Visibility logic must include this.chapters.length > 0 — buttons "
        "won't show on EN single-stream playback otherwise (the common case)"
    )


# ── Chapters API integration (Audiobook-Manager-6ub) ──


def test_chapters_fetch_called_in_play_book():
    """playBook must fetch /api/audiobooks/<id>/chapters so the EN
    single-stream path can populate this.chapters and reveal the buttons.
    Pattern asserts the template-literal URL exactly as it is built."""
    assert re.search(
        r"/api/audiobooks/\$\{bookId\}/chapters",
        SHELL_JS,
    ) or re.search(
        r"\$\{API_BASE\}/audiobooks/\$\{bookId\}/chapters",
        SHELL_JS,
    ), (
        "playBook must fetch /api/audiobooks/<id>/chapters — without it the "
        "chapters array stays empty and buttons stay hidden on EN single-stream"
    )


def test_chapters_reset_on_new_book():
    """this.chapters MUST be reset to [] when a new book starts so the
    previous book's chapters don't leak into _skipBackChapter /
    _skipForwardChapter on the EN single-stream path before the fetch
    resolves."""
    assert re.search(r"this\.chapters\s*=\s*\[\]", SHELL_JS), (
        "this.chapters reset to [] missing — previous book's chapters "
        "would leak into the new book's skip handlers"
    )


def test_skip_back_handler_uses_chapters_array():
    """_skipBackChapter must consult this.chapters before falling through
    to streaming / translatedEntries paths. Asserts both the chapters
    reference and the millisecond-threshold check that distinguishes
    restart-current vs jump-to-prev."""
    body = re.search(
        r"_skipBackChapter\(\)\s*\{(.+?)\n  \}\n",
        SHELL_JS,
        re.DOTALL,
    )
    assert body, "Could not extract _skipBackChapter function body"
    fn = body.group(1)
    assert "this.chapters" in fn, (
        "_skipBackChapter doesn't reference this.chapters — EN single-stream "
        "skip-back will dead-end at the defensive currentTime=0 fallback"
    )
    assert "RESTART_THRESHOLD_MS" in fn, (
        "_skipBackChapter chapters-array path missing the millisecond double-tap threshold check"
    )


def test_skip_forward_handler_uses_chapters_array():
    """_skipForwardChapter must consult this.chapters before falling through
    to streaming / translatedEntries paths. Asserts the next-chapter find
    pattern (start_ms > nowMs)."""
    body = re.search(
        r"_skipForwardChapter\(\)\s*\{(.+?)\n  \}\n",
        SHELL_JS,
        re.DOTALL,
    )
    assert body, "Could not extract _skipForwardChapter function body"
    fn = body.group(1)
    assert "this.chapters" in fn, (
        "_skipForwardChapter doesn't reference this.chapters — EN "
        "single-stream skip-forward is a no-op"
    )
    assert "start_ms > nowMs" in fn or "ch.start_ms > nowMs" in fn, (
        "_skipForwardChapter chapters-array path missing the next-chapter "
        "find pattern (start_ms > nowMs)"
    )


def test_shell_load_translated_entry_helper_used():
    """The new _loadTranslatedEntry helper is shared between the ended-handler
    auto-advance, skip-forward, and skip-back. Refactor lock-in: the previous
    inline duplication in the ended handler should be gone."""
    assert "_loadTranslatedEntry(" in SHELL_JS, (
        "_loadTranslatedEntry helper missing — chapter advance logic must "
        "share one code path across ended-handler, skip-forward, and skip-back"
    )


# ── streaming-translate.js public API ──


def test_streaming_jump_to_chapter_function_defined():
    """jumpToChapter is the public entry point for shell.js to ask the
    streaming pipeline to load a specific chapter."""
    assert re.search(r"function jumpToChapter\(targetChapter\)", STREAMING_JS), (
        "jumpToChapter function not defined in streaming-translate.js"
    )


def test_streaming_jump_to_chapter_exposed_on_public_api():
    """window.streamingTranslate.jumpToChapter must be exposed; otherwise
    shell.js can't invoke it."""
    assert re.search(r"jumpToChapter:\s*jumpToChapter", STREAMING_JS), (
        "jumpToChapter not exposed on window.streamingTranslate public API"
    )


def test_streaming_chapter_getters_exposed():
    """getCurrentChapter / getTotalChapters are needed by shell.js to know
    whether skip-forward is meaningful (don't jump past last chapter) and
    whether skip-back at chapter 0 should restart-only."""
    assert "getCurrentChapter:" in STREAMING_JS, "getCurrentChapter getter missing"
    assert "getTotalChapters:" in STREAMING_JS, "getTotalChapters getter missing"


def test_jump_to_chapter_validates_range():
    """jumpToChapter must reject targets >= totalChapters and < 0. Without
    bounds checks, the slow-path POST would request a nonexistent chapter
    and the player would dead-end."""
    body = re.search(
        r"function jumpToChapter\(targetChapter\)\s*\{(.+?)\n  \}\n",
        STREAMING_JS,
        re.DOTALL,
    )
    assert body, "Could not extract jumpToChapter function body"
    fn = body.group(1)
    assert "targetChapter < 0" in fn, "jumpToChapter doesn't reject negative target"
    assert "targetChapter >= totalChapters" in fn, (
        "jumpToChapter doesn't reject target past the end of the book"
    )


def test_jump_to_chapter_tears_down_mse():
    """A chapter jump must teardown the current MSE chain — leaving it would
    splice the wrong chapter's segments into the new chain."""
    body = re.search(
        r"function jumpToChapter\(targetChapter\)\s*\{(.+?)\n  \}\n",
        STREAMING_JS,
        re.DOTALL,
    )
    assert body
    fn = body.group(1)
    assert "mseChain.teardown()" in fn, "jumpToChapter must teardown current MSE chain"
    assert "clearPreload()" in fn, (
        "jumpToChapter must clear preload — stale preloadedNextChapter for "
        "currentChapter+1 is wrong if user jumps to chapter 50"
    )


# ── i18n parity (per feedback_in_app_docs_i18n_parity.md) ──


def test_i18n_keys_present_in_en_catalog():
    """New i18n keys must exist in en.json — fallback when zh-Hans missing."""
    assert "player.skipBackChapterTitle" in EN_JSON
    assert "player.skipForwardChapterTitle" in EN_JSON


def test_i18n_keys_present_in_zh_hans_catalog():
    """zh-Hans must have curated translations for any new player UI strings —
    feedback_in_app_docs_i18n_parity.md mandates first-party translation,
    not DeepL fallback."""
    assert "player.skipBackChapterTitle" in ZH_JSON, (
        "zh-Hans curated translation missing — would fall back to en string "
        "or DeepL overlay, against the i18n parity rule"
    )
    assert "player.skipForwardChapterTitle" in ZH_JSON


def test_i18n_zh_hans_actually_translated():
    """Sanity: zh-Hans values shouldn't be identical to en (a forgotten copy
    paste). Crude check: at least one CJK character in each string."""
    cjk_re = re.compile(r"[一-鿿]")
    for key in ("player.skipBackChapterTitle", "player.skipForwardChapterTitle"):
        val = ZH_JSON[key]
        assert cjk_re.search(val), (
            f"zh-Hans value for {key!r} has no CJK characters — likely "
            f"copy-pasted from en. Value: {val!r}"
        )
