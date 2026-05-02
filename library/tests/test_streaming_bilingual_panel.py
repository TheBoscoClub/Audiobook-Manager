"""Tests for the bilingual transcript panel — Task 17 of v8.3.2.

The bilingual transcript panel evolves the existing `#transcript-panel`
(library/web-v2/shell.html) into a two-column view: source cues on the left,
translated cues on the right. Pairing is by time-window overlap, not strict
1:1, because translation can merge or split cues.

This test covers:

1. Structural presence of the two-column markup in `shell.html`.
2. Presence of the new `pairVttCues()` helper on `window.subtitles` in
   `subtitles.js` (public API surface).
3. Behavioural verification of `pairVttCues()` by executing the actual JS
   file in a Node subprocess with a minimal `window`/`document` stub and
   calling the function with crafted inputs (1:1, 1:n, n:1, empty,
   no-overlap, all-overlap). This tests the real JS — no Python mirror to
   drift.
4. CSS presence of the two-column flex layout and the <=720px responsive
   stack rule in `library/web-v2/css/i18n.css` (the canonical player/CSS
   home for the transcript panel).

The test is pure-unit — no browser, no VM, no network. Cue-pairing is a pure
function; we test it without rendering.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404  # test runner invokes a hardcoded `node` path with JSON-encoded fixed inputs
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
WEB = PROJECT_ROOT / "library" / "web-v2"
SHELL_HTML = WEB / "shell.html"
SUBTITLES_JS = WEB / "js" / "subtitles.js"
I18N_CSS = WEB / "css" / "i18n.css"

_NODE_BIN = shutil.which("node")


def _run_node(script: str) -> Any:
    """Execute a Node snippet that prints one JSON line and returns the parsed object.

    The snippet is expected to build a `result` object and then
    `console.log(JSON.stringify(result))` it. We pick the last non-empty line
    as the JSON payload to tolerate any stray logs from the loaded subtitles.js.
    """
    if _NODE_BIN is None:
        pytest.skip("node binary not on PATH")
    proc = subprocess.run(  # nosec B603  # _NODE_BIN resolved via shutil.which; script is our controlled JSON-encoded test fixture
        [_NODE_BIN, "-e", script], capture_output=True, text=True, timeout=15, check=False
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node failed (rc={proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"node produced no output; stderr:\n{proc.stderr}"
    return json.loads(lines[-1])


def _load_subtitles_harness(test_body_js: str) -> str:
    """Wrap a test body in a Node harness that loads subtitles.js with a DOM stub.

    The harness stubs `window`, `document`, and a no-op `addEventListener`,
    then evaluates subtitles.js. subtitles.js exposes its public API as
    `window.subtitles`, from which we read `pairVttCues`.
    """
    # Path escaping: subtitles.js path goes into the JS as a string literal.
    js_path = str(SUBTITLES_JS).replace("\\", "\\\\").replace("'", "\\'")
    return rf"""
const fs = require('fs');
const vm = require('vm');

// Minimal DOM stub — subtitles.js touches document/window during its IIFE but
// gates all real DOM work behind DOMContentLoaded, which we never fire.
const listeners = {{}};
const docStub = {{
    addEventListener: (ev, fn) => {{ listeners[ev] = fn; }},
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({{
        classList: {{ add: ()=>{{}}, remove: ()=>{{}}, toggle: ()=>{{}} }},
        addEventListener: () => {{}},
        setAttribute: () => {{}},
        appendChild: () => {{}},
        append: () => {{}},
        dataset: {{}},
    }}),
}};
const winStub = {{}};
const ctx = {{
    window: winStub, document: docStub,
    setTimeout: setTimeout, clearTimeout: clearTimeout,
    setInterval: setInterval, clearInterval: clearInterval,
    fetch: () => Promise.resolve({{ ok: false, text: () => Promise.resolve(""), json: () => Promise.resolve([]) }}),
    Promise: Promise, console: console,
}};
vm.createContext(ctx);
const _subtitlesSrc = fs.readFileSync('{js_path}', 'utf8');
vm.runInContext(_subtitlesSrc, ctx);
const subs = ctx.window.subtitles;
if (!subs || typeof subs.pairVttCues !== 'function') {{
    throw new Error('window.subtitles.pairVttCues not exposed');
}}
{test_body_js}
"""


# ── Structural checks — static file grep ─────────────────────────────────


class TestShellHtmlMarkup:
    """The existing #transcript-panel must evolve into two-column bilingual markup."""

    @pytest.fixture(autouse=True)
    def _html(self):
        self.html = SHELL_HTML.read_text(encoding="utf-8")

    def test_transcript_panel_id_preserved(self):
        """The #transcript-panel id is the public handle; must not rename."""
        assert 'id="transcript-panel"' in self.html

    def test_transcript_close_button_preserved(self):
        """The #transcript-close button is the close affordance; must not rename."""
        assert 'id="transcript-close"' in self.html

    def test_transcript_panel_has_bilingual_class(self):
        """Panel must carry the .bilingual modifier so CSS can target two-column rules."""
        assert re.search(
            r'id="transcript-panel"[^>]*class="[^"]*\bbilingual\b', self.html
        ) or re.search(r'class="[^"]*\bbilingual\b[^"]*"[^>]*id="transcript-panel"', self.html), (
            "transcript-panel must carry the 'bilingual' CSS class"
        )

    def test_has_two_column_structure(self):
        """Panel interior must contain a source column and a target column."""
        # Column containers — use .col-source / .col-target as semantic markers.
        assert "col-source" in self.html, "missing .col-source column"
        assert "col-target" in self.html, "missing .col-target column"

    def test_source_header_uses_i18n_key(self):
        """Source-column header must bind to streaming.bilingual.sourceHeader."""
        assert 'data-i18n="streaming.bilingual.sourceHeader"' in self.html

    def test_target_header_uses_i18n_key(self):
        """Target-column header must bind to streaming.bilingual.targetHeader."""
        assert 'data-i18n="streaming.bilingual.targetHeader"' in self.html

    def test_title_uses_streaming_bilingual_key(self):
        """Panel title must bind to streaming.bilingual.title (bilingual upgrade)."""
        assert 'data-i18n="streaming.bilingual.title"' in self.html


class TestCssBilingualLayout:
    """The two-column flex layout + responsive stack live in i18n.css."""

    @pytest.fixture(autouse=True)
    def _css(self):
        self.css = I18N_CSS.read_text(encoding="utf-8")

    def test_bilingual_cols_flex_rule(self):
        """A .bilingual .cols rule must use display:flex (side-by-side columns)."""
        # Allow any whitespace between selector and declaration.
        m = re.search(
            r"\.transcript-panel\.bilingual\s+\.cols\s*{[^}]*display\s*:\s*flex", self.css
        )
        assert m is not None, ".transcript-panel.bilingual .cols must use display:flex"

    def test_responsive_stack_rule_720(self):
        """Below 720px viewport, the two columns must stack vertically."""
        # Look for a max-width: 720px media query that targets the columns
        # and sets flex-direction:column.
        mq = re.search(
            r"@media\s*\(\s*max-width\s*:\s*720px\s*\)\s*{(.*?)}\s*(?=@media|\Z|$)",
            self.css,
            re.DOTALL,
        )
        assert mq is not None, "missing @media (max-width: 720px) block"
        body = mq.group(1)
        assert re.search(r"\.cols", body), "720px block must target .cols"
        assert re.search(r"flex-direction\s*:\s*column", body), (
            "720px block must set flex-direction:column on the columns container"
        )

    def test_current_cue_highlight_rule(self):
        """Active-cue highlight for the bilingual panel must exist."""
        assert re.search(
            r"\.transcript-panel\.bilingual[^{]*\.(?:cue|current)[^{]*{", self.css
        ) or re.search(r"\.bilingual\s+[^{]*\.current\s*{", self.css), (
            "bilingual current-cue highlight rule missing"
        )


# ── Behavioural tests for pairVttCues via node subprocess ───────────────


class TestPairVttCuesBehaviour:
    """Execute subtitles.js in Node and call window.subtitles.pairVttCues."""

    def _call(self, src: list[dict], tgt: list[dict]) -> list:
        src_json = json.dumps(src)
        tgt_json = json.dumps(tgt)
        # NOTE: avoid naming locals `src`/`tgt` at -e top level — `-e` doesn't
        # create a fresh module scope and subtitles.js's internal let-bindings
        # can collide with top-level `const` declarations in some Node builds.
        body = f"""
const _srcIn = {src_json};
const _tgtIn = {tgt_json};
const pairs = subs.pairVttCues(_srcIn, _tgtIn);
// Normalise output: [ [srcCue|null, [tgtCue,...] ], ... ]
const out = pairs.map(([s, ts]) => [s, ts]);
console.log(JSON.stringify(out));
"""
        script = _load_subtitles_harness(body)
        return _run_node(script)

    def test_empty_inputs(self):
        assert self._call([], []) == []

    def test_strict_one_to_one(self):
        src = [
            {"startMs": 0, "endMs": 1000, "text": "Hello"},
            {"startMs": 1000, "endMs": 2000, "text": "world"},
        ]
        tgt = [
            {"startMs": 0, "endMs": 1000, "text": "你好"},
            {"startMs": 1000, "endMs": 2000, "text": "世界"},
        ]
        pairs = self._call(src, tgt)
        assert len(pairs) == 2
        assert pairs[0][0]["text"] == "Hello"
        assert len(pairs[0][1]) == 1 and pairs[0][1][0]["text"] == "你好"
        assert pairs[1][0]["text"] == "world"
        assert len(pairs[1][1]) == 1 and pairs[1][1][0]["text"] == "世界"

    def test_one_source_to_many_target(self):
        """Translator split one source cue into two target cues."""
        src = [{"startMs": 0, "endMs": 5000, "text": "A long sentence."}]
        tgt = [
            {"startMs": 0, "endMs": 2500, "text": "一个"},
            {"startMs": 2500, "endMs": 5000, "text": "长句。"},
        ]
        pairs = self._call(src, tgt)
        assert len(pairs) == 1
        assert pairs[0][0]["text"] == "A long sentence."
        assert len(pairs[0][1]) == 2
        assert [t["text"] for t in pairs[0][1]] == ["一个", "长句。"]

    def test_many_source_to_one_target(self):
        """Translator merged two source cues into one target cue.

        With time-window pairing, the target cue belongs to the first source cue
        whose end-time still precedes the target's start. The second source cue
        pairs with an empty target list.
        """
        src = [
            {"startMs": 0, "endMs": 1000, "text": "Part one"},
            {"startMs": 1000, "endMs": 2000, "text": "part two"},
        ]
        tgt = [{"startMs": 500, "endMs": 1800, "text": "第一部分第二部分"}]
        pairs = self._call(src, tgt)
        assert len(pairs) == 2
        # First source cue captures the overlapping target (tgt.start < src.end=1000).
        assert len(pairs[0][1]) == 1
        assert pairs[0][1][0]["text"] == "第一部分第二部分"
        # Second source cue has nothing left to pair with.
        assert pairs[1][1] == []

    def test_no_overlap_empty_target(self):
        """Source with no overlapping target cues yields empty target lists."""
        src = [
            {"startMs": 0, "endMs": 1000, "text": "A"},
            {"startMs": 1000, "endMs": 2000, "text": "B"},
        ]
        pairs = self._call(src, [])
        assert len(pairs) == 2
        assert pairs[0][1] == []
        assert pairs[1][1] == []

    def test_target_only_synthesizes_source_stubs(self):
        """When src is empty but tgt has cues, synthesize empty-text source
        stubs so the 双语文字记录 panel still renders.

        This is the v8.3.3 defensive pairing added for pre-v8.3.2 chapters
        whose streaming_segments.source_vtt_content was never persisted,
        and any future chapter where source upload fails. Without this,
        the bilingual panel appeared empty (Bug 2 from the zh-Hans QA run).
        Behavior: one pair per target cue, source stub has empty text and
        the target cue's time window.
        """
        tgt = [
            {"startMs": 0, "endMs": 1000, "text": "orphan"},
            {"startMs": 1000, "endMs": 2000, "text": "orphan2"},
        ]
        pairs = self._call([], tgt)
        assert len(pairs) == 2
        # Each pair: [synthesized src stub, [tgt cue]]
        assert pairs[0][0]["text"] == ""
        assert pairs[0][0]["startMs"] == 0
        assert pairs[0][0]["endMs"] == 1000
        assert pairs[0][1] == [{"startMs": 0, "endMs": 1000, "text": "orphan"}]
        assert pairs[1][0]["text"] == ""
        assert pairs[1][1] == [{"startMs": 1000, "endMs": 2000, "text": "orphan2"}]

    def test_both_empty_still_empty(self):
        """When both source and target are empty, the result is empty."""
        assert self._call([], []) == []

    def test_all_targets_under_first_source(self):
        """All target cues fall within the first source cue's time window."""
        src = [
            {"startMs": 0, "endMs": 10000, "text": "Opening sentence."},
            {"startMs": 10000, "endMs": 20000, "text": "Next sentence."},
        ]
        tgt = [
            {"startMs": 500, "endMs": 3000, "text": "开场"},
            {"startMs": 3000, "endMs": 6000, "text": "句子。"},
        ]
        pairs = self._call(src, tgt)
        assert len(pairs) == 2
        assert [t["text"] for t in pairs[0][1]] == ["开场", "句子。"]
        assert pairs[1][1] == []
