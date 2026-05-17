"""
Regression tests for upgrade.sh purge_cloudflare_cache user-gate handling.

Bug: when audiobook-purge-cache enforced require_audiobooks_user (added in
4bd9917d), upgrade.sh's purge_cloudflare_cache function began calling the
script directly without dropping privileges. The script then errored out
("must run as the audiobooks user") and the CDN cache was never purged.

Fix: purge_cloudflare_cache now (a) prefers the INSTALLED script (PATH)
over the project-tree copy because the installed script's adjacent libs
and configs are owned by the audiobooks user, (b) wraps the invocation
with `sudo -u audiobooks env CF_*=...` to forward Cloudflare credentials
across the privilege drop.

These tests are pure-grep regression guards — they don't actually invoke
the script (which would require Cloudflare credentials and live network).
"""

import re
from pathlib import Path

import pytest
pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).parent.parent.parent
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"


def _read_purge_function() -> str:
    """Extract the purge_cloudflare_cache function body from upgrade.sh."""
    text = UPGRADE_SH.read_text()
    match = re.search(
        r"^purge_cloudflare_cache\(\) \{\n(.*?)^\}\n",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "purge_cloudflare_cache() not found in upgrade.sh"
    return match.group(1)


def test_purge_cloudflare_cache_function_exists():
    """upgrade.sh must define purge_cloudflare_cache for both local and remote upgrade flows."""
    body = _read_purge_function()
    assert len(body) > 100, "purge_cloudflare_cache function is suspiciously short"


def test_purge_invocation_uses_sudo_to_audiobooks_user():
    """The purge script must be invoked under sudo -u audiobooks (script enforces require_audiobooks_user)."""
    body = _read_purge_function()
    assert "sudo -u audiobooks" in body, (
        "purge_cloudflare_cache must invoke the purge script under "
        "`sudo -u audiobooks` — the script enforces require_audiobooks_user "
        "and will reject root/operator invocations"
    )


def test_purge_invocation_forwards_cloudflare_credentials():
    """sudo -u strips env vars by default — purge_cloudflare_cache must forward CF_* explicitly."""
    body = _read_purge_function()
    # All three CF env vars must be forwarded across the privilege drop
    for var in ("CF_GLOBAL_API_KEY", "CF_AUTH_EMAIL", "CF_ZONE_ID"):
        assert f'"{var}=' in body, (
            f"purge_cloudflare_cache must forward {var} across `sudo -u audiobooks` "
            f"(audiobooks user has no ~/.config/api-keys.env of its own)"
        )


def test_purge_prefers_installed_script_over_project_copy():
    """
    Discovery order matters: when running locally as `bosco` from the project
    tree, the project's scripts/audiobook-purge-cache exists but its lib/ +
    config.env are 0600 owned by bosco — unreadable by the audiobooks user
    after privilege drop. The installed copy in /opt/audiobooks/scripts/ has
    audiobooks-readable adjacent files.
    """
    body = _read_purge_function()
    # Find the order of the three discovery paths
    command_v_pos = body.find("command -v audiobook-purge-cache")
    project_pos = body.find('"${SCRIPT_DIR}/scripts/audiobook-purge-cache"')
    assert command_v_pos != -1, "command -v audiobook-purge-cache discovery missing"
    assert project_pos != -1, "Project-tree fallback discovery missing"
    assert command_v_pos < project_pos, (
        "command -v (PATH) discovery must precede project-tree discovery — "
        "the installed script has audiobooks-readable adjacent files, the "
        "project copy does not"
    )


def test_purge_handles_already_audiobooks_user_path():
    """If somehow already running as audiobooks (install.sh edge case), don't double-sudo."""
    body = _read_purge_function()
    assert 'current_user="$(id -un)"' in body or "current_user=" in body, (
        "purge_cloudflare_cache must check current user before invoking sudo"
    )
    assert '"$current_user" == "audiobooks"' in body, (
        "purge_cloudflare_cache must detect the already-audiobooks case to "
        "avoid an unnecessary `sudo -u audiobooks` re-invocation"
    )


def test_purge_handles_missing_sudo_capability():
    """If sudo is unavailable or operator can't sudo to audiobooks, skip gracefully."""
    body = _read_purge_function()
    assert "sudo -n -u audiobooks true" in body, (
        "purge_cloudflare_cache must probe sudo capability before invocation"
    )


def test_legacy_install_dir_check_recognises_compat_symlink():
    """
    /usr/local/lib/audiobooks is INTENTIONALLY created as a backward-compat
    symlink → ${APP_DIR}/lib by install.sh. The audit_and_cleanup section
    must distinguish between a real legacy directory (warn + remove) and
    the expected compat symlink (no-op).
    """
    text = UPGRADE_SH.read_text()
    # Must use -L (symlink test) before -d (directory test)
    assert '[[ -L "/usr/local/lib/audiobooks" ]]' in text, (
        "audit_and_cleanup must use -L test for /usr/local/lib/audiobooks "
        "before treating it as a legacy real directory"
    )
    # Must verify the symlink points to the canonical lib location
    assert "readlink -f /usr/local/lib/audiobooks" in text, (
        "audit_and_cleanup must verify the symlink resolves to the canonical "
        "lib location before declaring it healthy"
    )
