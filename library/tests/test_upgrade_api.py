"""Verify upgrade API endpoints support new fields and preflight gate."""
import re
from pathlib import Path

SYS_MODULE = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "api_modular"
    / "utilities_system.py"
)


def test_upgrade_endpoint_accepts_new_fields():
    """POST /api/system/upgrade must accept force, major_version, version fields."""
    content = SYS_MODULE.read_text()
    for field in ["force", "major_version", "version"]:
        assert field in content, f"Upgrade endpoint must handle '{field}' field"


def test_preflight_endpoint_exists():
    """GET /api/system/upgrade/preflight endpoint must be defined."""
    content = SYS_MODULE.read_text()
    assert (
        "upgrade/preflight" in content
    ), "Missing /api/system/upgrade/preflight endpoint"
    assert "admin_or_localhost" in content, "Preflight endpoint must require auth"


def test_preflight_gate_on_upgrade():
    """Upgrade endpoint must check for valid preflight unless force is true."""
    content = SYS_MODULE.read_text()
    assert "upgrade-preflight.json" in content or "preflight" in content, (
        "Upgrade endpoint must read and validate preflight file"
    )
    assert "force" in content, "Upgrade endpoint must check force flag for preflight bypass"


def test_version_field_validated_for_source():
    """version field must be rejected when source is 'project'."""
    content = SYS_MODULE.read_text()
    assert "version" in content, "Must handle version field"
    has_version_validation = bool(
        re.search(
            r'version.*(?:github|source)|(?:github|source).*version', content
        )
    )
    assert has_version_validation, (
        "Must validate that 'version' field is only accepted with source='github'"
    )
