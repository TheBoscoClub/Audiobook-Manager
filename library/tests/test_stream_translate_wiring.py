"""Enforces `.claude/rules/upgrade-consistency.md` — no orphan scripts."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_worker_has_systemd_unit():
    unit = REPO / "systemd" / "audiobook-stream-translate.service"
    assert unit.exists()
    text = unit.read_text()
    assert "stream-translate-worker.py" in text
    assert "User=audiobooks" in text
    assert "Group=audiobooks" in text


def test_unit_in_target():
    target = (REPO / "systemd" / "audiobook.target").read_text()
    assert "audiobook-stream-translate.service" in target


def test_unit_in_manifest():
    manifest = (REPO / "scripts" / "install-manifest.sh").read_text()
    assert "audiobook-stream-translate.service" in manifest


def test_worker_in_manifest():
    manifest = (REPO / "scripts" / "install-manifest.sh").read_text()
    assert "stream-translate-worker.py" in manifest


def test_install_sh_copies_unit():
    install = (REPO / "install.sh").read_text()
    assert "audiobook-stream-translate.service" in install
    # Must end up enabled — either via legacy `systemctl enable
    # audiobook-stream-translate` (pre-Audiobook-Manager-hp2) or via the
    # new ``_enable_unit_smart "audiobook-stream-translate.service"`` helper
    # (v8.3.10+, which decides `--now` vs plain enable from WantedBy=).
    legacy = re.search(r"systemctl\s+enable(?:\s+--now)?\s+audiobook-stream-translate", install)
    helper = re.search(
        r'_enable_unit_smart\s+"audiobook-stream-translate\.service"', install
    )
    assert legacy or helper, (
        "install.sh no longer wires audiobook-stream-translate "
        "(neither `systemctl enable` literal nor `_enable_unit_smart` helper found)"
    )


def test_upgrade_sh_handles_unit():
    upgrade = (REPO / "upgrade.sh").read_text()
    assert "audiobook-stream-translate.service" in upgrade


def test_worker_executable_and_present():
    worker = REPO / "scripts" / "stream-translate-worker.py"
    assert worker.exists()
    assert worker.stat().st_mode & 0o111, "worker must be executable"
