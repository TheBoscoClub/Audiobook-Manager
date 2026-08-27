"""Verify smoke_probe.sh treats credential-gated optional services as non-fatal.

Regression guard for Audiobook-Manager-bqe: ``audiobook-downloader.service``
cannot be healthy on a credential-less test/QA VM (data-isolation policy,
testing.md — no Audible ``config.toml`` by design), so its timer leaves it in
``failed`` forever. The probe's ``failed)`` branch called ``_fail`` for every
unit, so every ``--remote`` deploy reported FAILED even though the application
was healthy and correctly versioned.

The fix downgrades a ``failed`` OPTIONAL credential-gated unit to a warning ONLY
when its credential file is absent — a genuine failure with the credential
present still aborts, and CORE units (api, proxy, redirect) always abort. These
tests source the real ``_probe_systemd`` and drive it with a mocked
``systemctl``/``sudo``, mapping the probe's failure counter to the same exit
decision ``run_smoke_probe`` makes.
"""

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SMOKE_PROBE = PROJECT_ROOT / "scripts" / "smoke_probe.sh"

pytestmark = pytest.mark.skipif(
    not SMOKE_PROBE.is_file(), reason="smoke_probe.sh not present (deployed installation)"
)

TIMEOUT = 30


def _write_fakes(bindir: Path, states: Path) -> None:
    systemctl = bindir / "systemctl"
    systemctl.write_text(
        "#!/bin/bash\n"
        'sub="$1"; shift\n'
        'case "$sub" in\n'
        "  is-active)\n"
        f'    st=$(awk -v u="$1" \'$1==u{{print $2}}\' "{states}")\n'
        '    [[ -z "$st" ]] && st="active"\n'
        '    echo "$st"\n'
        '    [[ "$st" == active ]] && exit 0 || exit 3 ;;\n'
        "  show) echo 0; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    # sudo passthrough (drop -n) so `sudo -n test -f <cred>` resolves honestly.
    (bindir / "sudo").write_text('#!/bin/bash\n[[ "$1" == "-n" ]] && shift\nexec "$@"\n')
    for f in (systemctl, bindir / "sudo"):
        f.chmod(0o755)


def _run_probe(tmp_path, states_text: str) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    states = tmp_path / "states"
    states.write_text(states_text)
    _write_fakes(bindir, states)
    var_dir = tmp_path / "var"  # no .audible/config.toml → credential absent
    var_dir.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["AUDIOBOOKS_VAR_DIR"] = str(var_dir)

    # Source the real probe, run only _probe_systemd, and translate its failure
    # counter into the exact exit decision run_smoke_probe uses (fail>0 ⇒ 1).
    script = (
        f'source "{SMOKE_PROBE}"\n'
        "_smoke_fail=0; _smoke_warn=0\n"
        "_probe_systemd\n"
        'echo "SMOKE_FAIL=$_smoke_fail SMOKE_WARN=$_smoke_warn"\n'
        "[[ $_smoke_fail -eq 0 ]] && exit 0 || exit 1\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
        env=env,
    )


def _counter(stdout: str, key: str) -> int:
    for tok in stdout.split():
        if tok.startswith(f"{key}="):
            return int(tok.split("=", 1)[1])
    raise AssertionError(f"{key} not found in probe output:\n{stdout}")


class TestOptionalCredentialGatedService:
    def test_downloader_failed_is_non_fatal_when_uncredentialed(self, tmp_path):
        """downloader=failed + everything else active ⇒ exit 0 with a warning."""
        result = _run_probe(
            tmp_path,
            "audiobook.target active\naudiobook-downloader.service failed\n",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _counter(result.stdout, "SMOKE_FAIL") == 0
        assert _counter(result.stdout, "SMOKE_WARN") >= 1
        assert "feature not configured" in result.stdout

    def test_core_api_failure_still_aborts(self, tmp_path):
        """api=failed is a CORE failure ⇒ non-zero exit regardless of creds."""
        result = _run_probe(
            tmp_path,
            "audiobook.target active\naudiobook-api.service failed\n",
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert _counter(result.stdout, "SMOKE_FAIL") >= 1

    def test_downloader_failure_aborts_when_credential_present(self, tmp_path):
        """If the Audible credential IS present, a failed downloader is real."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        states = tmp_path / "states"
        states.write_text("audiobook.target active\naudiobook-downloader.service failed\n")
        _write_fakes(bindir, states)
        var_dir = tmp_path / "var"
        (var_dir / ".audible").mkdir(parents=True)
        (var_dir / ".audible" / "config.toml").write_text("[account]\n")

        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env["AUDIOBOOKS_VAR_DIR"] = str(var_dir)
        script = (
            f'source "{SMOKE_PROBE}"\n'
            "_smoke_fail=0; _smoke_warn=0\n"
            "_probe_systemd\n"
            'echo "SMOKE_FAIL=$_smoke_fail SMOKE_WARN=$_smoke_warn"\n'
            "[[ $_smoke_fail -eq 0 ]] && exit 0 || exit 1\n"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            env=env,
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert _counter(result.stdout, "SMOKE_FAIL") >= 1
