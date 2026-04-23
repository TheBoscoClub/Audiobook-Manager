"""Regression guard for v8.3.8 upgrade-hardening fixes.

Pins the invariants that prevent the v8.3.7.1 prod regression from recurring:

1. Version-ordering: upgrade.sh captures pre-upgrade VERSION before any file
   write touches target/VERSION. Without this, apply_data_migrations's gate
   reads the already-overwritten VERSION and skips every data migration.
2. Migration gate weakening: installed_version is captured from the exported
   _DO_UPGRADE_PRE_VERSION env var; when unknown/empty, every migration runs
   unconditionally (idempotency guards handle repeats).
3. Release requirements mechanism: scripts/release-requirements.sh declares
   required config keys, systemd units, and DB columns — validator fails the
   upgrade on hard gaps.
4. Post-upgrade smoke probe: scripts/smoke_probe.sh actually exercises the
   running system (systemd is-active, /api/system/health, DB schema, RunPod
   endpoint reachability) and blocks "Successfully upgraded" on failure.
5. Install parity: install.sh writes streaming config stubs + runs the same
   release-requirements + smoke-probe gate before declaring install complete.

These tests are mechanical (grep/parse the scripts) so the invariants survive
as the files evolve — if someone removes the gate, the test fails immediately.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text()


# ──────────────────────────────────────────────────────────────────────────
# Version-ordering: do_upgrade captures pre-upgrade version before writing
# the new VERSION file.
# ──────────────────────────────────────────────────────────────────────────


def test_do_upgrade_captures_pre_version_before_file_work():
    """do_upgrade must call get_version "$target" BEFORE VERSION is overwritten.

    Without this, apply_data_migrations reads the new (just-written) VERSION
    and its gate rule "skip if installed > MIN_VERSION" fires on every
    migration — the exact bug that let v8.3.7.1 prod ship without migrations
    006 and 007.
    """
    upgrade = _read("upgrade.sh")
    func_match = re.search(
        r"^do_upgrade\s*\(\)\s*\{\n(.*?)^\}",
        upgrade,
        re.DOTALL | re.MULTILINE,
    )
    assert func_match, "do_upgrade function not found"
    body = func_match.group(1)

    # Find the FIRST call to get_version "$target" inside do_upgrade.
    first_get_version = body.find('get_version "$target"')
    # Find the FIRST write of $target/VERSION.
    # Either `cp ... VERSION $target/` or equivalent.
    version_write_match = re.search(r'cp\s+"\$\{?project\}?/VERSION"\s+"\$target', body)
    assert version_write_match, (
        "Could not locate target VERSION write in do_upgrade — the ordering "
        "invariant can't be enforced mechanically if the write is gone."
    )
    version_write_pos = version_write_match.start()

    assert first_get_version != -1, (
        'do_upgrade must call get_version "$target" to capture pre-upgrade '
        "version before any file write."
    )
    assert first_get_version < version_write_pos, (
        f"do_upgrade reads VERSION at offset {first_get_version} but writes "
        f"the new VERSION at offset {version_write_pos} — pre-upgrade version "
        "is being read AFTER the overwrite. Every data migration's version "
        "gate will see installed==new_version and skip."
    )


def test_do_upgrade_exports_pre_version_env_var():
    """do_upgrade must export _DO_UPGRADE_PRE_VERSION so apply_data_migrations sees it."""
    upgrade = _read("upgrade.sh")
    assert re.search(
        r"export\s+_DO_UPGRADE_PRE_VERSION=",
        upgrade,
    ), (
        "do_upgrade must export _DO_UPGRADE_PRE_VERSION so that the shared "
        "apply_data_migrations function (also reachable via the no-upgrade-needed "
        "path) uses the pre-upgrade version, not the just-written one."
    )


def test_apply_data_migrations_prefers_exported_pre_version():
    """apply_data_migrations must use _DO_UPGRADE_PRE_VERSION when set."""
    upgrade = _read("upgrade.sh")
    func_match = re.search(
        r"^apply_data_migrations\s*\(\)\s*\{\n(.*?)^\}",
        upgrade,
        re.DOTALL | re.MULTILINE,
    )
    assert func_match, "apply_data_migrations function not found"
    body = func_match.group(1)
    assert re.search(
        r'if\s+\[\[\s+-n\s+"\$\{?_DO_UPGRADE_PRE_VERSION',
        body,
    ), (
        "apply_data_migrations must prefer $_DO_UPGRADE_PRE_VERSION over "
        "reading target/VERSION — the file may have already been rewritten "
        "by the time this function runs."
    )


def test_migration_gate_runs_when_installed_version_empty():
    """Belt-and-suspenders: if installed_version is empty/unknown, every migration runs."""
    upgrade = _read("upgrade.sh")
    func_match = re.search(
        r"^apply_data_migrations\s*\(\)\s*\{\n(.*?)^\}",
        upgrade,
        re.DOTALL | re.MULTILINE,
    )
    body = func_match.group(1)
    # The gate guard must require installed_version to be BOTH non-empty AND
    # not "unknown" before it's allowed to short-circuit.
    assert re.search(
        r'if\s+\[\[\s+-n\s+"\$installed_version"\s+\]\]\s+&&\s+'
        r'\[\[\s+"\$installed_version"\s+!=\s+"unknown"\s+\]\]\s*;\s*then',
        body,
    ), (
        "Gate must treat empty OR 'unknown' installed_version as "
        "'must run' — never skip when we can't be sure. The previous gate "
        "only checked != 'unknown', so an empty string would slip through "
        "and silently skip migrations."
    )


# ──────────────────────────────────────────────────────────────────────────
# Release requirements manifest
# ──────────────────────────────────────────────────────────────────────────


def test_release_requirements_script_exists_and_is_executable():
    path = REPO / "scripts/release-requirements.sh"
    assert path.exists(), "scripts/release-requirements.sh must exist"
    assert path.stat().st_mode & 0o111, "release-requirements.sh must be executable"


def test_release_requirements_declares_required_arrays():
    """The manifest must declare REQUIRED_CONFIG_KEYS, REQUIRED_SYSTEMD_UNITS, REQUIRED_DB_COLUMNS."""
    body = _read("scripts/release-requirements.sh")
    for sym in (
        "REQUIRED_CONFIG_KEYS",
        "REQUIRED_SYSTEMD_UNITS",
        "REQUIRED_DB_COLUMNS",
        "validate_release_requirements",
    ):
        assert re.search(rf"\b{sym}\b", body), (
            f"release-requirements.sh must define {sym} — validator contract broken"
        )


def test_release_requirements_contains_streaming_db_columns():
    """The DB columns that killed v8.3.7.1 prod must be in the REQUIRED_DB_COLUMNS list."""
    body = _read("scripts/release-requirements.sh")
    for col in (
        "streaming_segments.retry_count",
        "streaming_segments.source_vtt_content",
        "audiobooks.chapter_count",
    ):
        assert col in body, (
            f"REQUIRED_DB_COLUMNS must include {col} — that's the exact schema "
            "gap that broke streaming on prod post-8.3.7.1 upgrade."
        )


def test_release_requirements_contains_streaming_service():
    """audiobook-stream-translate.service must be in REQUIRED_SYSTEMD_UNITS."""
    body = _read("scripts/release-requirements.sh")
    assert "audiobook-stream-translate.service" in body, (
        "REQUIRED_SYSTEMD_UNITS must include audiobook-stream-translate.service — "
        "otherwise the streaming pipeline can be missing its worker and the "
        "validator won't notice."
    )


# ──────────────────────────────────────────────────────────────────────────
# Smoke probe
# ──────────────────────────────────────────────────────────────────────────


def test_smoke_probe_script_exists_and_is_executable():
    path = REPO / "scripts/smoke_probe.sh"
    assert path.exists(), "scripts/smoke_probe.sh must exist"
    assert path.stat().st_mode & 0o111, "smoke_probe.sh must be executable"


def test_smoke_probe_sources_release_requirements():
    body = _read("scripts/smoke_probe.sh")
    assert re.search(
        r'source\s+"?\$\{?SCRIPT_DIR\}?/release-requirements\.sh"?',
        body,
    ), "smoke_probe.sh must source release-requirements.sh for shared arrays"


def test_smoke_probe_defines_run_smoke_probe():
    body = _read("scripts/smoke_probe.sh")
    assert re.search(
        r"^run_smoke_probe\s*\(\)",
        body,
        re.MULTILINE,
    ), "smoke_probe.sh must define run_smoke_probe as its entry point"


def test_smoke_probe_exits_nonzero_on_failure():
    """Probe must return 1 on any hard failure (upgrade.sh relies on this)."""
    body = _read("scripts/smoke_probe.sh")
    # run_smoke_probe's last meaningful branch: if _smoke_fail > 0 → return 1.
    assert re.search(
        r"if\s+\[\[\s+\$_smoke_fail\s+-eq\s+0\s+\]\]",
        body,
    ), "smoke_probe.sh must distinguish pass from fail via _smoke_fail counter"


# ──────────────────────────────────────────────────────────────────────────
# upgrade.sh wiring
# ──────────────────────────────────────────────────────────────────────────


def test_upgrade_sh_invokes_smoke_probe_before_success_banner():
    """upgrade.sh must fail the upgrade if smoke_probe.sh returns nonzero."""
    upgrade = _read("upgrade.sh")
    # Both success paths (do_github_upgrade and the project-tree main exit)
    # must call smoke_probe.sh before printing their "Successfully upgraded"
    # or "Upgrade complete!" banner. Match only actual echo statements, not
    # comments or docstrings that mention the strings.
    echo_banners = list(
        re.finditer(
            r'^\s*echo\s+-e\s+"\$\{GREEN\}(?:Successfully upgraded|Upgrade complete)',
            upgrade,
            re.MULTILINE,
        )
    )
    assert len(echo_banners) >= 2, (
        "upgrade.sh should have two echo banners (github + project-tree). "
        f"Found {len(echo_banners)} — can't enforce gate on all paths."
    )
    # smoke_probe invocation must appear BEFORE each banner within a reasonable
    # window (100 lines / ~4000 chars).
    for banner in echo_banners:
        preceding = upgrade[max(0, banner.start() - 4000) : banner.start()]
        assert "smoke_probe.sh" in preceding, (
            f"Success banner at char {banner.start()} has no smoke_probe.sh "
            "invocation in the preceding ~4000 chars. Gate is not enforced "
            "on this path."
        )


def test_upgrade_sh_aborts_on_smoke_probe_failure():
    """On smoke-probe failure, upgrade.sh must exit 1 (not continue).

    Match `bash ... smoke_probe.sh || { ... exit 1 ... }` without assuming
    the block is brace-balanced at the regex level (substitutions like
    ${release_dir} confuse [^}] matching). Instead, find every smoke_probe
    invocation and verify `exit 1` appears within a reasonable window of
    each one.
    """
    upgrade = _read("upgrade.sh")
    invocations = list(re.finditer(r'bash\s+"[^"]*smoke_probe\.sh"', upgrade))
    assert len(invocations) >= 2, (
        f"Expected smoke_probe.sh invocations in at least 2 places, found {len(invocations)}."
    )
    for m in invocations:
        window = upgrade[m.start() : m.start() + 1500]
        assert re.search(r"exit\s+1", window), (
            "upgrade.sh must hard-fail (exit 1) after smoke_probe.sh returns "
            f"nonzero. No `exit 1` within 1500 chars of invocation at "
            f"char {m.start()}."
        )


# ──────────────────────────────────────────────────────────────────────────
# install.sh parity
# ──────────────────────────────────────────────────────────────────────────


def test_install_sh_writes_streaming_config_stubs():
    """install.sh's audiobooks.conf template must include commented streaming stubs."""
    body = _read("install.sh")
    for stub in (
        "AUDIOBOOKS_DEEPL_API_KEY",
        "AUDIOBOOKS_RUNPOD_API_KEY",
        "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT",
        "AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT",
    ):
        assert stub in body, (
            f"install.sh must write a commented stub for {stub} into "
            "audiobooks.conf — otherwise fresh installs have to guess which "
            "keys to add for streaming. This is the exact gap that kept prod "
            "without RunPod endpoints for weeks."
        )


def test_install_sh_invokes_smoke_probe():
    """install.sh must call smoke_probe.sh before declaring install complete."""
    body = _read("install.sh")
    assert "smoke_probe.sh" in body, (
        "install.sh must invoke smoke_probe.sh. Same contract as upgrade.sh — "
        "files copied + systemd enabled is not proof that the thing works."
    )


# ──────────────────────────────────────────────────────────────────────────
# upgrade.sh audit_and_cleanup orphan-systemd fix (earlier work, re-pinned)
# ──────────────────────────────────────────────────────────────────────────


def test_audit_and_cleanup_accepts_project_arg():
    """audit_and_cleanup must take $project as third arg (fix for systemd wipe)."""
    upgrade = _read("upgrade.sh")
    func_match = re.search(
        r"^audit_and_cleanup\s*\(\)\s*\{\n(.*?)^\}",
        upgrade,
        re.DOTALL | re.MULTILINE,
    )
    body = func_match.group(1)
    assert re.search(r"local\s+project=", body), (
        "audit_and_cleanup must accept a project arg so its systemd orphan "
        "check has an authoritative source (the release being installed), "
        "not a blind fallback to SCRIPT_DIR/systemd."
    )
