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

# ─── Arg parsing ─────────────────────────────────────────────────────────────

_usage() {
    cat <<EOF
sampler-burst.sh — drain the streaming_segments backlog with N parallel workers

OPTIONS:
  --workers N     Number of parallel workers (default: 4, max: 16)
  --timeout DUR   Max wall-clock time (e.g. 30m, 2h, 10800s). Default: 6h.
  --force         Start even if the main systemd unit is active.
  -h, --help      Show this help.

ENVIRONMENT:
  API_BASE        Override the coordinator API base URL (default: http://localhost:5001)

Exit codes:
  0  queue drained successfully
  1  generic error
  2  invalid --workers / --timeout argument
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers)
            # Integer-only, 1..16. Reject anything else to prevent shell injection
            # via weird values reaching the `for` loop below.
            if [[ "${2:-}" =~ ^[0-9]+$ ]] && [[ "$2" -ge 1 ]] && [[ "$2" -le 16 ]]; then
                WORKERS="$2"
                shift 2
            else
                echo "error: --workers requires an integer 1..16" >&2
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
