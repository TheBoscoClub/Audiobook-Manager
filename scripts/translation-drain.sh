#!/bin/bash
# translation-drain.sh — scheduled whole-book translation drain (Audiobook-Manager-4j4).
#
# Run by audiobook-translation-drain.service (fired by
# audiobook-translation-drain.timer, which ships DISABLED — enabling it is an
# operator decision; see docs/MULTI-LANGUAGE-SETUP.md "Scheduled drain").
#
# Every newly imported book is queued into translation_queue for each
# non-English locale by the importer's post-insert hook. A row has two stages
# (localization/queue_schema.py): SUBTITLES (STT + MT, needs a GPU) and
# NARRATION (edge-tts, no GPU). This script:
#
#   lock -> release claims a dead run left -> count both stages
#   GPU stage (only if subtitle work is pending):
#     credential + free-port checks -> monthly budget (ledger) -> reserve
#     -> guard armed (--await, before the node exists) -> `up` (background
#     child, so a signal is handled at once) -> tunnel -> bounded health wait
#     -> batch --stage subtitles --drain-mode, watched for tunnel death,
#        endpoint death, guard exit and wall-clock
#     -> teardown: stop batch, release its claims, tunnel --stop,
#        down --yes (every abm-drain instance), verify 0 remain, record spend
#   NARRATION stage (host only, after the node is confirmed gone):
#     batch --stage narration --drain-mode (edge-tts), bounded wall-clock
#
# The same teardown runs from the EXIT trap, so every path out — success, any
# failure, SIGINT/SIGTERM — destroys the node. Anything that ends a stage early
# exits non-zero so the unit shows failed. An independent reaper
# (translation-drain-reaper.sh, its own timer) destroys abm-drain nodes this
# script cannot (SIGKILL, OOM, power loss).
#
# Configuration (lib/audiobook-config.sh defaults, override in audiobooks.conf):
#   AUDIOBOOKS_DRAIN_BUDGET_USD           per-run spend cap (10)
#   AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD   calendar-month ceiling, local time (100)
#   AUDIOBOOKS_DRAIN_MIN_RUN_USD          smallest cap worth renting for (1)
#   AUDIOBOOKS_DRAIN_MAX_MINUTES          wall-clock limit, subtitle batch (240)
#   AUDIOBOOKS_DRAIN_NARRATION_MAX_MINUTES  wall-clock limit, narration pass (120)
#   AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT    seconds to wait for vLLM + whisper (1800)
#   AUDIOBOOKS_DRAIN_LABEL                Vast label the drain owns (abm-drain-<host>)
#   AUDIOBOOKS_DRAIN_MAX_SUSPECT          runs losing the node with the same book
#                                         in flight before it is quarantined (3)
#   AUDIOBOOKS_DRAIN_MAX_ATTEMPTS         failed runs before a book is quarantined (3)
#   AUDIOBOOKS_DRAIN_LOCK                 flock file shared with the reaper
#   AUDIOBOOKS_DRAIN_LEDGER               append-only spend ledger (TSV)
#   GPU_NODE_* (see gpu-node.sh)          offer selection, SSH key, ports
#
# Credential: VAST_API_KEY, or VAST_API_KEY_FILE pointing at a file holding it
# (the derive-service-secret drop-in sets the pointer at service start). This
# script never reads, logs or passes the key; it only checks the file is
# readable and non-empty.
#
# Test seams (unit tests only; never set them in production):
#   DRAIN_CONFIG_LIB, DRAIN_GPU_NODE_CMD, DRAIN_GUARD_CMD, DRAIN_BATCH_CMD,
#   DRAIN_PYTHON, DRAIN_POLL_SECONDS, DRAIN_MAX_SECONDS,
#   DRAIN_NARRATION_MAX_SECONDS, DRAIN_TERM_GRACE, DRAIN_LOCK_WAIT,
#   DRAIN_HEALTH_EVERY, DRAIN_VERIFY_DELAY, DRAIN_REAPER_MAX_AGE
#
# Exit codes: 0 done (or nothing pending), 1 failure or refusal, 75 another
# drain (or the reaper) held the lock for DRAIN_LOCK_WAIT seconds, 130/143
# interrupted (node still torn down).

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
# GPU-stage failure: return to main, which tears down and still runs the
# (free) narration pass before exiting non-zero.
gfail() {
    log "ERROR: $*"
    return 1
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
MONTHLY="${AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD:-100}"
MIN_RUN="${AUDIOBOOKS_DRAIN_MIN_RUN_USD:-1}"
MAX_MINUTES="${AUDIOBOOKS_DRAIN_MAX_MINUTES:-240}"
NARRATION_MAX_MINUTES="${AUDIOBOOKS_DRAIN_NARRATION_MAX_MINUTES:-120}"
BOOTSTRAP_TIMEOUT="${AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT:-1800}"
LABEL="${AUDIOBOOKS_DRAIN_LABEL:-}" # default abm-drain-<host>, resolved in main
MAX_ATTEMPTS="${AUDIOBOOKS_DRAIN_MAX_ATTEMPTS:-3}"
MAX_SUSPECT="${AUDIOBOOKS_DRAIN_MAX_SUSPECT:-3}"
MARGIN_MINUTES="${AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES:-30}"
LOCK_FILE="${AUDIOBOOKS_DRAIN_LOCK:-${AUDIOBOOKS_RUN_DIR}/translation-drain.lock}"
LEDGER="${AUDIOBOOKS_DRAIN_LEDGER:-${AUDIOBOOKS_VAR_DIR}/translation-drain/spend-ledger.tsv}"
MT_PORT="${GPU_NODE_MT_PORT:-8000}"
WHISPER_PORT="${GPU_NODE_WHISPER_PORT:-8765}"
LIVE_FILE="${LEDGER}.live"
HEARTBEAT="${AUDIOBOOKS_DRAIN_REAPER_HEARTBEAT:-$(dirname "$LEDGER")/reaper-heartbeat}"
REAPER_TIMER=audiobook-translation-reaper.timer

POLL="${DRAIN_POLL_SECONDS:-30}"
HEALTH_EVERY="${DRAIN_HEALTH_EVERY:-60}"
TERM_GRACE="${DRAIN_TERM_GRACE:-300}"
LOCK_WAIT="${DRAIN_LOCK_WAIT:-120}"
VERIFY_DELAY="${DRAIN_VERIFY_DELAY:-5}"
REAPER_MAX_AGE="${DRAIN_REAPER_MAX_AGE:-1800}" # 3 reaper intervals
UP_WAIT="${DRAIN_UP_WAIT:-2500}"               # = drain_limits.UP_WAIT_SECONDS: bound on `up`, the guard's --await
PY="${DRAIN_PYTHON:-${AUDIOBOOKS_VENV}/bin/python}"
# An operator-set max price is honoured but shortens the node's lifetime so
# price x lifetime stays within the run's cap (drain_limits.plan).
EXPLICIT_MAX_PRICE="${GPU_NODE_MAX_PRICE:-}"
BATCH_SECONDS=""
LOST_JOB_FILE=""

read -r -a GPU_NODE <<<"${DRAIN_GPU_NODE_CMD:-${SCRIPT_DIR}/gpu-node.sh}"
read -r -a GUARD <<<"${DRAIN_GUARD_CMD:-${SCRIPT_DIR}/gpu-budget-guard.sh}"
if [[ -n "${DRAIN_BATCH_CMD:-}" ]]; then
    read -r -a BATCH <<<"$DRAIN_BATCH_CMD"
else
    BATCH=("$PY" "${SCRIPT_DIR}/batch-translate.py")
fi

# --- run state (read by the EXIT trap) --------------------------------------
NODE_REQUESTED=0
GUARD_PID=""
UP_PID=""
BATCH_PID=""
RUN_ID=""
CAP=""
CREDIT_BEFORE=""
MAX_ACCRUED="" # highest accrued-cost sample seen this run (never lowered)
MAX_DPH=""     # highest summed $/hr seen this run
RENT_START=""  # epoch just before `up` — uptime x rate is measured from here
TUNNEL_PIDFILE=""

# Every child gets fd 9 (the lock) closed: a disowned tunnel ssh that
# inherited it would hold the lock long after this script exited.
gpu_node() { "${GPU_NODE[@]}" "$@" 9>&-; }

pyrun() {
    PYTHONPATH="${LIB_PY_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$PY" "$@" 9>&-
}
queue_query() { pyrun -m localization.drain_queue --db "$AUDIOBOOKS_DATABASE" "$@"; }
ledger() { pyrun -m localization.drain_ledger --ledger "$LEDGER" "$@"; }

alive() { [[ -n "$1" ]] && kill -0 "$1" 2>/dev/null; }

# Interruptible sleep: a trapped signal ends `wait` at once, where a
# foreground `sleep` would delay the trap until it finished.
nap() {
    sleep "$1" &
    wait $! 2>/dev/null
}

is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }
is_money() { [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]]; }

# Anything listening on the tunnel's local ports (an operator's own tunnel to
# a different node, a local vLLM, anything) would answer for the drain's node.
ports_busy() {
    command -v ss >/dev/null 2>&1 || {
        log "ss not found — cannot prove ports ${MT_PORT}/${WHISPER_PORT} are free"
        return 0
    }
    local out
    out="$(ss -Htln "( sport = :${MT_PORT} or sport = :${WHISPER_PORT} )" 2>&1)" || return 0
    [[ -n "$out" ]]
}

healthy() {
    curl -fsS -o /dev/null --max-time 10 "http://127.0.0.1:${MT_PORT}/v1/models" 2>/dev/null \
        && curl -fsS -o /dev/null --max-time 10 "http://127.0.0.1:${WHISPER_PORT}/health" 2>/dev/null
}

tunnel_alive() {
    local pid
    pid="$(cat "$TUNNEL_PIDFILE" 2>/dev/null)" || return 1
    alive "$pid"
}

max_of() { awk -v a="${1:-0}" -v b="${2:-0}" 'BEGIN { printf "%.4f", (a > b ? a : b) }'; }

# Keep the HIGHEST sample, never the latest: once the guard or the reaper has
# destroyed the node, a teardown-time sample reads 0 and would erase the run's
# real cost from the ledger.
sample_accrued() {
    local out acc dph
    out="$(gpu_node billing 2>/dev/null)" || return 0
    acc="$(sed -n 's/.*accrued=\([0-9.]*\).*/\1/p' <<<"$out")"
    dph="$(sed -n 's/.*dph=\([0-9.]*\).*/\1/p' <<<"$out")"
    is_money "$acc" && MAX_ACCRUED="$(max_of "$MAX_ACCRUED" "$acc")"
    is_money "$dph" && MAX_DPH="$(max_of "$MAX_DPH" "$dph")"
    return 0
}

credit_now() {
    gpu_node billing 2>/dev/null | sed -n 's/^credit=\([0-9.]*\)$/\1/p'
}

# --- teardown (explicit at the end of the GPU stage, and from the EXIT trap) --
stop_child() { # pid label
    local pid="$1" what="$2"
    [[ -n "$pid" ]] || return 0
    if alive "$pid"; then
        log "stopping ${what} (pid ${pid}): SIGTERM, ${TERM_GRACE}s grace"
        kill -TERM "$pid" 2>/dev/null
        local waited=0
        while alive "$pid" && ((waited < TERM_GRACE)); do
            sleep 1
            waited=$((waited + 1))
        done
        if alive "$pid"; then
            log "${what} still running after ${TERM_GRACE}s — SIGKILL"
            kill -KILL "$pid" 2>/dev/null
        fi
    fi
    wait "$pid" 2>/dev/null
    return 0
}

release_claims() {
    local released
    if released="$(queue_query release --owner drain)"; then
        [[ "$released" == "0" ]] || log "returned ${released} interrupted queue row(s) to pending"
    else
        log "WARNING: could not release interrupted queue rows (they stay 'processing')"
    fi
}

instance_count() { gpu_node count 2>/dev/null; }

# Spend = max(highest accrued sample, uptime since RENT_START x highest rate,
# credit drop). A NEGATIVE credit drop (an operator top-up mid-run) is
# ignored rather than read as $0, and the credit drop alone is never trusted
# (Vast posts charges with a lag). With no accrued/rate evidence at all, no
# final is written and the full reservation stands.
record_spend() {
    [[ -n "$RUN_ID" ]] || return 0
    local after delta="" uptime_cost="" spend=""
    after="$(credit_now)"
    if is_money "$CREDIT_BEFORE" && is_money "$after"; then
        delta="$(awk -v b="$CREDIT_BEFORE" -v a="$after" 'BEGIN { d = b - a; if (d > 0) printf "%.4f", d }')"
    fi
    if is_money "$MAX_DPH" && [[ -n "$RENT_START" ]]; then
        uptime_cost="$(awk -v s="$RENT_START" -v n="$(date +%s)" -v d="$MAX_DPH" 'BEGIN { printf "%.4f", (n - s) / 3600 * d }')"
    fi
    if is_money "$MAX_ACCRUED" || is_money "$uptime_cost"; then
        spend="$(max_of "$(max_of "$MAX_ACCRUED" "$uptime_cost")" "$delta")"
        ledger final --run "$RUN_ID" --amount "$spend" \
            --note "max(accrued ${MAX_ACCRUED:-?}, uptime ${uptime_cost:-?}, credit-drop ${delta:-n/a})" \
            && log "spend recorded: \$${spend} (run ${RUN_ID})"
    else
        log "WARNING: spend not measurable — the \$${CAP} reservation stands in the ledger"
    fi
    rm -f "${LIVE_FILE:?}"
}

teardown_node() {
    stop_child "$BATCH_PID" "batch"
    BATCH_PID=""
    release_claims
    [[ "$NODE_REQUESTED" -eq 1 ]] || {
        alive "$GUARD_PID" && kill -TERM "$GUARD_PID" 2>/dev/null
        return 0
    }
    if alive "$UP_PID"; then
        log "teardown: interrupting gpu-node.sh up (pid ${UP_PID})"
        kill -TERM "$UP_PID" 2>/dev/null
        local w=0
        while alive "$UP_PID" && ((w < 30)); do
            sleep 1
            w=$((w + 1))
        done
        alive "$UP_PID" && kill -KILL "$UP_PID" 2>/dev/null
        wait "$UP_PID" 2>/dev/null
    fi
    UP_PID=""
    sample_accrued
    log "teardown: closing tunnel"
    gpu_node tunnel --stop || log "WARNING: tunnel --stop failed"
    # down destroys EVERY instance under the label, so a node whose id was
    # never learned (up interrupted mid-create) is found by label.
    local attempt count="" verified=0
    for attempt in 1 2 3; do
        log "teardown: destroying '${LABEL}' instances (attempt ${attempt})"
        gpu_node down --yes || log "WARNING: gpu-node.sh down reported failure"
        sleep "$VERIFY_DELAY"
        count="$(instance_count)"
        if [[ "$count" == "0" ]]; then
            verified=1
            break
        fi
        log "teardown: ${count:-unknown} '${LABEL}' instance(s) still listed"
    done
    if alive "$GUARD_PID"; then
        kill -TERM "$GUARD_PID" 2>/dev/null
        wait "$GUARD_PID" 2>/dev/null
    fi
    GUARD_PID=""
    if [[ $verified -ne 1 ]]; then
        log "!!! TEARDOWN UNVERIFIED — check console.vast.ai for label '${LABEL}' NOW (the reaper will retry) !!!"
        return 1
    fi
    log "verified: no '${LABEL}' instances remain"
    NODE_REQUESTED=0
    record_spend
    return 0
}

on_exit() {
    local rc=$?
    trap '' INT TERM # teardown must not be interrupted
    trap - EXIT
    if ! teardown_node; then
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

# --- GPU stage ----------------------------------------------------------------
wait_healthy() {
    local deadline=$((SECONDS + BOOTSTRAP_TIMEOUT))
    log "waiting up to ${BOOTSTRAP_TIMEOUT}s for vLLM (:${MT_PORT}) and whisper (:${WHISPER_PORT})"
    while ((SECONDS < deadline)); do
        alive "$GUARD_PID" || gfail "budget guard exited during bootstrap (cap reached or guard failure)" || return 1
        tunnel_alive || gfail "tunnel died during bootstrap" || return 1
        sample_accrued
        if healthy; then
            log "node healthy: vLLM and whisper answer through the tunnel"
            return 0
        fi
        nap "$POLL"
    done
    gfail "bootstrap timeout: services did not answer within ${BOOTSTRAP_TIMEOUT}s"
}

# run_batch STAGE MAX_SECONDS — watched for the GPU stage, bounded for both.
run_batch() {
    local stage="$1" max_seconds="$2"
    local -a args=(--db "$AUDIOBOOKS_DATABASE" --library "$AUDIOBOOKS_LIBRARY"
        --stage "$stage" --owner drain --drain-mode --max-attempts "$MAX_ATTEMPTS")
    if [[ "$stage" == "subtitles" ]]; then
        LOST_JOB_FILE="${TMPDIR:-/tmp}/translation-drain-lost.$$"
        rm -f "${LOST_JOB_FILE:?}"
        args+=(--lost-job-file "$LOST_JOB_FILE")
    fi
    log "starting ${stage} batch (limit ${max_seconds}s)"
    if [[ "$stage" == "subtitles" ]]; then
        AUDIOBOOKS_MT_ENDPOINT="http://127.0.0.1:${MT_PORT}" \
            AUDIOBOOKS_WHISPER_GPU_HOST=127.0.0.1 \
            AUDIOBOOKS_WHISPER_GPU_PORT="$WHISPER_PORT" \
            "${BATCH[@]}" "${args[@]}" 9>&- &
    else
        env -u AUDIOBOOKS_MT_ENDPOINT "${BATCH[@]}" "${args[@]}" 9>&- &
    fi
    BATCH_PID=$!
    local deadline=$((SECONDS + max_seconds)) next_health=$((SECONDS + HEALTH_EVERY)) misses=0 why=""
    while alive "$BATCH_PID"; do
        if ((SECONDS >= deadline)); then
            why="wall-clock limit ${max_seconds}s reached — stopping ${stage} batch"
            break
        fi
        if [[ "$stage" == "subtitles" ]]; then
            if ! alive "$GUARD_PID"; then
                why="budget guard exited mid-batch (cap reached or guard failure)"
                break
            fi
            if ! tunnel_alive; then
                why="tunnel died mid-batch — stopping batch"
                break
            fi
            if ((SECONDS >= next_health)); then
                next_health=$((SECONDS + HEALTH_EVERY))
                sample_accrued
                if healthy; then
                    misses=0
                else
                    misses=$((misses + 1))
                    log "health probe failed (${misses}/3)"
                    if ((misses >= 3)); then
                        why="node endpoints stopped answering — stopping batch"
                        break
                    fi
                fi
            fi
        fi
        nap "$POLL"
    done
    if [[ -n "$why" ]]; then
        log "ERROR: ${why}"
        # Stop the batch NOW, while the node is still in the state that made
        # us stop, so a book lost in flight can be judged before teardown.
        stop_child "$BATCH_PID" "batch"
        BATCH_PID=""
        [[ "$stage" == "subtitles" ]] && judge_lost_job
        return 1
    fi
    wait "$BATCH_PID"
    local rc=$?
    BATCH_PID=""
    case "$rc" in
        0) log "${stage} batch finished" ;;
        4)
            judge_lost_job
            gfail "batch lost the GPU endpoints (job released to pending)" || return 1
            ;;
        *) gfail "${stage} batch exited ${rc}" || return 1 ;;
    esac
}

# A book in flight when the endpoints vanished counts toward its 'suspect'
# quarantine ONLY if the loss was not our own doing (bd 2c9): the node still
# exists, the tunnel is still up and our guard is still running. If the node
# is gone (our guard, our reaper, the node's own dead-man switch, Vast) or the
# tunnel died (host-side), the book is blameless.
judge_lost_job() {
    local job=""
    [[ -n "$LOST_JOB_FILE" && -s "$LOST_JOB_FILE" ]] && job="$(head -c 32 "$LOST_JOB_FILE" | tr -dc '0-9')"
    rm -f "${LOST_JOB_FILE:?}"
    [[ -n "$job" ]] || return 0
    local count
    count="$(instance_count)"
    if [[ "$count" =~ ^[1-9][0-9]*$ ]] && tunnel_alive && alive "$GUARD_PID"; then
        local state
        state="$(queue_query suspect --job-id "$job" --max-suspect "$MAX_SUSPECT" \
            --error "GPU endpoints lost while this book was in flight; node still up")" \
            && log "queue row ${job} was in flight when the node's services died — suspect count raised (row now ${state})"
    else
        log "queue row ${job} was in flight when the node went away (node count ${count:-?}, tunnel/guard state) — not the book's fault, not counted"
    fi
}

# The independent reaper is the only cap that survives SIGKILL/OOM of this
# script; never rent without proof it is armed: its timer is active AND it
# completed a run WITH the Vast key, for THIS label, recently.
reaper_armed() {
    local active
    active="$(systemctl is-active "$REAPER_TIMER" 2>/dev/null)"
    [[ "$active" == "active" ]] || {
        log "reaper timer ${REAPER_TIMER} is '${active:-unknown}', not active"
        return 1
    }
    [[ -f "$HEARTBEAT" ]] || {
        log "reaper heartbeat ${HEARTBEAT} missing — the reaper has never run with a key"
        return 1
    }
    local beat_at beat_label age
    read -r beat_at beat_label <"$HEARTBEAT" || true
    is_uint "${beat_at:-}" || {
        log "reaper heartbeat unreadable"
        return 1
    }
    age=$(($(date +%s) - beat_at))
    if ((age > REAPER_MAX_AGE)); then
        log "reaper heartbeat is ${age}s old (limit ${REAPER_MAX_AGE}s)"
        return 1
    fi
    [[ "$beat_label" == "$LABEL" ]] || {
        log "reaper polices label '${beat_label}', not this drain's '${LABEL}'"
        return 1
    }
    return 0
}

# A run marker left by a run that died (we hold the lock, so it is not live).
# If its node is gone — the node's own dead-man switch fired while this host
# was down, or the reaper got it — charge its reservation (never less) before
# this run's budget is computed. If its node is somehow still up, do not rent:
# the reaper destroys it on its next pass.
settle_dead_run() {
    [[ -s "$LIVE_FILE" ]] || return 0
    local dead_run count amount
    dead_run="$(head -c 200 "$LIVE_FILE" | tr -d '[:space:]')"
    count="$(instance_count)"
    if [[ "$count" == "0" ]]; then
        amount="$(ledger settle --run "$dead_run" --note "drain: run died, node already gone")" \
            || gfail "could not settle dead run ${dead_run} in ${LEDGER} — not renting" || return 1
        log "settled dead run ${dead_run} at \$${amount} (its reservation floor)"
        rm -f "${LIVE_FILE:?}"
        return 0
    fi
    gfail "dead run ${dead_run} left ${count:-an unknown number of} '${LABEL}' instance(s) — not renting; the reaper destroys them" || return 1
}

gpu_stage() {
    reaper_armed || gfail "the independent reaper is not armed — not renting (enable ${REAPER_TIMER} with a Vast key)" || return 1
    if [[ -z "${VAST_API_KEY:-}" ]]; then
        [[ -n "${VAST_API_KEY_FILE:-}" ]] \
            || gfail "no Vast.ai credential: set VAST_API_KEY_FILE (derive-service-secret drop-in) — not renting" || return 1
        [[ -r "$VAST_API_KEY_FILE" && -s "$VAST_API_KEY_FILE" ]] \
            || gfail "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is missing, unreadable or empty — not renting" || return 1
    fi
    ! ports_busy || gfail "local port ${MT_PORT} or ${WHISPER_PORT} is already in use — refusing to rent (it could answer for another node)" || return 1

    settle_dead_run || return 1

    local verdict
    verdict="$(ledger budget --per-run "$BUDGET" --monthly "$MONTHLY" --min-run "$MIN_RUN" 2>&1)" \
        || gfail "monthly budget: ${verdict} — not renting; the queue stays pending" || return 1
    CAP="$(sed -n 's/^cap=\([0-9.]*\) .*/\1/p' <<<"$verdict")"
    is_money "$CAP" || gfail "could not parse budget verdict '${verdict}'" || return 1
    log "budget: ${verdict} (per-run \$${BUDGET}, monthly \$${MONTHLY})"

    # Vast credit is topped up MANUALLY (operator decision). A run the account
    # cannot pay for would be stopped by Vast mid-batch (credit exhaustion
    # killed the 2026-09 campaign); refuse it up front and say why, loudly.
    # An unreadable balance is a refusal too: it cannot be verified.
    local balance
    balance="$(credit_now)"
    is_money "$balance" \
        || gfail "could not read the Vast account balance — cannot verify the account can pay for a \$${CAP} run; not renting, queued books will wait" || return 1
    if awk -v b="$balance" -v c="$CAP" 'BEGIN { exit !(b < c) }'; then
        gfail "Vast balance \$${balance} below run cap \$${CAP} — top up the account manually; queued books will wait" || return 1
    fi
    CREDIT_BEFORE="$balance"
    log "Vast balance \$${balance} covers the \$${CAP} run cap"

    # Price and lifetime: with this host down only the node's own timer ends
    # it, so an orphan costs at most max price x lifetime — make that <= CAP.
    local -a price_arg=()
    [[ -n "$EXPLICIT_MAX_PRICE" ]] && price_arg=(--max-price "$EXPLICIT_MAX_PRICE")
    local limits
    limits="$(pyrun -m localization.drain_limits plan --cap "$CAP" --max-minutes "$MAX_MINUTES" \
        --bootstrap-seconds "$BOOTSTRAP_TIMEOUT" --margin-minutes "$MARGIN_MINUTES" "${price_arg[@]}" 2>&1)" \
        || gfail "node limits: ${limits} — not renting" || return 1
    GPU_NODE_MAX_LIFETIME="$(sed -n 's/.*lifetime=\([0-9]*\).*/\1/p' <<<"$limits")"
    GPU_NODE_MAX_PRICE="$(sed -n 's/.*max_price=\([0-9.]*\).*/\1/p' <<<"$limits")"
    BATCH_SECONDS="$(sed -n 's/.*batch_seconds=\([0-9]*\).*/\1/p' <<<"$limits")"
    if ! is_uint "$GPU_NODE_MAX_LIFETIME" || ! is_money "$GPU_NODE_MAX_PRICE" || ! is_uint "$BATCH_SECONDS"; then
        gfail "could not parse node limits '${limits}'" || return 1
    fi
    export GPU_NODE_MAX_LIFETIME GPU_NODE_MAX_PRICE
    log "node limits: ${limits} (on-node dead-man switch; offers above the price are not rented)"

    local run_id="drain-$(date +%s)-$$"
    # The live-run marker tells the reaper which reservation belongs to the
    # run holding the lock; written first so a reservation never exists
    # without it.
    mkdir -p "$(dirname "$LEDGER")" 2>/dev/null && chmod 0750 "$(dirname "$LEDGER")" 2>/dev/null
    printf '%s\n' "$run_id" >"$LIVE_FILE" || gfail "could not write ${LIVE_FILE} — not renting" || return 1
    ledger reserve --run "$run_id" --amount "$CAP" || gfail "could not write the spend ledger ${LEDGER} — not renting" || return 1
    RUN_ID="$run_id"
    NODE_REQUESTED=1

    # Armed before the node exists, so the cap covers create + boot too.
    "${GUARD[@]}" --cap "$CAP" --label "$LABEL" --await "$UP_WAIT" 9>&- &
    GUARD_PID=$!
    log "budget guard armed (pid ${GUARD_PID}, cap \$${CAP})"

    log "renting node (label '${LABEL}')"
    # Backgrounded DIRECTLY (not via the gpu_node function, which would put a
    # subshell in between that defers the TERM sent by the teardown) and
    # waited on, so a signal to this script runs its trap immediately rather
    # than when `up` returns (up to ~25 minutes later).
    RENT_START="$(date +%s)"
    "${GPU_NODE[@]}" up 9>&- &
    UP_PID=$!
    # `up` is bounded HERE too (drain_limits counts on it): a hung `up` must
    # not eat into the node lifetime the batch is promised.
    local up_deadline=$((SECONDS + UP_WAIT))
    while alive "$UP_PID"; do
        ((SECONDS < up_deadline)) || gfail "gpu-node.sh up still running after ${UP_WAIT}s" || return 1
        nap "$POLL"
    done
    wait "$UP_PID"
    local up_rc=$?
    UP_PID=""
    sample_accrued
    ((up_rc == 0)) || gfail "gpu-node.sh up failed (exit ${up_rc})" || return 1

    ! ports_busy || gfail "local port ${MT_PORT} or ${WHISPER_PORT} became busy — refusing to tunnel" || return 1
    gpu_node tunnel || gfail "gpu-node.sh tunnel failed" || return 1
    wait_healthy || return 1
    run_batch subtitles "${DRAIN_MAX_SECONDS:-$BATCH_SECONDS}" || return 1
    log "subtitle stage complete"
}

# --- main ---------------------------------------------------------------------
main() {
    declare -F require_audiobooks_user >/dev/null && require_audiobooks_user "$@"

    local k v
    for k in BUDGET_USD:"$BUDGET" MONTHLY_BUDGET_USD:"$MONTHLY" MIN_RUN_USD:"$MIN_RUN"; do
        v="${k#*:}"
        if ! is_money "$v" || ! awk -v b="$v" 'BEGIN { exit !(b > 0) }'; then
            fail "AUDIOBOOKS_DRAIN_${k%%:*} must be a positive number (got '${v}')"
        fi
    done
    for k in MAX_MINUTES NARRATION_MAX_MINUTES BOOTSTRAP_TIMEOUT MAX_ATTEMPTS MAX_SUSPECT MARGIN_MINUTES; do
        is_uint "${!k}" || fail "${k} must be a whole number (got '${!k}')"
    done

    [[ -n "$LABEL" ]] || LABEL="$(pyrun -m localization.drain_limits default-label)" \
        || fail "could not derive the default drain label"
    pyrun -m localization.drain_limits check-label "$LABEL" || fail "refusing label '${LABEL}'"
    # gpu-node.sh and gpu-budget-guard.sh scope everything to this label.
    export GPU_NODE_LABEL="$LABEL"
    TUNNEL_PIDFILE="${TMPDIR:-/tmp}/gpu-node-tunnel-${LABEL}.pid"

    # The derive-service-secret drop-in writes the key into this unit's
    # RuntimeDirectory and points VAST_API_KEY_FILE at it — but an
    # EnvironmentFile line VAST_API_KEY_FILE="" in audiobooks.conf overrides
    # that pointer. Fall back to the derived file so the pointer cannot be
    # blanked by accident.
    if [[ -z "${VAST_API_KEY:-}" && -z "${VAST_API_KEY_FILE:-}" &&
        -n "${RUNTIME_DIRECTORY:-}" && -s "${RUNTIME_DIRECTORY%%:*}/vast-api-key" ]]; then
        export VAST_API_KEY_FILE="${RUNTIME_DIRECTORY%%:*}/vast-api-key"
    fi

    mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null
    exec 9>>"$LOCK_FILE" || fail "cannot open lock file ${LOCK_FILE}"
    if ! flock -w "$LOCK_WAIT" 9; then
        log "another drain or the reaper held ${LOCK_FILE} for ${LOCK_WAIT}s — not starting"
        exit 75
    fi

    # We hold the lock, so any 'drain' claim is left over from a dead run;
    # an operator batch whose process is gone left its claims too.
    release_claims
    local stale
    stale="$(queue_query release-stale)" && [[ "$stale" == "0" ]] \
        || log "released ${stale:-?} stale claim(s) of owners that are gone"

    local subs narr rc=0
    subs="$(queue_query count --stage subtitles)" || fail "could not count pending subtitle work"
    narr="$(queue_query count --stage narration)" || fail "could not count pending narration work"
    if ! is_uint "$subs" || ! is_uint "$narr"; then
        fail "unexpected pending counts '${subs}'/'${narr}'"
    fi
    if [[ "$subs" -eq 0 && "$narr" -eq 0 ]]; then
        log "0 pending translation_queue rows — nothing to do, no node rented"
        exit 0
    fi
    log "pending: ${subs} subtitle row(s) (GPU), ${narr} narration row(s) (host)"

    if [[ "$subs" -gt 0 ]]; then
        gpu_stage || rc=1
        # Always tear down here, before any host-side work: the node must be
        # gone (and verified gone) before the run does anything else.
        if ! teardown_node; then
            exit 1 # unverified teardown: skip narration, alert now
        fi
    fi

    narr="$(queue_query count --stage narration)" || fail "could not count pending narration work"
    if [[ "$narr" -gt 0 ]]; then
        log "narration pass (host, edge-tts): ${narr} row(s)"
        run_batch narration "${DRAIN_NARRATION_MAX_SECONDS:-$((NARRATION_MAX_MINUTES * 60))}" || rc=1
        stop_child "$BATCH_PID" "narration batch"
        BATCH_PID=""
        release_claims
    fi
    if ((rc == 0)); then log "drain complete"; else log "drain finished with errors"; fi
    exit "$rc"
}

# One compound command: bash parses the whole { ... } block before running
# it, so an upgrade that rewrites this file mid-run cannot feed it new lines.
{
    main "$@"
}
