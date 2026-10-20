"""Behavioral guard for pre-rename CLI copies left in /usr/local/bin.

Root cause (Audiobook-Manager-dxw): early installs copied scripts into
``/usr/local/bin`` under their repo names (``build-conversion-queue``,
``find-duplicate-sources``, ``monitor-audiobook-conversion``). Later releases
link them under ``SCRIPT_ALIASES`` CLI names (``audiobook-build-queue`` ...)
but never removed the old regular-file copies, so the stale January 2026
versions kept shadowing the current scripts on ``PATH``.

``remove_pre_rename_bin_copies`` removes such a copy only when the current
install provably owns the command: the old name is a regular file AND the
alias name is a symlink into ``${target}/scripts/<old name>``.

The function and the alias table are extracted from upgrade.sh and run in
bash against a temporary bin directory, so these tests exercise the real code.
"""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

REPO = Path(__file__).resolve().parents[2]


def _snippet() -> str:
    upgrade = (REPO / "upgrade.sh").read_text()
    aliases = re.search(r"^declare -A SCRIPT_ALIASES=\(\n.*?^\)", upgrade, re.DOTALL | re.MULTILINE)
    func = re.search(
        r"^remove_pre_rename_bin_copies\s*\(\)\s*\{\n.*?^\}", upgrade, re.DOTALL | re.MULTILINE
    )
    assert aliases, "SCRIPT_ALIASES not found in upgrade.sh"
    assert func, "remove_pre_rename_bin_copies not found in upgrade.sh"
    return "\n".join(['BLUE="" GREEN="" YELLOW="" NC=""', aliases.group(0), func.group(0)])


def _run(target: Path, bin_dir: Path, dry_run: bool = False) -> subprocess.CompletedProcess:
    script = f'{_snippet()}\nDRY_RUN={"true" if dry_run else "false"}\nremove_pre_rename_bin_copies "$1" "" "$2"\n'
    return subprocess.run(
        ["bash", "-c", script, "bash", str(target), str(bin_dir)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def layout(tmp_path):
    target = tmp_path / "opt" / "audiobooks"
    scripts = target / "scripts"
    scripts.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (scripts / "build-conversion-queue").write_text("#!/bin/bash\necho current\n")
    return target, scripts, bin_dir


def test_removes_regular_file_copy_when_alias_link_is_ours(layout):
    target, scripts, bin_dir = layout
    stale = bin_dir / "build-conversion-queue"
    stale.write_text("#!/bin/bash\necho stale\n")
    (bin_dir / "audiobook-build-queue").symlink_to(scripts / "build-conversion-queue")

    result = _run(target, bin_dir)

    assert result.returncode == 0, result.stderr
    assert not stale.exists(), "stale pre-rename copy survived the audit"
    assert (bin_dir / "audiobook-build-queue").is_symlink(), "canonical alias link was touched"
    assert "build-conversion-queue" in result.stdout


def test_keeps_copy_when_alias_link_missing(layout):
    target, _, bin_dir = layout
    stale = bin_dir / "build-conversion-queue"
    stale.write_text("x")

    _run(target, bin_dir)

    assert stale.exists(), "removed a file the install does not provably own"


def test_keeps_copy_when_alias_link_points_elsewhere(layout, tmp_path):
    target, _, bin_dir = layout
    stale = bin_dir / "build-conversion-queue"
    stale.write_text("x")
    other = tmp_path / "elsewhere"
    other.write_text("x")
    (bin_dir / "audiobook-build-queue").symlink_to(other)

    _run(target, bin_dir)

    assert stale.exists(), "removed a copy although the alias link is not this install's"


def test_keeps_old_name_when_it_is_a_symlink(layout):
    target, scripts, bin_dir = layout
    old_link = bin_dir / "build-conversion-queue"
    old_link.symlink_to(scripts / "build-conversion-queue")
    (bin_dir / "audiobook-build-queue").symlink_to(scripts / "build-conversion-queue")

    _run(target, bin_dir)

    assert old_link.is_symlink(), "a symlink at the old name is not a stale copy"


def test_dry_run_reports_and_keeps(layout):
    target, scripts, bin_dir = layout
    stale = bin_dir / "build-conversion-queue"
    stale.write_text("x")
    (bin_dir / "audiobook-build-queue").symlink_to(scripts / "build-conversion-queue")

    result = _run(target, bin_dir, dry_run=True)

    assert stale.exists(), "dry run deleted a file"
    assert "DRY-RUN" in result.stdout


def test_audit_and_cleanup_calls_it():
    upgrade = (REPO / "upgrade.sh").read_text()
    body = re.search(r"^audit_and_cleanup\s*\(\)\s*\{\n(.*?)^\}", upgrade, re.DOTALL | re.MULTILINE)
    assert body, "audit_and_cleanup not found"
    assert re.search(r"^\s*remove_pre_rename_bin_copies\b", body.group(1), re.MULTILINE), (
        "audit_and_cleanup does not call remove_pre_rename_bin_copies"
    )
