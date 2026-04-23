"""Regression tests for v8.3.8 UID/GID auto-probe (install.sh + migrate script).

Motivation: hardcoding UID=935 GID=934 fails on hosts where either slot is
taken by an unrelated account. The probe scans for a free matched pair
(UID == GID) starting at a preferred value and walking upward. This
preserves cross-host portability while eliminating install-time collisions.

Pins:
1. install.sh has an _probe_free_uidgid helper that walks upward from a
   preferred value, returning the first N where BOTH UID N and GID N are free.
2. install.sh defaults the preferred UID and GID to 935 (matched pair) unless
   AUDIOBOOKS_PREFERRED_UID/GID env overrides are provided.
3. install.sh persists the resolved IDs as AUDIOBOOKS_UID / AUDIOBOOKS_GID
   (exported at resolution time) for downstream consumers.
4. migrate-audiobooks-uid.sh accepts --uid / --gid or auto-probes, also
   walking upward from 935.
5. upgrade.sh detects when audiobooks UID != GID and offers (in interactive
   mode only) to run the migration.
6. Dockerfile accepts AUDIOBOOKS_UID and AUDIOBOOKS_GID as build-args with
   matched defaults.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text()


def test_install_sh_has_probe_helper():
    body = _read("install.sh")
    assert re.search(
        r"_probe_free_uidgid\s*\(\)\s*\{",
        body,
    ), "install.sh must define _probe_free_uidgid helper"
    # The helper must check BOTH getent passwd $n AND getent group $n.
    probe_match = re.search(
        r"_probe_free_uidgid\s*\(\)\s*\{(.*?)^\s*\}",
        body,
        re.DOTALL | re.MULTILINE,
    )
    assert probe_match, "could not locate probe helper body"
    probe_body = probe_match.group(1)
    assert "getent passwd" in probe_body
    assert "getent group" in probe_body


def test_install_sh_defaults_matched_pair():
    """Preferred UID and GID default to the same number (matched)."""
    body = _read("install.sh")
    uid_match = re.search(r'AUDIOBOOKS_PREFERRED_UID="\$\{AUDIOBOOKS_PREFERRED_UID:-(\d+)\}"', body)
    gid_match = re.search(r'AUDIOBOOKS_PREFERRED_GID="\$\{AUDIOBOOKS_PREFERRED_GID:-(\d+)\}"', body)
    assert uid_match and gid_match, "install.sh must define both preferred UID and GID defaults"
    assert uid_match.group(1) == gid_match.group(1), (
        f"install.sh defaults are NOT matched: UID={uid_match.group(1)} "
        f"GID={gid_match.group(1)}. Matched pairs are the whole point."
    )


def test_install_sh_honors_env_overrides():
    """Operator can override the preferred UID/GID via env vars."""
    body = _read("install.sh")
    # Both variables use ${VAR:-default} pattern so operators can override.
    assert "${AUDIOBOOKS_PREFERRED_UID:-" in body
    assert "${AUDIOBOOKS_PREFERRED_GID:-" in body


def test_install_sh_exports_resolved_ids():
    """Downstream components need the resolved values."""
    body = _read("install.sh")
    assert "export AUDIOBOOKS_UID" in body
    assert "AUDIOBOOKS_GID" in body


def test_migrate_script_accepts_uid_gid_args():
    body = _read("scripts/migrate-audiobooks-uid.sh")
    assert "--uid" in body
    assert "--gid" in body
    # Must support auto-probe when no args given (either-neither validation).
    assert re.search(r"_probe_free_matched_id", body), (
        "migrate script must auto-probe when --uid/--gid not provided"
    )


def test_upgrade_sh_detects_uid_gid_mismatch():
    body = _read("upgrade.sh")
    # The prompt must only fire when UID != GID AND we're interactive.
    mismatch_gate = re.search(
        r'if\s+\[\[\s+"\$_ab_uid"\s+!=\s+"\$_ab_gid"\s+\]\].*?AUTO_YES.*?DRY_RUN',
        body,
        re.DOTALL,
    )
    assert mismatch_gate, (
        "upgrade.sh must gate the mismatch prompt on UID != GID AND AUTO_YES "
        "not true AND DRY_RUN not true"
    )
    # Within a reasonable window of the gate, the migration script must be
    # invoked (the full if/fi block is ~40 lines, so look in a 3000-char window).
    start = mismatch_gate.start()
    window = body[start : start + 3000]
    assert "migrate-audiobooks-uid.sh" in window, (
        "upgrade.sh mismatch-handling block must invoke "
        "scripts/migrate-audiobooks-uid.sh when operator opts in"
    )


def test_dockerfile_accepts_uid_gid_build_args():
    body = _read("Dockerfile")
    assert re.search(r"ARG\s+AUDIOBOOKS_UID=\d+", body), (
        "Dockerfile must accept AUDIOBOOKS_UID as a build-arg"
    )
    assert re.search(r"ARG\s+AUDIOBOOKS_GID=\d+", body), (
        "Dockerfile must accept AUDIOBOOKS_GID as a build-arg"
    )
    # The groupadd/useradd lines must consume the args (not hardcoded 934/935).
    assert re.search(r'groupadd.*--gid\s+"?\$AUDIOBOOKS_GID"?', body), (
        "groupadd must use $AUDIOBOOKS_GID build-arg, not a literal"
    )
    assert re.search(r'useradd.*--uid\s+"?\$AUDIOBOOKS_UID"?', body), (
        "useradd must use $AUDIOBOOKS_UID build-arg, not a literal"
    )


def test_dockerfile_defaults_match():
    body = _read("Dockerfile")
    uid_match = re.search(r"ARG\s+AUDIOBOOKS_UID=(\d+)", body)
    gid_match = re.search(r"ARG\s+AUDIOBOOKS_GID=(\d+)", body)
    assert uid_match and gid_match
    assert uid_match.group(1) == gid_match.group(1), (
        f"Dockerfile UID/GID defaults do not match: {uid_match.group(1)} vs {gid_match.group(1)}"
    )
