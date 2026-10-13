#!/bin/bash
# translation-drain-reaper.sh — independent killer of runaway drain GPU nodes.
#
# Run by audiobook-translation-reaper.service every 10 minutes
# (audiobook-translation-reaper.timer, ENABLED by default: it can only ever
# DESTROY instances, never rent one, so it is safe to have on everywhere).
#
# Why it exists: the drain's spend guard lives in the drain's own cgroup, so
# anything that kills the drain without running its trap — SIGKILL, the OOM
# killer, a systemd stop timeout — kills the guard too and leaves a node
# billing with no cap. (Host DOWN is covered on the node itself: gpu-node.sh's
# dead-man switch.) This runs as a separate unit on its own timer and destroys
# every instance labelled exactly AUDIOBOOKS_DRAIN_LABEL (abm-drain-<host>)
# that
#   (a) has no live drain run (the drain lock is free), or
#   (b) has lived longer than the drain could legitimately need
#       (localization.drain_limits — the same value the node's dead-man
#        switch uses), or
#   (c) has accrued the per-run cap (AUDIOBOOKS_DRAIN_BUDGET_USD), or
#   (d) would take the month past AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD: the
#       ledger's month-to-date (excluding the live run's reservation) plus the
#       live instances' accrued cost reaches the ceiling.
# (a)-(c) need only the Vast listing and are decided and EXECUTED first; the
# ledger is read only for (d), so a damaged ledger can never stop an orphan
# from being destroyed (it is reported, and the run exits non-zero, after).
#
# It never touches any other label, and refuses labels that are not
# abm-drain-<name>: the Vast account is shared between hosts and every host's
# reaper must police only its own drain's nodes.
#
# While it inspects and destroys it holds the drain lock if free, so a drain
# cannot start in the middle of a reap (the drain waits for the lock).
#
# Each run that completes with the key writes a heartbeat
# (AUDIOBOOKS_DRAIN_REAPER_HEARTBEAT: "<epoch> <label>"); the drain refuses to
# rent without a recent one.
#
# Exit codes: 0 nothing to do; 2 it destroyed something (a firing is always an
# incident — the unit shows failed so cachyos-sentinel alerts); 1 error,
# including any teardown it could not verify, a ledger it could not read, and
# a missing key on a host where the drain is configured.
#
# Credential: VAST_API_KEY, VAST_API_KEY_FILE, or the key derive-service-secret
# wrote into this unit's RuntimeDirectory.
#
# Test seams: DRAIN_CONFIG_LIB, DRAIN_PYTHON, VAST_API_BASE, DRAIN_VERIFY_DELAY.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PY_DIR="${SCRIPT_DIR}/../library"

log() { printf '%s drain-reaper: %s\n' "$(date '+%F %T')" "$*"; }
die() {
    log "ERROR: $*"
    exit 1
}

CONFIG_LIB="${DRAIN_CONFIG_LIB:-}"
if [[ -z "$CONFIG_LIB" ]]; then
    if [[ -f /usr/local/lib/audiobooks/audiobook-config.sh ]]; then
        CONFIG_LIB=/usr/local/lib/audiobooks/audiobook-config.sh
    else
        CONFIG_LIB="${SCRIPT_DIR}/../lib/audiobook-config.sh"
    fi
fi
[[ -f "$CONFIG_LIB" ]] || die "audiobook-config.sh not found ($CONFIG_LIB)"
# shellcheck source=/dev/null
source "$CONFIG_LIB"

API_BASE="${VAST_API_BASE:-https://console.vast.ai/api/v0}"
LABEL="${AUDIOBOOKS_DRAIN_LABEL:-}"
BUDGET="${AUDIOBOOKS_DRAIN_BUDGET_USD:-10}"
MONTHLY="${AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD:-100}"
MAX_MINUTES="${AUDIOBOOKS_DRAIN_MAX_MINUTES:-240}"
BOOTSTRAP_TIMEOUT="${AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT:-1800}"
MARGIN_MINUTES="${AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES:-30}"
LOCK_FILE="${AUDIOBOOKS_DRAIN_LOCK:-${AUDIOBOOKS_RUN_DIR}/translation-drain.lock}"
LEDGER="${AUDIOBOOKS_DRAIN_LEDGER:-${AUDIOBOOKS_VAR_DIR}/translation-drain/spend-ledger.tsv}"
LIVE_FILE="${LEDGER}.live"
HEARTBEAT="${AUDIOBOOKS_DRAIN_REAPER_HEARTBEAT:-$(dirname "$LEDGER")/reaper-heartbeat}"
PY="${DRAIN_PYTHON:-${AUDIOBOOKS_VENV}/bin/python}"
VERIFY_DELAY="${DRAIN_VERIFY_DELAY:-5}"

pyrun() { PYTHONPATH="${LIB_PY_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$PY" "$@" 9>&-; }
ledger() { pyrun -m localization.drain_ledger --ledger "$LEDGER" "$@"; }

[[ -n "$LABEL" ]] || LABEL="$(pyrun -m localization.drain_limits default-label)" \
    || die "could not derive the default drain label"
pyrun -m localization.drain_limits check-label "$LABEL" || die "refusing label '${LABEL}'"
for v in MAX_MINUTES BOOTSTRAP_TIMEOUT MARGIN_MINUTES; do
    [[ "${!v}" =~ ^[0-9]+$ ]] || die "${v} must be a whole number (got '${!v}')"
done
for v in BUDGET MONTHLY; do
    [[ "${!v}" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "${v} must be a number (got '${!v}')"
done
MAX_LIFE="$(pyrun -m localization.drain_limits lifetime --max-minutes "$MAX_MINUTES" \
    --bootstrap-seconds "$BOOTSTRAP_TIMEOUT" --margin-minutes "$MARGIN_MINUTES")" \
    || die "could not compute the drain node lifetime"

# --- credential ---------------------------------------------------------------
# The derive-service-secret drop-in's pointer can be blanked by an
# EnvironmentFile line; the derived file itself is authoritative.
if [[ -z "${VAST_API_KEY:-}" && -z "${VAST_API_KEY_FILE:-}" &&
    -n "${RUNTIME_DIRECTORY:-}" && -s "${RUNTIME_DIRECTORY%%:*}/vast-api-key" ]]; then
    VAST_API_KEY_FILE="${RUNTIME_DIRECTORY%%:*}/vast-api-key"
fi
drain_configured() {
    systemctl is-enabled --quiet audiobook-translation-drain.timer 2>/dev/null && return 0
    [[ -e "$LEDGER" || -e "$LIVE_FILE" ]]
}
if [[ -z "${VAST_API_KEY:-}" ]]; then
    if [[ -z "${VAST_API_KEY_FILE:-}" ]]; then
        if drain_configured; then
            die "no Vast.ai credential, but the drain is configured on this host — the reaper is NOT protecting it"
        fi
        log "no Vast.ai credential and no drain configured — nothing can have been rented; nothing to reap"
        exit 0
    fi
    [[ -r "$VAST_API_KEY_FILE" ]] || die "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is not readable"
    VAST_API_KEY="$(tr -d '[:space:]' <"$VAST_API_KEY_FILE")"
    [[ -n "$VAST_API_KEY" ]] || die "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is empty"
fi
command -v jq >/dev/null || die "jq is required"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/drain-reaper.XXXXXX")" || die "mktemp failed"
trap 'rm -rf "$WORKDIR"' EXIT
umask 077
printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
    "$VAST_API_KEY" >"${WORKDIR}/hdr"
unset VAST_API_KEY

api() {
    curl -sS --fail-with-body --max-time 45 -K "${WORKDIR}/hdr" -X "$1" "${API_BASE}$2"
}

# Exact-match label filter. Nothing else in this script selects instances.
labeled() {
    api GET "/instances/" | jq -e --arg l "$LABEL" '[.instances[]? | select(.label == $l)]'
}

heartbeat() {
    mkdir -p "$(dirname "$HEARTBEAT")" 2>/dev/null
    if ! { printf '%s %s\n' "$(date +%s)" "$LABEL" >"${HEARTBEAT}.tmp" \
        && mv -f "${HEARTBEAT}.tmp" "$HEARTBEAT"; }; then
        log "WARNING: could not write heartbeat ${HEARTBEAT} — the drain will refuse to rent"
    fi
}

# destroy_verified ID... — DELETE each, re-list until all gone (5 tries).
destroy_verified() {
    local id try remaining="" rows_after ids_json
    ids_json="$(printf '%s\n' "$@" | jq -R 'tonumber? // .' | jq -s .)"
    for id in "$@"; do
        api DELETE "/instances/${id}/" >/dev/null || log "delete call for ${id} errored (verifying anyway)"
    done
    for try in 1 2 3 4 5; do
        sleep "$VERIFY_DELAY"
        rows_after="$(labeled)" || {
            log "verify attempt ${try}: listing failed"
            continue
        }
        remaining="$(jq -r --argjson ids "$ids_json" \
            '[.[] | select(.id as $i | $ids | index($i))] | length' <<<"$rows_after")"
        [[ "$remaining" == "0" ]] && return 0
        log "verify attempt ${try}: ${remaining} still present — retrying delete"
        for id in "$@"; do api DELETE "/instances/${id}/" >/dev/null 2>&1 || true; done
    done
    log "!!! REAP UNVERIFIED — ${remaining:-unknown} '${LABEL}' instance(s) may still bill; check console.vast.ai NOW !!!"
    return 1
}

# --- look ---------------------------------------------------------------------
# Take the lock FIRST, then list: a drain that finished between a listing and
# the lock would otherwise look like an orphan — a "destroy" of a node that is
# already gone, a full orphan charge and a false alert.
drain_live=1
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null
exec 9>>"$LOCK_FILE" || die "cannot open ${LOCK_FILE}"
if flock -n 9; then
    drain_live=0 # we now hold the lock: no drain can start until we exit
fi
live_run=""
[[ -f "$LIVE_FILE" ]] && live_run="$(head -c 200 "$LIVE_FILE" | tr -d '[:space:]')"

rows="$(labeled)" || die "could not list Vast instances — cannot tell whether a node is billing"
count="$(jq 'length' <<<"$rows")"
if [[ "$count" == "0" ]]; then
    # No node, but a run marker and no live drain: that run died and its node
    # is already gone — destroyed by its own dead-man switch while this host
    # was down, or by Vast. Nobody measured what it cost; charge its
    # reservation (its cap — never less) so the month cannot under-count.
    if [[ $drain_live -eq 0 && -n "$live_run" ]]; then
        if amount="$(ledger settle --run "$live_run" --note "reaper: run died, node already gone")"; then
            log "settled dead run ${live_run} at \$${amount} (its reservation floor)"
            rm -f "$LIVE_FILE"
        else
            die "could not settle dead run ${live_run} in the ledger ${LEDGER}"
        fi
    fi
    heartbeat
    exit 0
fi
now="$(date +%s)"
accrued_total="$(jq -r --argjson now "$now" \
    '[.[] | ((($now - (.start_date // $now)) / 3600) * (.dph_total // 0))] | add // 0' <<<"$rows")"
log "${count} '${LABEL}' instance(s); drain live=${drain_live}; accrued \$${accrued_total}"

rc=0
destroyed=0

# --- (a) (b) (c): listing only, decided and executed BEFORE the ledger --------
declare -a doomed=() survivors=()
while IFS=$'\t' read -r id age spend; do
    reason=""
    if [[ $drain_live -eq 0 ]]; then
        reason="no live drain run holds the lock"
    elif ((age > MAX_LIFE)); then
        reason="age ${age}s exceeds the drain's maximum lifetime ${MAX_LIFE}s"
    elif awk -v s="$spend" -v c="$BUDGET" 'BEGIN { exit !(s >= c) }'; then
        reason="accrued \$${spend} reached the per-run cap \$${BUDGET}"
    fi
    if [[ -n "$reason" ]]; then
        log "REAPING instance ${id}: ${reason}"
        doomed+=("$id")
    else
        survivors+=("$id")
    fi
done < <(jq -r --argjson now "$now" '.[] |
    (($now - (.start_date // $now)) | floor) as $age |
    [.id, $age, (($age / 3600) * (.dph_total // 0) * 10000 | round / 10000)] | @tsv' <<<"$rows")

if [[ ${#doomed[@]} -gt 0 ]]; then
    if destroy_verified "${doomed[@]}"; then
        destroyed=$((destroyed + ${#doomed[@]}))
    else
        rc=1
    fi
fi

# --- (d): the monthly ceiling needs the ledger --------------------------------
if [[ ${#survivors[@]} -gt 0 ]]; then
    exclude=""
    [[ $drain_live -eq 1 ]] && exclude="$live_run"
    if mtd="$(ledger mtd --exclude-run "$exclude")"; then
        month_total="$(awk -v m="$mtd" -v a="$accrued_total" 'BEGIN { printf "%.4f", m + a }')"
        if awk -v t="$month_total" -v c="$MONTHLY" 'BEGIN { exit !(t >= c) }'; then
            log "REAPING ${#survivors[@]} instance(s): month-to-date \$${month_total} reached the monthly ceiling \$${MONTHLY}"
            if destroy_verified "${survivors[@]}"; then
                destroyed=$((destroyed + ${#survivors[@]}))
            else
                rc=1
            fi
        fi
    else
        log "!!! could not read the spend ledger ${LEDGER} — the monthly ceiling is NOT being enforced on live instance(s) ${survivors[*]}; repair the ledger !!!"
        rc=1
    fi
fi

# Every run that reached here also proves the ledger is readable (the drain's
# budget decisions and (d) depend on it) — after acting, never before.
if [[ ${#survivors[@]} -eq 0 ]] && ! ledger mtd >/dev/null; then
    log "!!! the spend ledger ${LEDGER} is unreadable — repair it; the drain will refuse to rent !!!"
    rc=1
fi

# A dead run's spend: what its node accrued, never less than its reservation
# (the cap it was allowed) — accrued is only what this listing still shows.
if [[ $drain_live -eq 0 && $destroyed -gt 0 ]]; then
    run="${live_run:-orphan-$(date +%Y%m%d%H%M%S)}"
    ledger settle --run "$run" --accrued "$accrued_total" --note "reaper: destroyed orphan" >/dev/null \
        || {
            log "!!! could not record \$${accrued_total} in the ledger — the month will under-count !!!"
            rc=1
        }
    rm -f "$LIVE_FILE"
fi

if [[ $rc -ne 0 ]]; then
    exit 1
fi
heartbeat
if [[ $destroyed -gt 0 ]]; then
    log "verified: ${destroyed} instance(s) destroyed"
    exit 2
fi
exit 0
