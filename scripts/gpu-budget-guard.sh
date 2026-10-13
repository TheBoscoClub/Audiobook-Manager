#!/bin/bash
# gpu-budget-guard.sh — hard spend cap and verified teardown for rented GPU nodes.
#
# TWO USES (per .claude/rules/upgrade-consistency.md "New-Script Wiring
# Enforcement"): (1) operator CLI, started by hand alongside a gpu-node.sh
# session from the project tree; (2) installed worker — install.sh / upgrade.sh
# copy it to ${AUDIOBOOKS_HOME}/scripts/ (install-manifest.sh
# CANONICAL_WORKERS) and scripts/translation-drain.sh starts it with --cap
# right after `gpu-node.sh up` and waits for it to confirm teardown. It has no
# unit of its own: it lives exactly as long as the node it guards.
#
# CREDENTIALS: VAST_API_KEY, else VAST_API_KEY_FILE (pointer to a file holding
# the key; set-but-unreadable is an error), else — operator CLI only — re-exec
# under ~/.claude/bin/with-secret. The key is never printed or put in argv.
#
# WHY THIS EXISTS
#   A translation campaign on 2026-09-11/12 was estimated at $12-20 and spent
#   $72.35 — the entire prepaid balance. Two causes, both of which this script
#   removes:
#     1. An instance billed $6.74/hr against an offer listing $2.70. Nothing
#        watched the ACTUAL rate after creation. ($33.46 of the overrun.)
#     2. Estimates were built on a 145x-realtime STT benchmark while real
#        end-to-end throughput is 25-35x. Nothing compared spend against a
#        ceiling as the run progressed, so the overrun was invisible until
#        the balance hit zero.
#
#   The lesson generalises: a budget you do not ENFORCE is a wish. This guard
#   polls actual billed spend and destroys the instance at the cap, whether or
#   not any human or agent is watching.
#
# USAGE
#   scripts/gpu-budget-guard.sh --max-idle 15        # destroy after 15 idle minutes
#   scripts/gpu-budget-guard.sh --cap 35             # per-session spend ceiling
#   scripts/gpu-budget-guard.sh --cap 35 --floor 64  # ...and a credit floor
#   scripts/gpu-budget-guard.sh --cap 35 --check     # one-shot report, no action
#   scripts/gpu-budget-guard.sh --cap 10 --await 1500 # arm before `up`: wait up to
#                                                     # 1500s for the instance to appear
#
#   --cap   is arithmetic I compute (uptime x rate, summed over labeled
#           instances). It resets when an instance is replaced, so it bounds
#           ONE session.
#   --max-idle destroys the node once no translation work has been running
#           locally for N consecutive minutes. This is the one that saves
#           money rather than merely bounding the loss: on 2026-09-13 a
#           repair finished in 25 minutes and the node then sat idle for
#           NINE HOURS while its operator waited to be asked whether to
#           tear it down — $0.90 of work, $18.75 of nothing. Re-renting
#           takes about two minutes, so an idle GPU is never worth holding.
#   --floor is the account's real credit balance as Vast reports it. It is
#           cumulative, survives node cycling, and needs no assumption about
#           the rate — which is precisely the blind spot that let a $6.74/hr
#           instance run against a $2.70 listing. PREFER --floor for a
#           campaign; use both for belt and braces.
#
# TEARDOWN IS VERIFIED, NOT ASSUMED
#   After issuing DELETE the guard re-queries the API up to VERIFY_TRIES times
#   and only reports success when the instance is GONE from the account
#   listing. An instance in "exited" state is NOT gone — its disk still bills —
#   so exited counts as still-present here.

set -uo pipefail

API_BASE="${VAST_API_BASE:-https://console.vast.ai/api/v0}"
LABEL="${GPU_NODE_LABEL:-abm-translate}"
CAP=""
FLOOR=""
MAX_IDLE=""
CHECK_ONLY=0
AWAIT=0
POLL_SECONDS="${GUARD_POLL_SECONDS:-120}"
VERIFY_TRIES=5
LOG="${GUARD_LOG:-/tmp/gpu-budget-guard.log}"

die() {
    printf 'guard: ERROR: %s\n' "$*" >&2
    exit 1
}
say() { printf '%s guard: %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cap)
            CAP="${2:-}"
            shift 2
            ;;
        --floor)
            FLOOR="${2:-}"
            shift 2
            ;;
        --max-idle)
            MAX_IDLE="${2:-}"
            shift 2
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --label)
            LABEL="${2:-}"
            shift 2
            ;;
        --await)
            AWAIT="${2:-}"
            shift 2
            ;;
        *) die "unknown argument: $1" ;;
    esac
done
[[ -n "$CAP" ]] || die "--cap DOLLARS is required"
[[ "$AWAIT" =~ ^[0-9]+$ ]] || die "--await takes whole seconds"

if [[ -z "${VAST_API_KEY:-}" && -n "${VAST_API_KEY_FILE:-}" ]]; then
    [[ -r "$VAST_API_KEY_FILE" ]] || die "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is not readable"
    VAST_API_KEY="$(tr -d '[:space:]' <"$VAST_API_KEY_FILE")"
    [[ -n "$VAST_API_KEY" ]] || die "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is empty"
fi
if [[ -z "${VAST_API_KEY:-}" ]]; then
    WITH_SECRET="${HOME}/.claude/bin/with-secret"
    [[ -x "$WITH_SECRET" ]] || die "VAST_API_KEY unset and with-secret not found"
    reexec_args=(--cap "$CAP" --label "$LABEL")
    [[ -n "$FLOOR" ]] && reexec_args+=(--floor "$FLOOR")
    [[ -n "$MAX_IDLE" ]] && reexec_args+=(--max-idle "$MAX_IDLE")
    [[ $CHECK_ONLY -eq 1 ]] && reexec_args+=(--check)
    [[ "$AWAIT" != "0" ]] && reexec_args+=(--await "$AWAIT")
    exec "$WITH_SECRET" VAST_API_KEY -- "$0" "${reexec_args[@]}"
fi

command -v jq >/dev/null || die "jq required"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/guard.XXXXXX")" || die "mktemp failed"
trap 'rm -rf "$WORKDIR"' EXIT
CURL_CONF="${WORKDIR}/hdr"
umask 077
printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
    "$VAST_API_KEY" >"$CURL_CONF"

api() {
    local method="$1" path="$2"
    curl -sS --fail-with-body --max-time 45 -K "$CURL_CONF" -X "$method" "${API_BASE}${path}"
}

# Every labeled instance in ANY state — an exited instance still bills storage.
labeled() {
    api GET "/instances/" | jq --arg l "$LABEL" '[.instances[]? | select(.label == $l)]'
}

# Is any local translation work in flight? Patterns are bracketed so the
# check never matches the shell running it (a self-match once spun a watcher
# for three idle hours on 2026-09-12).
work_running() {
    pgrep -f "[b]atch-translate.py" >/dev/null 2>&1 && return 0
    pgrep -f "[s]tream-translate-worker.py" >/dev/null 2>&1 && return 0
    pgrep -f "[s]ampler-reconcile.py" >/dev/null 2>&1 && return 0
    return 1
}

credit_now() {
    api GET "/users/current/" | jq -r '.credit // 0'
}

spend_now() {
    labeled | jq -r --argjson now "$(date +%s)" '
        [.[] | ((($now - (.start_date // $now)) / 3600) * (.dph_total // 0))] | add // 0'
}

destroy_all_verified() {
    local ids id try remaining
    ids="$(labeled | jq -r '.[].id')"
    [[ -n "$ids" ]] || {
        say "nothing to destroy"
        return 0
    }
    for id in $ids; do
        say "DESTROYING instance $id"
        api DELETE "/instances/${id}/" >/dev/null 2>&1 || say "delete call errored for $id (will verify anyway)"
    done
    for ((try = 1; try <= VERIFY_TRIES; try++)); do
        sleep 5
        remaining="$(labeled | jq 'length')"
        if [[ "$remaining" == "0" ]]; then
            say "VERIFIED: 0 '${LABEL}' instances remain (attempt ${try})"
            return 0
        fi
        say "verify attempt ${try}: ${remaining} still present, retrying delete"
        for id in $(labeled | jq -r '.[].id'); do
            api DELETE "/instances/${id}/" >/dev/null 2>&1 || true
        done
    done
    say "!!! TEARDOWN UNVERIFIED after ${VERIFY_TRIES} attempts — CHECK console.vast.ai MANUALLY !!!"
    return 1
}

report() {
    local spend count credit
    spend="$(spend_now)"
    count="$(labeled | jq 'length')"
    credit="$(api GET "/users/current/" | jq -r '.credit // 0')"
    printf 'instances=%s spend_this_session=$%.2f cap=$%s credit=$%.2f floor=$%s\n' \
        "$count" "$spend" "$CAP" "$credit" "${FLOOR:-none}"
}

if [[ $CHECK_ONLY -eq 1 ]]; then
    report
    exit 0
fi

say "guard armed: cap \$${CAP}${FLOOR:+, credit floor \$${FLOOR}}${MAX_IDLE:+, max idle ${MAX_IDLE}m}, label '${LABEL}', polling every ${POLL_SECONDS}s"
idle_since=""
# --await N: armed BEFORE the instance exists (the drain starts the guard
# before `up`, so the cap covers the whole create/boot window). Until the
# first labelled instance appears, an empty listing means "not yet", for at
# most N seconds; after that it means "torn down" and the guard exits.
seen=0
armed_at="$(date +%s)"
while true; do
    count="$(labeled | jq 'length')"
    if [[ "$count" == "0" ]]; then
        if [[ $seen -eq 0 ]] && (($(date +%s) - armed_at < AWAIT)); then
            sleep "$POLL_SECONDS"
            continue
        fi
        say "no '${LABEL}' instances — guard exiting"
        exit 0
    fi
    [[ -n "$count" ]] && seen=1
    spend="$(spend_now)"
    over="$(awk -v s="$spend" -v c="$CAP" 'BEGIN { print (s >= c) ? 1 : 0 }')"
    if [[ "$over" == "1" ]]; then
        say "CAP REACHED: \$$(printf '%.2f' "$spend") >= \$${CAP} — tearing down"
        destroy_all_verified
        exit $?
    fi
    if [[ -n "$MAX_IDLE" ]]; then
        if work_running; then
            idle_since=""
        else
            [[ -n "$idle_since" ]] || idle_since="$(date +%s)"
            idle_min=$((($(date +%s) - idle_since) / 60))
            if ((idle_min >= MAX_IDLE)); then
                say "IDLE ${idle_min}m >= ${MAX_IDLE}m with no translation work — tearing down"
                destroy_all_verified
                exit $?
            fi
        fi
    fi
    credit=""
    if [[ -n "$FLOOR" ]]; then
        credit="$(credit_now)"
        under="$(awk -v c="$credit" -v f="$FLOOR" 'BEGIN { print (c <= f) ? 1 : 0 }')"
        if [[ "$under" == "1" ]]; then
            say "CREDIT FLOOR HIT: \$$(printf '%.2f' "$credit") <= \$${FLOOR} — tearing down"
            destroy_all_verified
            exit $?
        fi
    fi
    say "spend \$$(printf '%.2f' "$spend") of \$${CAP}${credit:+ | credit \$$(printf '%.2f' "$credit") floor \$${FLOOR}}"
    sleep "$POLL_SECONDS"
done
