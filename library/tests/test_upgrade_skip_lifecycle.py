"""Verify --skip-service-lifecycle flag is parsed and respected."""

import subprocess
from pathlib import Path

import pytest

UPGRADE_SH = Path(__file__).resolve().parents[2] / "upgrade.sh"

pytestmark = pytest.mark.skipif(
    not UPGRADE_SH.is_file(), reason="upgrade.sh not present (deployed installation)"
)


def test_skip_lifecycle_flag_accepted():
    """upgrade.sh must accept --skip-service-lifecycle without error."""
    result = subprocess.run(
        ["bash", "-n", str(UPGRADE_SH)],
        capture_output=True,
        text=True,  # syntax check only
    )
    assert result.returncode == 0, f"Syntax error in upgrade.sh: {result.stderr}"


def test_skip_lifecycle_flag_in_source():
    """upgrade.sh source must contain SKIP_SERVICE_LIFECYCLE variable."""
    content = UPGRADE_SH.read_text()
    assert "SKIP_SERVICE_LIFECYCLE" in content, "Missing SKIP_SERVICE_LIFECYCLE variable"
    assert "--skip-service-lifecycle" in content, (
        "Missing --skip-service-lifecycle in argument parser"
    )


def test_skip_lifecycle_not_in_help():
    """--skip-service-lifecycle is internal and must NOT appear in --help output."""
    result = subprocess.run(["bash", str(UPGRADE_SH), "--help"], capture_output=True, text=True)
    assert "--skip-service-lifecycle" not in result.stdout, (
        "--skip-service-lifecycle should not appear in --help (internal flag)"
    )
