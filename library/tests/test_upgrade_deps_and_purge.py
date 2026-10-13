"""Unit tests for Audiobook-Manager-gwq (pip --upgrade + freeze diff), -soo
(manifest-driven obsolete-root purge) and the Caddy status-mirror cleanup.

Everything runs against tmp dirs with stubbed pip / stubbed upgrade.sh; the real
shell code is sliced out of (or executed from) the repo scripts, so a mutation of
the script turns these tests red.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
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
    end = text.index("apply_config_migrations() {", start)
    return text[start:end]


FAKE_PIP = """#!/bin/bash
echo "$*" >> "$PIP_LOG"
case "$1" in
    install)
        prev=""
        for a in "$@"; do
            if [[ "$prev" == "-r" ]]; then
                echo "REQ<$(cat "$a")>" >> "$PIP_LOG"
            fi
            prev="$a"
        done
        # only the --upgrade sync can fail; a rollback (--no-deps) succeeds
        if [[ " $* " == *" --upgrade "* ]]; then
            [[ "${PIP_INSTALL_RC:-0}" != 0 ]] && exit "$PIP_INSTALL_RC"
            # a successful sync changes what `pip freeze` reports
            [[ -n "${PIP_FREEZE_AFTER:-}" ]] && cp "$PIP_FREEZE_AFTER" "$PIP_FREEZE_OUT"
        fi
        exit 0 ;;
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


def _run_sync(tmp_path: Path, target: Path, freeze: str, pre: str | None = None, **env_extra: str):
    """`freeze` is what pip reports AFTER the sync; `pre` is what the venv held BEFORE it
    (default: exactly what the last deploy recorded, i.e. no drift between deploys)."""
    freeze_file = tmp_path / "freeze.out"
    after_file = tmp_path / "freeze.after"
    record = target / RECORD_REL
    if pre is None:
        pre = record.read_text() if record.exists() else freeze
    freeze_file.write_text(pre)
    after_file.write_text(freeze)
    env_extra.setdefault("PIP_FREEZE_AFTER", str(after_file))
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
    first = next(ln for ln in log.splitlines() if ln.startswith("install"))
    assert first.startswith("install --upgrade"), first
    assert "/dev/stdin" not in first, first  # pip re-checks /dev/stdin as the new uid: EACCES
    assert re.search(r"-r \S+/requirements\.txt", first), first
    assert "flask>=3" in log


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
    assert "(+1 added, -1 removed, ~1 changed vs the venv before this sync)" in out
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
    assert "no changes vs the venv before this sync" in proc.stdout


def test_pip_install_failure_fails_loudly_and_keeps_old_record(tmp_path):
    target = _make_install(tmp_path)
    (target / RECORD_REL).write_text("flask==3.0.0\n")
    proc, log = _run_sync(tmp_path, target, "flask==9\n", PIP_INSTALL_RC="1")
    assert proc.returncode != 0
    assert "FAILED" in proc.stdout
    assert (target / RECORD_REL).read_text() == "flask==3.0.0\n", "record must not advance"
    after_install = log.split("install --upgrade", 1)[1]
    assert "freeze" not in after_install.split("\n", 1)[1], "recorded a state it could not reach"


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
    # Normal sync and rebuild both go through sync_python_dependencies, called from
    # prepare_python_environment, which do_upgrade runs BEFORE any file copy.
    assert text.count('sync_python_dependencies "$target" "$use_sudo" "$req"') == 2
    body = text[text.index("do_upgrade() {") :]
    assert body.index("prepare_python_environment") < body.index("Upgrading scripts")
    assert "force_venv_rebuild" not in text


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
        AUDIOBOOKS_CADDY_STATUS_CLEANUP_DELAY="30",
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


# --------------------------------------------------------------------------
# review round: pip failure must abort BEFORE any code is copied
# --------------------------------------------------------------------------
def _mk_tree(base: Path, version: str, marker: str) -> Path:
    for d in ("scripts", "lib", "library/web-v2", "library/venv/bin"):
        (base / d).mkdir(parents=True, exist_ok=True)
    (base / "VERSION").write_text(version + "\n")
    (base / "scripts" / "tool.sh").write_text(marker + "\n")
    (base / "lib" / "audiobook-config.sh").write_text(marker + "\n")
    (base / "library" / "web-v2" / "index.html").write_text(marker + "\n")
    (base / "library" / "requirements.txt").write_text(f"flask>={version}\n")
    return base


def _run_do_upgrade(tmp_path: Path, pip_rc: int, replace_prepare_after_scripts: bool = False):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    pip = target / "library" / "venv" / "bin" / "pip"
    pip.write_text(FAKE_PIP)
    pip.chmod(0o755)
    py = target / "library" / "venv" / "bin" / "python"
    py.write_text("#!/bin/bash\nexit 0\n")
    py.chmod(0o755)
    freeze = tmp_path / "freeze.out"
    freeze.write_text("flask==1.0.0\n")
    calls = tmp_path / "calls.log"
    script = f"""
UPGRADE_SH_SOURCE_ONLY=1 source "{UPGRADE_SH}"
RED="" GREEN="" BLUE="" YELLOW="" NC=""
DRY_RUN=false MAJOR_VERSION=false
apply_data_migrations() {{ echo data_migrations >> "{calls}"; }}
apply_config_migrations() {{ echo config_migrations >> "{calls}"; }}
do_upgrade "{project}" "{target}"
echo "RC=$?"
"""
    env = os.environ.copy()
    env.update(
        PIP_LOG=str(tmp_path / "pip.log"),
        PIP_FREEZE_OUT=str(freeze),
        PIP_INSTALL_RC=str(pip_rc),
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    return proc, target, calls


def test_pip_failure_aborts_with_old_code_untouched(tmp_path):
    proc, target, calls = _run_do_upgrade(tmp_path, pip_rc=1)
    assert "RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert (target / "VERSION").read_text() == "1.0.0\n"
    assert (target / "scripts" / "tool.sh").read_text() == "OLD\n"
    assert (target / "lib" / "audiobook-config.sh").read_text() == "OLD\n"
    assert (target / "library" / "web-v2" / "index.html").read_text() == "OLD\n"
    assert not calls.exists(), "migrations ran despite the dependency failure"
    assert "aborted before any application file was changed" in proc.stdout
    # the previous package set is put back (freeze re-applied with --no-deps)
    log = (tmp_path / "pip.log").read_text()
    assert "/dev/stdin" not in log
    assert re.search(r"install --quiet --no-deps -r \S+/before\.txt", log)
    assert "REQ<flask==1.0.0" in log, "rollback must feed the pre-sync freeze"
    assert "Previous package set restored" in proc.stdout


def test_main_flow_restarts_services_when_do_upgrade_fails():
    """Both callers restart services on a non-zero do_upgrade (old code is still on disk)."""
    text = UPGRADE_SH.read_text()
    assert text.count('do_upgrade "$PROJECT_DIR" "$TARGET_DIR"\nupgrade_rc=$?') == 1
    tail = text[text.index('do_upgrade "$PROJECT_DIR" "$TARGET_DIR"') :]
    assert 'start_services "$use_sudo"' in tail[: tail.index("Start services after upgrade")]
    helper = HELPER.read_text()
    fail = helper[helper.index("if [[ $upgrade_exit -ne 0 ]]") :]
    assert 'systemctl start "${services_stop_order[i]}"' in fail[:900]


def test_rsync_preserves_install_record():
    text = UPGRADE_SH.read_text()
    assert "--exclude='.installed-requirements.txt'" in text
    assert "--exclude='venv.pre-upgrade'" in text


def test_rebuild_failure_restores_previous_venv(tmp_path):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    (target / "library" / "venv" / "bin" / "marker").write_text("old venv\n")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    fake_py = tmp_path / "fakepython"
    fake_py.write_text("#!/bin/bash\nexit 1\n")  # `-m venv` fails
    fake_py.chmod(0o755)
    script = rf"""
UPGRADE_SH_SOURCE_ONLY=1 source "{UPGRADE_SH}"
RED="" GREEN="" BLUE="" YELLOW="" NC=""
set +e
DRY_RUN=false MAJOR_VERSION=true
# route the system-python lookup to the failing stub
prepare_python_environment_py="{fake_py}"
sed_src=$(declare -f prepare_python_environment | sed 's|"/usr/bin/python3"|"{fake_py}"|; s|\[\[ -x /usr/bin/python3.1[34] \]\][^;]*;||g')
eval "$sed_src"
prepare_python_environment "{project}" "{target}" ""
echo "RC=$?"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert "RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert (target / "library" / "venv" / "bin" / "marker").read_text() == "old venv\n"
    assert not (target / "library" / "venv.pre-upgrade").exists()


# --------------------------------------------------------------------------
# review round: purge refuses repo checkouts and odd roots
# --------------------------------------------------------------------------
def _repo_copy_scenario(tmp_path: Path) -> dict[str, Path]:
    d = _fixture(tmp_path)
    _seed_obsolete(d["lib"])
    return d


def test_refuses_git_checkout(tmp_path):
    d = _repo_copy_scenario(tmp_path)
    (d["lib"] / ".git").mkdir()
    proc = _reconcile(d, "enforce")
    assert (d["lib"] / "upgrade.sh").exists() and (d["lib"] / "README.md").exists()
    assert "git checkout" in proc.stdout


def test_refuses_when_lib_dir_is_project_dir(tmp_path):
    d = _repo_copy_scenario(tmp_path)
    env_proj = d["lib"]
    env = os.environ.copy()
    env.update(
        PROJECT_DIR=str(ROOT),
        LIB_DIR=str(env_proj),
        STATE_DIR=str(d["state"]),
        LOG_DIR=str(d["log"]),
        CONFIG_DIR=str(d["config"]),
        CONF_FILE=str(d["CONF_FILE"]),
        USE_SUDO="",
        RECONCILE_MODE="enforce",
    )
    # copy the manifest + reconciler into LIB_DIR so PROJECT_DIR == LIB_DIR resolves
    (env_proj / "scripts").mkdir(exist_ok=True)
    for f in ("install-manifest.sh", "reconcile-filesystem.sh"):
        (env_proj / "scripts" / f).write_text((ROOT / "scripts" / f).read_text())
    env["PROJECT_DIR"] = str(env_proj)
    proc = subprocess.run(
        ["bash", str(env_proj / "scripts" / "reconcile-filesystem.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert (env_proj / "upgrade.sh").exists() and (env_proj / "README.md").exists()
    assert "project source tree" in proc.stdout


def test_refuses_lib_dir_via_symlink_to_project(tmp_path):
    """realpath comparison, not string comparison."""
    d = _repo_copy_scenario(tmp_path)
    link = tmp_path / "link-to-lib"
    link.symlink_to(d["lib"])
    d2 = dict(d)
    d2["lib"] = link
    env = os.environ.copy()
    env.update(
        PROJECT_DIR=str(d["lib"]),
        LIB_DIR=str(link),
        STATE_DIR=str(d["state"]),
        LOG_DIR=str(d["log"]),
        CONFIG_DIR=str(d["config"]),
        CONF_FILE=str(d["CONF_FILE"]),
        USE_SUDO="",
        RECONCILE_MODE="enforce",
    )
    # PROJECT_DIR must hold the manifest; point a copy at it
    (d["lib"] / "scripts").mkdir(exist_ok=True)
    (d["lib"] / "scripts" / "install-manifest.sh").write_text(MANIFEST.read_text())
    proc = subprocess.run(
        ["bash", str(RECONCILER)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert (d["lib"] / "upgrade.sh").exists()
    assert "project source tree" in proc.stdout


@pytest.mark.parametrize("bad,msg", [("", "empty or /"), ("/", "empty or /")])
def test_refuses_empty_or_root_lib_dir(tmp_path, bad, msg):
    harness = f"""
LIB_DIR="{bad}" PROJECT_DIR="{ROOT}" USE_SUDO="" RECONCILE_MODE=enforce DRY_RUN=false
source "{RECONCILER}"
OBSOLETE_ROOT_PATHS=("zz-nonexistent-sentinel")
_reconcile_obsolete_root_paths
"""
    proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=30)
    assert f"skipped: LIB_DIR is {msg}" in proc.stdout or "LIB_DIR resolves to /" in proc.stdout, (
        proc.stdout + proc.stderr
    )


def test_dot_entries_rejected(tmp_path):
    d = _fixture(tmp_path)
    (d["lib"] / "keep.txt").write_text("keep\n")
    harness = f"""
LIB_DIR="{d["lib"]}" PROJECT_DIR="/nonexistent" USE_SUDO="" RECONCILE_MODE=enforce DRY_RUN=false
source "{RECONCILER}"
OBSOLETE_ROOT_PATHS=("." "./keep.txt" "a/./b" "../x")
_reconcile_obsolete_root_paths
"""
    proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=30)
    assert (d["lib"] / "keep.txt").exists() and d["lib"].exists()
    assert proc.stdout.count("invalid OBSOLETE_ROOT_PATHS entry") == 4, proc.stdout + proc.stderr


def test_toctou_parent_swapped_to_symlink_is_not_deleted(tmp_path):
    """Parent is a real dir during the walk, a symlink by delete time -> skipped."""
    d = _fixture(tmp_path)
    (d["lib"] / "library").mkdir()
    outside = tmp_path / "outside"
    (outside / ".mypy_cache").mkdir(parents=True)
    (outside / ".mypy_cache" / "precious").write_text("keep\n")
    # `realpath` is called right before rm: shadow it to swap the dir for a symlink first
    swap = tmp_path / "swap.sh"
    swap.write_text(
        f"""
realpath() {{
    if [[ ! -L "{d["lib"]}/library" && "$*" == *"{d["lib"]}/library"* && -z "${{SWAPPED:-}}" ]] \
        && [[ -n "${{ARMED:-}}" ]]; then
        mv "{d["lib"]}/library" "{d["lib"]}/library.real"; ln -s "{outside}" "{d["lib"]}/library"; SWAPPED=1
    fi
    command realpath "$@"
}}
"""
    )
    harness = f"""
LIB_DIR="{d["lib"]}" PROJECT_DIR="/nonexistent" USE_SUDO="" RECONCILE_MODE=enforce DRY_RUN=false
source "{RECONCILER}"
source "{swap}"
OBSOLETE_ROOT_PATHS=("library/.mypy_cache")
# the entry exists inside the real dir so the walk accepts it
mkdir -p "{d["lib"]}/library/.mypy_cache"
ARMED=1
_reconcile_obsolete_root_paths
"""
    proc = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=30)
    assert (outside / ".mypy_cache" / "precious").read_text() == "keep\n", proc.stdout + proc.stderr
    assert "no longer inside install dir" in proc.stdout


# --------------------------------------------------------------------------
# review round: Caddy dir placeholder, delayed removal only on success
# --------------------------------------------------------------------------
def test_caddy_dir_placeholder_substitution_yields_old_text_by_default():
    conf = (ROOT / "caddy" / "audiobooks.conf").read_text()
    assert "root /etc/caddy" not in conf and "root * /etc/caddy" not in conf
    assert conf.count("__CADDY_DIR__") >= 4
    for path in (UPGRADE_SH, INSTALL_SH):
        assert "s|__CADDY_DIR__|" in path.read_text(), path.name
    out = subprocess.run(
        [
            "sed",
            "-e",
            "s|__CADDY_DIR__|/etc/caddy|g",
            "-e",
            "s|__NATIVE_PORT__|8443|g",
            "-e",
            "s|__DOCKER_PORT__|8443|g",
            str(ROOT / "caddy" / "audiobooks.conf"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert (
        "__" not in re.sub(r"\{[^}]*\}", "", out).replace("__", "", 0) or "__CADDY_DIR__" not in out
    )
    assert out.count("root /etc/caddy") == 3 and out.count("root * /etc/caddy") == 1
    custom = conf.replace("__CADDY_DIR__", "/srv/caddy-x")
    assert "root /srv/caddy-x" in custom
    assert not [
        ln for ln in custom.splitlines() if ln.strip().startswith("root") and "/etc/caddy" in ln
    ]


def test_noop_path_does_not_sleep_even_with_long_delay(tmp_path):
    env, control, mirror, inst = _helper_env(tmp_path)  # delay env = 30
    _stub_upgrade(inst, 'echo "Already at version 1.2.3"; exit 1\n')
    proj = tmp_path / "proj"
    proj.mkdir()
    (control / "upgrade-request").write_text(
        f'{{"type": "upgrade_check", "source": "project", "project_path": "{proj}"}}'
    )
    t0 = time.monotonic()
    subprocess.run(["bash", str(HELPER)], env=env, capture_output=True, text=True, timeout=60)
    assert time.monotonic() - t0 < 10, "non-success path slept (would lose a queued request)"
    assert not mirror.exists()


def test_success_path_delays_removal_via_trap_function(tmp_path):
    text = HELPER.read_text()
    fn = text[
        text.index("_cleanup_caddy_status() {") : text.index("trap _cleanup_caddy_status EXIT")
    ]
    mirror = tmp_path / "upgrade-status.json"
    mirror.write_text("{}")
    script = f'CADDY_STATUS_FILE="{mirror}"; _CADDY_CLEANUP_SLEEP=2\n{fn}\n_cleanup_caddy_status\n'
    p = subprocess.Popen(["bash", "-c", script])
    time.sleep(0.7)
    assert mirror.exists(), "success-path delay not honoured"
    p.wait(timeout=10)
    assert not mirror.exists()
    # and the success tail of do_upgrade is what raises the delay
    tail = text[text.index("# The Caddy mirror is removed by the EXIT trap") :]
    assert '_CADDY_CLEANUP_SLEEP="$CADDY_STATUS_CLEANUP_DELAY"' in tail[:600]


# --------------------------------------------------------------------------
# review round 2: sudo path (stdin requirements), interrupted rebuild,
# scheduler in the CLI service set, configurable Caddy dir
# --------------------------------------------------------------------------
SUDO_SHIM = """#!/bin/bash
# Emulates `sudo -u audiobooks CMD`: the audiobooks user cannot read a data file
# unless it is world-readable inside a world-searchable directory. Plain `sudo CMD`
# (root) is unrestricted. `sudo chown` is a no-op here.
if [[ "$1" == "-u" ]]; then
    shift 2
    for a in "$@"; do
        if [[ -f "$a" && ! -x "$a" ]]; then
            fmode=$(stat -c %a "$a"); dmode=$(stat -c %a "$(dirname "$a")")
            if [[ $((8#$fmode & 4)) -eq 0 || $((8#$dmode & 1)) -eq 0 ]]; then
                echo "audiobooks: Permission denied: $a" >&2
                exit 1
            fi
        fi
    done
    exec "$@"
fi
[[ "$1" == "chown" ]] && exit 0
exec "$@"
"""


def _shim_dir(tmp_path: Path) -> Path:
    d = tmp_path / "shim"
    d.mkdir()
    sudo = d / "sudo"
    sudo.write_text(SUDO_SHIM)
    sudo.chmod(0o755)
    return d


def _run_sync_sudo(tmp_path: Path, target: Path, req: Path, **env_extra: str):
    freeze_file = tmp_path / "freeze.out"
    freeze_file.write_text("flask==1.0.0\n")
    log = tmp_path / "pip.log"
    script = (
        'RED="" GREEN="" BLUE="" YELLOW="" NC=""\n'
        + _deps_functions()
        + f'\nsync_python_dependencies "{target}" sudo "{req}"\n'
    )
    env = os.environ.copy()
    env["PATH"] = f"{_shim_dir(tmp_path)}:{env['PATH']}"
    env.update({"PIP_LOG": str(log), "PIP_FREEZE_OUT": str(freeze_file)})
    env.update(env_extra)
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False, timeout=30
    )
    return proc, (log.read_text() if log.exists() else "")


def test_sudo_path_rollback_actually_restores(tmp_path):
    """Pre-sync freeze is a 0600 mktemp file the venv owner cannot read: it must go in via stdin."""
    target = _make_install(tmp_path)
    proc, log = _run_sync_sudo(
        tmp_path, target, target / "library" / "requirements.txt", PIP_INSTALL_RC="1"
    )
    assert proc.returncode != 0
    assert "Previous package set restored" in proc.stdout, proc.stdout + proc.stderr
    assert "Could not restore" not in proc.stdout
    assert "REQ<flask==1.0.0" in log


def test_from_github_requirements_in_private_dir_reach_venv_owner(tmp_path):
    """--from-github extracts into mktemp -d (0700); the venv owner cannot enter it."""
    target = _make_install(tmp_path)
    release = tmp_path / "release" / "library"
    release.mkdir(parents=True)
    req = release / "requirements.txt"
    req.write_text("flask>=9\n")
    req.chmod(0o644)
    release.parent.chmod(0o700)
    release.chmod(0o700)
    proc, log = _run_sync_sudo(tmp_path, target, req)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REQ<flask>=9" in log


def test_sudo_shim_is_a_real_discriminator(tmp_path):
    """Falsify the shim itself: handing the venv owner a private path must fail."""
    private = tmp_path / "private"
    private.mkdir()
    f = private / "r.txt"
    f.write_text("x\n")
    private.chmod(0o700)
    shim = _shim_dir(tmp_path) / "sudo"
    ok = subprocess.run([str(shim), "-u", "audiobooks", "true", str(f)], capture_output=True)
    assert ok.returncode == 1


def test_requirements_txt_has_no_includes_so_a_single_staged_file_is_complete():
    """pip is handed ONE staged copy; a relative `-r other.txt` inside it would not resolve."""
    lines = (ROOT / "library" / "requirements.txt").read_text().splitlines()
    assert not [ln for ln in lines if re.match(r"\s*(-r|-c|--requirement|--constraint)\b", ln)]


def _prepare_harness(tmp_path, target, project, major, prelude=""):
    """Run the real prepare_python_environment with the system python replaced by a fake
    that can create a stub venv (so no network and no real pip are involved)."""
    fake_py = tmp_path / "fakepython"
    fake_py.write_text(
        f"""#!/bin/bash
# fake `python -m venv DIR`: build a stub venv whose pip comes from the shared stub
if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    [[ -n "${{FAKE_VENV_FAIL:-}}" ]] && exit 1
    mkdir -p "$3/bin"
    cp "{target}/../pip-stub" "$3/bin/pip"
    printf '#!/bin/bash\\nexit 0\\n' > "$3/bin/python"
    chmod +x "$3/bin/pip" "$3/bin/python"
    echo rebuilt > "$3/bin/marker"
    exit 0
fi
exit 1
"""
    )
    fake_py.chmod(0o755)
    (tmp_path / "pip-stub").write_text(FAKE_PIP)
    freeze = tmp_path / "freeze.out"
    freeze.write_text("flask==1.0.0\n")
    script = f"""
UPGRADE_SH_SOURCE_ONLY=1 source "{UPGRADE_SH}"
RED="" GREEN="" BLUE="" YELLOW="" NC=""
set +e
DRY_RUN=false MAJOR_VERSION={"true" if major else "false"}
src=$(declare -f prepare_python_environment | sed 's|"/usr/bin/python3"|"{fake_py}"|; s|\\[\\[ -x /usr/bin/python3.1[34] \\]\\][^;]*;||g')
eval "$src"
{prelude}
prepare_python_environment "{project}" "{target}" ""
echo "RC=$?"
"""
    env = os.environ.copy()
    env.update(PIP_LOG=str(tmp_path / "pip.log"), PIP_FREEZE_OUT=str(freeze))
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env, check=False
    )


def _venv_with_marker(root: Path, marker: str) -> None:
    (root / "bin").mkdir(parents=True, exist_ok=True)
    pip = root / "bin" / "pip"
    pip.write_text(FAKE_PIP)
    pip.chmod(0o755)
    py = root / "bin" / "python"
    py.write_text("#!/bin/bash\nexit 0\n")
    py.chmod(0o755)
    (root / "bin" / "marker").write_text(marker + "\n")


def test_stale_aside_is_restored_not_deleted(tmp_path):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    lib = target / "library"
    _venv_with_marker(lib / "venv", "half-built")
    _venv_with_marker(lib / "venv.pre-upgrade", "good")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    proc = _prepare_harness(tmp_path, target, project, major=False)
    assert "RC=0" in proc.stdout, proc.stdout + proc.stderr
    assert "interrupted rebuild" in proc.stdout
    assert (lib / "venv" / "bin" / "marker").read_text() == "good\n"
    assert not (lib / "venv.pre-upgrade").exists()


def test_stale_aside_survives_a_rebuild_run(tmp_path):
    """The NEXT --major-version run must not rm -rf the only good copy."""
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    lib = target / "library"
    _venv_with_marker(lib / "venv", "half-built")
    _venv_with_marker(lib / "venv.pre-upgrade", "good")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    # rebuild fails: the restored good venv must be what is left
    proc = _prepare_harness(
        tmp_path, target, project, major=True, prelude="export FAKE_VENV_FAIL=1"
    )
    assert "RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert (lib / "venv" / "bin" / "marker").read_text() == "good\n"


def test_mv_failure_aborts_without_deleting_original(tmp_path):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    lib = target / "library"
    _venv_with_marker(lib / "venv", "original")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    prelude = "mv() { return 1; }; export FAKE_VENV_FAIL=1"
    proc = _prepare_harness(tmp_path, target, project, major=True, prelude=prelude)
    assert "RC=1" in proc.stdout, proc.stdout + proc.stderr
    assert "Could not move" in proc.stdout
    assert (lib / "venv" / "bin" / "marker").read_text() == "original\n"


def test_successful_rebuild_leaves_no_aside(tmp_path):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    lib = target / "library"
    _venv_with_marker(lib / "venv", "original")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    proc = _prepare_harness(tmp_path, target, project, major=True)
    assert "RC=0" in proc.stdout, proc.stdout + proc.stderr
    assert (lib / "venv" / "bin" / "marker").read_text() == "rebuilt\n"
    assert not (lib / "venv.pre-upgrade").exists()


def _bash_array(text: str, start_pat: str) -> list[str]:
    m = re.search(start_pat + r"=\(\n(.*?)\n\s*\)", text, re.S)
    assert m, start_pat
    return [ln.strip().strip('"') for ln in m.group(1).splitlines() if ln.strip()]


def test_cli_and_helper_stop_the_same_services_including_scheduler():
    cli = _bash_array(UPGRADE_SH.read_text(), "AUDIOBOOK_STOP_ORDER")
    helper = _bash_array(HELPER.read_text(), "local services_stop_order")
    assert cli == helper, (cli, helper)
    assert "audiobook-scheduler" in cli


def test_cli_start_order_is_reverse_of_stop_order_and_no_inline_lists_remain():
    text = UPGRADE_SH.read_text()
    out = subprocess.run(
        [
            "bash",
            "-c",
            f'UPGRADE_SH_SOURCE_ONLY=1 source "{UPGRADE_SH}"; '
            'printf "%s " "${AUDIOBOOK_START_ORDER[@]}"; echo; '
            'printf "%s " "${AUDIOBOOK_STOP_ORDER[@]}"',
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.splitlines()
    assert out[0].split() == list(reversed(out[1].split()))
    assert (
        "for svc in audiobook-api audiobook-proxy audiobook-redirect audiobook-converter"
        not in text
    )


def test_resolve_caddy_dir_reads_installed_config(tmp_path):
    conf = tmp_path / "audiobooks.conf"
    conf.write_text('AUDIOBOOKS_DATA=/x\nAUDIOBOOKS_CADDY_DIR="/srv/caddy-x"\n')

    def resolve(conf_path, env_val=""):
        env = os.environ.copy()
        env.pop("AUDIOBOOKS_CADDY_DIR", None)
        if env_val:
            env["AUDIOBOOKS_CADDY_DIR"] = env_val
        return subprocess.run(
            [
                "bash",
                "-c",
                f'UPGRADE_SH_SOURCE_ONLY=1 source "{UPGRADE_SH}"; YELLOW="" NC=""; '
                f'_resolve_caddy_dir "{conf_path}"',
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=30,
        ).stdout.strip()

    assert resolve(conf) == "/srv/caddy-x"  # non-default honoured from the config
    assert resolve(conf, "/env/caddy") == "/env/caddy"  # env wins, like the helper
    assert resolve(tmp_path / "missing.conf") == "/etc/caddy"  # default
    conf.write_text("AUDIOBOOKS_CADDY_DIR='/a|b'\n")
    assert resolve(conf) == "/etc/caddy"  # unsafe value ignored
    conf.write_text('AUDIOBOOKS_CADDY_DIR="relative/dir"\n')
    assert resolve(conf) == "/etc/caddy"


def test_caddy_block_uses_resolved_dir_in_both_installers():
    up = UPGRADE_SH.read_text()
    block = up[up.index("Upgrading Caddy maintenance page") : up.index('caddy_changed" == "true')]
    assert "AUDIOBOOKS_CADDY_DIR" not in block and block.count("${caddy_dir}") >= 3
    assert "s|__CADDY_DIR__|${caddy_dir}|g" in block
    inst = INSTALL_SH.read_text()
    assert "AUDIOBOOKS_CADDY_DIR" in inst and "s|__CADDY_DIR__|${caddy_dir}|g" in inst


# --------------------------------------------------------------------------
# review round 3: REAL pip as a different uid; completion marker; services
# --------------------------------------------------------------------------
REAL_PIP = Path(sys.prefix) / "bin" / "pip"


def _can_sudo_claude() -> bool:
    try:
        return (
            subprocess.run(
                ["sudo", "-n", "-u", "claude", "true"], capture_output=True, timeout=20
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_real_uid = pytest.mark.skipif(
    not (REAL_PIP.exists() and _can_sudo_claude()),
    reason="needs `sudo -n -u claude` (passwordless) and the project venv's real pip",
)

WRAP_PIP = """#!/bin/bash
# real pip, but never touching the network or the venv: install is forced to
# --dry-run --no-index --no-deps. A FAIL file next to the wrapper makes the
# --upgrade sync fail (the rollback install, without --upgrade, still runs).
if [[ "$1" == "install" ]]; then
    if [[ -e "$(dirname "$0")/FAIL" && " $* " == *" --upgrade "* ]]; then
        echo "forced failure" >&2
        exit 1
    fi
    exec "{real}" "$@" --dry-run --no-index --no-deps
fi
exec "{real}" "$@"
"""


@pytest.fixture
def uid_tree():
    """A tree another uid can enter (pytest's tmp_path is 0700, so use a plain mkdtemp)."""
    root = Path(tempfile.mkdtemp(prefix="am-uid-"))
    root.chmod(0o755)
    target = root / "target"
    (target / "library" / "venv" / "bin").mkdir(parents=True)
    for d in (
        target,
        target / "library",
        target / "library" / "venv",
        target / "library" / "venv" / "bin",
    ):
        d.chmod(0o755)
    wrapper = target / "library" / "venv" / "bin" / "pip"
    wrapper.write_text(WRAP_PIP.format(real=REAL_PIP))
    wrapper.chmod(0o755)
    yield root, target
    shutil.rmtree(root, ignore_errors=True)


def _real_sync(root: Path, target: Path, req: Path):
    script = (
        'RED="" GREEN="" BLUE="" YELLOW="" NC=""\n'
        + _deps_functions()
        + f'\nUPGRADE_VENV_OWNER=claude\nsync_python_dependencies "{target}" sudo "{req}"\n'
    )
    env = os.environ.copy()
    env["TMPDIR"] = str(root)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False, timeout=120
    )


@needs_real_uid
@pytest.mark.parametrize("mode_and_feed", [(0o600, "redirect"), (0o644, "pipe")])
def test_real_pip_as_other_uid_cannot_read_dev_stdin(mode_and_feed):
    """Control: documents the defect that sank the stdin approach. pip open()s
    /dev/stdin, which resolves to the underlying inode, and the permission check is
    repeated as the new uid: a 0600 file (or a pipe) fed on stdin is EACCES."""
    mode, feed = mode_and_feed
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o755)
        req = Path(d) / "r.txt"
        req.write_text("pip\n")
        req.chmod(mode)
        src = f'< "{req}"' if feed == "redirect" else ""
        pre = f'cat "{req}" | ' if feed == "pipe" else ""
        proc = subprocess.run(
            f'{pre}sudo -n -u claude "{REAL_PIP}" install --dry-run --no-index --no-deps '
            f"-r /dev/stdin {src}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert proc.returncode != 0, proc.stdout
    assert "Permission denied" in proc.stderr and "/dev/stdin" in proc.stderr


@needs_real_uid
def test_real_pip_as_other_uid_reads_the_file_the_sync_hands_it(uid_tree):
    root, target = uid_tree
    req = root / "requirements.txt"
    req.write_text("pip\n")
    proc = _real_sync(root, target, req)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Dependencies synced" in proc.stdout
    assert "Permission denied" not in proc.stdout + proc.stderr
    assert (target / RECORD_REL).exists()


@needs_real_uid
def test_real_pip_other_uid_from_github_style_private_release_dir(uid_tree):
    root, target = uid_tree
    release = root / "release" / "library"
    release.mkdir(parents=True)
    req = release / "requirements.txt"
    req.write_text("pip\n")
    req.chmod(0o600)
    release.chmod(0o700)
    release.parent.chmod(0o700)
    proc = _real_sync(root, target, req)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_real_uid
def test_real_pip_other_uid_rollback_works_after_failed_sync(uid_tree):
    root, target = uid_tree
    (target / "library" / "venv" / "bin" / "FAIL").write_text("")
    req = root / "requirements.txt"
    req.write_text("pip\n")
    proc = _real_sync(root, target, req)
    assert proc.returncode != 0
    assert "Previous package set restored" in proc.stdout, proc.stdout + proc.stderr
    assert "Could not restore" not in proc.stdout


@needs_real_uid
def test_staged_scratch_dirs_are_removed(uid_tree):
    root, target = uid_tree
    req = root / "requirements.txt"
    req.write_text("pip\n")
    _real_sync(root, target, req)
    leftovers = [p.name for p in root.iterdir() if p.name.startswith("tmp.")]
    assert leftovers == [], leftovers


def test_stage_dir_is_traversable_by_other_users(tmp_path):
    out = subprocess.run(
        [
            "bash",
            "-c",
            _deps_functions() + '\nd=$(_make_stage_dir); stat -c %a "$d"; rm -rf "$d"',
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        timeout=30,
    )
    assert out.stdout.strip() == "755"


def test_completed_rebuild_with_stale_aside_removes_it_loudly(tmp_path):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    lib = target / "library"
    _venv_with_marker(lib / "venv", "finished-rebuild")
    (lib / "venv" / ".rebuild-complete").write_text("")
    _venv_with_marker(lib / "venv.pre-upgrade", "stale")
    (lib / "venv.pre-upgrade" / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.13.5\n")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    proc = _prepare_harness(tmp_path, target, project, major=False)
    assert "RC=0" in proc.stdout, proc.stdout + proc.stderr
    assert not (lib / "venv.pre-upgrade").exists()
    assert (lib / "venv" / "bin" / "marker").read_text() == "finished-rebuild\n"
    assert "Removing stale" in proc.stdout and "version = 3.13.5" in proc.stdout
    assert re.search(r"modified \d{4}-\d{2}-\d{2}", proc.stdout)


def test_marker_written_only_when_rebuild_completes(tmp_path):
    target = _mk_tree(tmp_path / "target", "1.0.0", "OLD")
    lib = target / "library"
    _venv_with_marker(lib / "venv", "original")
    project = _mk_tree(tmp_path / "project", "2.0.0", "NEW")
    proc = _prepare_harness(tmp_path, target, project, major=True)
    assert "RC=0" in proc.stdout
    assert (lib / "venv" / ".rebuild-complete").exists()
    # an interrupted rebuild never has the marker: restored, not removed (tested above);
    # and a failed pip leaves no marker on the venv that is put back
    target2 = _mk_tree(tmp_path / "t2" / "target", "1.0.0", "OLD")
    _venv_with_marker(target2 / "library" / "venv", "original")
    (tmp_path / "t2" / "project").mkdir(parents=True, exist_ok=True)
    project2 = _mk_tree(tmp_path / "t2" / "project", "2.0.0", "NEW")
    proc2 = _prepare_harness(
        tmp_path / "t2", target2, project2, major=True, prelude="export FAKE_VENV_FAIL=1"
    )
    assert "RC=1" in proc2.stdout
    assert not (target2 / "library" / "venv" / ".rebuild-complete").exists()


def test_stream_translate_uses_the_venv_and_is_in_both_stop_lists():
    daemon = (ROOT / "scripts" / "stream-translate-daemon.sh").read_text()
    assert 'VENV_PYTHON="${AUDIOBOOKS_VENV}/bin/python"' in daemon
    cli = _bash_array(UPGRADE_SH.read_text(), "AUDIOBOOK_STOP_ORDER")
    helper = _bash_array(HELPER.read_text(), "local services_stop_order")
    for lst in (cli, helper):
        assert "audiobook-stream-translate" in lst
        assert lst.index("audiobook-stream-translate") < lst.index("audiobook-api")
    assert cli == helper


def test_drift_between_deploys_is_shown(tmp_path):
    """Venv hand-downgraded below a floor after the last deploy: the diff must say so,
    even though the record file (last deploy) equals the post-sync set."""
    target = _make_install(tmp_path)
    (target / RECORD_REL).write_text("flask==3.0.0\nurllib3==2.8.0\n")
    proc, _ = _run_sync(
        tmp_path,
        target,
        "flask==3.0.0\nurllib3==2.8.0\n",
        pre="flask==3.0.0\nurllib3==2.7.0\n",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "  ~ urllib3 2.7.0 -> 2.8.0" in proc.stdout
    assert "(+0 added, -0 removed, ~1 changed vs the venv before this sync)" in proc.stdout
    assert "no changes" not in proc.stdout
    assert (target / RECORD_REL).read_text() == "flask==3.0.0\nurllib3==2.8.0\n"


def test_missing_pre_sync_freeze_falls_back_to_record_and_says_so(tmp_path):
    target = _make_install(tmp_path)
    (target / RECORD_REL).write_text("flask==3.0.0\n")
    proc, _ = _run_sync(tmp_path, target, "flask==3.1.0\n", pre="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "  ~ flask 3.0.0 -> 3.1.0" in proc.stdout
    assert "pre-sync freeze unavailable" in proc.stdout
