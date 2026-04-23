#!/bin/bash
# smoke_probe.sh — Post-upgrade/post-install functional smoke probe
#
# This is the HARD GATE that upgrade.sh and install.sh call before printing
# "Successfully upgraded" / "Installation complete". It actually exercises
# the running system instead of trusting that rsync + systemctl enable was
# enough.
#
# Why this exists:
#   The v8.3.7.1 prod upgrade printed "Successfully upgraded to version
#   8.3.7.1" while the streaming worker was in start-limit-hit state and
#   two DB columns were missing. No probe, no knowledge. A passing file
#   copy is NOT proof that the thing works — only running the thing and
#   seeing it work is proof.
#
# What this probes:
#   1. Every required systemd service unit is `active` (not just enabled).
#   2. The API responds to /api/system/health with 200.
#   3. The API returns the version we just installed (not a cached value).
#   4. The DB has every REQUIRED_DB_COLUMNS entry from release-requirements.sh.
#   5. If RunPod endpoints are configured, each /health endpoint returns a
#      non-error response (workers may be idle — we don't wake them, just
#      verify the endpoint is reachable + auth succeeds).
#   6. The stream-translate worker, if enabled, is not in a crash loop —
#      systemctl show NRestarts and compare to a reasonable threshold.
#
# What this does NOT do:
#   - Submit a real inference job (cost, latency).
#   - Download audiobook data.
#   - Reset DB state.
# It is purely observational and safe to run on any live system.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/release-requirements.sh"

# Color codes (fall back when run standalone).
_red="${RED:-\033[0;31m}"
_yellow="${YELLOW:-\033[1;33m}"
_green="${GREEN:-\033[0;32m}"
_blue="${BLUE:-\033[0;34m}"
_nc="${NC:-\033[0m}"

_smoke_fail=0
_smoke_warn=0

_fail() {
    echo -e "  ${_red}✗ $1${_nc}"
    _smoke_fail=$((_smoke_fail + 1))
}
_warn() {
    echo -e "  ${_yellow}⚠ $1${_nc}"
    _smoke_warn=$((_smoke_warn + 1))
}
_pass() {
    echo -e "  ${_green}✓ $1${_nc}"
}

# ─── Probe 1: systemd service activity ──────────────────────────────────────
_probe_systemd() {
    echo -e "${_blue}Probing systemd services...${_nc}"
    local use_sudo=""
    if ! systemctl is-active audiobook.target &>/dev/null; then
        # Need sudo for is-active? No — is-active works without sudo. But
        # some distros gate status on sudo; try both.
        if sudo -n systemctl is-active audiobook.target &>/dev/null; then
            use_sudo="sudo -n"
        fi
    fi

    # Services that are INTENTIONALLY inactive between runs. These are
    # triggered by timers (downloader, enrichment) or lifecycle paths
    # (shutdown-saver runs on stop; upgrade-helper runs on path-unit
    # activation). "inactive" is the healthy steady state for these.
    local -A _expected_inactive
    _expected_inactive=(
        [audiobook-downloader.service]=1
        [audiobook-enrichment.service]=1
        [audiobook-shutdown-saver.service]=1
        [audiobook-upgrade-helper.service]=1
    )

    for unit in "${REQUIRED_SYSTEMD_UNITS[@]}"; do
        # Only probe `.service` units for is-active — targets/timers/paths
        # have different activity semantics.
        if [[ "$unit" != *.service ]]; then
            continue
        fi
        # is-active can emit multiple lines if multiple units match, and some
        # systemd versions duplicate the line. Pin to the first token only —
        # anything past the first whitespace or newline is discarded so the
        # case statement below can match cleanly.
        local state_raw
        state_raw=$($use_sudo systemctl is-active "$unit" 2>/dev/null || echo "inactive")
        local state
        state=$(echo "$state_raw" | head -1 | awk '{print $1}')
        [[ -z "$state" ]] && state="inactive"
        case "$state" in
            active)
                _pass "$unit: $state"
                ;;
            activating)
                _warn "$unit: $state (still starting — may be init-scanning)"
                ;;
            failed)
                _fail "$unit: $state"
                ;;
            inactive)
                if [[ -n "${_expected_inactive[$unit]:-}" ]]; then
                    _pass "$unit: inactive (expected — timer/path-triggered)"
                else
                    # For non-timer services, inactive is concerning — but
                    # check if this was a oneshot that exited cleanly.
                    local exit_status
                    exit_status=$($use_sudo systemctl show "$unit" --property=ExecMainStatus --value 2>/dev/null | head -1)
                    if [[ "$exit_status" == "0" ]]; then
                        _pass "$unit: inactive (completed cleanly, oneshot)"
                    else
                        _fail "$unit: $state (ExecMainStatus=$exit_status)"
                    fi
                fi
                ;;
            *)
                _warn "$unit: $state"
                ;;
        esac

        # Crash-loop detection — if NRestarts > 3, the unit is struggling.
        local restarts
        restarts=$($use_sudo systemctl show "$unit" --property=NRestarts --value 2>/dev/null | head -1)
        if [[ -n "$restarts" ]] && [[ "$restarts" =~ ^[0-9]+$ ]] && [[ "$restarts" -gt 3 ]]; then
            _fail "$unit: excessive restarts ($restarts) — check journalctl -u $unit"
        fi
    done
}

# ─── Probe 2: API reachability + version ────────────────────────────────────
_probe_api() {
    echo -e "${_blue}Probing API...${_nc}"
    local api_port="${API_PORT:-5001}"
    local api_base="http://127.0.0.1:${api_port}"
    local expected_version="${EXPECTED_VERSION:-}"

    # Health endpoint
    local health_body
    if ! health_body=$(curl -s --max-time 5 "${api_base}/api/system/health" 2>/dev/null); then
        _fail "API health endpoint unreachable at ${api_base}/api/system/health"
        return
    fi
    if ! echo "$health_body" | grep -q '"status"\s*:\s*"ok"'; then
        _fail "API health returned non-ok: $health_body"
    else
        _pass "API health: ok"
    fi

    # Version endpoint
    if [[ -n "$expected_version" ]]; then
        local version_body
        version_body=$(curl -s --max-time 5 "${api_base}/api/system/version" 2>/dev/null)
        local got_version
        got_version=$(echo "$version_body" | grep -oP '"version"\s*:\s*"\K[^"]+' | head -1)
        if [[ "$got_version" == "$expected_version" ]]; then
            _pass "API version: $got_version (matches expected)"
        else
            _fail "API version mismatch: expected '$expected_version', got '$got_version'"
        fi
    fi
}

# ─── Probe 3: DB schema ──────────────────────────────────────────────────────
_probe_db_schema() {
    echo -e "${_blue}Probing database schema...${_nc}"
    local db_path="${DB_PATH:-${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}/db/audiobooks.db}"
    local use_sudo="${USE_SUDO:-}"

    if [[ ! -f "$db_path" ]]; then
        _warn "DB not found at $db_path — skipping schema probe"
        return
    fi

    local _sqlite_cmd="sqlite3"
    if [[ -n "$use_sudo" ]]; then
        _sqlite_cmd="sudo -u audiobooks sqlite3"
    fi

    for entry in "${REQUIRED_DB_COLUMNS[@]}"; do
        local table="${entry%%.*}"
        local column="${entry##*.}"
        if $_sqlite_cmd "$db_path" "PRAGMA table_info(${table});" 2>/dev/null \
            | awk -F'|' '{print $2}' | grep -qx "$column"; then
            _pass "DB column: $entry"
        else
            _fail "DB column missing: $entry (data-migration did not apply)"
        fi
    done
}

# ─── Probe 4: RunPod endpoints (if configured) ───────────────────────────────
_probe_runpod() {
    local conf_file="${1:-/etc/audiobooks/audiobooks.conf}"
    local api_key streaming_ep backlog_ep
    if [[ ! -f "$conf_file" ]]; then
        return
    fi
    api_key=$(grep -oP '^AUDIOBOOKS_RUNPOD_API_KEY=\K.*' "$conf_file" 2>/dev/null | head -1)
    api_key="${api_key%\"}"; api_key="${api_key#\"}"
    streaming_ep=$(grep -oP '^AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT=\K.*' "$conf_file" 2>/dev/null | head -1)
    streaming_ep="${streaming_ep%\"}"; streaming_ep="${streaming_ep#\"}"
    backlog_ep=$(grep -oP '^AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT=\K.*' "$conf_file" 2>/dev/null | head -1)
    backlog_ep="${backlog_ep%\"}"; backlog_ep="${backlog_ep#\"}"

    if [[ -z "$api_key" ]] || [[ -z "$streaming_ep" ]]; then
        # Streaming not configured — skip. The release-requirements validator
        # already surfaced this as a feature-disabled warning.
        return
    fi

    echo -e "${_blue}Probing RunPod endpoints...${_nc}"
    local response
    for ep_pair in "streaming:$streaming_ep" "backlog:$backlog_ep"; do
        local label="${ep_pair%%:*}"
        local ep_id="${ep_pair##*:}"
        [[ -z "$ep_id" ]] && continue
        response=$(curl -s --max-time 5 \
            -H "Authorization: Bearer ${api_key}" \
            "https://api.runpod.ai/v2/${ep_id}/health" 2>/dev/null || echo "")
        if [[ -z "$response" ]]; then
            _warn "RunPod $label endpoint $ep_id: unreachable or timeout"
        elif echo "$response" | grep -q '"workers"'; then
            # "workers":{"ready":N,"idle":M,"unhealthy":0,...}
            local ready
            ready=$(echo "$response" | grep -oP '"ready"\s*:\s*\K[0-9]+' | head -1)
            local unhealthy
            unhealthy=$(echo "$response" | grep -oP '"unhealthy"\s*:\s*\K[0-9]+' | head -1)
            if [[ -n "$unhealthy" ]] && [[ "$unhealthy" != "0" ]]; then
                _warn "RunPod $label endpoint $ep_id: $unhealthy unhealthy worker(s)"
            elif [[ "$ready" == "0" ]]; then
                _warn "RunPod $label endpoint $ep_id: 0 ready workers (cold-start on first request)"
            else
                _pass "RunPod $label endpoint $ep_id: $ready worker(s) ready"
            fi
        else
            _warn "RunPod $label endpoint $ep_id: unexpected response: ${response:0:100}"
        fi
    done
}

# ─── Main entry point ────────────────────────────────────────────────────────

run_smoke_probe() {
    echo ""
    echo -e "${_blue}=== Post-upgrade functional smoke probe ===${_nc}"
    _smoke_fail=0
    _smoke_warn=0

    _probe_systemd
    _probe_api
    _probe_db_schema
    _probe_runpod "${1:-/etc/audiobooks/audiobooks.conf}"

    echo ""
    if [[ $_smoke_fail -eq 0 ]] && [[ $_smoke_warn -eq 0 ]]; then
        echo -e "${_green}=== Smoke probe: ALL CHECKS PASSED ===${_nc}"
        return 0
    elif [[ $_smoke_fail -eq 0 ]]; then
        echo -e "${_yellow}=== Smoke probe: ${_smoke_warn} warning(s), 0 failures ===${_nc}"
        return 0
    else
        echo -e "${_red}=== Smoke probe: ${_smoke_fail} FAILURE(S), ${_smoke_warn} warning(s) ===${_nc}"
        echo -e "${_red}Install/upgrade is NOT safe to declare complete.${_nc}"
        echo -e "${_yellow}Review the failures above, fix, then re-run:${_nc}"
        echo "  sudo bash $(basename "${BASH_SOURCE[0]}")"
        return 1
    fi
}

# Allow direct invocation: `bash scripts/smoke_probe.sh` runs the probe.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    run_smoke_probe "$@"
fi
