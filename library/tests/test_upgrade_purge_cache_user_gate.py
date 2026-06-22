"""
Regression tests for upgrade.sh purge_cloudflare_cache (inline-curl design).

Design: purge_cloudflare_cache purges the Cloudflare CDN cache INLINE via curl
rather than delegating to a standalone CLI script. The operator-specific
`audiobook-purge-cache` script was removed from the generic repo; the only
remaining purge pathways are this inline function (used by upgrade.sh after
deploying web assets) and the `/api/system/purge-cache` API endpoint.

The inline function:
  - resolves the operator's api-keys.env via SUDO_USER's home (so it works
    when upgrade.sh runs under sudo, where $HOME is /root),
  - skips gracefully (return 0) when Cloudflare credentials are absent,
  - resolves CF_ZONE_ID from the environment, then from
    /etc/audiobooks/audiobooks.conf, and skips gracefully if still unset,
  - otherwise POSTs {"purge_everything":true} to the Cloudflare zone
    purge_cache endpoint and treats any failure as non-fatal.

These tests are pure-grep regression guards — they don't actually invoke the
function (which would require Cloudflare credentials and live network).
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


def test_purge_is_inline_curl_not_script_delegation():
    """
    The function must purge INLINE via curl against the Cloudflare zone
    purge_cache endpoint — it must NOT delegate to the removed
    audiobook-purge-cache CLI script, nor probe/sudo for it.
    """
    body = _read_purge_function()
    assert "curl" in body, "purge_cloudflare_cache must do an inline curl"
    assert "/zones/" in body and "purge_cache" in body, (
        "purge_cloudflare_cache must POST to the Cloudflare "
        ".../zones/<id>/purge_cache endpoint inline"
    )
    # The operator-specific CLI script is gone — no references may remain.
    assert "audiobook-purge-cache" not in body, (
        "purge_cloudflare_cache must not reference the removed "
        "audiobook-purge-cache CLI script"
    )
    assert "command -v audiobook-purge-cache" not in body, (
        "purge_cloudflare_cache must not probe for the removed CLI script"
    )
    assert "sudo -u audiobooks" not in body, (
        "purge_cloudflare_cache no longer drops privileges to the audiobooks "
        "user — it purges inline as the calling operator"
    )


def test_purge_skips_gracefully_when_credentials_missing():
    """Missing Cloudflare credentials must short-circuit with a non-fatal return 0."""
    body = _read_purge_function()
    assert '-z "$CF_GLOBAL_API_KEY"' in body and '-z "$CF_AUTH_EMAIL"' in body, (
        "purge_cloudflare_cache must guard on empty CF_GLOBAL_API_KEY / CF_AUTH_EMAIL"
    )
    assert "no credentials" in body, (
        "purge_cloudflare_cache must log a skip message when credentials are missing"
    )
    assert "return 0" in body, (
        "purge_cloudflare_cache must return 0 (non-fatal) on the missing-credentials path"
    )


def test_purge_skips_gracefully_when_zone_id_missing():
    """Missing CF_ZONE_ID must short-circuit with a non-fatal return 0."""
    body = _read_purge_function()
    assert '-z "$CF_ZONE_ID"' in body, (
        "purge_cloudflare_cache must guard on an empty CF_ZONE_ID"
    )
    assert "CF_ZONE_ID not set" in body, (
        "purge_cloudflare_cache must log a skip message when CF_ZONE_ID is unset"
    )
    # CF_ZONE_ID may be sourced from the system config when not in the environment.
    assert "/etc/audiobooks/audiobooks.conf" in body, (
        "purge_cloudflare_cache must fall back to /etc/audiobooks/audiobooks.conf "
        "for CF_ZONE_ID when it is not set in the environment"
    )


def test_purge_sources_operator_api_keys_via_sudo_user_home():
    """
    Credentials live in the operator's ~/.config/api-keys.env. When upgrade.sh
    runs under sudo, $HOME is /root — the function must resolve SUDO_USER's
    home so it sources the operator's keys, not root's.
    """
    body = _read_purge_function()
    assert "SUDO_USER" in body, (
        "purge_cloudflare_cache must consult SUDO_USER to find the operator's home"
    )
    assert "getent passwd" in body, (
        "purge_cloudflare_cache must resolve the operator's home via getent passwd"
    )
    assert "api-keys.env" in body, (
        "purge_cloudflare_cache must source the operator's ~/.config/api-keys.env"
    )
    assert "CF_KEYS_FILE" in body, (
        "purge_cloudflare_cache must honor the CF_KEYS_FILE override"
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
