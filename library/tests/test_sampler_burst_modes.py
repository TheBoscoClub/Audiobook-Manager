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
