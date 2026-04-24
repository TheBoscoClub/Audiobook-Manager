"""Regression guard for sampler-burst.sh pool-sizing modes (v8.3.8.4).

Pins the invariants for --workers / --add-workers semantics:

1. Mutual exclusion: --workers and --add-workers cannot be combined.
2. Replace mode (default --workers N): existing burst workers are gracefully
   SIGTERMed before new workers spawn. Grace window covers an in-flight
   segment (~60s cold-GPU).
3. Add mode (--add-workers N): existing burst workers are left alone; new
   workers stack on top.
4. Cap enforcement: total worker count (systemd + burst) never exceeds the
   MAX_WORKERS_TOTAL constant. Requested counts that would overflow are
   clamped to the available slot budget with a user-visible note.
5. Worker discovery: the "existing burst worker" heuristic filters the
   systemd unit's MainPID out of the stream-translate-worker.py process
   set — works whether the prior burst parent is alive, exited, or
   re-parented to init via nohup.

These are mechanical (grep/parse the script) so the invariants survive
refactoring. If someone removes a gate, the test fails immediately.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BURST = (REPO / "scripts" / "sampler-burst.sh").read_text()


# ──────────────────────────────────────────────────────────────────────────
# Arg parsing / mutual exclusion
# ──────────────────────────────────────────────────────────────────────────


def test_add_workers_flag_registered():
    """--add-workers is recognized in the arg parser."""
    assert "--add-workers)" in BURST, "--add-workers case missing from arg parser"


def test_workers_and_add_workers_mutually_exclusive():
    """Using --workers with --add-workers errors out with a clear message."""
    # Both branches must check _mode_set and emit the mutex error.
    assert BURST.count("mutually exclusive") >= 1, (
        "mutual exclusion error message missing from --workers/--add-workers handlers"
    )
    assert "_mode_set=1" in BURST, "mode-set sentinel missing"


def test_workers_default_is_replace_mode():
    """--workers (without --add-workers) sets MODE=replace by default."""
    assert 'MODE="replace"' in BURST, "default MODE=replace not set"


def test_add_workers_sets_mode_add():
    """--add-workers flips MODE to 'add'."""
    # Must appear in the --add-workers) branch.
    idx = BURST.index("--add-workers)")
    # Find the next case branch end
    next_branch = BURST.index(";;", idx)
    section = BURST[idx:next_branch]
    assert 'MODE="add"' in section, "--add-workers branch does not set MODE=add"


# ──────────────────────────────────────────────────────────────────────────
# Cap enforcement
# ──────────────────────────────────────────────────────────────────────────


def test_max_workers_total_constant_is_16():
    """The project-wide cap is 16 including the systemd worker."""
    assert "MAX_WORKERS_TOTAL=16" in BURST


def test_cap_math_accounts_for_systemd_worker():
    """available_slots math subtracts systemd worker count."""
    # Both REPLACE and ADD modes subtract sysd_count.
    assert "MAX_WORKERS_TOTAL - sysd_count" in BURST


def test_add_mode_subtracts_existing_count():
    """Add mode's available_slots = MAX - systemd - existing."""
    assert "MAX_WORKERS_TOTAL - sysd_count - existing_count" in BURST


def test_clamp_emits_user_visible_note():
    """When requested > available, the excess is clamped and a note is printed."""
    # Grep for the characteristic note phrase.
    assert "cap=" in BURST and "Spawning $available_slots" in BURST, (
        "clamp note not emitted when requested exceeds available slots"
    )


def test_full_cap_refuses_to_spawn():
    """When available_slots <= 0, the script exits without spawning."""
    assert "pool already at cap" in BURST, "missing at-cap exit"


# ──────────────────────────────────────────────────────────────────────────
# Existing-worker discovery
# ──────────────────────────────────────────────────────────────────────────


def test_existing_worker_discovery_uses_pgrep():
    """Discovery uses pgrep on the Python worker cmdline."""
    assert "pgrep -f 'stream-translate-worker\\.py'" in BURST


def test_systemd_worker_excluded_from_burst_count():
    """systemd MainPID is filtered out of the existing-burst set."""
    # The filter happens in _existing_burst_worker_pids.
    assert '"$pid" != "$sysd_pid"' in BURST


def test_systemd_mainpid_lookup_uses_systemctl_show():
    """Lookup MainPID via systemctl show (canonical source)."""
    assert (
        "systemctl show -p MainPID --value audiobook-stream-translate.service" in BURST
    )


# ──────────────────────────────────────────────────────────────────────────
# Replace-mode termination
# ──────────────────────────────────────────────────────────────────────────


def test_replace_mode_sends_sigterm_not_sigkill():
    """Replace mode SIGTERMs existing workers (so they finish the current segment).

    The _terminate_workers helper uses SIGTERM first, then SIGKILL only after
    the grace window expires.
    """
    # _terminate_workers must TERM before KILL.
    idx_term = BURST.index('_terminate_workers() {')
    idx_done = BURST.index("}\n", idx_term)
    helper_body = BURST[idx_term:idx_done]
    assert "kill -TERM" in helper_body, "TERM missing from _terminate_workers"
    assert "kill -KILL" in helper_body, "KILL fallback missing from _terminate_workers"
    # TERM must come before the KILL-after-grace block.
    assert helper_body.index("kill -TERM") < helper_body.index("kill -KILL")


def test_replace_mode_grace_window_is_90s():
    """Grace window covers an in-flight segment (cold GPU ~60s, +margin)."""
    assert "_terminate_workers 90" in BURST


def test_replace_mode_skipped_when_no_existing_workers():
    """If existing_count==0, no SIGTERM is sent (nothing to replace)."""
    assert '[[ $existing_count -gt 0 ]]' in BURST


# ──────────────────────────────────────────────────────────────────────────
# User gate — applied regardless of mode
# ──────────────────────────────────────────────────────────────────────────


def test_sampler_burst_calls_require_audiobooks_user():
    """The shared helper is invoked early — no code paths that skip it."""
    assert 'require_audiobooks_user "$@"' in BURST


# ──────────────────────────────────────────────────────────────────────────
# Detach-by-default (v8.3.8.5): interactive invocations must return the
# shell immediately after spawning workers, not block on drain-polling.
# Ctrl+C on a drain-polling sampler-burst would previously fire the EXIT
# trap and kill the workers the user just spawned.
# ──────────────────────────────────────────────────────────────────────────


def test_wait_flag_is_registered():
    """--wait is a recognized flag."""
    assert "--wait)" in BURST


def test_detach_is_default():
    """WAIT=0 at declaration — drain-loop opt-in, not opt-out."""
    # Match both the constant and the branch that short-circuits on WAIT=0.
    assert "WAIT=0" in BURST
    assert '[[ "$WAIT" -eq 0 ]]' in BURST


def test_traps_only_installed_in_wait_mode():
    """EXIT/INT/TERM traps must be gated on --wait.

    Without this gate, closing the user's terminal (or Ctrl+C) would fire
    the trap and SIGTERM every worker the script just spawned — the exact
    opposite of what interactive users want.
    """
    # The three trap installs must live inside an `if [[ $WAIT -eq 1 ]]` block.
    idx = BURST.index('trap \'_cleanup EXIT\' EXIT')
    # Back-walk to the enclosing `if`.
    prefix = BURST[:idx]
    gate_idx = prefix.rindex('if [[ "$WAIT" -eq 1 ]]')
    # No `fi` between the gate and the traps — the traps are inside the block.
    assert "fi" not in BURST[gate_idx:idx]


def test_detach_branch_exits_zero():
    """Under detach default, the script exits 0 after spawning — no drain loop."""
    # Find the "Workers dispatched" message and confirm an `exit 0` follows.
    idx = BURST.index("Workers dispatched")
    tail = BURST[idx : idx + 1200]
    assert "exit 0" in tail, "detach branch missing exit 0"


def test_detach_branch_emits_monitoring_hints():
    """Detach message points users at sqlite3/pgrep/tail for progress watching."""
    idx = BURST.index("Workers dispatched")
    tail = BURST[idx : idx + 1200]
    for token in ("sqlite3", "pgrep -af stream-translate-worker", "tail -f"):
        assert token in tail, f"detach message missing monitoring hint: {token}"


# ──────────────────────────────────────────────────────────────────────────
# Venv-path enforcement (v8.3.8.6 prod incident)
# ──────────────────────────────────────────────────────────────────────────
#
# Prod ran v8.3.8.5 sampler-burst with PYTHON_BIN=${AUDIOBOOKS_HOME}/venv/bin/python
# (missing /library/) — the broken-path -x check fell back to /usr/bin/python3,
# which has no edge_tts module. Every TTS synthesis call failed and 4 of every
# 5 segments completed with VTT only, no audio_path. User clicked play and
# heard one segment then silence. Pin the canonical wiring + hard-fail.


def test_python_bin_uses_canonical_venv():
    """PYTHON_BIN MUST resolve from AUDIOBOOKS_VENV (the canonical export from
    audiobook-config.sh), not from a hardcoded ${AUDIOBOOKS_HOME}/venv/... path
    that drifts from the real venv location at ${AUDIOBOOKS_HOME}/library/venv."""
    assert 'PYTHON_BIN="${AUDIOBOOKS_VENV}/bin/python"' in BURST, (
        "PYTHON_BIN must derive from AUDIOBOOKS_VENV, not a hardcoded path"
    )
    # The drifted path (the v8.3.8.5 bug) must NOT appear.
    assert 'PYTHON_BIN="${AUDIOBOOKS_HOME}/venv/bin/python"' not in BURST, (
        "Bug-shaped PYTHON_BIN reintroduced — wrong by /library/"
    )


def test_python_bin_has_no_silent_python3_fallback():
    """The script MUST NOT fall back to system python3 when the venv path is
    missing — that path has no edge_tts module and produces silent TTS failures.
    Hard-fail with a clear diagnostic instead."""
    # The bug-shaped fallback was: [[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
    assert 'PYTHON_BIN="python3"' not in BURST, (
        "Silent fallback to system python3 reintroduced — would mask edge_tts ImportError"
    )


def test_preflight_rejects_missing_venv_python():
    """Pre-flight MUST exit non-zero with a clear message when PYTHON_BIN
    isn't executable. Spawning workers against a broken venv silently kills
    the audio pipeline."""
    # Look for the explicit -x guard and its error path.
    assert '! -x "$PYTHON_BIN"' in BURST, (
        "Pre-flight executable check on PYTHON_BIN missing"
    )
    # The error message must reference AUDIOBOOKS_VENV so the operator knows
    # which path to fix.
    assert "AUDIOBOOKS_VENV" in BURST, (
        "Pre-flight error message missing AUDIOBOOKS_VENV reference"
    )


def test_preflight_verifies_edge_tts_importable():
    """Pre-flight MUST refuse to spawn workers when the venv exists but has
    no edge_tts. A venv that lacks edge_tts produces VTT-only segments —
    every audio playback dead-ends after the systemd worker's one segment."""
    assert "import edge_tts" in BURST, (
        "Pre-flight edge_tts importability check missing"
    )
