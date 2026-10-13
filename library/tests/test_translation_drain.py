"""Behavioural tests for scripts/translation-drain.sh (Audiobook-Manager-4j4).

The drain rents a billed Vast.ai GPU node, so its one non-negotiable property
is that EVERY path out of it destroys the node. These tests run the real
script against stubbed collaborators — gpu-node.sh, gpu-budget-guard.sh,
batch-translate.py, curl and ss — that append what they were asked to do to a
shared call log. Nothing here touches the network, Vast.ai or a real node.

The database is a real SQLite file with the real two-stage ``translation_queue``
schema, and the spend ledger is the real ``localization.drain_ledger``, so the
counts, claim release and monthly budget all go through production code.
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
LIB = REPO / "library"
sys.path.insert(0, str(LIB))

from localization.queue_schema import ensure_queue_schema  # noqa: E402

GPU_NODE_STUB = r"""#!/bin/bash
S="$STUB_DIR"
log() { echo "$*" >> "$S/calls"; }
case "$1" in
  up)
    log "up label=${GPU_NODE_LABEL} keyfile=${VAST_API_KEY_FILE:-unset} guard_armed=$([[ -f $S/guard-started ]] && echo yes || echo no)"
    if [[ -n "${STUB_UP_SLEEP:-}" ]]; then
      trap 'log up-got-TERM; kill "$sp" 2>/dev/null; exit 143' TERM
      echo 1 > "$S/instances"   # the create landed; up is still "polling"
      sleep "$STUB_UP_SLEEP" & sp=$!
      touch "$S/up-running"
      wait "$sp"
    fi
    [[ "${STUB_FAIL:-}" == "up" ]] && { echo 1 > "$S/instances"; exit 1; }
    echo 1 > "$S/instances"
    exit 0 ;;
  tunnel)
    if [[ "${2:-}" == "--stop" ]]; then
      log "tunnel-stop"
      [[ -f "$S/tunnel.pid" ]] && kill "$(cat "$S/tunnel.pid")" 2>/dev/null
      rm -f "$TMPDIR/gpu-node-tunnel-${GPU_NODE_LABEL}.pid"
      exit 0
    fi
    log "tunnel"
    [[ "${STUB_FAIL:-}" == "tunnel" ]] && exit 1
    if [[ "${STUB_FAIL:-}" == "tunnel-dies" ]]; then
      (exec -a stub-tunnel sleep 2) & p=$!
    else
      (exec -a stub-tunnel sleep 300) & p=$!
    fi
    disown "$p"
    echo "$p" > "$S/tunnel.pid"
    echo "$p" > "$TMPDIR/gpu-node-tunnel-${GPU_NODE_LABEL}.pid"
    exit 0 ;;
  down)
    log "down $2"
    [[ "${STUB_FAIL:-}" == "down-ineffective" ]] && exit 0
    echo 0 > "$S/instances"; touch "$S/destroyed"; exit 0 ;;
  count)
    cat "$S/instances" 2>/dev/null || echo 0; exit 0 ;;
  billing)
    if [[ -f "$S/destroyed" ]]; then echo "credit=${STUB_CREDIT_AFTER-47.00}"; else echo "credit=${STUB_CREDIT_BEFORE-50.00}"; fi
    echo "instances=$(cat "$S/instances" 2>/dev/null || echo 0) accrued=${STUB_ACCRUED-2.5}"
    exit 0 ;;
esac
log "gpu-node-unexpected $*"; exit 2
"""

GUARD_STUB = r"""#!/bin/bash
S="$STUB_DIR"
echo "guard $*" >> "$S/calls"
touch "$S/guard-started"
trap 'exit 143' TERM
n=0
while [[ ! -f "$S/destroyed" ]]; do
  sleep 0.1 & wait $!; n=$((n + 1))
  if [[ "${STUB_FAIL:-}" == "guard-cap" && $n -ge 15 && -f "$S/batch-running" ]]; then
    echo "guard-cap-teardown" >> "$S/calls"; echo 0 > "$S/instances"; touch "$S/destroyed"; exit 0
  fi
done
exit 0
"""

BATCH_STUB = r"""#!/bin/bash
S="$STUB_DIR"
stage=""; prev=""
for a in "$@"; do [[ "$prev" == "--stage" ]] && stage="$a"; prev="$a"; done
echo "batch-$stage $* mt=${AUDIOBOOKS_MT_ENDPOINT:-unset} whisper=${AUDIOBOOKS_WHISPER_GPU_HOST:-unset}:${AUDIOBOOKS_WHISPER_GPU_PORT:-unset}" >> "$S/calls"
q() { "$STUB_PY" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(sys.argv[2]); c.commit()" "$STUB_DB" "$1"; }
if [[ "$stage" == "narration" ]]; then
  [[ "${STUB_FAIL:-}" == "narration" ]] && exit 1
  q "UPDATE translation_queue SET narration_done_at=CURRENT_TIMESTAMP, state='completed' WHERE state='pending' AND subtitles_done_at IS NOT NULL"
  exit 0
fi
q "UPDATE translation_queue SET state='processing', claimed_by='drain', started_at=CURRENT_TIMESTAMP WHERE state='pending' AND subtitles_done_at IS NULL"
case "${STUB_FAIL:-}" in
  batch) exit 3 ;;
  endpoints-lost)
    q "UPDATE translation_queue SET state='pending', claimed_by=NULL WHERE claimed_by='drain'"
    exit 4 ;;
  hang|sigterm|guard-cap|tunnel-dies|health-dies)
    trap 'echo batch-got-TERM >> "$S/calls"; exit 143' TERM
    touch "$S/batch-running"
    while true; do sleep 0.1 & wait $!; done ;;
esac
q "UPDATE translation_queue SET subtitles_done_at=CURRENT_TIMESTAMP, state='pending', claimed_by=NULL WHERE state='processing'"
exit 0
"""

CURL_STUB = r"""#!/bin/bash
[[ "${STUB_FAIL:-}" == "bootstrap" ]] && exit 7
[[ "${STUB_FAIL:-}" == "health-dies" && -f "$STUB_DIR/batch-running" ]] && exit 7
exit 0
"""

SS_STUB = r"""#!/bin/bash
[[ -n "${STUB_PORTS_BUSY:-}" ]] && echo "LISTEN 0 128 127.0.0.1:8000 0.0.0.0:*"
exit 0
"""


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def _make_db(path: Path, pending: int, excluded_pending: int = 0, narration_pending: int = 0):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT, asin TEXT, "
        "translation_excluded INTEGER DEFAULT 0)"
    )
    ensure_queue_schema(conn)
    book = 0

    def add(excluded=0, subs_done=False):
        nonlocal book
        book += 1
        conn.execute(
            "INSERT INTO audiobooks (id, title, translation_excluded) VALUES (?, 't', ?)",
            (book, excluded),
        )
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale, subtitles_done_at) "
            "VALUES (?, 'zh-Hans', ?)",
            (book, "2026-01-01 00:00:00" if subs_done else None),
        )

    for _ in range(pending):
        add()
    for _ in range(excluded_pending):
        add(excluded=1)
    for _ in range(narration_pending):
        add(subs_done=True)
    conn.commit()
    conn.close()


class Rig:
    """A temp tree holding stubs, a config lib, a key file, a ledger and a DB."""

    def __init__(self, tmp: Path, pending=1, excluded_pending=0, narration_pending=0):
        self.tmp = tmp
        self.stubs = tmp / "stubs"
        self.stubs.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.tmpdir = tmp / "tmp"
        self.tmpdir.mkdir()
        self.db = tmp / "audiobooks.db"
        _make_db(self.db, pending, excluded_pending, narration_pending)
        self.lock = tmp / "run" / "translation-drain.lock"
        self.ledger = tmp / "var" / "spend-ledger.tsv"
        self.gpu_node = _write_exec(tmp / "gpu-node-stub.sh", GPU_NODE_STUB)
        self.guard = _write_exec(tmp / "guard-stub.sh", GUARD_STUB)
        self.batch = _write_exec(tmp / "batch-stub.sh", BATCH_STUB)
        _write_exec(self.bin / "curl", CURL_STUB)
        _write_exec(self.bin / "ss", SS_STUB)
        self.keyfile = tmp / "vast-api-key"
        self.keyfile.write_text("not-a-real-key\n")
        self.keyfile.chmod(0o600)
        self.config = tmp / "config.sh"
        self.config.write_text(
            f'AUDIOBOOKS_DATABASE="{self.db}"\n'
            f'AUDIOBOOKS_LIBRARY="{tmp / "Library"}"\n'
            f'AUDIOBOOKS_RUN_DIR="{tmp / "run"}"\n'
            f'AUDIOBOOKS_VAR_DIR="{tmp / "var"}"\n'
            f'AUDIOBOOKS_VENV="{tmp / "venv"}"\n'
        )

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "TMPDIR": str(self.tmpdir),
            "STUB_DIR": str(self.stubs),
            "STUB_DB": str(self.db),
            "STUB_PY": sys.executable,
            "DRAIN_CONFIG_LIB": str(self.config),
            "DRAIN_GPU_NODE_CMD": str(self.gpu_node),
            "DRAIN_GUARD_CMD": str(self.guard),
            "DRAIN_BATCH_CMD": str(self.batch),
            "DRAIN_PYTHON": sys.executable,
            "DRAIN_POLL_SECONDS": "0.1",
            "DRAIN_HEALTH_EVERY": "0",
            "DRAIN_TERM_GRACE": "3",
            "DRAIN_LOCK_WAIT": "1",
            "DRAIN_VERIFY_DELAY": "0",
            "AUDIOBOOKS_DRAIN_LOCK": str(self.lock),
            "AUDIOBOOKS_DRAIN_LEDGER": str(self.ledger),
            "AUDIOBOOKS_TRANSLATION_EXCLUDE": str(self.tmp / "no-exclusions.txt"),
            "VAST_API_KEY_FILE": str(self.keyfile),
        }
        env.update(extra)
        return env

    def run(self, timeout: float = 90, **extra: str) -> subprocess.CompletedProcess:
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

    def rows(self) -> list[tuple]:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT state, subtitles_done_at IS NOT NULL, narration_done_at IS NOT NULL, "
                "claimed_by FROM translation_queue ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def ledger_lines(self) -> list[list[str]]:
        if not self.ledger.exists():
            return []
        return [ln.split("\t") for ln in self.ledger.read_text().splitlines()]

    def seed_ledger(self, amount: float, epoch: float | None = None, run: str = "earlier") -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        epoch = time.time() if epoch is None else epoch
        month = time.strftime("%Y-%m", time.localtime(epoch))
        with self.ledger.open("a") as fh:
            fh.write(f"{int(epoch)}\t{month}\t{run}\treserve\t{amount:.4f}\t\n")
            fh.write(f"{int(epoch)}\t{month}\t{run}\tfinal\t{amount:.4f}\t\n")


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _out(proc) -> str:
    return f"\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"


def _assert_torn_down(r: Rig, proc) -> None:
    verbs = r.verbs()
    assert "down" in verbs, f"node was NOT destroyed.\ncalls={r.calls()}{_out(proc)}"
    assert "down --yes" in r.calls()
    assert verbs.index("tunnel-stop") < verbs.index("down"), r.calls()


def _assert_nothing_rented(r: Rig, proc) -> None:
    assert not any(v in ("up", "guard") for v in r.verbs()), f"{r.calls()}{_out(proc)}"


# ── no work, no rent ─────────────────────────────────────────────────────────


def test_zero_pending_rents_nothing(tmp_path: Path):
    r = Rig(tmp_path, pending=0)
    proc = r.run()
    assert proc.returncode == 0, _out(proc)
    assert r.calls() == [], f"drain touched the GPU tooling with nothing pending: {r.calls()}"
    assert "nothing to do, no node rented" in proc.stdout


def test_only_excluded_or_quarantined_rents_nothing(tmp_path: Path):
    """Rows for permanently excluded books are retired unprocessed by the
    batch, and quarantined rows are not pending work, so neither may justify
    a node rental on its own."""
    r = Rig(tmp_path, pending=0, excluded_pending=2)
    with sqlite3.connect(r.db) as conn:
        conn.execute("INSERT INTO audiobooks (id, title) VALUES (50, 't')")
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale, state, drain_attempts) "
            "VALUES (50, 'zh-Hans', 'quarantined', 3)"
        )
    proc = r.run()
    assert proc.returncode == 0, _out(proc)
    assert r.calls() == []


def test_narration_only_work_never_rents(tmp_path: Path):
    r = Rig(tmp_path, pending=0, narration_pending=1)
    proc = r.run()
    assert proc.returncode == 0, _out(proc)
    _assert_nothing_rented(r, proc)
    assert r.verbs() == ["batch-narration"], r.calls()
    assert r.rows() == [("completed", 1, 1, None)]


def test_missing_credential_refuses_before_renting(rig: Rig):
    proc = rig.run(VAST_API_KEY_FILE=str(rig.tmp / "absent"))
    assert proc.returncode == 1
    _assert_nothing_rented(rig, proc)
    assert "not renting" in proc.stdout


def test_busy_ports_refuse_before_renting(rig: Rig):
    proc = rig.run(STUB_PORTS_BUSY="1")
    assert proc.returncode == 1
    _assert_nothing_rented(rig, proc)
    assert "already in use" in proc.stdout
    assert rig.ledger_lines() == [], "a refused run must not reserve budget"


# ── happy path: two stages, node gone before narration ──────────────────────


def test_happy_path_two_stages_in_order(rig: Rig):
    proc = rig.run(AUDIOBOOKS_DRAIN_BUDGET_USD="7.5")
    assert proc.returncode == 0, _out(proc)
    core = [
        v
        for v in rig.verbs()
        if v
        in {"guard", "up", "tunnel", "batch-subtitles", "tunnel-stop", "down", "batch-narration"}
    ]
    assert core == [
        "guard",
        "up",
        "tunnel",
        "batch-subtitles",
        "tunnel-stop",
        "down",
        "batch-narration",
    ], rig.calls()
    calls = rig.calls()
    # The guard is armed before the node can exist and covers the create window.
    up_line = next(c for c in calls if c.startswith("up "))
    assert "guard_armed=yes" in up_line and "label=abm-drain" in up_line, up_line
    guard_line = next(c for c in calls if c.startswith("guard "))
    assert "--cap 7.50" in guard_line and "--label abm-drain" in guard_line
    assert "--await" in guard_line, guard_line
    sub_line = next(c for c in calls if c.startswith("batch-subtitles"))
    assert "--drain-mode" in sub_line and "--owner drain" in sub_line
    assert "--max-attempts 3" in sub_line
    assert "mt=http://127.0.0.1:8000" in sub_line and "whisper=127.0.0.1:8765" in sub_line
    narr_line = next(c for c in calls if c.startswith("batch-narration"))
    assert "mt=unset" in narr_line, "narration must not see the (destroyed) MT endpoint"
    assert rig.rows() == [("completed", 1, 1, None)]
    # Ledger: reservation at the cap, then the measured spend:
    # max(credit delta 50.00-47.00, accrued 2.5) = 3.00.
    kinds = [(ln[3], float(ln[4])) for ln in rig.ledger_lines()]
    assert kinds == [("reserve", 7.5), ("final", 3.0)], kinds
    assert not Path(str(rig.ledger) + ".live").exists()


def test_invalid_budget_refused_before_renting(rig: Rig):
    proc = rig.run(AUDIOBOOKS_DRAIN_BUDGET_USD="lots")
    assert proc.returncode == 1
    assert rig.calls() == []


# ── every GPU failure destroys the node and exits non-zero ──────────────────


@pytest.mark.parametrize(
    "fail,extra,expect",
    [
        ("up", {}, "gpu-node.sh up failed"),
        ("tunnel", {}, "gpu-node.sh tunnel failed"),
        ("bootstrap", {"AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT": "1"}, "bootstrap timeout"),
        ("batch", {}, "subtitles batch exited 3"),
        ("hang", {"DRAIN_MAX_SECONDS": "1"}, "wall-clock limit"),
        ("guard-cap", {}, "budget guard exited mid-batch"),
        ("tunnel-dies", {}, "tunnel died"),
        ("health-dies", {}, "stopped answering"),
        ("endpoints-lost", {}, "lost the GPU endpoints"),
    ],
)
def test_gpu_failure_tears_down_and_fails(rig: Rig, fail, extra, expect):
    proc = rig.run(STUB_FAIL=fail, **extra)
    assert proc.returncode != 0, _out(proc)
    assert expect in proc.stdout, _out(proc)
    _assert_torn_down(rig, proc)
    if fail in ("hang", "guard-cap", "tunnel-dies", "health-dies"):
        assert "batch-got-TERM" in rig.calls()
    # The interrupted claim went back to pending — never 'failed' — and the
    # subtitle stage is still open for the next drain.
    if fail != "batch":
        assert rig.rows() == [("pending", 0, 0, None)], rig.rows()


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
    assert rig.rows() == [("pending", 0, 0, None)]
    assert "batch-narration" not in rig.verbs(), "a signalled run must not start new work"


def test_sigterm_during_up_is_handled_immediately(rig: Rig):
    """bash defers traps while a FOREGROUND child runs; `up` runs as a
    background child + wait, so the trap fires at once and destroys the node
    the half-finished `up` created (found by label, id never learned)."""
    proc = subprocess.Popen(
        ["bash", str(DRAIN)],
        env=rig.env(STUB_UP_SLEEP="60"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not (rig.stubs / "up-running").exists():
            assert time.monotonic() < deadline, "up never started"
            time.sleep(0.05)
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        elapsed = time.monotonic() - t0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 143, out + err
    assert elapsed < 15, f"trap waited {elapsed:.1f}s — deferred until `up` returned"
    assert "up-got-TERM" in rig.calls()
    _assert_torn_down(rig, subprocess.CompletedProcess([], 143, out, err))


def test_unverified_teardown_fails_and_skips_narration(tmp_path: Path):
    r = Rig(tmp_path, pending=1, narration_pending=1)
    proc = r.run(STUB_FAIL="down-ineffective")
    assert proc.returncode == 1, _out(proc)
    assert "TEARDOWN UNVERIFIED" in proc.stdout
    assert r.verbs().count("down") >= 3, "down must be retried"
    assert "batch-narration" not in r.verbs()


def test_gpu_failure_still_runs_free_narration(tmp_path: Path):
    r = Rig(tmp_path, pending=1, narration_pending=1)
    proc = r.run(STUB_FAIL="batch")
    assert proc.returncode == 1
    verbs = r.verbs()
    assert verbs.index("down") < verbs.index("batch-narration"), r.calls()


def test_narration_failure_exits_nonzero_and_stays_pending(tmp_path: Path):
    r = Rig(tmp_path, pending=0, narration_pending=1)
    proc = r.run(STUB_FAIL="narration")
    assert proc.returncode == 1
    assert r.rows() == [("pending", 1, 0, None)]


# ── monthly ceiling and ledger ───────────────────────────────────────────────


def _guard_cap(r: Rig) -> str:
    line = next(c for c in r.calls() if c.startswith("guard "))
    return line.split("--cap ")[1].split()[0]


def test_monthly_under_budget_uses_per_run_cap(rig: Rig):
    rig.seed_ledger(40.0)
    proc = rig.run()
    assert proc.returncode == 0, _out(proc)
    assert _guard_cap(rig) == "10.00"


def test_monthly_remaining_below_per_run_lowers_cap(rig: Rig):
    rig.seed_ledger(94.25)
    proc = rig.run()
    assert proc.returncode == 0, _out(proc)
    assert _guard_cap(rig) == "5.75"
    reserve = [ln for ln in rig.ledger_lines() if ln[3] == "reserve" and ln[2] != "earlier"]
    assert float(reserve[0][4]) == 5.75


@pytest.mark.parametrize("spent", [100.0, 130.0, 99.5])
def test_monthly_at_or_over_ceiling_refuses(rig: Rig, spent):
    # 99.50 leaves $0.50, under the $1 minimum worth renting for.
    rig.seed_ledger(spent)
    proc = rig.run()
    assert proc.returncode == 1, _out(proc)
    _assert_nothing_rented(rig, proc)
    assert "monthly" in proc.stdout and "not renting" in proc.stdout
    assert rig.rows() == [("pending", 0, 0, None)]
    assert [ln[2] for ln in rig.ledger_lines()] == ["earlier", "earlier"], "no new reservation"


def test_month_rollover_resets_budget(rig: Rig):
    # $100 spent in a previous calendar month does not count this month.
    rig.seed_ledger(100.0, epoch=time.time() - 40 * 86400, run="last-month")
    rig.seed_ledger(95.0, run="this-month")
    proc = rig.run()
    assert proc.returncode == 0, _out(proc)
    assert _guard_cap(rig) == "5.00", "last month's $100 must not count; this month's $95 must"


def test_ledger_survives_between_runs(tmp_path: Path):
    """A second run (new process — a restart) sees the first run's spend."""
    r = Rig(tmp_path)
    assert r.run(AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD="12").returncode == 0
    with sqlite3.connect(r.db) as conn:  # queue one more book
        conn.execute("INSERT INTO audiobooks (id, title) VALUES (99, 't')")
        conn.execute("INSERT INTO translation_queue (audiobook_id, locale) VALUES (99, 'zh-Hans')")
    (r.stubs / "calls").unlink()
    (r.stubs / "destroyed").unlink()
    proc = r.run(AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD="12")
    assert proc.returncode == 0, _out(proc)
    # First run's final was $3.00 → $9.00 of $12 left, under the $10 per-run cap.
    assert _guard_cap(r) == "9.00"


def test_reservation_stands_when_spend_unmeasurable(rig: Rig):
    proc = rig.run(STUB_CREDIT_BEFORE="", STUB_CREDIT_AFTER="", STUB_ACCRUED="")
    assert proc.returncode == 0, _out(proc)
    kinds = [ln[3] for ln in rig.ledger_lines()]
    assert kinds == ["reserve"], "an unmeasured run keeps its full reservation"


# ── no overlap ───────────────────────────────────────────────────────────────


def test_lock_prevents_overlap(rig: Rig):
    rig.lock.parent.mkdir(parents=True, exist_ok=True)
    with open(rig.lock, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = rig.run()
    assert proc.returncode == 75, _out(proc)
    assert "held" in proc.stdout
    assert rig.calls() == [], rig.calls()


def test_lock_is_released_after_a_run(rig: Rig):
    """Children (notably the disowned tunnel ssh) must not inherit the lock fd."""
    assert rig.run().returncode == 0
    with open(rig.lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held


def test_stale_drain_claims_released_at_start(tmp_path: Path):
    r = Rig(tmp_path, pending=0)
    with sqlite3.connect(r.db) as conn:
        conn.execute("INSERT INTO audiobooks (id, title) VALUES (7, 't'), (8, 't')")
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale, state, claimed_by) VALUES "
            "(7, 'zh-Hans', 'processing', 'drain'), (8, 'zh-Hans', 'processing', 'api:123')"
        )
    proc = r.run(STUB_FAIL="up")
    assert "returned 1 interrupted queue row" in proc.stdout, _out(proc)
    states = dict(
        sqlite3.connect(r.db)
        .execute("SELECT audiobook_id, state FROM translation_queue")
        .fetchall()
    )
    assert states[8] == "processing", "an API-held row must not be touched by the drain"


# ── wiring ───────────────────────────────────────────────────────────────────


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
    assert timer not in upgrade[start : upgrade.index("\n    )", start)]
    service = (REPO / "systemd" / f"{timer}.service").read_text()
    assert not any(line.strip() == "[Install]" for line in service.splitlines()), (
        "a static service cannot be enabled into a target by accident"
    )


def test_drain_stop_timeout_exceeds_worst_case_teardown():
    unit = (REPO / "systemd" / "audiobook-translation-drain.service").read_text()
    stop = next(ln for ln in unit.splitlines() if ln.startswith("TimeoutStopSec="))
    minutes = int(stop.split("=")[1].removesuffix("min"))
    # Worst case from the script's defaults: batch grace 300 + up interrupt 30
    # + spend sample 120 + 3 x (down 120 + verify 5 + count 60) + record 120.
    worst = 300 + 30 + 120 + 3 * (120 + 5 + 60) + 120
    assert minutes * 60 > worst, f"TimeoutStopSec {minutes}min <= worst-case teardown {worst}s"
    assert "KillMode=mixed" in unit


def test_drain_tools_are_manifest_workers():
    manifest = (REPO / "scripts" / "install-manifest.sh").read_text()
    for name in (
        "translation-drain.sh",
        "gpu-node.sh",
        "gpu-budget-guard.sh",
        "batch-translate.py",
    ):
        assert f'"{name}|audiobook-translation-drain.service' in manifest, name
    assert '"translation-drain-reaper.sh|audiobook-translation-reaper.service' in manifest
    for unit in (
        "audiobook-translation-drain.service",
        "audiobook-translation-drain.timer",
        "audiobook-translation-reaper.service",
        "audiobook-translation-reaper.timer",
    ):
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


def test_gpu_node_pip_wait_is_bounded():
    src = (REPO / "scripts" / "gpu-node.sh").read_text()
    line = next(ln for ln in src.splitlines() if "[p]ip install' >/dev/null" in ln)
    assert "-ge ${PIP_WAIT}" in line and "exit 3" in line, line
