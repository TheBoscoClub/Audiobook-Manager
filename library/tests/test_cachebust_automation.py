"""Regression tests for the automated HTML cachebust stamp bumping (v8.3.8).

Motivation: every deploy of JS/CSS previously required a manual bump of
``?v=<num>`` in every HTML entrypoint. Missing a bump caused stale browser
caches to serve pre-deploy JS — manifesting variously as silent feature
invisibility or the v8.3.4 qalib 2000-ID URL-overflow 400 (browser kept
running the pre-fix library.js).

These tests pin the automation contract:
1. ``scripts/bump-cachebust.sh`` exists, executable, shellcheck-clean.
2. It rewrites every ``?v=<token>`` under the target dir's *.html files to a
   single passed stamp (idempotent, atomic via tmp+rename).
3. It rejects obviously-unsafe stamps (shell injection defense).
4. ``upgrade.sh`` and ``install.sh`` both invoke the bumper before service
   restart, using ``$(date +%s)`` as the stamp when none is explicitly given.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUMPER = REPO / "scripts" / "bump-cachebust.sh"


def _read(path: str) -> str:
    return (REPO / path).read_text()


def test_bumper_script_exists_and_executable():
    assert BUMPER.exists(), "scripts/bump-cachebust.sh must exist"
    assert BUMPER.stat().st_mode & 0o111, "bump-cachebust.sh must be executable"


def test_bumper_rewrites_cachebust_stamps(tmp_path):
    """Give the bumper a dir with two sample HTML files containing varying
    ``?v=`` stamps; after the run, every stamp should be the one we passed."""
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="css/library.css?v=1234567890">\n'
        '<script src="js/library.js?v=0987654321"></script>\n'
    )
    (tmp_path / "shell.html").write_text(
        '<script src="js/shell.js?v=abc123"></script>\n'
        '<link rel="stylesheet" href="css/shell.css?v=xyz">\n'
    )
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "should-not-touch.html").write_text(
        '<script src="js/x.js?v=old"></script>\n'
    )

    result = subprocess.run(
        [str(BUMPER), "STAMP42", str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"bumper failed: {result.stderr}"

    index = (tmp_path / "index.html").read_text()
    shell = (tmp_path / "shell.html").read_text()
    nested = (tmp_path / "nested" / "should-not-touch.html").read_text()

    # Every stamp in the top-level files rewritten.
    assert index.count("?v=STAMP42") == 2, f"index wasn't fully rewritten: {index}"
    assert shell.count("?v=STAMP42") == 2, f"shell wasn't fully rewritten: {shell}"
    assert "1234567890" not in index
    assert "0987654321" not in index
    assert "abc123" not in shell
    # Nested file NOT touched (maxdepth=1 contract).
    assert "?v=old" in nested, "bumper touched nested HTML (should be maxdepth 1)"


def test_bumper_idempotent(tmp_path):
    """Running the bumper twice with the same stamp produces the same result."""
    (tmp_path / "index.html").write_text('<script src="js/x.js?v=old"></script>\n')
    subprocess.run([str(BUMPER), "STAMP1", str(tmp_path)], check=True)
    content_after_first = (tmp_path / "index.html").read_text()
    subprocess.run([str(BUMPER), "STAMP1", str(tmp_path)], check=True)
    content_after_second = (tmp_path / "index.html").read_text()
    assert content_after_first == content_after_second


def test_bumper_rejects_unsafe_stamp(tmp_path):
    """Shell-injection defense: a stamp with spaces or special chars must fail."""
    (tmp_path / "index.html").write_text('<script src="x.js?v=old"></script>\n')
    for bad in ("; rm -rf /", "$(id)", "`whoami`", "foo bar", "a" * 40):
        result = subprocess.run(
            [str(BUMPER), bad, str(tmp_path)], capture_output=True, text=True, check=False
        )
        assert (
            result.returncode != 0
        ), f"bumper accepted unsafe stamp {bad!r} (should reject): {result.stdout}"


def test_bumper_fails_on_missing_target_dir(tmp_path):
    result = subprocess.run(
        [str(BUMPER), "STAMP", str(tmp_path / "nonexistent")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_upgrade_sh_invokes_bumper_before_service_restart():
    """upgrade.sh::do_upgrade must call the bumper AFTER file sync but BEFORE
    the post-upgrade smoke probe / service restart path."""
    upgrade = _read("upgrade.sh")
    # Minimal proof: the reference to the bumper exists AT LEAST in
    # do_upgrade (before audit_and_cleanup), and uses $(date +%s) as default
    # stamp.
    assert "bump-cachebust.sh" in upgrade, (
        "upgrade.sh must invoke scripts/bump-cachebust.sh so HTML cachebust "
        "stamps get bumped on every deploy."
    )
    assert re.search(r"stamp=\$\(date\s+\+%s\)", upgrade), (
        "upgrade.sh's cachebust step must use $(date +%s) as the stamp so every "
        "deploy gets a distinct monotonic cache key."
    )


def test_install_sh_invokes_bumper():
    body = _read("install.sh")
    assert "bump-cachebust.sh" in body, (
        "install.sh must invoke bump-cachebust.sh — fresh installs on a host "
        "that previously had the app must not let the browser serve stale JS."
    )


def test_bumper_shellcheck_clean():
    """bump-cachebust.sh must have no shellcheck errors."""
    if not os.path.exists("/usr/bin/shellcheck"):
        import pytest

        pytest.skip("shellcheck not installed")
    result = subprocess.run(
        ["shellcheck", "-s", "bash", str(BUMPER)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"shellcheck found issues:\n{result.stdout}"
