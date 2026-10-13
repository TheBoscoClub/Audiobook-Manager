"""Tests for scripts/install-manifest.sh and scripts/reconcile-filesystem.sh.

These exercise the bash reconciler against scratch fixture trees in tmp_path,
so they run fast and don't need sudo, a VM, or any audiobooks install. They
cover the bits that actually mattered in the v8.1.0.1 drift incident:

1. The manifest loads as valid bash and its arrays expand correctly.
2. CONFIG_CANONICAL_DEFAULTS stays aligned with library/config.py defaults
   (the drift that caused cover 404s on dev/QA).
3. The reconciler reports phantoms and drift without mutating anything in
   report mode.
4. Enforce mode deletes phantoms, strips legacy config keys, and preserves
   non-legacy user customizations.
5. The pre-commit hook's hardcoded-path blocker would catch us if we
   regressed by putting a literal /var/lib/audiobooks into either script.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "scripts" / "install-manifest.sh"
RECONCILER = PROJECT_ROOT / "scripts" / "reconcile-filesystem.sh"
CONFIG_PY = PROJECT_ROOT / "library" / "config.py"


def _run_reconciler(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(RECONCILER)], capture_output=True, text=True, env=env, check=False
    )


def _make_fixture(tmp_path: Path) -> dict[str, Path]:
    """Build a minimal scratch tree matching what the reconciler expects."""
    lib = tmp_path / "lib"
    state = tmp_path / "state"
    log = tmp_path / "log"
    config = tmp_path / "config"
    systemd = tmp_path / "systemd"
    bin_dir = tmp_path / "bin"
    for d in (lib, state, log, config, systemd, bin_dir):
        d.mkdir(parents=True)
    return {
        "LIB_DIR": lib,
        "STATE_DIR": state,
        "LOG_DIR": log,
        "CONFIG_DIR": config,
        "SYSTEMD_DIR": systemd,
        "BIN_DIR": bin_dir,
        "CONF_FILE": config / "audiobooks.conf",
    }


def _env_from_fixture(fx: dict[str, Path], mode: str = "report") -> dict[str, str]:
    return {
        "PROJECT_DIR": str(PROJECT_ROOT),
        "LIB_DIR": str(fx["LIB_DIR"]),
        "STATE_DIR": str(fx["STATE_DIR"]),
        "LOG_DIR": str(fx["LOG_DIR"]),
        "CONFIG_DIR": str(fx["CONFIG_DIR"]),
        "CONF_FILE": str(fx["CONF_FILE"]),
        "SYSTEMD_DIR": str(fx["SYSTEMD_DIR"]),
        "BIN_DIR": str(fx["BIN_DIR"]),
        "USE_SUDO": "",
        "RECONCILE_MODE": mode,
    }


# ---------------------------------------------------------------------------
# Manifest structural checks
# ---------------------------------------------------------------------------


def test_manifest_and_reconciler_exist_and_are_executable():
    assert MANIFEST.is_file(), f"missing: {MANIFEST}"
    assert RECONCILER.is_file(), f"missing: {RECONCILER}"
    assert os.access(MANIFEST, os.X_OK)
    assert os.access(RECONCILER, os.X_OK)


def test_manifest_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(MANIFEST)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_reconciler_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(RECONCILER)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_manifest_arrays_populated(tmp_path):
    """Sourcing the manifest must produce non-empty required arrays."""
    fx = _make_fixture(tmp_path)
    script = f"""
        export LIB_DIR={fx["LIB_DIR"]}
        export STATE_DIR={fx["STATE_DIR"]}
        export LOG_DIR={fx["LOG_DIR"]}
        export CONFIG_DIR={fx["CONFIG_DIR"]}
        source {MANIFEST}
        echo REQUIRED_VENVS=${{#REQUIRED_VENVS[@]}}
        echo PHANTOM_PATHS=${{#PHANTOM_PATHS[@]}}
        echo REQUIRED_DIRS=${{#REQUIRED_DIRS[@]}}
        echo CANONICAL_UNITS=${{#CANONICAL_UNITS[@]}}
        echo CANONICAL_WRAPPERS=${{#CANONICAL_WRAPPERS[@]}}
        echo CONFIG_CANONICAL_DEFAULTS=${{#CONFIG_CANONICAL_DEFAULTS[@]}}
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    counts = dict(line.split("=") for line in result.stdout.strip().splitlines())
    for key in (
        "REQUIRED_VENVS",
        "PHANTOM_PATHS",
        "REQUIRED_DIRS",
        "CANONICAL_UNITS",
        "CANONICAL_WRAPPERS",
        "CONFIG_CANONICAL_DEFAULTS",
    ):
        assert int(counts[key]) > 0, f"{key} is empty"


def test_canonical_units_match_systemd_directory():
    """Every unit in systemd/ (minus non-unit files) must be in CANONICAL_UNITS."""
    systemd_dir = PROJECT_ROOT / "systemd"
    on_disk = {
        f.name
        for f in systemd_dir.iterdir()
        if f.suffix in {".service", ".timer", ".target", ".path"}
    }
    script = f"""
        export LIB_DIR=/tmp STATE_DIR=/tmp LOG_DIR=/tmp CONFIG_DIR=/tmp
        source {MANIFEST}
        printf '%s\\n' "${{CANONICAL_UNITS[@]}}"
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    in_manifest = set(result.stdout.strip().splitlines())
    missing = on_disk - in_manifest
    assert not missing, f"units in systemd/ but not in manifest: {missing}"


def test_config_canonical_defaults_are_covered_in_config_py():
    """Every key in CONFIG_CANONICAL_DEFAULTS must have a corresponding get_config
    fallback in library/config.py. This is what keeps install.sh template drift
    from resurfacing: if someone adds a new key to the manifest without wiring
    it into config.py, the test fails."""
    script = f"""
        export LIB_DIR=/tmp STATE_DIR=/tmp LOG_DIR=/tmp CONFIG_DIR=/tmp
        source {MANIFEST}
        for entry in "${{CONFIG_CANONICAL_DEFAULTS[@]}}"; do
            echo "${{entry%%|*}}"
        done | sort -u
    """
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    manifest_keys = set(result.stdout.strip().splitlines())
    config_py_text = CONFIG_PY.read_text()

    for key in manifest_keys:
        pattern = rf'get_config\(\s*["\']{re.escape(key)}["\']'
        assert re.search(pattern, config_py_text), (
            f"{key} is in CONFIG_CANONICAL_DEFAULTS but has no "
            f"get_config() fallback in library/config.py"
        )


# ---------------------------------------------------------------------------
# Reconciler behavior — report mode
# ---------------------------------------------------------------------------


def test_report_mode_does_not_mutate(tmp_path):
    fx = _make_fixture(tmp_path)
    phantom = fx["LIB_DIR"] / "venv"
    phantom.mkdir()
    (phantom / "marker").write_text("do not delete me")
    fx["CONF_FILE"].write_text("AUDIOBOOKS_COVERS=/opt/audiobooks/library/web-v2/covers\n")

    result = _run_reconciler(_env_from_fixture(fx, mode="report"))

    assert "phantom path present" in result.stdout
    assert "legacy AUDIOBOOKS_COVERS" in result.stdout
    assert phantom.is_dir(), "report mode must not delete phantoms"
    assert (phantom / "marker").exists()
    assert "AUDIOBOOKS_COVERS=" in fx["CONF_FILE"].read_text()


def test_report_counts_drift_as_non_zero(tmp_path):
    fx = _make_fixture(tmp_path)
    (fx["LIB_DIR"] / "venv").mkdir()
    result = _run_reconciler(_env_from_fixture(fx, mode="report"))
    assert "Drift:  " in result.stdout
    match = re.search(r"Drift:\s+(\d+)", result.stdout)
    assert match and int(match.group(1)) > 0


# ---------------------------------------------------------------------------
# Optional credential file mode audit (Audiobook-Manager-nsz)
# ---------------------------------------------------------------------------


def _credential_env(
    tmp_path: Path, file_mode: int, want_mode: str, mode: str = "report"
) -> tuple[dict[str, str], Path]:
    """Fixture whose manifest declares one optional credential file.

    The real manifest is copied and its OPTIONAL_CREDENTIAL_FILES array is
    overridden, so the rest of the manifest still loads. The owner is the
    current user, so only the mode can drift.
    """
    fx = _make_fixture(tmp_path)
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    cred = fx["CONFIG_DIR"] / "fake-credential"
    cred.write_text("not-a-secret\n")
    cred.chmod(file_mode)
    owner = _current_owner()
    override = f'\nOPTIONAL_CREDENTIAL_FILES=("{cred}|{owner}|{want_mode}|FAKE_VAR")\n'
    (project / "scripts" / "install-manifest.sh").write_text(MANIFEST.read_text() + override)
    env = _env_from_fixture(fx, mode=mode)
    env["PROJECT_DIR"] = str(project)
    return env, cred


def _current_owner() -> str:
    import grp
    import pwd

    return f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}"


@pytest.mark.parametrize("want", ["0600", "600"])
def test_credential_mode_matching_manifest_is_not_drift(tmp_path, want):
    """0600 on disk (stat prints 600) vs manifest "0600" must not be drift."""
    env, cred = _credential_env(tmp_path, 0o600, want)
    result = _run_reconciler(env)
    assert "mode drift" not in result.stdout, result.stdout
    assert stat_mode(cred) == 0o600


def test_credential_mode_mismatch_is_reported_not_repaired_in_report_mode(tmp_path):
    env, cred = _credential_env(tmp_path, 0o644, "0600")
    result = _run_reconciler(env)
    assert "credential file mode drift" in result.stdout, result.stdout
    assert stat_mode(cred) == 0o644, "report mode must not chmod"


def test_credential_mode_mismatch_is_repaired_in_enforce_mode(tmp_path):
    env, cred = _credential_env(tmp_path, 0o644, "0600", mode="enforce")
    result = _run_reconciler(env)
    assert "credential file mode drift" in result.stdout, result.stdout
    assert stat_mode(cred) == 0o600
    # Second pass converges: the repaired file no longer reports drift.
    again = _run_reconciler(env)
    assert "mode drift" not in again.stdout, again.stdout


def test_credential_setgid_four_digit_mode_compares_numerically(tmp_path):
    """setgid (2xxx) survives the compare: 2640 on disk is not drift vs "2640"."""
    env, cred = _credential_env(tmp_path, 0o2640, "2640")
    cred.chmod(0o2640)  # chmod via pathlib keeps setgid when owner's group matches
    if stat_mode(cred) != 0o2640:
        pytest.skip("filesystem did not retain the setgid bit")
    result = _run_reconciler(env)
    assert "mode drift" not in result.stdout, result.stdout
    # Dropping setgid from the file is real drift, even though 640 == 0640 digits.
    cred.chmod(0o640)
    result = _run_reconciler(env)
    assert "credential file mode drift" in result.stdout, result.stdout


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o7777


# ---------------------------------------------------------------------------
# Reconciler behavior — enforce mode
# ---------------------------------------------------------------------------


def test_enforce_mode_deletes_phantoms(tmp_path):
    fx = _make_fixture(tmp_path)
    phantom_venv = fx["LIB_DIR"] / "venv"
    phantom_covers = fx["LIB_DIR"] / "library" / "web-v2" / "covers"
    phantom_venv.mkdir()
    phantom_covers.mkdir(parents=True)

    _run_reconciler(_env_from_fixture(fx, mode="enforce"))

    assert not phantom_venv.exists()
    assert not phantom_covers.exists()


def test_enforce_mode_strips_only_legacy_config(tmp_path):
    fx = _make_fixture(tmp_path)
    fx["CONF_FILE"].write_text(
        "AUDIOBOOKS_COVERS=/opt/audiobooks/library/web-v2/covers\n"
        "AUDIOBOOKS_DATA=/custom/path\n"
        "AUTH_ENABLED=true\n"
    )

    _run_reconciler(_env_from_fixture(fx, mode="enforce"))

    text = fx["CONF_FILE"].read_text()
    assert "AUDIOBOOKS_COVERS=" not in text, "legacy key must be stripped"
    assert "AUDIOBOOKS_DATA=/custom/path" in text, "user customization preserved"
    assert "AUTH_ENABLED=true" in text, "unrelated keys preserved"


def test_enforce_mode_preserves_user_covers_override(tmp_path):
    """If a user explicitly set AUDIOBOOKS_COVERS to a non-legacy path, the
    reconciler must leave it alone."""
    fx = _make_fixture(tmp_path)
    fx["CONF_FILE"].write_text("AUDIOBOOKS_COVERS=/mnt/mybigdisk/covers\n")
    _run_reconciler(_env_from_fixture(fx, mode="enforce"))
    assert "AUDIOBOOKS_COVERS=/mnt/mybigdisk/covers" in fx["CONF_FILE"].read_text()


def test_enforce_mode_creates_missing_dirs(tmp_path):
    fx = _make_fixture(tmp_path)
    expected = [
        fx["LIB_DIR"] / "library" / "data",
        fx["STATE_DIR"] / "db",
        fx["STATE_DIR"] / "covers",
        fx["STATE_DIR"] / ".run",
    ]
    for d in expected:
        assert not d.exists()

    _run_reconciler(_env_from_fixture(fx, mode="enforce"))

    for d in expected:
        assert d.is_dir(), f"enforce should create {d}"


def test_enforce_mode_is_idempotent(tmp_path):
    fx = _make_fixture(tmp_path)
    (fx["LIB_DIR"] / "venv").mkdir()
    fx["CONF_FILE"].write_text("AUDIOBOOKS_COVERS=/opt/audiobooks/library/covers\n")

    first = _run_reconciler(_env_from_fixture(fx, mode="enforce"))
    second = _run_reconciler(_env_from_fixture(fx, mode="enforce"))

    # First run should fix something; second should find nothing to fix.
    match1 = re.search(r"Fixed:\s+(\d+)", first.stdout)
    match2 = re.search(r"Fixed:\s+(\d+)", second.stdout)
    assert match1 and match2
    assert int(match1.group(1)) > 0
    assert int(match2.group(1)) == 0, "second run must be a no-op"


# ---------------------------------------------------------------------------
# Regression guard: no hardcoded paths in the new scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", [MANIFEST, RECONCILER])
def test_scripts_have_no_hardcoded_var_lib(script):
    """Both scripts must route state paths through STATE_DIR, not literals."""
    text = script.read_text()
    lines = [
        line
        for line in text.splitlines()
        if "/var/lib/audiobooks" in line and not line.strip().startswith("#")
    ]
    assert not lines, (
        f"{script.name} contains literal /var/lib/audiobooks outside comments:\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Regression guard: install.sh and upgrade.sh default RECONCILE_MODE=enforce
# so documented-legacy items (PHANTOM_PATHS, legacy config keys, __pycache__)
# are cleaned automatically. Previously both defaulted to `report`, which meant
# drift accumulated release after release — two empty phantom cover dirs
# survived on QA from the v8.1.0.1 drift incident until 8.3.2 because the
# reconciler reported them on every upgrade but never touched them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_name", ["install.sh", "upgrade.sh"])
def test_invoker_defaults_to_enforce_mode(script_name):
    """Every RECONCILE_MODE invocation in install.sh / upgrade.sh must default
    to `enforce`. If someone regresses this back to `report`, drift starts
    accumulating silently again.
    """
    text = (PROJECT_ROOT / script_name).read_text()
    # Every occurrence of RECONCILE_MODE="${RECONCILE_MODE:-...}" must resolve
    # to enforce. We search for the pattern and assert no `:-report}` form.
    default_spec = re.findall(r'RECONCILE_MODE="\$\{RECONCILE_MODE:-(\w+)\}"', text)
    assert default_spec, f"{script_name} sets RECONCILE_MODE but no default was found"
    for mode in default_spec:
        assert mode == "enforce", (
            f"{script_name} invokes the reconciler with default "
            f"RECONCILE_MODE={mode}; must be 'enforce' so phantom paths and "
            f"legacy config drift are cleaned on every install/upgrade"
        )
