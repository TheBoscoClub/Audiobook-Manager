"""Behavioural tests for scripts/translation-drain-reaper.sh (Audiobook-Manager-4j4).

The reaper is the independent backstop that destroys abm-drain nodes a dying
drain left billing. It runs the real script against a stubbed Vast.ai API: a
``curl`` on PATH that serves ``GET /instances/`` from a JSON state file and
applies ``DELETE /instances/<id>/`` to it, logging every request. Nothing here
reaches the network.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

REPO = Path(__file__).resolve().parents[2]
REAPER = REPO / "scripts" / "translation-drain-reaper.sh"

CURL_STUB = f"""#!{sys.executable}
import json, os, sys
state = os.environ["STUB_STATE"]
log = os.environ["STUB_LOG"]
args = sys.argv[1:]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
url = args[-1]
with open(log, "a") as fh:
    fh.write(f"{{method}} {{url}}\\n")
if os.environ.get("STUB_API_DOWN"):
    sys.exit(22)
data = json.load(open(state))
if method == "GET" and url.endswith("/instances/"):
    print(json.dumps({{"instances": data}}))
elif method == "DELETE":
    iid = int(url.rstrip("/").rsplit("/", 1)[1])
    if not os.environ.get("STUB_DELETE_IGNORED"):
        data = [i for i in data if i["id"] != iid]
        json.dump(data, open(state, "w"))
    print("{{}}")
else:
    sys.exit(22)
"""


SYSTEMCTL_STUB = r"""#!/bin/bash
# is-enabled audiobook-translation-drain.timer -> STUB_DRAIN_ENABLED
[[ "$1" == is-enabled && -n "${STUB_DRAIN_ENABLED:-}" ]] && exit 0
exit 1
"""

L = "abm-drain-testhost"  # this host's drain label


class Reap:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_text(CURL_STUB)
        curl.chmod(0o755)
        sysctl = self.bin / "systemctl"
        sysctl.write_text(SYSTEMCTL_STUB)
        sysctl.chmod(0o755)
        self.heartbeat = tmp / "var" / "reaper-heartbeat"
        self.state = tmp / "instances.json"
        self.log = tmp / "api.log"
        self.lock = tmp / "run" / "translation-drain.lock"
        self.ledger = tmp / "var" / "spend-ledger.tsv"
        self.keyfile = tmp / "key"
        self.keyfile.write_text("not-a-real-key\n")
        self.config = tmp / "config.sh"
        self.config.write_text(
            f'AUDIOBOOKS_RUN_DIR="{tmp / "run"}"\nAUDIOBOOKS_VAR_DIR="{tmp / "var"}"\n'
            f'AUDIOBOOKS_VENV="{tmp / "venv"}"\n'
        )
        self.instances([])

    def instances(self, rows: list[dict]) -> None:
        self.state.write_text(json.dumps(rows))

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "TMPDIR": str(self.tmp),
            "STUB_STATE": str(self.state),
            "STUB_LOG": str(self.log),
            "DRAIN_CONFIG_LIB": str(self.config),
            "DRAIN_PYTHON": sys.executable,
            "DRAIN_VERIFY_DELAY": "0",
            "VAST_API_BASE": "http://127.0.0.1:9/api/v0",  # unroutable belt; curl is stubbed
            "VAST_API_KEY_FILE": str(self.keyfile),
            "AUDIOBOOKS_DRAIN_LOCK": str(self.lock),
            "AUDIOBOOKS_DRAIN_LEDGER": str(self.ledger),
            "AUDIOBOOKS_DRAIN_LABEL": L,
            "AUDIOBOOKS_DRAIN_REAPER_HEARTBEAT": str(self.heartbeat),
        }
        env.update(extra)
        return env

    def run(self, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(REAPER)], env=self.env(**extra), capture_output=True, text=True, timeout=60
        )

    def deletes(self) -> list[str]:
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.startswith("DELETE")]

    def remaining_ids(self) -> list[int]:
        return [i["id"] for i in json.loads(self.state.read_text())]

    def hold_lock(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock, "a")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh

    def seed_ledger(self, amount: float) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        month = time.strftime("%Y-%m", time.localtime(now))
        self.ledger.write_text(f"{int(now)}\t{month}\tearlier\tfinal\t{amount:.4f}\t\n")


def _inst(iid: int, label: str, age_s: float = 600, dph: float = 1.5) -> dict:
    return {
        "id": iid,
        "label": label,
        "start_date": time.time() - age_s,
        "dph_total": dph,
        "actual_status": "running",
    }


@pytest.fixture
def reap(tmp_path: Path) -> Reap:
    return Reap(tmp_path)


def _out(p) -> str:
    return f"\n--- stdout\n{p.stdout}\n--- stderr\n{p.stderr}"


def test_nothing_labelled_nothing_done(reap: Reap):
    reap.instances([_inst(5, "abm-translate")])
    p = reap.run()
    assert p.returncode == 0, _out(p)
    assert reap.deletes() == []


def test_no_live_drain_reaps_orphan_and_never_other_labels(reap: Reap):
    reap.instances(
        [
            _inst(1, L),
            _inst(2, "abm-translate"),
            _inst(3, L + "-x"),
            _inst(6, "abm-drain"),
            {"id": 4, "label": None, "start_date": None, "dph_total": 9},
        ]
    )
    p = reap.run()
    assert p.returncode == 2, _out(p)
    assert reap.deletes() == ["DELETE http://127.0.0.1:9/api/v0/instances/1/"]
    assert sorted(reap.remaining_ids()) == [2, 3, 4, 6]
    assert "no live drain run" in p.stdout
    lines = reap.ledger.read_text().splitlines()
    assert lines and lines[-1].split("\t")[3] == "final", "orphan spend must be recorded"


def test_live_young_cheap_node_is_left_alone(reap: Reap):
    reap.instances([_inst(1, L, age_s=600, dph=1.5)])
    with reap.hold_lock():
        p = reap.run()
    assert p.returncode == 0, _out(p)
    assert reap.deletes() == []


def test_live_but_too_old_is_reaped(reap: Reap):
    # Lifetime at defaults: 240*60 + 1800 + 1500 + 300 + 30*60 = 19800 s.
    reap.instances([_inst(1, L, age_s=20000, dph=0.01)])
    with reap.hold_lock():
        p = reap.run()
    assert p.returncode == 2, _out(p)
    assert "exceeds the drain's maximum lifetime" in p.stdout
    assert reap.remaining_ids() == []


def test_live_but_over_per_run_cap_is_reaped(reap: Reap):
    reap.instances([_inst(1, L, age_s=3600, dph=12.0)])
    with reap.hold_lock():
        p = reap.run()
    assert p.returncode == 2, _out(p)
    assert "reached the per-run cap" in p.stdout


def test_live_node_reaped_at_monthly_ceiling(reap: Reap):
    # $96 already spent this month + $4.50 accrued on the live node >= $100.
    reap.seed_ledger(96.0)
    reap.instances([_inst(1, L, age_s=3 * 3600, dph=1.5)])
    with reap.hold_lock():
        p = reap.run()
    assert p.returncode == 2, _out(p)
    assert "monthly ceiling" in p.stdout


def test_live_node_under_ceiling_not_reaped(reap: Reap):
    reap.seed_ledger(90.0)
    reap.instances([_inst(1, L, age_s=3 * 3600, dph=1.5)])
    with reap.hold_lock():
        p = reap.run()
    assert p.returncode == 0, _out(p)


def test_unverified_delete_fails_loudly(reap: Reap):
    reap.instances([_inst(1, L)])
    p = reap.run(STUB_DELETE_IGNORED="1")
    assert p.returncode == 1, _out(p)
    assert "REAP UNVERIFIED" in p.stdout
    assert len(reap.deletes()) > 1, "delete must be retried"


def test_api_down_is_an_error_not_silence(reap: Reap):
    p = reap.run(STUB_API_DOWN="1")
    assert p.returncode == 1, _out(p)
    assert "could not list" in p.stdout


def test_no_credential_configured_exits_quietly(reap: Reap):
    env = reap.env()
    env.pop("VAST_API_KEY_FILE")
    p = subprocess.run(["bash", str(REAPER)], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, _out(p)
    assert not reap.log.exists(), "no API call without a credential"


@pytest.mark.parametrize("label", ["abm-translate", "abm-drain", "abm-drain-Bad", "x"])
def test_refuses_labels_without_host_identity(reap: Reap, label):
    reap.instances([_inst(2, label), _inst(3, "abm-drain")])
    p = reap.run(AUDIOBOOKS_DRAIN_LABEL=label)
    assert p.returncode == 1, _out(p)
    assert reap.deletes() == []


def test_label_x_never_touches_label_y(reap: Reap):
    """HIGH-3: two hosts, one Vast account. Host A's reaper sees host B's
    orphan-looking node and must leave it alone."""
    reap.instances(
        [_inst(1, "abm-drain-hosta"), _inst(2, "abm-drain-hostb"), _inst(3, "abm-drain-hosta-2")]
    )
    p = reap.run(AUDIOBOOKS_DRAIN_LABEL="abm-drain-hosta")
    assert p.returncode == 2, _out(p)
    assert reap.deletes() == ["DELETE http://127.0.0.1:9/api/v0/instances/1/"]
    assert sorted(reap.remaining_ids()) == [2, 3]


# ── HIGH-2: a damaged ledger never blocks reaping ────────────────────────────


def _corrupt_ledger(reap: Reap) -> None:
    reap.ledger.parent.mkdir(parents=True, exist_ok=True)
    reap.ledger.write_text("torn line with no tabs\n")


def test_corrupt_ledger_still_reaps_orphan_then_fails(reap: Reap):
    _corrupt_ledger(reap)
    reap.instances([_inst(1, L)])
    p = reap.run()
    assert reap.deletes() == ["DELETE http://127.0.0.1:9/api/v0/instances/1/"], _out(p)
    assert reap.remaining_ids() == []
    assert p.returncode == 1, "the ledger problem must still be reported"


def test_corrupt_ledger_still_reaps_over_age_live_node(reap: Reap):
    _corrupt_ledger(reap)
    reap.instances([_inst(1, L, age_s=20000, dph=0.01)])
    with reap.hold_lock():
        p = reap.run()
    assert reap.remaining_ids() == [], _out(p)


def test_corrupt_ledger_with_young_live_node_fails_loudly(reap: Reap):
    _corrupt_ledger(reap)
    reap.instances([_inst(1, L, age_s=600, dph=1.5)])
    with reap.hold_lock():
        p = reap.run()
    assert p.returncode == 1, _out(p)
    assert "could not read the spend ledger" in p.stdout
    assert reap.deletes() == []


# ── MED-5: a silently-off reaper is not silent ───────────────────────────────


def test_no_key_on_a_drain_host_is_an_error(reap: Reap):
    env = reap.env(STUB_DRAIN_ENABLED="1")
    env.pop("VAST_API_KEY_FILE")
    p = subprocess.run(["bash", str(REAPER)], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1, _out(p)
    assert "NOT protecting it" in p.stdout


def test_no_key_with_a_ledger_is_an_error(reap: Reap):
    reap.seed_ledger(1.0)
    env = reap.env()
    env.pop("VAST_API_KEY_FILE")
    p = subprocess.run(["bash", str(REAPER)], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1, _out(p)


def test_blanked_pointer_uses_runtime_directory(reap: Reap):
    rundir = reap.tmp / "runtime"
    rundir.mkdir()
    (rundir / "vast-api-key").write_text("not-a-real-key\n")
    reap.instances([_inst(1, L)])
    p = reap.run(VAST_API_KEY_FILE="", RUNTIME_DIRECTORY=str(rundir))
    assert p.returncode == 2, _out(p)


def test_heartbeat_written_only_after_a_keyed_successful_run(reap: Reap):
    p = reap.run()
    assert p.returncode == 0, _out(p)
    beat_at, beat_label = reap.heartbeat.read_text().split()
    assert beat_label == L and abs(int(beat_at) - time.time()) < 60
    reap.heartbeat.unlink()
    p = reap.run(STUB_API_DOWN="1")
    assert p.returncode == 1
    assert not reap.heartbeat.exists(), "a failed run must not vouch for the reaper"


def test_reaper_timer_enabled_and_independent():
    timer = (REPO / "systemd" / "audiobook-translation-reaper.timer").read_text()
    directives = [ln.strip() for ln in timer.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(d.startswith("PartOf=") for d in directives), (
        "an app stop must not stop the reaper"
    )
    assert "OnUnitActiveSec=10min" in timer
    upgrade = (REPO / "upgrade.sh").read_text()
    start = upgrade.index("local standalone_units=(")
    assert "audiobook-translation-reaper.timer" in upgrade[start : upgrade.index("\n    )", start)]
    install = (REPO / "install.sh").read_text()
    loop = next(
        ln for ln in install.splitlines() if ln.strip().startswith("for svc in audiobook-api")
    )
    assert "audiobook-translation-reaper.timer" in loop


def test_gpu_node_down_destroys_every_drain_instance_only(tmp_path: Path):
    """G: `down` destroys every abm-drain instance (not just the newest) and
    nothing under another label."""
    r = Reap(tmp_path)
    r.instances([_inst(10, L), _inst(11, L), _inst(12, "abm-translate")])
    p = subprocess.run(
        ["bash", str(REPO / "scripts" / "gpu-node.sh"), "down", "--yes"],
        env={**r.env(), "GPU_NODE_LABEL": L},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, _out(p)
    assert sorted(r.deletes()) == [
        "DELETE http://127.0.0.1:9/api/v0/instances/10/",
        "DELETE http://127.0.0.1:9/api/v0/instances/11/",
    ]
    assert r.remaining_ids() == [12]
