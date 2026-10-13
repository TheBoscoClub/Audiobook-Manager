#!/bin/bash
# translation-drain.sh — scheduled whole-book translation drain (Audiobook-Manager-4j4).
#
# Run by audiobook-translation-drain.service (fired by
# audiobook-translation-drain.timer, which ships DISABLED — enabling it is an
# operator decision; see docs/MULTI-LANGUAGE-SETUP.md "Scheduled drain").
#
# Every newly imported book is queued into translation_queue for each
# non-English locale by the importer's post-insert hook
# (library/scanner/post_insert.py). This script drains that queue on a rented
# Vast.ai GPU node, then destroys the node:
#
#   lock -> count pending (0 => exit 0, nothing rented)
#        -> gpu-node.sh up -> gpu-budget-guard.sh --cap (background, at once)
#        -> gpu-node.sh tunnel -> wait for vLLM + whisper to answer (bounded)
#        -> batch-translate.py over the queue (bounded wall-clock)
#        -> [EXIT trap] stop batch, release its claims, tunnel --stop,
#           down --yes, wait for the guard to confirm 0 instances
#
# The teardown lives ONLY in the EXIT trap, so every path out of this script —
# success, any failure, SIGINT/SIGTERM — runs the same code and destroys the
# node. Any failure (including the wall-clock limit, the guard's spend cap
# firing, or an unverified teardown) exits non-zero so the unit shows failed.
#
# Configuration (lib/audiobook-config.sh defaults, override in audiobooks.conf):
#   AUDIOBOOKS_DRAIN_BUDGET_USD        per-run spend cap passed to the guard (10)
#   AUDIOBOOKS_DRAIN_MAX_MINUTES       wall-clock limit for the batch (240)
#   AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT seconds to wait for vLLM + whisper (1800)
#   AUDIOBOOKS_DRAIN_LABEL             Vast instance label owned by the drain
#                                      (abm-drain — distinct from the operator
#                                      CLI's abm-translate, so the drain never
#                                      destroys a hand-run session's node)
#   AUDIOBOOKS_DRAIN_STT_ONLY          true: subtitles (STT + MT) only, no TTS
#   AUDIOBOOKS_DRAIN_LOCK              flock file preventing overlapping runs
#   GPU_NODE_* (see gpu-node.sh)       offer selection, SSH key, ports
#
# Credential: VAST_API_KEY, or VAST_API_KEY_FILE pointing at a file holding it
# (the derive-service-secret drop-in sets the pointer at service start). The
# key is never read, logged or passed on a command line by this script; the
# check below only tests that the file is readable and non-empty.
#
# Test seams (unit tests only; never set them in production):
#   DRAIN_CONFIG_LIB, DRAIN_GPU_NODE_CMD, DRAIN_GUARD_CMD, DRAIN_BATCH_CMD,
#   DRAIN_PYTHON, DRAIN_POLL_SECONDS, DRAIN_MAX_SECONDS, DRAIN_TERM_GRACE,
#   DRAIN_GUARD_WAIT
#
# Exit codes: 0 done (or nothing pending), 1 failure, 75 another drain holds
# the lock, 130/143 interrupted (node still torn down).

# Teardown functions are reached only through the EXIT/INT/TERM traps.
# shellcheck disable=SC2329
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PY_DIR="${SCRIPT_DIR}/../library"

log() { printf '%s translation-drain: %s\n' "$(date '+%F %T')" "$*"; }
# exit (not return) so the EXIT trap tears down whatever was rented
fail() {
    log "ERROR: $*"
    exit 1
}

# --- configuration ----------------------------------------------------------
CONFIG_LIB="${DRAIN_CONFIG_LIB:-}"
if [[ -z "$CONFIG_LIB" ]]; then
    if [[ -f /usr/local/lib/audiobooks/audiobook-config.sh ]]; then
        CONFIG_LIB=/usr/local/lib/audiobooks/audiobook-config.sh
    else
        CONFIG_LIB="${SCRIPT_DIR}/../lib/audiobook-config.sh"
    fi
fi
[[ -f "$CONFIG_LIB" ]] || {
    log "ERROR: audiobook-config.sh not found ($CONFIG_LIB)"
    exit 1
}
# shellcheck source=/dev/null
source "$CONFIG_LIB"

BUDGET="${AUDIOBOOKS_DRAIN_BUDGET_USD:-10}"
MAX_MINUTES="${AUDIOBOOKS_DRAIN_MAX_MINUTES:-240}"
BOOTSTRAP_TIMEOUT="${AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT:-1800}"
LABEL="${AUDIOBOOKS_DRAIN_LABEL:-abm-drain}"
STT_ONLY="${AUDIOBOOKS_DRAIN_STT_ONLY:-true}"
LOCK_FILE="${AUDIOBOOKS_DRAIN_LOCK:-${AUDIOBOOKS_RUN_DIR}/translation-drain.lock}"
MT_PORT="${GPU_NODE_MT_PORT:-8000}"
WHISPER_PORT="${GPU_NODE_WHISPER_PORT:-8765}"

POLL="${DRAIN_POLL_SECONDS:-30}"
TERM_GRACE="${DRAIN_TERM_GRACE:-300}"
GUARD_WAIT="${DRAIN_GUARD_WAIT:-300}"
PY="${DRAIN_PYTHON:-${AUDIOBOOKS_VENV}/bin/python}"

read -r -a GPU_NODE <<<"${DRAIN_GPU_NODE_CMD:-${SCRIPT_DIR}/gpu-node.sh}"
read -r -a GUARD <<<"${DRAIN_GUARD_CMD:-${SCRIPT_DIR}/gpu-budget-guard.sh}"
if [[ -n "${DRAIN_BATCH_CMD:-}" ]]; then
    read -r -a BATCH <<<"$DRAIN_BATCH_CMD"
else
    BATCH=("$PY" "${SCRIPT_DIR}/batch-translate.py")
fi

# Both gpu-node.sh and gpu-budget-guard.sh scope everything to this label.
export GPU_NODE_LABEL="$LABEL"

# --- run state (read by the EXIT trap) --------------------------------------
NODE_REQUESTED=0
GUARD_PID=""
BATCH_PID=""
DRAIN_START_UTC=""

# Every child gets fd 9 (the lock) closed: a disowned tunnel ssh that
# inherited it would hold the lock long after this script exited.
gpu_node() { "${GPU_NODE[@]}" "$@" 9>&-; }

queue_query() {
    PYTHONPATH="${LIB_PY_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
        "$PY" -m localization.drain_queue --db "$AUDIOBOOKS_DATABASE" "$@" 9>&-
}

guard_alive() { [[ -n "$GUARD_PID" ]] && kill -0 "$GUARD_PID" 2>/dev/null; }

# Interruptible sleep: a trapped signal ends `wait` at once, where a
# foreground `sleep` would delay the trap until it finished.
nap() {
    sleep "$1" &
    wait $! 2>/dev/null
}

is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }

# --- teardown (EXIT trap only) ----------------------------------------------
stop_batch() {
    [[ -n "$BATCH_PID" ]] || return 0
    if kill -0 "$BATCH_PID" 2>/dev/null; then
        log "stopping batch (pid ${BATCH_PID}): SIGTERM, ${TERM_GRACE}s grace to finish the current book"
        kill -TERM "$BATCH_PID" 2>/dev/null
        local waited=0
        while kill -0 "$BATCH_PID" 2>/dev/null && ((waited < TERM_GRACE)); do
            sleep 1
            waited=$((waited + 1))
        done
        if kill -0 "$BATCH_PID" 2>/dev/null; then
            log "batch still running after ${TERM_GRACE}s — SIGKILL"
            kill -KILL "$BATCH_PID" 2>/dev/null
        fi
    fi
    wait "$BATCH_PID" 2>/dev/null
    BATCH_PID=""
}

release_claims() {
    [[ -n "$DRAIN_START_UTC" ]] || return 0
    local released
    if released="$(queue_query release --since "$DRAIN_START_UTC")"; then
        [[ "$released" == "0" ]] || log "returned ${released} interrupted queue row(s) to pending"
    else
        log "WARNING: could not release interrupted queue rows (they stay 'processing')"
    fi
}

# Wait (bounded) for the guard: after `down`, it sees 0 labeled instances and
# exits 0 — that exit IS the confirmation. Anything else falls back to an
# explicit one-shot check.
confirm_teardown() {
    local guard_rc=""
    if [[ -n "$GUARD_PID" ]]; then
        local waited=0
        while guard_alive && ((waited < GUARD_WAIT)); do
            sleep 1
            waited=$((waited + 1))
        done
        if guard_alive; then
            log "guard did not exit within ${GUARD_WAIT}s — stopping it"
            kill -TERM "$GUARD_PID" 2>/dev/null
        fi
        wait "$GUARD_PID" 2>/dev/null
        guard_rc=$?
        GUARD_PID=""
    fi
    if [[ "$guard_rc" == "0" ]]; then
        log "guard confirmed: no '${LABEL}' instances remain"
        return 0
    fi
    local report
    report="$("${GUARD[@]}" --cap "$BUDGET" --label "$LABEL" --check 9>&- 2>&1)"
    if [[ "$report" =~ (^|[[:space:]])instances=0([[:space:]]|$) ]]; then
        log "verified by check: no '${LABEL}' instances remain"
        return 0
    fi
    log "!!! TEARDOWN UNVERIFIED — check console.vast.ai for label '${LABEL}' NOW (check said: ${report:-nothing}) !!!"
    return 1
}

teardown() {
    local rc=0
    stop_batch
    release_claims
    if [[ "$NODE_REQUESTED" -eq 1 ]]; then
        log "teardown: closing tunnel"
        gpu_node tunnel --stop || log "WARNING: tunnel --stop failed"
        local attempt destroyed=0
        for attempt in 1 2 3; do
            log "teardown: destroying node (attempt ${attempt})"
            if gpu_node down --yes; then
                destroyed=1
                break
            fi
            sleep 5
        done
        [[ $destroyed -eq 1 ]] || {
            log "ERROR: gpu-node.sh down failed 3 times"
            rc=1
        }
        confirm_teardown || rc=1
    elif [[ -n "$GUARD_PID" ]]; then
        kill -TERM "$GUARD_PID" 2>/dev/null
    fi
    return "$rc"
}

on_exit() {
    local rc=$?
    trap '' INT TERM # teardown must not be interrupted
    trap - EXIT
    if ! teardown; then
        ((rc == 0)) && rc=1
    fi
    log "exit ${rc}"
    exit "$rc"
}

on_signal() {
    log "received SIG$1 — tearing down"
    if [[ "$1" == "INT" ]]; then exit 130; else exit 143; fi
}

trap on_exit EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

# --- steps ------------------------------------------------------------------
wait_healthy() {
    local deadline=$((SECONDS + BOOTSTRAP_TIMEOUT))
    log "waiting up to ${BOOTSTRAP_TIMEOUT}s for vLLM (:${MT_PORT}) and whisper (:${WHISPER_PORT})"
    while ((SECONDS < deadline)); do
        guard_alive || fail "budget guard exited during bootstrap (cap reached or guard failure)"
        if curl -fsS -o /dev/null --max-time 10 "http://127.0.0.1:${MT_PORT}/v1/models" 2>/dev/null \
            && curl -fsS -o /dev/null --max-time 10 "http://127.0.0.1:${WHISPER_PORT}/health" 2>/dev/null; then
            log "node healthy: vLLM and whisper answer through the tunnel"
            return 0
        fi
        nap "$POLL"
    done
    fail "bootstrap timeout: services did not answer within ${BOOTSTRAP_TIMEOUT}s"
}

run_batch() {
    local max_seconds="${DRAIN_MAX_SECONDS:-$((MAX_MINUTES * 60))}"
    local -a args=(--db "$AUDIOBOOKS_DATABASE" --library "$AUDIOBOOKS_LIBRARY")
    [[ "$STT_ONLY" == "true" ]] && args+=(--stt-only)
    log "starting batch (limit ${max_seconds}s): ${BATCH[*]##*/} ${args[*]}"
    AUDIOBOOKS_MT_ENDPOINT="http://127.0.0.1:${MT_PORT}" \
        AUDIOBOOKS_WHISPER_GPU_HOST=127.0.0.1 \
        AUDIOBOOKS_WHISPER_GPU_PORT="$WHISPER_PORT" \
        "${BATCH[@]}" "${args[@]}" 9>&- &
    BATCH_PID=$!
    local deadline=$((SECONDS + max_seconds))
    while kill -0 "$BATCH_PID" 2>/dev/null; do
        guard_alive || fail "budget guard exited mid-batch (cap reached or guard failure) — stopping"
        ((SECONDS < deadline)) || fail "wall-clock limit ${max_seconds}s reached — stopping batch"
        nap "$POLL"
    done
    wait "$BATCH_PID"
    local rc=$?
    BATCH_PID=""
    ((rc == 0)) || fail "batch exited ${rc}"
    log "batch finished"
}

main() {
    declare -F require_audiobooks_user >/dev/null && require_audiobooks_user "$@"

    if ! [[ "$BUDGET" =~ ^[0-9]+(\.[0-9]+)?$ ]] || ! awk -v b="$BUDGET" 'BEGIN { exit !(b > 0) }'; then
        fail "AUDIOBOOKS_DRAIN_BUDGET_USD must be a positive number (got '${BUDGET}')"
    fi
    is_uint "$MAX_MINUTES" || fail "AUDIOBOOKS_DRAIN_MAX_MINUTES must be an integer (got '${MAX_MINUTES}')"
    is_uint "$BOOTSTRAP_TIMEOUT" || fail "AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT must be an integer (got '${BOOTSTRAP_TIMEOUT}')"

    mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null
    exec 9>>"$LOCK_FILE" || fail "cannot open lock file ${LOCK_FILE}"
    if ! flock -n 9; then
        log "another drain holds ${LOCK_FILE} — not starting"
        exit 75
    fi

    local pending
    pending="$(queue_query count)" || fail "could not count pending translation_queue rows"
    is_uint "$pending" || fail "unexpected pending count '${pending}'"
    if [[ "$pending" -eq 0 ]]; then
        log "0 pending translation_queue rows — nothing to do, no node rented"
        exit 0
    fi
    log "${pending} pending translation_queue row(s)"

    if [[ -z "${VAST_API_KEY:-}" ]]; then
        [[ -n "${VAST_API_KEY_FILE:-}" ]] \
            || fail "no Vast.ai credential: set VAST_API_KEY_FILE (derive-service-secret drop-in) — not renting"
        [[ -r "$VAST_API_KEY_FILE" && -s "$VAST_API_KEY_FILE" ]] \
            || fail "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is missing, unreadable or empty — not renting"
    fi

    DRAIN_START_UTC="$(date -u '+%Y-%m-%d %H:%M:%S')"
    NODE_REQUESTED=1
    log "renting node (label '${LABEL}', spend cap \$${BUDGET})"
    gpu_node up || fail "gpu-node.sh up failed"

    "${GUARD[@]}" --cap "$BUDGET" --label "$LABEL" 9>&- &
    GUARD_PID=$!
    log "budget guard started (pid ${GUARD_PID}, cap \$${BUDGET})"

    gpu_node tunnel || fail "gpu-node.sh tunnel failed"
    wait_healthy
    run_batch
    log "drain complete"
}

# One compound command: bash parses the whole { ... } block before running
# it, so an upgrade that rewrites this file mid-run cannot feed it new lines.
{
    main "$@"
    exit $?
}
