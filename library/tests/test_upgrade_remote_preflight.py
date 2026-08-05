"""Verify ``upgrade.sh --remote`` satisfies the preflight gate on its own.

Regression guard for Audiobook-Manager-90x: ``do_remote_upgrade`` rsync'd the
project to a fresh remote temp dir and invoked the remote ``upgrade.sh`` with
``--yes`` only. The remote side then hit ``validate_preflight``, found no
preflight report recorded for that source, printed
"Preflight check required before upgrade." and exited 1 — so every ``--remote``
deploy failed unless ``--force`` was passed, which defeats the drift-detection
gate entirely.

The fix makes ``do_remote_upgrade`` run ``--check`` first, in the SAME remote
temp dir, so the recorded source (``project:<remote_tmp>``) matches the real
run's expected source. Two layers of proof here:

* ``TestPreflightGateBehaviour`` drives the REAL ``generate_preflight`` /
  ``validate_preflight`` functions out of upgrade.sh and shows that a same-dir
  ``--check`` (what the remote flow now performs) is exactly what turns the
  "Preflight check required" abort into a pass.
* ``TestRemoteOrchestration`` runs the REAL ``do_remote_upgrade`` end-to-end via
  ``upgrade.sh --remote`` with mocked ``ssh``/``rsync``/``curl``. The fake ssh
  models the remote gate (``--check`` writes the report, ``--yes`` requires it
  unless ``--force``), so a regression that dropped the ``--check`` call would
  make the mocked ``--yes`` abort and fail the test.
"""

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPGRADE_SH = PROJECT_ROOT / "upgrade.sh"

pytestmark = pytest.mark.skipif(
    not UPGRADE_SH.is_file(), reason="upgrade.sh not present (deployed installation)"
)

TIMEOUT = 60
PREFLIGHT_ABORT = "Preflight check required before upgrade."


def _slice(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _gate_functions() -> str:
    """Extract the real generate_preflight + validate_preflight from upgrade.sh."""
    text = UPGRADE_SH.read_text()
    return _slice(text, "generate_preflight() {", "# Deployed-tree dev-artifact purge")


class TestPreflightGateBehaviour:
    """Exercise the real gate functions the remote flow depends on."""

    HARNESS_PROLOGUE = r"""
set -uo pipefail
RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
UPGRADE_SOURCE="project"
REQUESTED_VERSION=""
MAJOR_VERSION="false"
FORCE="false"
# get_version is defined elsewhere in upgrade.sh; stub it — the gate cares about
# the SOURCE identifier, not the version delta.
get_version() { echo "8.4.2.0"; }
"""

    def _run(self, tmp_path, body: str) -> subprocess.CompletedProcess:
        var_dir = tmp_path / "var"
        project = tmp_path / "project"
        target = tmp_path / "target"
        for d in (var_dir, project, target):
            d.mkdir(parents=True, exist_ok=True)
        script = (
            self.HARNESS_PROLOGUE
            + f'AUDIOBOOKS_VAR_DIR="{var_dir}"\n'
            + f'TARGET_DIR="{target}"\n'
            + f'PROJECT_DIR="{project}"\n'
            + _gate_functions()
            + body.format(project=project, target=target)
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )

    def test_missing_preflight_reproduces_abort(self, tmp_path):
        """No prior --check ⇒ the exact failure every --remote run used to hit."""
        result = self._run(tmp_path, 'validate_preflight "{project}"\n')
        assert result.returncode == 1, result.stdout + result.stderr
        assert PREFLIGHT_ABORT in result.stdout

    def test_same_dir_check_then_validate_passes(self, tmp_path):
        """generate_preflight (what --check does) in the same dir satisfies the gate."""
        result = self._run(
            tmp_path,
            'generate_preflight "{project}" "{target}"\n'
            'validate_preflight "{project}"\n',
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert PREFLIGHT_ABORT not in result.stdout
        assert "Preflight validated" in result.stdout


class TestRemoteOrchestration:
    """Drive the real do_remote_upgrade with a fake remote that models the gate."""

    @pytest.fixture
    def fakebin(self, tmp_path):
        bind = tmp_path / "bin"
        bind.mkdir()
        marker = tmp_path / "preflight.marker"
        log = tmp_path / "ssh.log"

        ssh = bind / "ssh"
        ssh.write_text(
            "#!/bin/bash\n"
            'cmd="${@: -1}"\n'
            f'echo "$cmd" >> "{log}"\n'
            'case "$cmd" in\n'
            "  *upgrade.sh*--check*)\n"
            f'    touch "{marker}"; exit 0 ;;\n'
            "  *upgrade.sh*--yes*)\n"
            f'    if [[ "$cmd" == *--force* || -f "{marker}" ]]; then exit 0; fi\n'
            f'    echo "{PREFLIGHT_ABORT}"; exit 1 ;;\n'
            '  "sudo -n true") exit 0 ;;\n'
            '  *) bash -c "$cmd"; exit 0 ;;\n'
            "esac\n"
        )
        # rsync-over-ssh transport can't drive a stub; the fake remote's gate is
        # modelled by the marker file, not by synced content, so a no-op is fine.
        (bind / "rsync").write_text("#!/bin/bash\nexit 0\n")
        # Health check: return a version immediately so the wait loop exits fast.
        (bind / "curl").write_text('#!/bin/bash\necho \'{"version":"8.4.2.0"}\'\nexit 0\n')
        for f in (ssh, bind / "rsync", bind / "curl"):
            f.chmod(0o755)
        return {"bin": bind, "marker": marker, "log": log}

    def _run_remote(self, tmp_path, fakebin, extra_flags):
        project = tmp_path / "project"
        project.mkdir(exist_ok=True)
        (project / "install.sh").write_text("#!/bin/bash\n")
        import os

        env = dict(os.environ)
        env["PATH"] = f"{fakebin['bin']}:{env['PATH']}"
        # Force the Cloudflare purge to no-op (no creds file).
        env["CF_KEYS_FILE"] = str(tmp_path / "no-such-keys.env")
        cmd = [
            "bash",
            str(UPGRADE_SH),
            "--from-project",
            str(project),
            "--remote",
            "127.0.0.1",
            "--user",
            "tester",
            "--target",
            "/tmp/fake-target",
            "--yes",
            *extra_flags,
        ]
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT, check=False, env=env
        )

    def test_remote_deploy_does_not_abort_on_preflight(self, tmp_path, fakebin):
        result = self._run_remote(tmp_path, fakebin, [])
        assert PREFLIGHT_ABORT not in result.stdout, result.stdout
        assert result.returncode == 0, result.stdout + result.stderr

        log = fakebin["log"].read_text().splitlines()
        check_idx = next((i for i, line in enumerate(log) if "--check" in line), None)
        yes_idx = next((i for i, line in enumerate(log) if "--yes" in line), None)
        assert check_idx is not None, f"remote flow never ran --check:\n{log}"
        assert yes_idx is not None, f"remote flow never ran the real upgrade:\n{log}"
        assert check_idx < yes_idx, "the --check must precede the --yes run"
        # Both must target the SAME remote temp dir for the source to match.
        remote_tmp = log[check_idx].split("--from-project")[1].split("'")[1]
        assert remote_tmp in log[yes_idx], "check/upgrade used different project dirs"

    def test_force_still_bypasses_without_check(self, tmp_path, fakebin):
        """--force keeps working and skips the --check (gate intentionally bypassed)."""
        result = self._run_remote(tmp_path, fakebin, ["--force"])
        assert result.returncode == 0, result.stdout + result.stderr
        log = fakebin["log"].read_text()
        assert "--check" not in log, "under --force the preflight check must be skipped"
