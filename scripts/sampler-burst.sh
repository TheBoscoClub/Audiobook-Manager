#!/bin/bash
# sampler-burst.sh — fan-out parallel stream-translate workers to drain a backlog
#
# Why this exists:
#   When a sampler reconcile enqueues dozens of pending segments, the single
#   audiobook-stream-translate.service systemd unit processes them serially.
#   With dual-farm routing (Bundle B), parallel workers naturally spread
#   across whichever STT providers the operator configured (RunPod + Vast.ai,
#   local GPU, etc.) giving an N× speedup on backfills without any code
#   changes to the worker. This script spawns N stream-translate-worker.py
#   processes, waits until the sampler_jobs + streaming_segments queues are
#   both drained, then gracefully signals them to exit.
#
# Usage:
#   sudo -u audiobooks /opt/audiobooks/scripts/sampler-burst.sh
#   sudo -u audiobooks /opt/audiobooks/scripts/sampler-burst.sh --workers 6
#   sudo -u audiobooks /opt/audiobooks/scripts/sampler-burst.sh --workers 2 --timeout 30m
#
# Safety:
#   - The default systemd unit audiobook-stream-translate.service should be
#     STOPPED while burst runs (otherwise you get N+1 workers competing). The
#     script refuses to start if the systemd unit is active and --force was
#     not passed.
#   - Workers exit on SIGTERM after finishing their current segment.
#   - The script tears down all spawned workers on SIGINT/SIGTERM/EXIT.

set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────────────

# Two-layer sourcing mirrors the systemd units (Environment= then
# EnvironmentFile=): canonical DEFAULTS first (audiobook-config.sh), then
# OPERATOR OVERRIDES on top (/etc/audiobooks/audiobooks.conf — contains the
# library path override plus STT/DeepL/TTS backend credentials).
#
# Without this, burst workers inherit only the bare defaults and dispatch to
# /srv/audiobooks/Library with no provider configured → every segment fails.

CONFIG_LIB="/usr/local/lib/audiobooks/audiobook-config.sh"
if [[ ! -f "$CONFIG_LIB" ]]; then
    # Dev-tree fallback (running from checkout).
    _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${_here}/../lib/audiobook-config.sh" ]]; then
        CONFIG_LIB="${_here}/../lib/audiobook-config.sh"
    fi
fi
# shellcheck source=/dev/null
[[ -f "$CONFIG_LIB" ]] && source "$CONFIG_LIB"

# Operator overrides + provider credentials. set -a exports every assignment
# so the spawned Python workers inherit AUDIOBOOKS_RUNPOD_*, AUDIOBOOKS_VASTAI_*,
# AUDIOBOOKS_DEEPL_API_KEY, etc.
CONFIG_ENV="${AUDIOBOOKS_CONFIG:-/etc/audiobooks/audiobooks.conf}"
if [[ -f "$CONFIG_ENV" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$CONFIG_ENV"
    set +a
fi

: "${AUDIOBOOKS_HOME:=/opt/audiobooks}"
: "${AUDIOBOOKS_VAR_DIR:=/var/lib/audiobooks}"
: "${AUDIOBOOKS_LIBRARY:=/srv/audiobooks/Library}"
: "${AUDIOBOOKS_DATABASE:=${AUDIOBOOKS_VAR_DIR}/db/audiobooks.db}"

WORKER_SCRIPT="${AUDIOBOOKS_HOME}/scripts/stream-translate-worker.py"
API_BASE="${API_BASE:-http://localhost:5001}"
PYTHON_BIN="${AUDIOBOOKS_HOME}/venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

WORKERS=4
TIMEOUT_SEC=$((6 * 3600)) # 6h default ceiling
FORCE=0
# Mode: "replace" (default — gracefully terminate existing burst workers
# before spawning the new set) or "add" (leave existing alone, stack more
# workers on top, subject to the max-total cap).
MODE="replace"
# Max total stream-translate-worker.py processes allowed on this host,
# INCLUDING the systemd audiobook-stream-translate.service worker. Hitting
# the cap means new requests are clamped to the remaining slots, with a
# note explaining what was skipped.
MAX_WORKERS_TOTAL=16

# ─── Arg parsing ─────────────────────────────────────────────────────────────

_usage() {
    cat <<EOF
sampler-burst.sh — drain the streaming_segments backlog with N parallel workers

POOL-SIZING OPTIONS (mutually exclusive):
  --workers N       REPLACE semantics (default mode). Gracefully SIGTERM any
                    existing burst workers, wait for their current segment
                    to finish, then spawn N fresh workers. Default: 4.
  --add-workers N   ADD semantics. Leave existing burst workers running and
                    spawn N additional workers on top of them.

  Cap: total stream-translate-worker.py processes across the host is
  capped at $MAX_WORKERS_TOTAL (including the systemd on-demand worker). If
  the requested N would push total above the cap, N is clamped to the
  available slots and a note is emitted.

OTHER OPTIONS:
  --timeout DUR   Max wall-clock time (e.g. 30m, 2h, 10800s). Default: 6h.
  --force         Start even if the main systemd unit is active.
  -h, --help      Show this help.

ENVIRONMENT:
  API_BASE        Override the coordinator API base URL (default: http://localhost:5001)

Exit codes:
  0  queue drained successfully
  1  generic error
  2  invalid --workers / --add-workers / --timeout argument
  3  timeout reached with work still in queue
EOF
}

# Convert "30m", "2h", "300s" → seconds.
_parse_duration() {
    local s="$1"
    if [[ "$s" =~ ^([0-9]+)([smh]?)$ ]]; then
        local n="${BASH_REMATCH[1]}" u="${BASH_REMATCH[2]}"
        case "$u" in
            "" | s) echo "$n" ;;
            m) echo $((n * 60)) ;;
            h) echo $((n * 3600)) ;;
        esac
    else
        echo ""
    fi
}

_mode_set=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers)
            if [[ $_mode_set -eq 1 ]]; then
                echo "error: --workers and --add-workers are mutually exclusive" >&2
                exit 2
            fi
            # Integer-only, 1..16. Reject anything else to prevent shell injection
            # via weird values reaching the `for` loop below.
            if [[ "${2:-}" =~ ^[0-9]+$ ]] && [[ "$2" -ge 1 ]] && [[ "$2" -le $MAX_WORKERS_TOTAL ]]; then
                WORKERS="$2"
                MODE="replace"
                _mode_set=1
                shift 2
            else
                echo "error: --workers requires an integer 1..$MAX_WORKERS_TOTAL" >&2
                exit 2
            fi
            ;;
        --add-workers)
            if [[ $_mode_set -eq 1 ]]; then
                echo "error: --workers and --add-workers are mutually exclusive" >&2
                exit 2
            fi
            if [[ "${2:-}" =~ ^[0-9]+$ ]] && [[ "$2" -ge 1 ]] && [[ "$2" -le $MAX_WORKERS_TOTAL ]]; then
                WORKERS="$2"
                MODE="add"
                _mode_set=1
                shift 2
            else
                echo "error: --add-workers requires an integer 1..$MAX_WORKERS_TOTAL" >&2
                exit 2
            fi
            ;;
        --timeout)
            local_dur=$(_parse_duration "${2:-}")
            if [[ -n "$local_dur" ]] && [[ "$local_dur" -gt 0 ]]; then
                TIMEOUT_SEC="$local_dur"
                shift 2
            else
                echo "error: --timeout must be a duration like 30m / 2h / 300s" >&2
                exit 2
            fi
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h | --help)
            _usage
            exit 0
            ;;
        *)
            echo "error: unknown argument '$1'" >&2
            _usage >&2
            exit 2
            ;;
    esac
done

# ─── Pre-flight ──────────────────────────────────────────────────────────────

if [[ ! -f "$WORKER_SCRIPT" ]]; then
    echo "error: stream-translate-worker.py not found at $WORKER_SCRIPT" >&2
    exit 1
fi

if systemctl is-active audiobook-stream-translate.service &>/dev/null; then
    if [[ "$FORCE" -ne 1 ]]; then
        cat >&2 <<EOF
error: audiobook-stream-translate.service is currently active.

Running burst workers alongside the systemd unit would cause N+1 workers
competing for the same queue. Stop the unit first:

  sudo systemctl stop audiobook-stream-translate.service

Or pass --force to acknowledge and proceed anyway.
EOF
        exit 1
    fi
    echo "warning: audiobook-stream-translate.service is active — --force acknowledged"
fi

# ─── User gate ───────────────────────────────────────────────────────────────
# Defined in audiobook-config.sh (canonical shared helper). Fails fast with
# a useful diagnostic instead of spawning workers that can't write the DB.
require_audiobooks_user "$@"

# ─── Pool sizing (existing workers + cap enforcement) ────────────────────────
#
# Count existing stream-translate-worker.py processes across the host and
# decide how many new workers to spawn. The cap ($MAX_WORKERS_TOTAL) applies
# to the TOTAL running worker count, including the systemd audiobook-stream-
# translate.service worker when it's active.
#
# Discovery heuristic: any stream-translate-worker.py process that isn't
# the systemd unit's MainPID is considered a "burst worker" — this covers
# children of a prior sampler-burst invocation (whose parent may still be
# alive, may have exited, or may have been re-parented to init via nohup).

_systemd_worker_pid() {
    if ! systemctl is-active audiobook-stream-translate.service &>/dev/null; then
        echo ""
        return
    fi
    local pid
    pid=$(systemctl show -p MainPID --value audiobook-stream-translate.service 2>/dev/null)
    [[ "$pid" == "0" ]] && pid=""
    echo "$pid"
}

_existing_burst_worker_pids() {
    local sysd_pid="$1"
    local pid
    for pid in $(pgrep -f 'stream-translate-worker\.py' 2>/dev/null); do
        [[ "$pid" != "$sysd_pid" ]] && echo "$pid"
    done
}

_terminate_workers() {
    # Usage: _terminate_workers GRACE_SEC pid1 pid2 ...
    # SIGTERMs each pid, polls up to GRACE_SEC for graceful exit (lets the
    # worker finish its current segment), then SIGKILLs survivors.
    local grace="$1"
    shift
    local pid
    for pid in "$@"; do
        [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && kill -TERM "$pid" 2>/dev/null || true
    done
    while ((grace > 0)); do
        local alive=0
        for pid in "$@"; do
            [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && alive=$((alive + 1))
        done
        [[ $alive -eq 0 ]] && return 0
        sleep 1
        grace=$((grace - 1))
    done
    for pid in "$@"; do
        [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    done
}

sysd_pid="$(_systemd_worker_pid)"
mapfile -t existing_worker_pids < <(_existing_burst_worker_pids "$sysd_pid")
existing_count=${#existing_worker_pids[@]}
sysd_count=0
[[ -n "$sysd_pid" ]] && sysd_count=1

# ─── Cap math ────────────────────────────────────────────────────────────────
# REPLACE: we'll SIGTERM the existing burst workers before spawning new,
# so the available slot budget = MAX - systemd (existing burst workers
# don't subtract because they're about to go away).
# ADD: existing stay, so available = MAX - systemd - existing.
if [[ "$MODE" == "add" ]]; then
    available_slots=$((MAX_WORKERS_TOTAL - sysd_count - existing_count))
else
    available_slots=$((MAX_WORKERS_TOTAL - sysd_count))
fi

# Pool-sizing report
echo "Pool state: existing burst=$existing_count, systemd=$sysd_count, requested=$WORKERS (mode=$MODE, cap=$MAX_WORKERS_TOTAL)"

if [[ $available_slots -le 0 ]]; then
    echo "note: pool already at cap ($sysd_count systemd + $existing_count burst = $MAX_WORKERS_TOTAL). No new workers spawned."
    exit 0
fi

target_workers=$WORKERS
if [[ $WORKERS -gt $available_slots ]]; then
    echo "note: requested $WORKERS new worker(s), but cap=$MAX_WORKERS_TOTAL with $sysd_count systemd + $existing_count existing leaves $available_slots slot(s). Spawning $available_slots."
    target_workers=$available_slots
fi
WORKERS=$target_workers

# ─── Replace-mode termination ────────────────────────────────────────────────
# Before spawning fresh workers, gracefully SIGTERM the existing set so each
# finishes its current segment and exits. 90s grace window covers a cold-GPU
# segment (~60s) with headroom.
if [[ "$MODE" == "replace" ]] && [[ $existing_count -gt 0 ]]; then
    echo "Replace mode: SIGTERMing $existing_count existing burst worker(s); grace=90s for in-flight segments..."
    _terminate_workers 90 "${existing_worker_pids[@]}"
    echo "  existing workers terminated"
fi

# ─── Spawn workers ───────────────────────────────────────────────────────────

declare -a WORKER_PIDS=()

_cleanup() {
    local sig="${1:-EXIT}"
    [[ "$sig" != "EXIT" ]] && echo "" >&2 && echo "Received $sig — shutting down workers..." >&2
    for pid in "${WORKER_PIDS[@]:-}"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    # Give them 30s to finish their current segment.
    local grace=30
    while ((grace > 0)); do
        local alive=0
        for pid in "${WORKER_PIDS[@]:-}"; do
            [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && alive=$((alive + 1))
        done
        [[ "$alive" -eq 0 ]] && break
        sleep 1
        grace=$((grace - 1))
    done
    # Anyone still alive: SIGKILL.
    for pid in "${WORKER_PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    done
}
trap '_cleanup SIGINT' INT
trap '_cleanup SIGTERM' TERM
trap '_cleanup EXIT' EXIT

echo "Spawning ${WORKERS} stream-translate worker(s)..."
# Per-invocation log dir so concurrent bursts (and different users)
# don't collide on /tmp/sampler-burst-${i}.log — v8.3.8.2 had a flat
# naming scheme that would fail on the second user with "Permission
# denied" when prior-run files weren't group-writable.
LOG_DIR="${TMPDIR:-/tmp}/sampler-burst-$$"
mkdir -p "$LOG_DIR"
echo "  log dir: $LOG_DIR"
for i in $(seq 1 "$WORKERS"); do
    "$PYTHON_BIN" "$WORKER_SCRIPT" \
        --db "$AUDIOBOOKS_DATABASE" \
        --library "$AUDIOBOOKS_LIBRARY" \
        --api-base "$API_BASE" \
        >"${LOG_DIR}/worker-${i}.log" 2>&1 &
    WORKER_PIDS+=("$!")
    echo "  worker $i: PID $!"
done

# ─── Wait for drain ──────────────────────────────────────────────────────────

_pending_count() {
    # Count pending+processing sampler segments. On DB-query failure, echo
    # a sentinel that the caller treats as "don't know; keep waiting" (the
    # wall-clock timeout is the real safety net). A prior version echoed 0
    # on failure — which the loop interpreted as "drained" and exited
    # immediately, masking permission problems (see v8.3.8.2 regression
    # where a user without DB group membership saw "drained after 0s"
    # even though 21k segments were still pending).
    local n
    if ! n=$(sqlite3 "$AUDIOBOOKS_DATABASE" "
        SELECT COUNT(*) FROM streaming_segments
        WHERE origin='sampler'
          AND state IN ('pending','processing','in_flight')
    " 2>/dev/null); then
        # Query failed — return a large positive number so the main loop
        # keeps polling instead of exiting early. The pre-flight check
        # above should have caught permission issues, so hitting this is
        # a transient DB-locked / disk-read anomaly; re-polling recovers.
        echo "999999"
        return
    fi
    echo "$n"
}

start_ts="$(date +%s)"
echo "Waiting for queue drain... (timeout ${TIMEOUT_SEC}s)"
while true; do
    pending="$(_pending_count)"
    elapsed=$(($(date +%s) - start_ts))
    if [[ "$pending" -eq 0 ]]; then
        echo "Queue drained after ${elapsed}s"
        break
    fi
    if [[ "$elapsed" -ge "$TIMEOUT_SEC" ]]; then
        echo "Timeout (${TIMEOUT_SEC}s) reached with $pending segment(s) still pending"
        exit 3
    fi
    echo "  ${pending} segment(s) pending (elapsed ${elapsed}s)"
    sleep 10
done

echo "Done."
exit 0
