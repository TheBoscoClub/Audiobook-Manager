"""Verify preflight check infrastructure exists in upgrade.sh."""
from pathlib import Path

UPGRADE_SH = Path(__file__).resolve().parents[2] / "upgrade.sh"


def test_preflight_functions_exist():
    """upgrade.sh must define generate_preflight and validate_preflight."""
    content = UPGRADE_SH.read_text()
    assert "generate_preflight()" in content, "Missing generate_preflight() function"
    assert "validate_preflight()" in content, "Missing validate_preflight() function"


def test_preflight_file_path_defined():
    """Preflight file path must be defined using config variable."""
    content = UPGRADE_SH.read_text()
    assert "upgrade-preflight.json" in content, "Missing preflight JSON filename"
    import re
    hardcoded = re.findall(r'/var/lib/audiobooks/\.control/upgrade-preflight', content)
    assert len(hardcoded) == 0, "Preflight path must use $AUDIOBOOKS_VAR_DIR, not hardcoded path"


def test_force_bypasses_preflight():
    """When --force is set, preflight validation must be skipped."""
    content = UPGRADE_SH.read_text()
    assert "FORCE" in content, "Missing FORCE variable for --force flag"
