#!/bin/bash
# translation-drain-reaper.sh — independent killer of runaway drain GPU nodes.
#
# Run by audiobook-translation-reaper.service every 10 minutes
# (audiobook-translation-reaper.timer, ENABLED by default: it can only ever
# DESTROY instances, never rent one, so it is safe to have on everywhere).
#
# Why it exists: the drain's spend guard lives in the drain's own cgroup, so
# anything that kills the drain without running its trap — SIGKILL, the OOM
# killer, power loss, a systemd stop timeout — kills the guard too and leaves
# a node billing with no cap. This runs as a separate unit on its own timer
# and destroys every instance labelled AUDIOBOOKS_DRAIN_LABEL (abm-drain) that
#   (a) has no live drain run (the drain lock is free), or
#   (b) has lived longer than the drain could legitimately need
#       (AUDIOBOOKS_DRAIN_MAX_MINUTES + bootstrap timeout + up wait + stop grace
#        + AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES), or
#   (c) has accrued the per-run cap (AUDIOBOOKS_DRAIN_BUDGET_USD), or
#   (d) would take the month past AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD: the
#       ledger's month-to-date (excluding the live run's reservation) plus the
#       live instances' accrued cost reaches the ceiling.
# It never touches any other label — abm-translate is the operator's hand-run
# session — and refuses to run if configured with that label.
#
# While it inspects and destroys it holds the drain lock, so a drain cannot
# start in the middle of a reap (the drain waits for the lock).
#
# Exit codes: 0 nothing to do; 2 it destroyed something (a firing is always an
# incident — the unit shows failed so cachyos-sentinel alerts); 1 error,
# including any teardown it could not verify.
#
# Credential: VAST_API_KEY or VAST_API_KEY_FILE (derive-service-secret drop-in).
# With neither configured it exits 0: a host with no Vast credential cannot
# have rented anything through the drain.
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
LABEL="${AUDIOBOOKS_DRAIN_LABEL:-abm-drain}"
BUDGET="${AUDIOBOOKS_DRAIN_BUDGET_USD:-10}"
MONTHLY="${AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD:-100}"
MAX_MINUTES="${AUDIOBOOKS_DRAIN_MAX_MINUTES:-240}"
BOOTSTRAP_TIMEOUT="${AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT:-1800}"
MARGIN_MINUTES="${AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES:-30}"
LOCK_FILE="${AUDIOBOOKS_DRAIN_LOCK:-${AUDIOBOOKS_RUN_DIR}/translation-drain.lock}"
LEDGER="${AUDIOBOOKS_DRAIN_LEDGER:-${AUDIOBOOKS_VAR_DIR}/translation-drain/spend-ledger.tsv}"
LIVE_FILE="${LEDGER}.live"
PY="${DRAIN_PYTHON:-${AUDIOBOOKS_VENV}/bin/python}"
VERIFY_DELAY="${DRAIN_VERIFY_DELAY:-5}"
# Drain worst case: up (<=1500 s) + bootstrap + batch + stop grace (300 s).
UP_WAIT=1500
STOP_GRACE=300

[[ "$LABEL" != "abm-translate" ]] || die "AUDIOBOOKS_DRAIN_LABEL is the operator's abm-translate label — refusing"
for v in MAX_MINUTES BOOTSTRAP_TIMEOUT MARGIN_MINUTES; do
    [[ "${!v}" =~ ^[0-9]+$ ]] || die "${v} must be a whole number (got '${!v}')"
done
for v in BUDGET MONTHLY; do
    [[ "${!v}" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "${v} must be a number (got '${!v}')"
done
MAX_LIFE=$((MAX_MINUTES * 60 + BOOTSTRAP_TIMEOUT + UP_WAIT + STOP_GRACE + MARGIN_MINUTES * 60))

# --- credential ---------------------------------------------------------------
if [[ -z "${VAST_API_KEY:-}" ]]; then
    if [[ -z "${VAST_API_KEY_FILE:-}" ]]; then
        log "no Vast.ai credential configured — the drain cannot have rented anything; nothing to reap"
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

ledger() {
    PYTHONPATH="${LIB_PY_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
        "$PY" -m localization.drain_ledger --ledger "$LEDGER" "$@" 9>&-
}

# --- look ---------------------------------------------------------------------
rows="$(labeled)" || die "could not list Vast instances — cannot tell whether a node is billing"
count="$(jq 'length' <<<"$rows")"
if [[ "$count" == "0" ]]; then
    exit 0
fi

drain_live=1
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null
exec 9>>"$LOCK_FILE" || die "cannot open ${LOCK_FILE}"
if flock -n 9; then
    drain_live=0 # we now hold the lock: no drain can start until we exit
fi
live_run=""
[[ -f "$LIVE_FILE" ]] && live_run="$(head -c 200 "$LIVE_FILE" | tr -d '[:space:]')"

now="$(date +%s)"
accrued_total="$(jq -r --argjson now "$now" \
    '[.[] | ((($now - (.start_date // $now)) / 3600) * (.dph_total // 0))] | add // 0' <<<"$rows")"
exclude=""
[[ $drain_live -eq 1 ]] && exclude="$live_run"
mtd="$(ledger mtd --exclude-run "$exclude")" || die "could not read the spend ledger ${LEDGER}"
month_total="$(awk -v m="$mtd" -v a="$accrued_total" 'BEGIN { printf "%.4f", m + a }')"
over_ceiling="$(awk -v t="$month_total" -v c="$MONTHLY" 'BEGIN { print (t >= c) ? 1 : 0 }')"

log "${count} '${LABEL}' instance(s); drain live=${drain_live}; month-to-date incl. live accrued \$${month_total} of \$${MONTHLY}"

# --- decide -------------------------------------------------------------------
declare -a doomed=()
while IFS=$'\t' read -r id age spend; do
    reason=""
    if [[ $drain_live -eq 0 ]]; then
        reason="no live drain run holds the lock"
    elif ((age > MAX_LIFE)); then
        reason="age ${age}s exceeds the drain's maximum lifetime ${MAX_LIFE}s"
    elif awk -v s="$spend" -v c="$BUDGET" 'BEGIN { exit !(s >= c) }'; then
        reason="accrued \$${spend} reached the per-run cap \$${BUDGET}"
    elif [[ "$over_ceiling" == "1" ]]; then
        reason="month-to-date \$${month_total} reached the monthly ceiling \$${MONTHLY}"
    fi
    if [[ -n "$reason" ]]; then
        log "REAPING instance ${id}: ${reason}"
        doomed+=("$id")
    fi
done < <(jq -r --argjson now "$now" '.[] |
    (($now - (.start_date // $now)) | floor) as $age |
    [.id, $age, (($age / 3600) * (.dph_total // 0) * 10000 | round / 10000)] | @tsv' <<<"$rows")

[[ ${#doomed[@]} -gt 0 ]] || exit 0

# --- destroy and verify -------------------------------------------------------
for id in "${doomed[@]}"; do
    api DELETE "/instances/${id}/" >/dev/null || log "delete call for ${id} errored (verifying anyway)"
done
remaining=""
for try in 1 2 3 4 5; do
    sleep "$VERIFY_DELAY"
    rows_after="$(labeled)" || {
        log "verify attempt ${try}: listing failed"
        continue
    }
    remaining="$(jq -r --argjson ids "$(printf '%s\n' "${doomed[@]}" | jq -R 'tonumber? // .' | jq -s .)" \
        '[.[] | select(.id as $i | $ids | index($i))] | length' <<<"$rows_after")"
    [[ "$remaining" == "0" ]] && break
    log "verify attempt ${try}: ${remaining} still present — retrying delete"
    for id in "${doomed[@]}"; do api DELETE "/instances/${id}/" >/dev/null 2>&1 || true; done
done

# A dead run's spend is what its node accrued; record it (largest final wins).
if [[ $drain_live -eq 0 ]]; then
    run="${live_run:-orphan-$(date +%Y%m%d%H%M%S)}"
    ledger final --run "$run" --amount "$accrued_total" --note "reaper estimate" \
        || log "WARNING: could not record \$${accrued_total} in the ledger"
    rm -f "$LIVE_FILE"
fi

if [[ "$remaining" != "0" ]]; then
    log "!!! REAP UNVERIFIED — ${remaining:-unknown} '${LABEL}' instance(s) may still bill; check console.vast.ai NOW !!!"
    exit 1
fi
log "verified: ${#doomed[@]} instance(s) destroyed"
exit 2
