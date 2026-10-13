"""${VAR} references in path settings (Audiobook-Manager-1sk).

systemd's EnvironmentFile= does not expand ${...}, so a service receives the
literal text of a conf-file template such as
``AUDIOBOOKS_LIBRARY=${AUDIOBOOKS_DATA}/Library``; a unit whose
WorkingDirectory is the install root then resolved it as a RELATIVE path
(creating /opt/audiobooks/${AUDIOBOOKS_DATA}/Library). Three layers now expand
and guard: library/config.py, lib/audiobook-config.sh and the install-manifest
reconciler. Every test here runs in a subprocess with a scratch HOME, so no
installed config is modified.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIB_SH = PROJECT_ROOT / "lib" / "audiobook-config.sh"
RECONCILER = PROJECT_ROOT / "scripts" / "reconcile-filesystem.sh"
MANIFEST = PROJECT_ROOT / "scripts" / "install-manifest.sh"
EXAMPLE_CONF = PROJECT_ROOT / "etc" / "audiobooks.conf.example"
LIBRARY_DIR = PROJECT_ROOT / "library"

LITERAL_LIBRARY = "${AUDIOBOOKS_DATA}/Library"


def _py_environ(tmp_path: Path, extra: dict[str, str]) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("AUDIOBOOKS_")}
    base["HOME"] = str(tmp_path / "home")
    base["AUDIOBOOKS_SKIP_USER_GATE"] = "1"
    # Never read the host's installed config: the sandbox project file is the only source.
    base["AUDIOBOOKS_SKIP_SYSTEM_CONFIG"] = "1"
    base.update(extra)
    return base


def _py(
    tmp_path: Path, extra: dict[str, str], expr: str, project_conf: str = ""
) -> subprocess.CompletedProcess:
    """Evaluate ``expr`` in a fresh interpreter importing a sandbox copy of config.py."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "library").mkdir(parents=True, exist_ok=True)
    shutil.copy(LIBRARY_DIR / "config.py", sandbox / "library" / "config.py")
    (sandbox / "config.env").write_text(project_conf)
    return subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, test-only
        [sys.executable, "-c", f"import config; print({expr})"],
        cwd=sandbox / "library",
        env=_py_environ(tmp_path, extra),
        capture_output=True,
        text=True,
        check=False,
    )


def _sh(
    tmp_path: Path, extra: dict[str, str], body: str, user_conf: str | None = None
) -> subprocess.CompletedProcess:
    """Source a sandbox copy of lib/audiobook-config.sh whose system-conf path is dead.

    The copy differs from the real file only in that one path, so the host's
    installed system config cannot leak into the assertions.
    """
    sandbox = tmp_path / "sandbox" / "lib"
    sandbox.mkdir(parents=True, exist_ok=True)
    text = LIB_SH.read_text().replace("/etc/audiobooks/audiobooks.conf", "/nonexistent/none.conf")
    assert "/nonexistent/none.conf" in text
    (sandbox / "audiobook-config.sh").write_text(text)
    home = tmp_path / "home"
    if user_conf is not None:
        (home / ".config" / "audiobooks").mkdir(parents=True, exist_ok=True)
        (home / ".config" / "audiobooks" / "audiobooks.conf").write_text(user_conf)
    # Minimal process environment: only PATH, a scratch HOME and the test's values.
    proc_env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    proc_env.update(extra)
    return subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, test-only
        [
            "bash",
            "-c",
            f'source "{sandbox / "audiobook-config.sh"}"; rc=$?; {body}; exit $rc',
        ],
        env=proc_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _manifest_path_keys() -> set[str]:
    script = f"""
        export LIB_DIR=/tmp STATE_DIR=/tmp LOG_DIR=/tmp CONFIG_DIR=/tmp
        source {MANIFEST}
        printf '%s\\n' "${{CONFIG_PATH_KEYS[@]}}"
    """
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return set(out.stdout.split())


# --------------------------------------------------------------------------
# Python: library/config.py
# --------------------------------------------------------------------------


class TestPythonExpansion:
    def test_literal_env_reference_resolves(self, tmp_path):
        out = _py(
            tmp_path,
            {"AUDIOBOOKS_DATA": "/x", "AUDIOBOOKS_LIBRARY": LITERAL_LIBRARY},
            "config.AUDIOBOOKS_LIBRARY",
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "/x/Library"

    def test_nested_chain_resolves_in_dependency_order(self, tmp_path):
        out = _py(
            tmp_path,
            {
                "AUDIOBOOKS_DATA": "/x",
                "AUDIOBOOKS_LIBRARY": "${AUDIOBOOKS_DATA}/Library",
                "AUDIOBOOKS_LOGS": "$AUDIOBOOKS_LIBRARY/logs",
                "AUDIOBOOKS_WEBM_CACHE": "${AUDIOBOOKS_LOGS}/webm",
            },
            "config.AUDIOBOOKS_WEBM_CACHE",
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "/x/Library/logs/webm"

    def test_reference_to_computed_default(self, tmp_path):
        """${AUDIOBOOKS_HOME} resolves although it is in no environment or conf source."""
        out = _py(
            tmp_path,
            {"AUDIOBOOKS_CERTS": "${AUDIOBOOKS_HOME}/certs-x"},
            "config.AUDIOBOOKS_CERTS == config.AUDIOBOOKS_HOME / 'certs-x'",
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "True"

    def test_legacy_alias_is_expanded(self, tmp_path):
        out = _py(
            tmp_path,
            {"AUDIOBOOKS_DATA": "/x", "AUDIOBOOKS_LIBRARY": LITERAL_LIBRARY},
            "config.AUDIOBOOK_DIR",
        )
        assert out.stdout.strip() == "/x/Library"

    def test_conf_file_reference_resolves(self, tmp_path):
        out = _py(
            tmp_path,
            {},
            "config.AUDIOBOOKS_SOURCES",
            project_conf='AUDIOBOOKS_DATA="/y"\nAUDIOBOOKS_SOURCES="${AUDIOBOOKS_DATA}/Sources"\n',
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "/y/Sources"

    def test_unknown_variable_is_an_error_naming_the_setting(self, tmp_path):
        out = _py(
            tmp_path,
            {"AUDIOBOOKS_LIBRARY": "${NO_SUCH_VAR_1SK}/Library"},
            "config.AUDIOBOOKS_LIBRARY",
        )
        assert out.returncode != 0
        assert "ConfigError" in out.stderr
        assert "AUDIOBOOKS_LIBRARY" in out.stderr
        assert "NO_SUCH_VAR_1SK" in out.stderr

    def test_cycle_is_an_error_naming_the_setting(self, tmp_path):
        out = _py(
            tmp_path,
            {
                "AUDIOBOOKS_LIBRARY": "${AUDIOBOOKS_SOURCES}/a",
                "AUDIOBOOKS_SOURCES": "${AUDIOBOOKS_LIBRARY}/b",
            },
            "config.AUDIOBOOKS_LIBRARY",
        )
        assert out.returncode != 0
        assert "circular" in out.stderr
        assert "AUDIOBOOKS_LIBRARY" in out.stderr

    def test_guard_refuses_dollar_that_is_not_a_reference(self, tmp_path):
        out = _py(tmp_path, {"AUDIOBOOKS_LIBRARY": "/x/$"}, "config.AUDIOBOOKS_LIBRARY")
        assert out.returncode != 0
        assert "unexpanded" in out.stderr
        assert "AUDIOBOOKS_LIBRARY" in out.stderr

    def test_guard_covers_legacy_alias(self, tmp_path):
        out = _py(tmp_path, {"COVER_DIR": "/x/$"}, "config.COVER_DIR")
        assert out.returncode != 0
        assert "COVER_DIR" in out.stderr

    def test_plain_values_unchanged(self, tmp_path):
        out = _py(tmp_path, {"AUDIOBOOKS_DATA": "/plain/path"}, "config.AUDIOBOOKS_LIBRARY")
        assert out.stdout.strip() == "/plain/path/Library"

    def test_path_keys_are_subset_of_manifest_list(self, tmp_path):
        out = _py(tmp_path, {}, "sorted(config._PATH_KEYS)")
        assert out.returncode == 0, out.stderr
        py_keys = set(json.loads(out.stdout.replace("'", '"')))
        assert py_keys <= _manifest_path_keys(), py_keys - _manifest_path_keys()


# --------------------------------------------------------------------------
# Shell: lib/audiobook-config.sh
# --------------------------------------------------------------------------


class TestShellExpansion:
    def test_literal_preset_reference_resolves(self, tmp_path):
        """The systemd case: the variable is ALREADY set literally before sourcing."""
        out = _sh(
            tmp_path,
            {"AUDIOBOOKS_DATA": "/x", "AUDIOBOOKS_LIBRARY": LITERAL_LIBRARY},
            'echo "$AUDIOBOOKS_LIBRARY"',
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "/x/Library"

    def test_nested_chain(self, tmp_path):
        out = _sh(
            tmp_path,
            {
                "AUDIOBOOKS_DATA": "/x",
                "AUDIOBOOKS_LIBRARY": "${AUDIOBOOKS_DATA}/Library",
                "AUDIOBOOKS_LOGS": "$AUDIOBOOKS_LIBRARY/logs",
                "AUDIOBOOKS_WEBM_CACHE": "${AUDIOBOOKS_LOGS}/webm",
            },
            'echo "$AUDIOBOOKS_WEBM_CACHE"',
        )
        assert out.stdout.strip() == "/x/Library/logs/webm"

    def test_default_derived_from_literal_variable(self, tmp_path):
        """A default built from a literal-valued variable ends up expanded too."""
        out = _sh(
            tmp_path,
            {"AUDIOBOOKS_DATA": "${AUDIOBOOKS_VAR_DIR}/d", "AUDIOBOOKS_VAR_DIR": "/v"},
            'echo "$AUDIOBOOKS_LIBRARY"',
        )
        assert out.stdout.strip() == "/v/d/Library"

    def test_conf_file_reference_resolves(self, tmp_path):
        out = _sh(
            tmp_path,
            {"AUDIOBOOKS_DATA": "/y"},
            'echo "$AUDIOBOOKS_LIBRARY"',
            user_conf='AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"\n',
        )
        assert out.stdout.strip() == "/y/Library"

    def test_conf_loader_does_not_run_command_substitution(self, tmp_path):
        marker = tmp_path / "pwned"
        out = _sh(tmp_path, {}, "true", user_conf=f"AUDIOBOOKS_LIBRARY=/z/$(touch {marker})\n")
        assert not marker.exists()
        assert out.returncode != 0  # the leftover '$' is refused by the guard

    def test_unknown_variable_is_refused_naming_the_setting(self, tmp_path):
        out = _sh(tmp_path, {"AUDIOBOOKS_LIBRARY": "${NO_SUCH_VAR_1SK}/L"}, "true")
        assert out.returncode == 1
        assert "AUDIOBOOKS_LIBRARY" in out.stderr
        assert "NO_SUCH_VAR_1SK" in out.stderr

    def test_cycle_is_refused(self, tmp_path):
        out = _sh(
            tmp_path,
            {
                "AUDIOBOOKS_LIBRARY": "${AUDIOBOOKS_SOURCES}/a",
                "AUDIOBOOKS_SOURCES": "${AUDIOBOOKS_LIBRARY}/b",
            },
            "true",
        )
        assert out.returncode == 1
        assert "circular" in out.stderr

    def test_guard_refuses_stray_dollar(self, tmp_path):
        out = _sh(tmp_path, {"AUDIOBOOKS_LIBRARY": "/x/$"}, "true")
        assert out.returncode == 1
        assert "unexpanded" in out.stderr
        assert "AUDIOBOOKS_LIBRARY" in out.stderr

    def test_shell_list_equals_manifest_list(self):
        text = LIB_SH.read_text()
        start = text.index("_AB_PATH_VARS=(") + len("_AB_PATH_VARS=(")
        shell_keys = set(text[start : text.index(")", start)].split())
        assert shell_keys == _manifest_path_keys()


# --------------------------------------------------------------------------
# Reconciler: scripts/reconcile-filesystem.sh
# --------------------------------------------------------------------------

CONF_BODY = (
    '# comment AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"\n'
    'AUDIOBOOKS_DATA="/x"\n'
    'AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"\n'
    "AUDIOBOOKS_LOGS=$AUDIOBOOKS_LIBRARY/logs\n"
    'AUDIOBOOKS_CERTS="${NO_SUCH_VAR_1SK}/c"\n'
    'AUDIOBOOKS_API_PORT="5001"\n'
    'SOME_SECRET_LIKE="a$b"\n'
)


def _reconcile(tmp_path: Path, mode: str) -> tuple[subprocess.CompletedProcess, Path]:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    conf = cfg / "audiobooks.conf"
    if not conf.exists():
        conf.write_text(CONF_BODY)
    for d in ("lib", "state", "log"):
        (tmp_path / d).mkdir(exist_ok=True)
    proc_env = {
        **os.environ,
        "PROJECT_DIR": str(PROJECT_ROOT),
        "LIB_DIR": str(tmp_path / "lib"),
        "STATE_DIR": str(tmp_path / "state"),
        "LOG_DIR": str(tmp_path / "log"),
        "CONFIG_DIR": str(cfg),
        "CONF_FILE": str(conf),
        "SYSTEMD_DIR": str(tmp_path / "sd"),
        "BIN_DIR": str(tmp_path / "bin"),
        "USE_SUDO": "",
        "RECONCILE_MODE": mode,
    }
    result = subprocess.run(
        ["bash", str(RECONCILER)], capture_output=True, text=True, env=proc_env, check=False
    )
    return result, conf


class TestReconcilerReferences:
    def test_report_mode_reports_and_does_not_modify(self, tmp_path):
        result, conf = _reconcile(tmp_path, "report")
        assert result.returncode == 0, result.stderr
        assert "unexpanded reference" in result.stdout
        assert "AUDIOBOOKS_LIBRARY=${AUDIOBOOKS_DATA}/Library" in result.stdout
        assert "-> /x/Library" in result.stdout
        assert conf.read_text() == CONF_BODY
        assert not list(conf.parent.glob("*.bak-expand-*"))

    def test_enforce_mode_rewrites_with_backup(self, tmp_path):
        result, conf = _reconcile(tmp_path, "enforce")
        assert result.returncode == 0, result.stderr
        new = conf.read_text()
        assert 'AUDIOBOOKS_LIBRARY="/x/Library"' in new
        assert 'AUDIOBOOKS_LOGS="/x/Library/logs"' in new
        # unresolvable, non-path and comment lines are untouched
        assert 'AUDIOBOOKS_CERTS="${NO_SUCH_VAR_1SK}/c"' in new
        assert 'SOME_SECRET_LIKE="a$b"' in new
        assert 'AUDIOBOOKS_API_PORT="5001"' in new
        assert '# comment AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"' in new
        backups = list(conf.parent.glob("audiobooks.conf.bak-expand-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == CONF_BODY
        assert "cannot be expanded (undefined variable NO_SUCH_VAR_1SK)" in result.stdout
        assert "[fix]   expanded AUDIOBOOKS_LIBRARY" in result.stdout

    def test_enforce_is_idempotent(self, tmp_path):
        _reconcile(tmp_path, "enforce")
        result, conf = _reconcile(tmp_path, "enforce")
        # nothing new was written; only the unresolvable CERTS line is still reported
        assert len(list(conf.parent.glob("audiobooks.conf.bak-expand-*"))) == 1
        assert "AUDIOBOOKS_LIBRARY=${" not in result.stdout


def test_example_conf_documents_that_systemd_does_not_expand():
    assert "does NOT expand" in EXAMPLE_CONF.read_text()
