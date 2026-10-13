"""Behavioural tests for scripts/translation-drain.sh (Audiobook-Manager-4j4).

The drain rents a billed Vast.ai GPU node, so its one non-negotiable property
is that EVERY path out of it destroys the node. These tests run the real
script against stubbed collaborators — gpu-node.sh, gpu-budget-guard.sh,
batch-translate.py and curl — that append what they were asked to do to a
shared call log. Nothing here touches the network, Vast.ai or a real node.

The database is a real SQLite file with a real ``translation_queue`` table, so
the pending count and the claim release go through the actual
``localization.drain_queue`` code the installed drain uses.
"""

from __future__ import annotations

import fcntl
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_repo_source

REPO = Path(__file__).resolve().parents[2]
DRAIN = REPO / "scripts" / "translation-drain.sh"

GPU_NODE_STUB = r"""#!/bin/bash
S="$STUB_DIR"
case "$1" in
  up)
    echo "up label=${GPU_NODE_LABEL} keyfile=${VAST_API_KEY_FILE:-unset}" >> "$S/calls"
    [[ "${STUB_FAIL:-}" == "up" ]] && exit 1
    exit 0 ;;
  tunnel)
    if [[ "${2:-}" == "--stop" ]]; then echo "tunnel-stop" >> "$S/calls"; exit 0; fi
    # Record whether the guard was already running when the tunnel opened.
    for _ in $(seq 1 30); do [[ -f "$S/guard-started" ]] && break; sleep 0.1; done
    if [[ -f "$S/guard-started" ]]; then g=yes; else g=no; fi
    echo "tunnel guard_started=$g" >> "$S/calls"
    [[ "${STUB_FAIL:-}" == "tunnel" ]] && exit 1
    exit 0 ;;
  down)
    echo "down $2" >> "$S/calls"
    touch "$S/destroyed"
    exit 0 ;;
esac
echo "gpu-node-unexpected $*" >> "$S/calls"; exit 2
"""

GUARD_STUB = r"""#!/bin/bash
S="$STUB_DIR"
if [[ " $* " == *" --check "* ]]; then
  echo "guard-check" >> "$S/calls"
  if [[ -f "$S/destroyed" ]]; then echo "instances=0 spend_this_session=\$0.00"; else echo "instances=1"; fi
  exit 0
fi
echo "guard $*" >> "$S/calls"
touch "$S/guard-started"
trap 'exit 143' TERM
n=0
while [[ ! -f "$S/destroyed" ]]; do
  sleep 0.1; n=$((n + 1))
  if [[ "${STUB_FAIL:-}" == "guard-cap" && $n -ge 10 ]]; then
    echo "guard-cap-teardown" >> "$S/calls"; touch "$S/destroyed"; exit 0
  fi
done
echo "guard-confirmed" >> "$S/calls"
exit 0
"""

BATCH_STUB = r"""#!/bin/bash
S="$STUB_DIR"
echo "batch $* mt=${AUDIOBOOKS_MT_ENDPOINT:-unset} whisper=${AUDIOBOOKS_WHISPER_GPU_HOST:-unset}:${AUDIOBOOKS_WHISPER_GPU_PORT:-unset}" >> "$S/calls"
# Claim the pending row the way batch-translate.py does.
"$STUB_PY" - "$STUB_DB" <<'EOF'
import sqlite3, sys, time
c = sqlite3.connect(sys.argv[1])
now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
c.execute("UPDATE translation_queue SET state='processing', started_at=? WHERE state='pending'", (now,))
c.commit()
EOF
case "${STUB_FAIL:-}" in
  batch) exit 3 ;;
  hang|sigterm|guard-cap)
    trap 'echo batch-got-TERM >> "$S/calls"; exit 143' TERM
    touch "$S/batch-running"
    while true; do sleep 0.1; done ;;
esac
"$STUB_PY" - "$STUB_DB" <<'EOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("UPDATE translation_queue SET state='completed' WHERE state='processing'")
c.commit()
EOF
exit 0
"""

CURL_STUB = r"""#!/bin/bash
[[ "${STUB_FAIL:-}" == "bootstrap" ]] && exit 7
exit 0
"""


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def _make_db(path: Path, pending: int, excluded_pending: int = 0) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT, asin TEXT,
                                 translation_excluded INTEGER DEFAULT 0);
        CREATE TABLE translation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL, priority INTEGER DEFAULT 0,
            state TEXT DEFAULT 'pending', step TEXT DEFAULT 'stt', error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, started_at TIMESTAMP,
            finished_at TIMESTAMP, last_progress_at TIMESTAMP, total_chapters INTEGER,
            UNIQUE(audiobook_id, locale));
        """
    )
    book = 0
    for _ in range(pending):
        book += 1
        conn.execute("INSERT INTO audiobooks (id, title) VALUES (?, 't')", (book,))
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale) VALUES (?, 'zh-Hans')", (book,)
        )
    for _ in range(excluded_pending):
        book += 1
        conn.execute(
            "INSERT INTO audiobooks (id, title, translation_excluded) VALUES (?, 't', 1)", (book,)
        )
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale) VALUES (?, 'zh-Hans')", (book,)
        )
    conn.commit()
    conn.close()


class Rig:
    """A temp tree holding stubs, a config lib, a key file and a database."""

    def __init__(self, tmp: Path, pending: int = 1, excluded_pending: int = 0):
        self.tmp = tmp
        self.stubs = tmp / "stubs"
        self.stubs.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.db = tmp / "audiobooks.db"
        _make_db(self.db, pending, excluded_pending)
        self.lock = tmp / "run" / "translation-drain.lock"
        self.gpu_node = _write_exec(tmp / "gpu-node-stub.sh", GPU_NODE_STUB)
        self.guard = _write_exec(tmp / "guard-stub.sh", GUARD_STUB)
        self.batch = _write_exec(tmp / "batch-stub.sh", BATCH_STUB)
        _write_exec(self.bin / "curl", CURL_STUB)
        self.keyfile = tmp / "vast-api-key"
        self.keyfile.write_text("not-a-real-key\n")
        self.keyfile.chmod(0o600)
        self.config = tmp / "config.sh"
        self.config.write_text(
            f'AUDIOBOOKS_DATABASE="{self.db}"\n'
            f'AUDIOBOOKS_LIBRARY="{tmp / "Library"}"\n'
            f'AUDIOBOOKS_RUN_DIR="{tmp / "run"}"\n'
            f'AUDIOBOOKS_VENV="{tmp / "venv"}"\n'
        )

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "STUB_DIR": str(self.stubs),
            "STUB_DB": str(self.db),
            "STUB_PY": sys.executable,
            "DRAIN_CONFIG_LIB": str(self.config),
            "DRAIN_GPU_NODE_CMD": str(self.gpu_node),
            "DRAIN_GUARD_CMD": str(self.guard),
            "DRAIN_BATCH_CMD": str(self.batch),
            "DRAIN_PYTHON": sys.executable,
            "DRAIN_POLL_SECONDS": "0.1",
            "DRAIN_TERM_GRACE": "3",
            "DRAIN_GUARD_WAIT": "5",
            "AUDIOBOOKS_DRAIN_LOCK": str(self.lock),
            "AUDIOBOOKS_TRANSLATION_EXCLUDE": str(self.tmp / "no-exclusions.txt"),
            "VAST_API_KEY_FILE": str(self.keyfile),
        }
        env.update(extra)
        return env

    def run(self, timeout: float = 60, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(DRAIN)],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def calls(self) -> list[str]:
        path = self.stubs / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def verbs(self) -> list[str]:
        return [line.split()[0] for line in self.calls()]

    def queue_states(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT state FROM translation_queue ORDER BY id")]
        finally:
            conn.close()


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _assert_torn_down(r: Rig, proc: subprocess.CompletedProcess) -> None:
    verbs = r.verbs()
    assert "down" in verbs, (
        f"node was NOT destroyed.\ncalls={r.calls()}\n{proc.stdout}\n{proc.stderr}"
    )
    assert "down --yes" in r.calls()
    assert verbs.index("tunnel-stop") < verbs.index("down"), r.calls()


# ── no work, no rent ─────────────────────────────────────────────────────────


def test_zero_pending_rents_nothing(tmp_path: Path):
    r = Rig(tmp_path, pending=0)
    proc = r.run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert r.calls() == [], f"drain touched the GPU tooling with nothing pending: {r.calls()}"
    assert "nothing to do, no node rented" in proc.stdout


def test_only_excluded_pending_rents_nothing(tmp_path: Path):
    """Rows for permanently excluded books are retired unprocessed by the
    batch, so they must not justify a node rental on their own."""
    r = Rig(tmp_path, pending=0, excluded_pending=2)
    proc = r.run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert r.calls() == []


def test_missing_credential_refuses_before_renting(rig: Rig):
    proc = rig.run(VAST_API_KEY_FILE=str(rig.tmp / "absent"))
    assert proc.returncode == 1
    assert rig.calls() == [], rig.calls()
    assert "not renting" in proc.stdout


# ── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_order_and_guard_right_after_up(rig: Rig):
    proc = rig.run(AUDIOBOOKS_DRAIN_BUDGET_USD="7.5")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    verbs = rig.verbs()
    core = [v for v in verbs if v in {"up", "guard", "tunnel", "batch", "tunnel-stop", "down"}]
    assert core == ["up", "guard", "tunnel", "batch", "tunnel-stop", "down"], rig.calls()
    calls = rig.calls()
    # Guard started immediately after up, before anything else ran.
    assert verbs[verbs.index("up") + 1] == "guard", calls
    assert "tunnel guard_started=yes" in calls, calls
    # Budget key reaches --cap; label is the drain's own.
    guard_line = next(c for c in calls if c.startswith("guard --cap"))
    assert "--cap 7.5" in guard_line and "--label abm-drain" in guard_line, guard_line
    assert calls[0].startswith("up label=abm-drain keyfile="), calls[0]
    # The guard confirmed teardown (exited 0 after down).
    assert "guard-confirmed" in calls
    # Batch got the tunnel endpoints and STT-only default.
    batch_line = next(c for c in calls if c.startswith("batch "))
    assert "--stt-only" in batch_line
    assert "mt=http://127.0.0.1:8000" in batch_line and "whisper=127.0.0.1:8765" in batch_line
    assert rig.queue_states() == ["completed"]


def test_stt_only_false_runs_full_batch(rig: Rig):
    proc = rig.run(AUDIOBOOKS_DRAIN_STT_ONLY="false")
    assert proc.returncode == 0
    batch_line = next(c for c in rig.calls() if c.startswith("batch "))
    assert "--stt-only" not in batch_line


def test_invalid_budget_refused_before_renting(rig: Rig):
    proc = rig.run(AUDIOBOOKS_DRAIN_BUDGET_USD="lots")
    assert proc.returncode == 1
    assert rig.calls() == []


# ── every failure still destroys the node and exits non-zero ─────────────────


def test_up_failure_still_runs_down(rig: Rig):
    proc = rig.run(STUB_FAIL="up")
    assert proc.returncode != 0
    _assert_torn_down(rig, proc)
    assert "guard" not in rig.verbs()


def test_tunnel_failure_still_runs_down(rig: Rig):
    proc = rig.run(STUB_FAIL="tunnel")
    assert proc.returncode != 0
    _assert_torn_down(rig, proc)


def test_bootstrap_timeout_still_runs_down(rig: Rig):
    proc = rig.run(STUB_FAIL="bootstrap", AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT="1")
    assert proc.returncode != 0
    assert "bootstrap timeout" in proc.stdout
    assert "batch" not in rig.verbs()
    _assert_torn_down(rig, proc)


def test_batch_failure_still_runs_down(rig: Rig):
    proc = rig.run(STUB_FAIL="batch")
    assert proc.returncode != 0
    assert "batch exited 3" in proc.stdout
    _assert_torn_down(rig, proc)


def test_wall_clock_exceeded_stops_batch_and_runs_down(rig: Rig):
    proc = rig.run(STUB_FAIL="hang", DRAIN_MAX_SECONDS="1")
    assert proc.returncode != 0
    assert "wall-clock limit" in proc.stdout
    assert "batch-got-TERM" in rig.calls()
    _assert_torn_down(rig, proc)
    # The interrupted claim went back to pending for the next drain.
    assert rig.queue_states() == ["pending"]


def test_guard_cap_mid_batch_fails_and_runs_down(rig: Rig):
    proc = rig.run(STUB_FAIL="guard-cap")
    assert proc.returncode != 0
    assert "budget guard exited mid-batch" in proc.stdout
    _assert_torn_down(rig, proc)


def test_sigterm_mid_batch_tears_down(rig: Rig):
    proc = subprocess.Popen(
        ["bash", str(DRAIN)],
        env=rig.env(STUB_FAIL="sigterm"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not (rig.stubs / "batch-running").exists():
            assert time.monotonic() < deadline, "batch never started"
            assert proc.poll() is None, proc.communicate()
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 143, out + err
    assert "received SIGTERM" in out
    assert "batch-got-TERM" in rig.calls()
    _assert_torn_down(rig, subprocess.CompletedProcess([], proc.returncode, out, err))
    assert rig.queue_states() == ["pending"]


# ── no overlap ───────────────────────────────────────────────────────────────


def test_lock_prevents_overlap(rig: Rig):
    rig.lock.parent.mkdir(parents=True, exist_ok=True)
    with open(rig.lock, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = rig.run()
    assert proc.returncode == 75, proc.stdout + proc.stderr
    assert "another drain holds" in proc.stdout
    assert rig.calls() == [], rig.calls()


def test_lock_is_released_after_a_run(rig: Rig):
    """Children (notably the disowned tunnel ssh) must not inherit the lock fd."""
    assert rig.run().returncode == 0
    with open(rig.lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held


# ── wiring: installed, never enabled ─────────────────────────────────────────


def test_drain_timer_is_not_enabled_by_install_or_upgrade():
    timer = "audiobook-translation-drain"
    target = (REPO / "systemd" / "audiobook.target").read_text()
    assert timer not in target, "audiobook.target must not pull in the drain"
    install = (REPO / "install.sh").read_text()
    enable_loop = next(
        line for line in install.splitlines() if line.strip().startswith("for svc in audiobook-api")
    )
    assert timer not in enable_loop
    upgrade = (REPO / "upgrade.sh").read_text()
    start = upgrade.index("local standalone_units=(")
    assert timer not in upgrade[start : upgrade.index(")", start)]
    service = (REPO / "systemd" / f"{timer}.service").read_text()
    assert not any(line.strip() == "[Install]" for line in service.splitlines()), (
        "a static service cannot be enabled into a target by accident"
    )


def test_drain_tools_are_manifest_workers():
    manifest = (REPO / "scripts" / "install-manifest.sh").read_text()
    for name in (
        "translation-drain.sh",
        "gpu-node.sh",
        "gpu-budget-guard.sh",
        "batch-translate.py",
    ):
        assert f'"{name}|audiobook-translation-drain.service' in manifest, name
    for unit in ("audiobook-translation-drain.service", "audiobook-translation-drain.timer"):
        assert f'"{unit}"' in manifest
    assert os.access(DRAIN, os.X_OK)


def test_vast_tools_accept_key_file_pointer(tmp_path: Path):
    """With VAST_API_KEY_FILE set, the tools must never fall back to with-secret
    (absent for the service user); an unreadable pointer is an error."""
    for script in ("gpu-node.sh", "gpu-budget-guard.sh"):
        args = ["status"] if script == "gpu-node.sh" else ["--cap", "1", "--check"]
        proc = subprocess.run(
            ["bash", str(REPO / "scripts" / script), *args],
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "VAST_API_KEY_FILE": str(tmp_path / "absent"),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        assert "VAST_API_KEY_FILE" in proc.stderr and "not readable" in proc.stderr, proc.stderr
        assert "with-secret" not in proc.stderr
