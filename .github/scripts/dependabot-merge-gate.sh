#!/bin/bash
# Dependabot merge gate — merge only when every other check on the PR head is green.
#
# Why this exists: `gh pr merge --auto` waits for REQUIRED status checks only, and
# this repository deliberately keeps required_status_checks null on main (see
# ~/.claude/rules/github.md, solo-workflow baseline). With nothing required,
# --auto merged the instant the PR was mergeable — PRs #155 and #177 landed on
# 2026-09-29 with "Docker Build Check: FAILURE" on their own heads
# (Audiobook-Manager-0xl). This script is the gate branch protection no longer
# provides: it reads the head commit's check runs and commit statuses and
# answers GREEN, RED or PENDING.
#
# Usage:
#   dependabot-merge-gate.sh evaluate CHECK_RUNS_JSON STATUS_JSON
#       Pure function over two saved API responses
#       (GET /repos/{o}/{r}/commits/{sha}/check-runs and .../status).
#       Prints one VERDICT line. Exit 0 GREEN, 1 RED, 2 PENDING.
#   dependabot-merge-gate.sh wait OWNER/REPO SHA [TIMEOUT_MIN] [INTERVAL_SEC]
#       Polls the live API until the verdict is not PENDING.
#       Exit 0 GREEN, 1 RED, 2 timed out while still PENDING, 3 API error.
#
# Environment:
#   GITHUB_RUN_ID   When set, check runs belonging to this workflow run are
#                   excluded — the gate must not wait on, or count, itself.
#
# Verdict rules (applied to every check run not excluded above):
#   * any run with status != completed                      -> PENDING
#   * any completed run whose conclusion is not one of
#     success / skipped / neutral                            -> RED
#   * no runs at all (other workflows not yet queued)       -> PENDING
#   * combined commit status: failure or error -> RED; pending with at least
#     one status -> PENDING; no statuses at all is not a signal.
#   * otherwise                                             -> GREEN
# PENDING is the default on uncertainty: an empty or unparseable payload must
# never read as green.

set -euo pipefail

WORK_DIR=""
ACCEPTED='["success","skipped","neutral"]'

die() {
    echo "ERROR: $*" >&2
    exit 3
}

# evaluate CHECK_RUNS_JSON STATUS_JSON
evaluate() {
    local runs_file="$1" status_file="$2"
    local run_filter='true'
    if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
        run_filter=".details_url | test(\"/actions/runs/${GITHUB_RUN_ID}/\") | not"
    fi

    local total returned
    total=$(jq -r '.total_count // empty' "$runs_file") || die "check-runs payload is not JSON"
    returned=$(jq -r '.check_runs | length' "$runs_file") || die "check-runs payload has no check_runs array"
    [[ -n "$total" ]] || die "check-runs payload has no total_count"
    if (( total > returned )); then
        die "check-runs payload truncated: total_count=$total, returned=$returned"
    fi

    local considered pending red
    considered=$(jq -r --argjson acc "$ACCEPTED" \
        "[.check_runs[] | select($run_filter)] | length" "$runs_file")
    pending=$(jq -r \
        "[.check_runs[] | select($run_filter) | select(.status != \"completed\") | .name] | join(\", \")" \
        "$runs_file")
    red=$(jq -r --argjson acc "$ACCEPTED" \
        "[.check_runs[] | select($run_filter) | select(.status == \"completed\") \
          | select((.conclusion // \"missing\") as \$c | \$acc | index(\$c) | not) \
          | \"\\(.name)=\\(.conclusion // \"missing\")\"] | join(\", \")" \
        "$runs_file")

    local st_state st_total st_red
    st_state=$(jq -r '.state // empty' "$status_file") || die "status payload is not JSON"
    st_total=$(jq -r '.total_count // 0' "$status_file")
    st_red=$(jq -r \
        '[.statuses[]? | select(.state == "failure" or .state == "error") | "\(.context)=\(.state)"] | join(", ")' \
        "$status_file")

    # RED wins over PENDING: a failure already observed is final whatever else
    # is still running.
    if [[ -n "$red" || -n "$st_red" ]]; then
        echo "VERDICT=RED failed: ${red}${red:+${st_red:+, }}${st_red}"
        return 1
    fi
    if [[ -n "$pending" ]]; then
        echo "VERDICT=PENDING waiting on: $pending"
        return 2
    fi
    if (( considered == 0 )); then
        echo "VERDICT=PENDING no other check runs reported yet"
        return 2
    fi
    if (( st_total > 0 )) && [[ "$st_state" == "pending" ]]; then
        echo "VERDICT=PENDING commit statuses still pending"
        return 2
    fi
    echo "VERDICT=GREEN $considered check runs concluded success/skipped/neutral"
    return 0
}

# wait OWNER/REPO SHA [TIMEOUT_MIN] [INTERVAL_SEC]
wait_for_green() {
    local repo="$1" sha="$2" timeout_min="${3:-45}" interval="${4:-30}"
    local deadline=$(( $(date +%s) + timeout_min * 60 ))
    # Script-level EXIT trap on a global, deliberately: a RETURN trap set here
    # stays armed after this function returns and fires again when main()
    # returns, by which time the local is gone — under `set -u` that second
    # firing was an "unbound variable" error that turned a GREEN verdict into
    # exit 1 (run 37066878118, 2026-10-02).
    WORK_DIR=$(mktemp -d)
    trap 'rm -rf "${WORK_DIR:-}"' EXIT
    local tmp="$WORK_DIR"

    local rc=2
    while :; do
        gh api "repos/${repo}/commits/${sha}/check-runs?per_page=100" > "$tmp/runs.json" \
            || die "GET check-runs failed for $sha"
        gh api "repos/${repo}/commits/${sha}/status" > "$tmp/status.json" \
            || die "GET status failed for $sha"
        set +e
        evaluate "$tmp/runs.json" "$tmp/status.json"
        rc=$?
        set -e
        if (( rc != 2 )); then
            return "$rc"
        fi
        if (( $(date +%s) >= deadline )); then
            echo "VERDICT=TIMEOUT still pending after ${timeout_min} min"
            return 2
        fi
        sleep "$interval"
    done
}

main() {
    local cmd="${1:-}"
    case "$cmd" in
        evaluate)
            [[ $# -eq 3 ]] || die "usage: $0 evaluate CHECK_RUNS_JSON STATUS_JSON"
            evaluate "$2" "$3"
            ;;
        wait)
            [[ $# -ge 3 && $# -le 5 ]] || die "usage: $0 wait OWNER/REPO SHA [TIMEOUT_MIN] [INTERVAL_SEC]"
            wait_for_green "$2" "$3" "${4:-}" "${5:-}"
            ;;
        *)
            die "usage: $0 evaluate CHECK_RUNS_JSON STATUS_JSON | wait OWNER/REPO SHA [TIMEOUT_MIN] [INTERVAL_SEC]"
            ;;
    esac
}

main "$@"
