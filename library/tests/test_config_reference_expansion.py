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
        assert "contains '$'" in out.stderr
        assert "rename" in out.stderr  # L6: says how to get out of it
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


class TestPythonRegressions:
    def test_non_path_value_with_dollar_is_untouched(self, tmp_path):
        """M1: a display name like 'Pay $5 Library' must neither expand nor raise."""
        out = _py(
            tmp_path,
            {"WEBAUTHN_RP_NAME": "Pay $5 Library $HOME ${NOPE}"},
            "config.get_config('WEBAUTHN_RP_NAME')",
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "Pay $5 Library $HOME ${NOPE}"

    def test_digit_led_name_is_not_a_reference(self, tmp_path):
        out = _py(tmp_path, {"AUDIOBOOKS_DATA": "/x/$5"}, "config.AUDIOBOOKS_DATA")
        assert out.returncode != 0  # '$5' is a literal '$', refused by the path guard
        assert "contains '$'" in out.stderr

    def test_direct_environ_reader_sees_expanded_path(self, tmp_path):
        """M2: ~40 call sites read os.environ directly and used to get the literal."""
        out = _py(
            tmp_path,
            {
                "AUDIOBOOKS_DATA": "/x",
                "AUDIOBOOKS_LIBRARY": LITERAL_LIBRARY,
                "AUDIOBOOKS_LOGS": "$AUDIOBOOKS_LIBRARY/logs",
                "AUTH_KEY_FILE": "${AUDIOBOOKS_VAR_DIR}/auth.key",
                "NOT_A_PATH_SETTING": "keep $AUDIOBOOKS_DATA",
            },
            "[__import__('os').environ[k] for k in "
            "('AUDIOBOOKS_LIBRARY','AUDIOBOOKS_LOGS','AUTH_KEY_FILE','NOT_A_PATH_SETTING')]",
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == (
            "['/x/Library', '/x/Library/logs', '/var/lib/audiobooks/auth.key', "
            "'keep $AUDIOBOOKS_DATA']"
        )


# Settings that both implementations must resolve identically (M4). Each case
# is an environment; the keys compared are those both sides compute.
PARITY_KEYS = (
    "AUDIOBOOKS_HOME",
    "AUDIOBOOKS_DATA",
    "AUDIOBOOKS_LIBRARY",
    "AUDIOBOOKS_SOURCES",
    "AUDIOBOOKS_LOGS",
    "AUDIOBOOKS_DATABASE",
    "AUDIOBOOKS_COVERS",
    "AUDIOBOOKS_CERTS",
    "AUDIOBOOKS_VENV",
    "AUDIOBOOKS_WEBM_CACHE",
    "AUDIOBOOKS_STREAMING_AUDIO_DIR",
    "AUDIOBOOKS_CONVERTER",
)
PARITY_CASES = [
    {},
    {"AUDIOBOOKS_DATA": "${AUDIOBOOKS_VAR_DIR}/media"},  # reference to a built-in default
    {"AUDIOBOOKS_VAR_DIR": "/v", "AUDIOBOOKS_DATA": "$AUDIOBOOKS_VAR_DIR/m"},
    {"AUDIOBOOKS_LOGS": "${AUDIOBOOKS_LIBRARY}/l", "AUDIOBOOKS_DATA": "/d"},
    {"AUDIOBOOKS_CERTS": "${AUDIOBOOKS_HOME}/c"},
    {"AUDIOBOOKS_COVERS": "${AUDIOBOOKS_DATABASE}.covers", "AUDIOBOOKS_VAR_DIR": "/v2"},
]


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda c: ",".join(sorted(c)) or "defaults")
def test_python_and_shell_resolve_identically(tmp_path, case):
    exprs = ", ".join(f"str(config.{k})" for k in PARITY_KEYS)
    py = _py(tmp_path, case, f"'|'.join([{exprs}])")
    assert py.returncode == 0, py.stderr
    echo = " ".join(f'"${k}"' for k in PARITY_KEYS)
    sh = _sh(tmp_path, case, f"printf '%s|' {echo}")
    assert sh.returncode == 0, sh.stderr
    assert sh.stdout.rstrip("|").split("|") == py.stdout.strip().split("|")


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

    def test_unknown_variable_is_refused_and_unset(self, tmp_path):
        """M3: the refused setting is UNSET (never the literal); the others still expand."""
        out = _sh(
            tmp_path,
            {
                "AUDIOBOOKS_LIBRARY": "${NO_SUCH_VAR_1SK}/L",
                "AUDIOBOOKS_DATA": "/x",
                "AUDIOBOOKS_SOURCES": "${AUDIOBOOKS_DATA}/S",
            },
            'echo "lib=${AUDIOBOOKS_LIBRARY+SET}|src=${AUDIOBOOKS_SOURCES-}"',
        )
        assert out.returncode == 1
        assert "AUDIOBOOKS_LIBRARY" in out.stderr
        assert "NO_SUCH_VAR_1SK" in out.stderr
        assert out.stdout.strip() == "lib=|src=/x/S"

    def test_refusal_is_reported_once_to_stderr_and_journal(self, tmp_path):
        fake = tmp_path / "fakebin"
        fake.mkdir()
        log = tmp_path / "logger.calls"
        (fake / "logger").write_text(f'#!/bin/bash\necho "$*" >> "{log}"\n')
        (fake / "logger").chmod(0o755)
        out = _sh(
            tmp_path,
            {"AUDIOBOOKS_LIBRARY": "${NO_SUCH_VAR_1SK}/L", "PATH": f"{fake}:{os.environ['PATH']}"},
            "true",
        )
        assert out.returncode == 1
        assert out.stderr.count("AUDIOBOOKS_LIBRARY") == 1
        calls = log.read_text().splitlines()
        assert len(calls) == 1 and "AUDIOBOOKS_LIBRARY" in calls[0]

    def test_cycle_is_refused_and_both_unset(self, tmp_path):
        out = _sh(
            tmp_path,
            {
                "AUDIOBOOKS_LIBRARY": "${AUDIOBOOKS_SOURCES}/a",
                "AUDIOBOOKS_SOURCES": "${AUDIOBOOKS_LIBRARY}/b",
            },
            'echo "${AUDIOBOOKS_LIBRARY+SET}${AUDIOBOOKS_SOURCES+SET}"',
        )
        assert out.returncode == 1
        assert "circular" in out.stderr
        assert out.stdout.strip() == ""

    def test_guard_refuses_stray_dollar(self, tmp_path):
        out = _sh(tmp_path, {"AUDIOBOOKS_LIBRARY": "/x/$"}, "true")
        assert out.returncode == 1
        assert "contains '$'" in out.stderr
        assert "rename" in out.stderr
        assert "AUDIOBOOKS_LIBRARY" in out.stderr
        assert out.stdout.strip() == ""

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
        assert "cannot be expanded (undefined variable NO_SUCH_VAR_1SK" in result.stdout
        assert "[fix]   expanded AUDIOBOOKS_LIBRARY" in result.stdout

    def test_enforce_is_idempotent(self, tmp_path):
        _reconcile(tmp_path, "enforce")
        result, conf = _reconcile(tmp_path, "enforce")
        # nothing new was written; only the unresolvable CERTS line is still reported
        assert len(list(conf.parent.glob("audiobooks.conf.bak-expand-*"))) == 1
        assert "AUDIOBOOKS_LIBRARY=${" not in result.stdout


RC_OP_ENV = {
    "HOME": "/home/operator-1sk",
    "PROJECT_DIR": str(PROJECT_ROOT),
    "AUDIOBOOKS_HOME": str(PROJECT_ROOT),
    "key": "AUDIOBOOKS_TRIGGERS",
}


def _reconcile_conf(
    tmp_path: Path, conf_text: str, mode: str = "enforce", use_sudo: str = ""
) -> tuple[subprocess.CompletedProcess, Path]:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    conf = cfg / "audiobooks.conf"
    conf.write_bytes(conf_text.encode())
    conf.chmod(0o640)
    for d in ("lib", "state", "log"):
        (tmp_path / d).mkdir(exist_ok=True)
    proc_env = {
        **os.environ,
        **RC_OP_ENV,
        "PROJECT_DIR": str(PROJECT_ROOT),
        "LIB_DIR": str(tmp_path / "lib"),
        "STATE_DIR": str(tmp_path / "state"),
        "LOG_DIR": str(tmp_path / "log"),
        "CONFIG_DIR": str(cfg),
        "CONF_FILE": str(conf),
        "SYSTEMD_DIR": str(tmp_path / "sd"),
        "BIN_DIR": str(tmp_path / "bin"),
        "USE_SUDO": use_sudo,
        "RECONCILE_MODE": mode,
    }
    result = subprocess.run(
        ["bash", str(RECONCILER)], capture_output=True, text=True, env=proc_env, check=False
    )
    return result, conf


class TestReconcilerLookupAndFormats:
    def test_operator_environment_never_leaks_into_values(self, tmp_path):
        """H1: HOME / PROJECT_DIR / AUDIOBOOKS_HOME of the operator, and the
        script's own loop variable, must not be used to resolve a reference."""
        text = (
            "AUDIOBOOKS_STAGING=$HOME/staging\n"
            'AUDIOBOOKS_WEBM_CACHE="${PROJECT_DIR}/webm"\n'
            'AUDIOBOOKS_TRIGGERS="${key}/trig"\n'
            'AUDIOBOOKS_CERTS="${AUDIOBOOKS_HOME}/c"\n'
        )
        result, conf = _reconcile_conf(tmp_path, text)
        new = conf.read_text()
        state, lib = tmp_path / "state", tmp_path / "lib"
        # HOME is what the units set (AUDIOBOOKS_VAR_DIR == STATE_DIR), not the operator's
        assert f"AUDIOBOOKS_STAGING={state}/staging" in new or f'"{state}/staging"' in new
        assert "/home/operator-1sk" not in new
        # AUDIOBOOKS_HOME comes from the install layout (LIB_DIR), not the operator's env
        assert f'AUDIOBOOKS_CERTS="{lib}/c"' in new
        assert str(PROJECT_ROOT) not in new
        # not provided by the units: left unexpanded and reported, never guessed
        assert 'AUDIOBOOKS_WEBM_CACHE="${PROJECT_DIR}/webm"' in new
        assert 'AUDIOBOOKS_TRIGGERS="${key}/trig"' in new
        assert "cannot be expanded (undefined variable PROJECT_DIR" in result.stdout
        assert "cannot be expanded (undefined variable key" in result.stdout

    def test_inline_comment_export_crlf_unquoted(self, tmp_path):
        text = (
            'AUDIOBOOKS_DATA="/x"  # data root\n'
            'AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library" # keep me\n'
            "export AUDIOBOOKS_SOURCES=${AUDIOBOOKS_DATA}/Sources # unquoted\n"
            "AUDIOBOOKS_LOGS='${AUDIOBOOKS_DATA}/logs'\r\n"
            "AUDIOBOOKS_API_PORT=5001\r\n"
        )
        result, conf = _reconcile_conf(tmp_path, text)
        assert result.returncode == 0, result.stderr
        assert conf.read_bytes().decode() == (
            'AUDIOBOOKS_DATA="/x"  # data root\n'
            'AUDIOBOOKS_LIBRARY="/x/Library" # keep me\n'
            'export AUDIOBOOKS_SOURCES="/x/Sources" # unquoted\n'
            'AUDIOBOOKS_LOGS="/x/logs"\r\n'
            "AUDIOBOOKS_API_PORT=5001\r\n"
        )

    def test_unrecognised_quoting_is_reported_not_rewritten(self, tmp_path):
        text = 'AUDIOBOOKS_DATA="/x"\nAUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}"/Library\n'
        result, conf = _reconcile_conf(tmp_path, text)
        assert conf.read_text() == text
        assert "quoting form the reconciler will not rewrite" in result.stdout

    def test_rewrite_is_atomic_and_preserves_mode(self, tmp_path):
        text = 'AUDIOBOOKS_DATA="/x"\nAUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"\n'
        before = None
        result, conf = _reconcile_conf(tmp_path, text)
        assert "[fix]   expanded AUDIOBOOKS_LIBRARY" in result.stdout
        assert oct(conf.stat().st_mode & 0o777) == "0o640"
        leftovers = [
            p.name
            for p in conf.parent.iterdir()
            if p.name.startswith("audiobooks.conf.") and ".bak-expand-" not in p.name
        ]
        assert leftovers == []  # no replacement temp left beside the config
        (bak,) = conf.parent.glob("audiobooks.conf.bak-expand-*")
        assert bak.read_text() == text
        assert before is None

    def test_all_config_access_goes_through_the_sudo_path(self, tmp_path):
        """L3: reads, the backup and the swap all use USE_SUDO, like other /etc writes."""
        wrapper = tmp_path / "fakesudo"
        calls = tmp_path / "sudo.calls"
        wrapper.write_text(f'#!/bin/bash\necho "$1" >> "{calls}"\nexec "$@"\n')
        wrapper.chmod(0o755)
        text = 'AUDIOBOOKS_DATA="/x"\nAUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"\n'
        _reconcile_conf(tmp_path, text, use_sudo=str(wrapper))
        used = set(calls.read_text().split())
        assert {"cat", "cp", "mktemp", "tee", "chown", "chmod", "mv"} <= used


def test_example_conf_documents_that_systemd_does_not_expand():
    assert "does NOT expand" in EXAMPLE_CONF.read_text()
