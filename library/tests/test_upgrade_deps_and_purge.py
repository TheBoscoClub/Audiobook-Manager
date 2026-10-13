"""Unit tests for Audiobook-Manager-gwq (pip --upgrade + freeze diff), -soo
(manifest-driven obsolete-root purge) and the Caddy status-mirror cleanup.

Everything runs against tmp dirs with stubbed pip / stubbed upgrade.sh; the real
shell code is sliced out of (or executed from) the repo scripts, so a mutation of
the script turns these tests red.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

ROOT = Path(__file__).resolve().parents[2]
UPGRADE_SH = ROOT / "upgrade.sh"
INSTALL_SH = ROOT / "install.sh"
MANIFEST = ROOT / "scripts" / "install-manifest.sh"
RECONCILER = ROOT / "scripts" / "reconcile-filesystem.sh"
HELPER = ROOT / "scripts" / "upgrade-helper-process"
RECORD_REL = "library/.installed-requirements.txt"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _deps_functions() -> str:
    text = UPGRADE_SH.read_text()
    start = text.index("INSTALLED_REQUIREMENTS_REL=")
    end = text.index("force_venv_rebuild() {", start)
    return text[start:end]


FAKE_PIP = """#!/bin/bash
echo "$*" >> "$PIP_LOG"
case "$1" in
    install) exit "${PIP_INSTALL_RC:-0}" ;;
    freeze)
        [[ "${PIP_FREEZE_RC:-0}" != 0 ]] && exit "$PIP_FREEZE_RC"
        cat "$PIP_FREEZE_OUT" ;;
esac
"""


def _make_install(tmp_path: Path) -> Path:
    target = tmp_path / "install"
    (target / "library" / "venv" / "bin").mkdir(parents=True)
    pip = target / "library" / "venv" / "bin" / "pip"
    pip.write_text(FAKE_PIP)
    pip.chmod(0o755)
    (target / "library" / "requirements.txt").write_text("flask>=3\n")
    return target


def _run_sync(tmp_path: Path, target: Path, freeze: str, **env_extra: str):
    freeze_file = tmp_path / "freeze.out"
    freeze_file.write_text(freeze)
    log = tmp_path / "pip.log"
    script = (
        'RED="" GREEN="" BLUE="" YELLOW="" NC=""\n'
        + _deps_functions()
        + f'\nsync_python_dependencies "{target}" ""\n'
    )
    env = os.environ.copy()
    env.update({"PIP_LOG": str(log), "PIP_FREEZE_OUT": str(freeze_file)})
    env.update(env_extra)
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False, timeout=30
    )
    return proc, (log.read_text() if log.exists() else "")


# --------------------------------------------------------------------------
# gwq: pip --upgrade, freeze record, diff, loud failure
# --------------------------------------------------------------------------
def test_pip_command_uses_upgrade_and_requirements(tmp_path):
    target = _make_install(tmp_path)
    proc, log = _run_sync(tmp_path, target, "flask==3.1.0\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    first = log.splitlines()[0]
    assert first.startswith("install --upgrade"), first
    assert f"-r {target}/library/requirements.txt" in first


def test_first_deploy_records_sorted_freeze_and_prints_count_only(tmp_path):
    target = _make_install(tmp_path)
    proc, _ = _run_sync(tmp_path, target, "zlib==1\nflask==3.1.0\nattrs==2\n")
    assert proc.returncode == 0
    record = (target / RECORD_REL).read_text().splitlines()
    assert record == ["attrs==2", "flask==3.1.0", "zlib==1"]
    assert "Installed packages: 3 (first recorded deploy" in proc.stdout
    assert not re.search(r"^  [+~-] ", proc.stdout, re.M), "first deploy must not print a diff"


def test_diff_shows_added_removed_changed_with_versions(tmp_path):
    target = _make_install(tmp_path)
    (target / RECORD_REL).write_text("anyio==4.12.1\nflask==3.0.0\nurllib3==2.7.0\n")
    proc, _ = _run_sync(tmp_path, target, "flask==3.0.0\nrequests==2.33.0\nurllib3==2.8.0\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "  + requests 2.33.0" in out
    assert "  - anyio 4.12.1" in out
    assert "  ~ urllib3 2.7.0 -> 2.8.0" in out
    assert "flask" not in re.sub(r"Installed packages.*", "", out), "unchanged package listed"
    assert "(+1 added, -1 removed, ~1 changed)" in out
    assert "→" not in out, "unicode arrow would corrupt upgrade-helper-process version parsing"
    assert (target / RECORD_REL).read_text().splitlines() == [
        "flask==3.0.0",
        "requests==2.33.0",
        "urllib3==2.8.0",
    ]


def test_no_change_says_so(tmp_path):
    target = _make_install(tmp_path)
    (target / RECORD_REL).write_text("flask==3.0.0\n")
    proc, _ = _run_sync(tmp_path, target, "flask==3.0.0\n")
    assert proc.returncode == 0
    assert "no changes since last deploy" in proc.stdout


def test_pip_install_failure_fails_loudly_and_keeps_old_record(tmp_path):
    target = _make_install(tmp_path)
    (target / RECORD_REL).write_text("flask==3.0.0\n")
    proc, log = _run_sync(tmp_path, target, "flask==9\n", PIP_INSTALL_RC="1")
    assert proc.returncode != 0
    assert "FAILED" in proc.stdout
    assert (target / RECORD_REL).read_text() == "flask==3.0.0\n", "record must not advance"
    assert "freeze" not in log, "must not record a state it could not reach"


def test_pip_freeze_failure_fails(tmp_path):
    target = _make_install(tmp_path)
    proc, _ = _run_sync(tmp_path, target, "x==1\n", PIP_FREEZE_RC="1")
    assert proc.returncode != 0
    assert not (target / RECORD_REL).exists()


def test_missing_requirements_fails(tmp_path):
    target = _make_install(tmp_path)
    (target / "library" / "requirements.txt").unlink()
    proc, log = _run_sync(tmp_path, target, "x==1\n")
    assert proc.returncode != 0
    assert log == ""


def test_no_swallowed_or_minimal_fallback_pip_installs_remain():
    """Every requirements.txt install site: --upgrade, no stderr hiding, no 2-package fallback."""
    for path in (UPGRADE_SH, INSTALL_SH):
        text = path.read_text()
        for m in re.finditer(r"pip\"? install[^\n]*(\\\n[^\n]*)*", text):
            stmt = m.group(0)
            if "requirements.txt" in stmt:
                assert "--upgrade" in stmt, f"{path.name}: {stmt!r}"
                assert "2>/dev/null" not in stmt, f"{path.name}: stderr swallowed: {stmt!r}"
        assert not re.search(r"install --quiet (flask mutagen|Flask)", text, re.I), (
            f"{path.name}: minimal-package fallback must not exist"
        )
    assert "--upgrade" in (ROOT / "Dockerfile").read_text().split("requirements.txt")[1].split(
        "\n"
    )[0] or (
        "pip install --no-cache-dir --upgrade -r requirements.txt"
        in (ROOT / "Dockerfile").read_text()
    )


def test_all_venv_populating_paths_use_the_shared_sync():
    text = UPGRADE_SH.read_text()
    assert text.count('sync_python_dependencies "$target" "$use_sudo"') >= 3
    assert 'force_venv_rebuild "$target" "$use_sudo" || return 1' in text


# --------------------------------------------------------------------------
# soo: manifest-driven obsolete-root purge
# --------------------------------------------------------------------------
def _fixture(tmp_path: Path, marker: bool = True) -> dict[str, Path]:
    d = {n: tmp_path / n for n in ("lib", "state", "log", "config", "systemd", "bin")}
    for p in d.values():
        p.mkdir(parents=True)
    d["CONF_FILE"] = d["config"] / "audiobooks.conf"
    if marker:
        (d["lib"] / "VERSION").write_text("8.7.1\n")
        (d["lib"] / "install-manifest.json").write_text("{}\n")
    return d


def _reconcile(d: dict[str, Path], mode: str):
    env = os.environ.copy()
    env.update(
        PROJECT_DIR=str(ROOT),
        LIB_DIR=str(d["lib"]),
        STATE_DIR=str(d["state"]),
        LOG_DIR=str(d["log"]),
        CONFIG_DIR=str(d["config"]),
        CONF_FILE=str(d["CONF_FILE"]),
        SYSTEMD_DIR=str(d["systemd"]),
        BIN_DIR=str(d["bin"]),
        USE_SUDO="",
        RECONCILE_MODE=mode,
    )
    return subprocess.run(
        ["bash", str(RECONCILER)], capture_output=True, text=True, env=env, check=False, timeout=60
    )


def _seed_obsolete(lib: Path) -> None:
    (lib / "upgrade.sh").write_text("old\n")
    (lib / "README.md").write_text("old\n")
    (lib / ".mypy_cache").mkdir()
    (lib / ".mypy_cache" / "x").write_text("c\n")
    (lib / "library" / ".mypy_cache").mkdir(parents=True)
    (lib / "library" / ".mypy_cache" / "y").write_text("c\n")
    (lib / "scripts").mkdir()
    (lib / "scripts" / "purge-users.sh").write_text("dev\n")


def _seed_keepers(lib: Path) -> None:
    (lib / "my-notes.txt").write_text("keep\n")  # not listed
    (lib / "scripts" / "audiobook-api").parent.mkdir(exist_ok=True)
    (lib / "scripts" / "audiobook-api").write_text("keep\n")
    (lib / "library" / "certs").mkdir(parents=True, exist_ok=True)
    (lib / "library" / "certs" / "server.crt").write_text("keep\n")


def test_enforce_removes_exactly_listed_paths(tmp_path):
    d = _fixture(tmp_path)
    lib = d["lib"]
    _seed_obsolete(lib)
    _seed_keepers(lib)
    proc = _reconcile(d, "enforce")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for gone in (
        "upgrade.sh",
        "README.md",
        ".mypy_cache",
        "library/.mypy_cache",
        "scripts/purge-users.sh",
    ):
        assert not (lib / gone).exists(), gone
    for kept in (
        "my-notes.txt",
        "scripts/audiobook-api",
        "library/certs/server.crt",
        "VERSION",
        "install-manifest.json",
    ):
        assert (lib / kept).exists(), kept
    assert "removed obsolete" in proc.stdout


def test_report_mode_deletes_nothing_but_reports(tmp_path):
    d = _fixture(tmp_path)
    _seed_obsolete(d["lib"])
    proc = _reconcile(d, "report")
    assert (d["lib"] / "upgrade.sh").exists()
    assert (d["lib"] / ".mypy_cache" / "x").exists()
    assert "obsolete install-root path present" in proc.stdout
    assert "removed obsolete" not in proc.stdout


def test_refuses_non_install_dir(tmp_path):
    d = _fixture(tmp_path, marker=False)
    _seed_obsolete(d["lib"])
    proc = _reconcile(d, "enforce")
    assert (d["lib"] / "upgrade.sh").exists(), "purged a dir that is not an audiobooks install"
    assert "not an audiobooks install" in proc.stdout


def test_partial_marker_also_refuses(tmp_path):
    d = _fixture(tmp_path)
    (d["lib"] / "install-manifest.json").unlink()
    _seed_obsolete(d["lib"])
    _reconcile(d, "enforce")
    assert (d["lib"] / "upgrade.sh").exists()


def test_symlinked_parent_is_not_followed(tmp_path):
    d = _fixture(tmp_path)
    outside = tmp_path / "outside"
    (outside / ".mypy_cache").mkdir(parents=True)
    (outside / ".mypy_cache" / "precious").write_text("keep\n")
    (d["lib"] / "library").symlink_to(outside)  # library/.mypy_cache would resolve outside
    proc = _reconcile(d, "enforce")
    assert (outside / ".mypy_cache" / "precious").read_text() == "keep\n"
    assert "symlinked parent" in proc.stdout


def test_symlink_entry_is_unlinked_not_followed(tmp_path):
    d = _fixture(tmp_path)
    outside = tmp_path / "outside"
    (outside / "dir").mkdir(parents=True)
    (outside / "dir" / "precious").write_text("keep\n")
    (d["lib"] / ".mypy_cache").symlink_to(outside / "dir")
    _reconcile(d, "enforce")
    assert not (d["lib"] / ".mypy_cache").is_symlink()
    assert (outside / "dir" / "precious").read_text() == "keep\n"


def test_manifest_list_is_explicit_and_safe():
    out = subprocess.run(
        [
            "bash",
            "-c",
            f'LIB_DIR=/x STATE_DIR=/y LOG_DIR=/l CONFIG_DIR=/c; source "{MANIFEST}"; '
            'printf "%s\\n" "${OBSOLETE_ROOT_PATHS[@]}"',
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.split("\n")
    entries = [e for e in out if e]
    assert "upgrade.sh" in entries and "scripts/purge-users.sh" in entries
    for e in entries:
        assert not e.startswith("/") and ".." not in e.split("/"), e
        assert not re.search(r"[*?\[]", e), f"glob in explicit list: {e}"
    assert "library/certs" not in entries and not any(
        e.startswith("library/certs") for e in entries
    )


def test_purge_users_skip_is_documented():
    for path in (UPGRADE_SH, INSTALL_SH):
        text = path.read_text()
        idx = text.index("install-hooks.sh | purge-users.sh) continue")
        assert "deliberately NOT installed" in text[idx - 700 : idx], path.name


# --------------------------------------------------------------------------
# Caddy: unavailable.html is installed everywhere maintenance.html is
# --------------------------------------------------------------------------
def test_unavailable_html_installed_alongside_maintenance_html():
    up = UPGRADE_SH.read_text()
    assert re.search(
        r"for caddy_file in audiobooks\.conf maintenance\.html unavailable\.html; do", up
    )
    assert "caddy/unavailable.html" in INSTALL_SH.read_text()
    assert (ROOT / "caddy" / "unavailable.html").is_file()


def test_caddy_substitution_placeholders_still_match_upgrade_sed():
    conf = (ROOT / "caddy" / "audiobooks.conf").read_text()
    up = UPGRADE_SH.read_text()
    for ph in ("__NATIVE_PORT__", "__DOCKER_PORT__"):
        assert ph in conf and f"s|{ph}|" in up


# --------------------------------------------------------------------------
# Caddy status mirror removed on every terminal path
# --------------------------------------------------------------------------
def _helper_env(tmp_path: Path, with_caddy_dir: bool = True):
    var = tmp_path / "var"
    caddy = tmp_path / "caddy"
    inst = tmp_path / "inst"
    (var / ".control").mkdir(parents=True)
    (inst / "scripts").mkdir(parents=True)
    if with_caddy_dir:
        caddy.mkdir()
    env = os.environ.copy()
    env.update(
        AUDIOBOOKS_VAR_DIR=str(var),
        AUDIOBOOKS_CADDY_DIR=str(caddy),
        AUDIOBOOKS_INSTALL_DIR=str(inst),
        AUDIOBOOKS_HOME=str(tmp_path / "nohome"),
        AUDIOBOOKS_CADDY_STATUS_CLEANUP_DELAY="0",
    )
    return env, var / ".control", caddy / "upgrade-status.json", inst


def _stub_upgrade(inst: Path, body: str) -> None:
    s = inst / "scripts" / "upgrade.sh"
    s.write_text("#!/bin/bash\n" + body)
    s.chmod(0o755)


def test_noop_path_removes_caddy_mirror_keeps_control_status(tmp_path):
    env, control, mirror, inst = _helper_env(tmp_path)
    _stub_upgrade(inst, 'echo "Already at version 1.2.3"; exit 1\n')
    proj = tmp_path / "proj"
    proj.mkdir()
    (control / "upgrade-request").write_text(
        f'{{"type": "upgrade_check", "source": "project", "project_path": "{proj}"}}'
    )
    proc = subprocess.run(
        ["bash", str(HELPER)], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert "Already up to date" in (control / "upgrade-status").read_text()
    assert not mirror.exists(), "stale Caddy mirror left behind on no-op path"


def test_failure_path_removes_caddy_mirror(tmp_path):
    env, control, mirror, _ = _helper_env(tmp_path)
    (control / "upgrade-request").write_text('{"type": "bogus"}')
    proc = subprocess.run(
        ["bash", str(HELPER)], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 1
    assert '"stage": "error"' in (control / "upgrade-status").read_text()
    assert not mirror.exists()


def test_early_exit_no_request_removes_caddy_mirror(tmp_path):
    env, control, mirror, _ = _helper_env(tmp_path)
    proc = subprocess.run(
        ["bash", str(HELPER)], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 1
    assert not mirror.exists()


def test_absent_caddy_dir_is_not_an_error(tmp_path):
    env, control, mirror, _ = _helper_env(tmp_path, with_caddy_dir=False)
    (control / "upgrade-request").write_text('{"type": "bogus"}')
    proc = subprocess.run(
        ["bash", str(HELPER)], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 1  # the bogus request, not a cleanup error
    assert "upgrade-status.json" not in proc.stderr, proc.stderr
    assert not mirror.exists()


def test_sigterm_removes_caddy_mirror(tmp_path):
    env, control, mirror, inst = _helper_env(tmp_path)
    _stub_upgrade(inst, "sleep 2\nexit 0\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    (control / "upgrade-request").write_text(
        f'{{"type": "upgrade_check", "source": "project", "project_path": "{proj}"}}'
    )
    p = subprocess.Popen(
        ["bash", str(HELPER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    for _ in range(100):
        if mirror.exists():
            break
        time.sleep(0.05)
    assert mirror.exists(), "mirror never written; test cannot prove cleanup"
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=30)
    assert not mirror.exists(), "SIGTERM left the Caddy mirror behind"


def test_helper_uses_config_variable_not_literal_caddy_path():
    text = HELPER.read_text()
    assert "AUDIOBOOKS_CADDY_DIR" in text
    assert text.count("/etc/caddy") == 1  # only the fallback default
    assert "AUDIOBOOKS_CADDY_DIR" in (ROOT / "lib" / "audiobook-config.sh").read_text()


def test_docker_entrypoint_restricts_generated_tls_key():
    text = (ROOT / "docker-entrypoint.sh").read_text()
    gen = text.index("Generating self-signed TLS certificate")
    assert 'chmod 600 "$AUDIOBOOKS_CERTS/server.key"' in text[gen:]
    # install.sh: system install 640 (audiobooks group reads), user install 600 (owner only)
    inst = INSTALL_SH.read_text()
    assert 'chmod 640 "${CERT_DIR}/server.key"' in inst
    assert 'chmod 600 "${CERT_DIR}/server.key"' in inst
