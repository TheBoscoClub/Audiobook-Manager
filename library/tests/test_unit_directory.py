"""Unit files live in the unit directory, not in /etc/systemd/system.

A system install keeps its unit files in /usr/local/lib/systemd/system, which
systemd searches (systemd.unit(5): units installed by the administrator).
/etc/systemd/system keeps what belongs to the administrator: drop-ins and the
links ``systemctl enable`` writes. Installs made before the move have regular
unit files in /etc/systemd/system; upgrade.sh takes them out one unit at a
time (Audiobook-Manager-b0my.1).

These run upgrade.sh's two helpers against scratch directories with a
``systemctl`` shell function standing in for the real one, so they need no
root, no systemd and no install.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

ROOT = Path(__file__).resolve().parents[2]
UPGRADE_SH = ROOT / "upgrade.sh"

UNIT_DIR = "/usr/local/lib/systemd/system"
LEGACY_DIR = "/etc/systemd/system"


@pytest.mark.parametrize(
    "script", ["install.sh", "upgrade.sh", "uninstall.sh", "scripts/install-manifest.sh"]
)
def test_every_script_names_the_same_two_directories(script):
    text = (ROOT / script).read_text()
    assert re.findall(r'^SYSTEM_UNIT_DIR="([^"]+)"', text, re.M) == [UNIT_DIR]
    assert re.findall(r'^SYSTEM_UNIT_LEGACY_DIR="([^"]+)"', text, re.M) == [LEGACY_DIR]


def _run(tmp_path: Path, body: str, is_enabled: str = "enabled") -> subprocess.CompletedProcess:
    """Run ``body`` with upgrade.sh sourced and both directories in tmp_path."""
    (tmp_path / "units").mkdir(exist_ok=True)
    (tmp_path / "legacy").mkdir(exist_ok=True)
    script = f"""
UPGRADE_SH_SOURCE_ONLY=1 source "{UPGRADE_SH}"
set +e
RED="" GREEN="" BLUE="" YELLOW="" NC=""
SYSTEM_UNIT_DIR="{tmp_path}/units"
SYSTEM_UNIT_LEGACY_DIR="{tmp_path}/legacy"
systemctl() {{
    echo "$*" >> "{tmp_path}/calls.log"
    if [[ "$1" == "is-enabled" ]]; then
        echo "{is_enabled}"
        [[ "{is_enabled}" == "enabled" ]]
        return
    fi
    return 0
}}
{body}
"""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60, check=False
    )


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_lookup_prefers_the_unit_directory_then_the_legacy_one(tmp_path):
    proc = _run(
        tmp_path,
        f"""
: > "{tmp_path}/legacy/both.service"; : > "{tmp_path}/units/both.service"
: > "{tmp_path}/legacy/old.service"
echo "both=$(_unit_file_path both.service)"
echo "old=$(_unit_file_path old.service)"
_unit_file_path gone.service; echo "gone-rc=$?"
""",
    )
    assert f"both={tmp_path}/units/both.service" in proc.stdout, proc.stdout + proc.stderr
    assert f"old={tmp_path}/legacy/old.service" in proc.stdout
    assert "gone-rc=1" in proc.stdout


def test_an_enabled_unit_is_moved_and_enabled_again(tmp_path):
    proc = _run(
        tmp_path,
        f"""
: > "{tmp_path}/legacy/a.service"; : > "{tmp_path}/units/a.service"
_retire_legacy_unit a.service ""
""",
    )
    assert not (tmp_path / "legacy" / "a.service").exists(), proc.stdout + proc.stderr
    assert (tmp_path / "units" / "a.service").exists()
    assert _calls(tmp_path) == [
        "is-enabled a.service",
        "disable a.service",
        "daemon-reload",
        "enable a.service",
    ]


@pytest.mark.parametrize("state", ["disabled", "static", "masked", "indirect"])
def test_a_unit_that_was_not_enabled_is_moved_and_left_as_it_was(tmp_path, state):
    proc = _run(
        tmp_path,
        f"""
: > "{tmp_path}/legacy/a.timer"; : > "{tmp_path}/units/a.timer"
_retire_legacy_unit a.timer ""
""",
        is_enabled=state,
    )
    assert not (tmp_path / "legacy" / "a.timer").exists(), proc.stdout + proc.stderr
    assert "enable a.timer" not in _calls(tmp_path)


def test_a_symlink_in_the_legacy_directory_is_left_alone(tmp_path):
    proc = _run(
        tmp_path,
        f"""
ln -s /dev/null "{tmp_path}/legacy/a.service"; : > "{tmp_path}/units/a.service"
_retire_legacy_unit a.service ""
""",
    )
    assert (tmp_path / "legacy" / "a.service").is_symlink(), proc.stdout + proc.stderr
    assert _calls(tmp_path) == []


def test_the_only_copy_of_a_unit_is_never_removed(tmp_path):
    proc = _run(
        tmp_path,
        f"""
: > "{tmp_path}/legacy/a.service"
_retire_legacy_unit a.service ""
""",
    )
    assert (tmp_path / "legacy" / "a.service").exists(), proc.stdout + proc.stderr
    assert _calls(tmp_path) == []
