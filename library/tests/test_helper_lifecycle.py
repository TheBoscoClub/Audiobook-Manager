"""Verify upgrade-helper-process implements the 9-step lifecycle."""

from pathlib import Path

import pytest

HELPER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "upgrade-helper-process"

pytestmark = pytest.mark.skipif(
    not HELPER_PATH.is_file(),
    reason="upgrade-helper-process not at project path (deployed installation)",
)

REQUIRED_STAGES = [
    "preflight_recheck",
    "backing_up",
    "stopping_services",
    "upgrading",
    "rebuilding_venv",
    "migrating_config",
    "starting_services",
    "verifying",
    "complete",
]


def test_all_lifecycle_stages_present():
    """Helper must reference all 9 lifecycle stages."""
    content = HELPER_PATH.read_text()
    missing = [s for s in REQUIRED_STAGES if s not in content]
    assert missing == [], f"Missing lifecycle stages: {missing}"


def test_skip_service_lifecycle_flag_passed():
    """Helper must pass --skip-service-lifecycle --yes to upgrade.sh."""
    content = HELPER_PATH.read_text()
    assert "--skip-service-lifecycle" in content, "Must pass --skip-service-lifecycle to upgrade.sh"
    assert "--yes" in content, "Must pass --yes to upgrade.sh"


def test_no_echo_y_pipe_hack():
    """Helper must not use 'echo y |' pipe hack."""
    content = HELPER_PATH.read_text()
    assert (
        'echo "y"' not in content and "echo 'y'" not in content and "echo y |" not in content
    ), "Must use --yes flag, not echo y pipe hack"


def test_new_request_fields_parsed():
    """Helper must parse force, major_version, version from request JSON."""
    content = HELPER_PATH.read_text()
    for field in ["force", "major_version", "version"]:
        assert field in content, f"Must parse '{field}' from request JSON"


def test_force_and_major_version_forwarded_to_upgrade_sh():
    """--force and --major-version must be forwarded to upgrade.sh command."""
    content = HELPER_PATH.read_text()
    assert '"--force"' in content, "Must pass --force flag to upgrade.sh when force=true"
    assert (
        '"--major-version"' in content
    ), "Must pass --major-version flag to upgrade.sh when major_version=true"


def test_all_services_in_stop_order():
    """Stop order must include ALL audiobook.target services."""
    content = HELPER_PATH.read_text()
    required_services = [
        "audiobook-downloader.timer",
        "audiobook-shutdown-saver",
        "audiobook-scheduler",
        "audiobook-mover",
        "audiobook-converter",
        "audiobook-redirect",
        "audiobook-proxy",
        "audiobook-api",
    ]
    for svc in required_services:
        assert svc in content, f"Service '{svc}' missing from helper lifecycle"


def test_no_hardcoded_paths():
    """Helper must source audiobook-config.sh and use config variables for paths."""
    content = HELPER_PATH.read_text()
    assert (
        "audiobook-config.sh" in content
    ), "Helper must source audiobook-config.sh for path variables"
    for line in content.splitlines():
        if line.startswith("CONTROL_DIR=") and "/var/lib/audiobooks" in line:
            assert False, "CONTROL_DIR must use $AUDIOBOOKS_VAR_DIR, not hardcoded path"
        if line.startswith("INSTALL_DIR=") and "/opt/audiobooks" in line:
            assert False, "INSTALL_DIR must use config variable, not hardcoded /opt/audiobooks"


def test_final_status_written_before_service_start():
    """Final status must be written BEFORE starting services (spec: Status File Durability)."""
    content = HELPER_PATH.read_text()
    result_write_pos = content.find('write_status "false" "complete"')
    if result_write_pos < 0:
        result_write_pos = content.find('"upgrade_result"')
    start_services_pos = content.find('"starting_services"')
    assert result_write_pos > 0, "Must write upgrade result to status file"
    assert start_services_pos > 0, "Must have starting_services stage"
    assert result_write_pos < start_services_pos, (
        "Upgrade result must be written to status file BEFORE starting services "
        f"(result at pos {result_write_pos}, start_services at pos {start_services_pos}). "
        "Per spec: 'The helper writes the final upgrade result... BEFORE restarting the API.'"
    )
