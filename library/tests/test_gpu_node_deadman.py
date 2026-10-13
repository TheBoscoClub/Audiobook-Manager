"""On-node dead-man switch in scripts/gpu-node.sh (Audiobook-Manager-4j4 HIGH-1).

The generated onstart script is checked for the timer and its value, and the
embedded dead-man script itself is EXECUTED in a sandbox: /workspace rewritten
to a temp dir, ``curl`` stubbed on PATH, and ``sleep``/``kill`` replaced by
exported shell functions so nothing waits and nothing real is signalled.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

REPO = Path(__file__).resolve().parents[2]
GPU_NODE = REPO / "scripts" / "gpu-node.sh"


def onstart(tmp_path: Path, lifetime: str | None) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "VAST_API_KEY": "dummy"}
    if lifetime is not None:
        env["GPU_NODE_MAX_LIFETIME"] = lifetime
    proc = subprocess.run(
        ["bash", str(GPU_NODE), "onstart"], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_onstart_arms_dead_man_with_the_lifetime_before_pip(tmp_path: Path):
    script = onstart(tmp_path, "19800")
    assert "LIFETIME=19800\n" in script
    assert script.index("dead-man.sh") < script.index("pip install"), (
        "the switch must be armed before anything that can fail"
    )
    assert script.index("setsid nohup /workspace/dead-man.sh") < script.index("set -e")
    proc = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "dummy" not in script, "the account key must never reach the node"


@pytest.mark.parametrize("value", [None, "0", "abc"])
def test_onstart_without_a_lifetime_has_no_switch(tmp_path: Path, value):
    assert "dead-man" not in onstart(tmp_path, value)


def _dead_man(tmp_path: Path) -> Path:
    script = onstart(tmp_path, "5")
    body = re.search(r"<<'DEADMAN'\n(.*?)\nDEADMAN\n", script, re.S).group(1)
    ws = tmp_path / "ws"
    ws.mkdir()
    path = ws / "dead-man.sh"
    path.write_text(body.replace("/workspace", str(ws)))
    return path


CURL = """#!/bin/bash
echo "curl $*" >> "$LOG"
for a in "$@"; do
  case "$a" in
    DELETE) [[ -n "${FAIL_DELETE:-}" ]] && exit 22 ;;
    PUT) [[ -n "${FAIL_PUT:-}" ]] && exit 22 ;;
  esac
done
exit 0
"""


def _run(tmp_path: Path, boot_age: int | None = None, **env_extra: str) -> list[str]:
    dm = _dead_man(tmp_path)
    if boot_age is not None:
        (dm.parent / ".dead-man-boot").write_text(f"{int(time.time()) - boot_age}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(CURL)
    (bin_dir / "curl").chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LOG": str(log),
        "CONTAINER_ID": "4242",
        "CONTAINER_API_KEY": "instance-scoped",
        **env_extra,
    }
    wrapper = (
        'sleep() { echo "sleep $*" >> "$LOG"; }; kill() { echo "kill $*" >> "$LOG"; }; '
        f'export -f sleep kill; bash "{dm}"'
    )
    proc = subprocess.run(
        ["bash", "-c", wrapper], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return log.read_text().splitlines()


def test_dead_man_destroys_itself_via_instance_key(tmp_path: Path):
    calls = _run(tmp_path)
    assert calls[0] in ("sleep 5", "sleep 4"), "waits the lifetime first"
    delete = next(c for c in calls if "DELETE" in c)
    assert "/instances/4242/" in delete and "Bearer instance-scoped" in delete


def test_dead_man_falls_back_to_stop_then_kill(tmp_path: Path):
    calls = _run(tmp_path, FAIL_DELETE="1")
    assert any("PUT" in c and '{"state":"stopped"}' in c for c in calls), calls
    assert "kill -TERM 1" in calls


def test_dead_man_without_vast_vars_still_stops_the_container(tmp_path: Path):
    calls = _run(tmp_path, CONTAINER_ID="", CONTAINER_API_KEY="")
    assert not any(c.startswith("curl") for c in calls)
    assert "kill -TERM 1" in calls and "kill -KILL 1" in calls


def test_restart_does_not_rearm_a_full_lifetime(tmp_path: Path):
    """Vast re-runs onstart on a container restart; the boot epoch persisted
    on /workspace makes the timer resume, not restart."""
    calls = _run(tmp_path, boot_age=3)
    assert calls[0] in ("sleep 2", "sleep 1"), calls[0]


def test_restart_after_the_lifetime_acts_at_once(tmp_path: Path):
    calls = _run(tmp_path, boot_age=100)
    assert calls[0] == "sleep 0"
    assert any("DELETE" in c for c in calls)


def test_dead_man_log_names_the_step_that_worked(tmp_path: Path):
    _run(tmp_path)
    log = (tmp_path / "ws" / "dead-man.log").read_text()
    assert "RESULT step 1: destroy accepted" in log


def test_dead_man_log_when_every_step_fails(tmp_path: Path):
    _run(tmp_path, FAIL_DELETE="1", FAIL_PUT="1")
    log = (tmp_path / "ws" / "dead-man.log").read_text()
    assert "RESULT step" not in log.replace("RESULT: still running", "")
    assert "RESULT: still running after every step" in log
