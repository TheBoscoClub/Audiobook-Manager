"""Regression guard for Audiobook-Manager-c8l.

audiobook-shutdown-saver.service previously declared `User=root`, but its
ExecStart (`audiobook-save-staging`) calls `require_audiobooks_user()` which
exits 1 unless the invoking UID matches the `audiobooks` user. Result: every
shutdown attempt to flush /tmp/audiobook-staging to the library FAILED with
"audiobook-save-staging must run as the audiobooks user" — staging files
were never persisted on reboot.

The fix is to declare `User=audiobooks Group=audiobooks` in the unit. This
test enforces that invariant going forward.

Discovered 2026-04-27 during v8.3.9 prod upgrade verification (the helper's
post-upgrade restart loop logged "Failed to start audiobook-shutdown-saver"
and journalctl showed the user-mismatch error).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNIT_FILE = REPO / "systemd" / "audiobook-shutdown-saver.service"


def _service_section() -> dict[str, str]:
    """Parse `Key=Value` pairs from the [Service] section of the unit file."""
    text = UNIT_FILE.read_text()
    in_service = False
    pairs: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_service = line == "[Service]"
            continue
        if not in_service or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def test_runs_as_audiobooks_user():
    """User= must be 'audiobooks' so save-staging's user gate doesn't reject it."""
    pairs = _service_section()
    assert pairs.get("User") == "audiobooks", (
        f"audiobook-shutdown-saver.service [Service] User must be 'audiobooks', "
        f"got {pairs.get('User')!r}. Running as root causes "
        f"audiobook-save-staging to exit 1 with require_audiobooks_user error, "
        f"meaning tmpfs staging is never persisted on shutdown. "
        f"See Audiobook-Manager-c8l."
    )


def test_runs_with_audiobooks_group():
    """Group= must be 'audiobooks' for consistent ownership of any created files."""
    pairs = _service_section()
    assert pairs.get("Group") == "audiobooks", (
        f"audiobook-shutdown-saver.service [Service] Group must be 'audiobooks', "
        f"got {pairs.get('Group')!r}."
    )


def test_execstart_points_to_save_staging():
    """ExecStart wiring guard — protects against the unit getting silently rewired."""
    pairs = _service_section()
    assert pairs.get("ExecStart") == "/usr/local/bin/audiobook-save-staging", (
        f"audiobook-shutdown-saver.service ExecStart must run audiobook-save-staging, "
        f"got {pairs.get('ExecStart')!r}."
    )
