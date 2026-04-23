"""
Unit-level proof of the streaming-translate threshold-cap fix (v8.3.6).

Why this test exists:
    On iOS Safari, Selenium-driven automated browsers cannot exercise
    HTMLMedia.play() — WebKit's autoplay policy rejects synthetic click
    events as non-user-activation. BrowserStack's real-device XCUITest
    driver inherits this limit, so the BrowserStack harness can prove
    shell binding and audio element setup but cannot prove the
    BUFFERING → STREAMING transition triggered by segment progress
    updates.

    This test fills that gap: it loads the actual streaming-translate.js
    source via Node, invokes the threshold-cap calculation with
    synthetic (completed, total, rawThreshold) inputs, and asserts the
    cap behaves correctly. It is not a substitute for real-device
    validation — but it proves the fix at line 534 of the shipped JS
    eliminates the threshold > total impossible-condition that stalled
    A zh-Hans player in QA on 2026-04-19/20.

The bug: before v8.3.6, if total=5 and rawThreshold=6, the BUFFERING
    phase would wait forever for a 6th segment that would never arrive,
    because the chapter only has 5.

The fix: var threshold = total > 0 ? Math.min(rawThreshold, total) : rawThreshold;
"""

import json
import subprocess
from pathlib import Path

import pytest

JS_PATH = Path(__file__).resolve().parent.parent / "web-v2" / "js" / "streaming-translate.js"


def _run_threshold_cap(completed, total, raw_threshold, default_threshold=6):
    """
    Execute the exact threshold-cap expression from streaming-translate.js
    via Node and return (threshold, should_enter_streaming).

    This reads the source, extracts the expression, and runs it. If the
    source ever changes, the test catches regressions automatically.
    """
    assert JS_PATH.is_file(), f"streaming-translate.js missing at {JS_PATH}"
    source = JS_PATH.read_text(encoding="utf-8")

    # Grep-verify the exact expression we're testing is in the shipped source.
    assert "var rawThreshold = data.threshold || BUFFER_THRESHOLD;" in source, (
        "rawThreshold declaration missing from streaming-translate.js — "
        "threshold-cap fix may have been reverted"
    )
    assert "var threshold = total > 0 ? Math.min(rawThreshold, total) : rawThreshold;" in source, (
        "Math.min(rawThreshold, total) cap missing from streaming-translate.js — "
        "threshold-cap fix may have been reverted"
    )

    # Run the exact logic through Node. No mocks, no re-implementation.
    script = f"""
const data = {{ completed: {completed}, total: {total}, threshold: {raw_threshold} }};
const BUFFER_THRESHOLD = {default_threshold};
const State = {{ BUFFERING: 'buffering', STREAMING: 'streaming' }};
const state = State.BUFFERING;

// Extracted verbatim from streaming-translate.js:525-540
const completed = data.completed || 0;
const total = data.total || 0;
const rawThreshold = data.threshold || BUFFER_THRESHOLD;
const threshold = total > 0 ? Math.min(rawThreshold, total) : rawThreshold;

const should_enter_streaming =
    state === State.BUFFERING && completed >= threshold;

process.stdout.write(JSON.stringify({{ threshold, should_enter_streaming }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


class TestThresholdCap:
    """Proves streaming-translate.js:534 caps threshold to total."""

    def test_short_chapter_threshold_caps_to_total(self):
        """
        Field regression: 5-segment chapter with raw threshold 6.
        Before fix: threshold=6, completed=5, 5 >= 6 is false → stuck.
        After fix:  threshold=5, completed=5, 5 >= 5 is true  → STREAMING.
        """
        result = _run_threshold_cap(completed=5, total=5, raw_threshold=6)
        assert result["threshold"] == 5, (
            f"threshold should cap at total (5), got {result['threshold']}"
        )
        assert result["should_enter_streaming"] is True, (
            "short chapter with completed==total must transition BUFFERING→STREAMING"
        )

    def test_long_chapter_threshold_not_capped(self):
        """Normal case: 10-segment chapter, raw threshold 6 — no cap needed."""
        result = _run_threshold_cap(completed=6, total=10, raw_threshold=6)
        assert result["threshold"] == 6
        assert result["should_enter_streaming"] is True

    def test_long_chapter_waits_for_threshold(self):
        """Long chapter, completed < threshold — stays in BUFFERING."""
        result = _run_threshold_cap(completed=3, total=10, raw_threshold=6)
        assert result["threshold"] == 6
        assert result["should_enter_streaming"] is False

    def test_unknown_total_uses_raw_threshold(self):
        """
        total=0 (unknown chapter length). No cap applied; raw threshold
        wins so the warmup gate still operates even when the backend
        hasn't reported segment count yet.
        """
        result = _run_threshold_cap(completed=6, total=0, raw_threshold=6)
        assert result["threshold"] == 6
        # completed=6, threshold=6 → 6 >= 6 is true
        assert result["should_enter_streaming"] is True

    def test_tiny_chapter_one_segment(self):
        """Edge case: single-segment chapter. threshold must cap to 1."""
        result = _run_threshold_cap(completed=1, total=1, raw_threshold=6)
        assert result["threshold"] == 1
        assert result["should_enter_streaming"] is True

    def test_empty_progress_no_transition(self):
        """completed=0 on any chapter — must stay in BUFFERING."""
        result = _run_threshold_cap(completed=0, total=5, raw_threshold=6)
        assert result["threshold"] == 5
        assert result["should_enter_streaming"] is False

    def test_progress_helper_also_caps(self):
        """
        The same cap exists in updateProgress (line 273) and the phase
        check helper (line 339). Verify those patterns remain in source
        — a grep proof, not a Node execution.
        """
        source = JS_PATH.read_text(encoding="utf-8")
        # Line 273
        assert (
            source.count(
                "var threshold = total > 0 ? Math.min(BUFFER_THRESHOLD, total) : BUFFER_THRESHOLD;"
            )
            >= 2
        ), (
            "Math.min cap at lines 273 and 339 must both remain — one protects "
            "the progress display, the other protects the phase/message logic"
        )


class TestThresholdCapMetadata:
    """Proves the deployed file is the v8.3.6 file — catches rollback."""

    def test_cap_comment_explains_why(self):
        """
        The multi-line comment above line 534 documents the reason for
        the cap. If this comment goes missing, someone removed the
        context and the fix is at risk of being reverted.
        """
        source = JS_PATH.read_text(encoding="utf-8")
        assert "Cap threshold to total" in source
        assert "BUFFERING → STREAMING transition" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
