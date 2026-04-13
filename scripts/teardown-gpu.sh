#!/bin/bash
# teardown-gpu.sh — Destroy Vast.ai instances and stop RunPod pods
#
# Called by translation-daemon.sh after queue is empty and verified.
# Prevents paying for idle GPU resources.
#
# Usage:
#   ./scripts/teardown-gpu.sh          # destroy all
#   ./scripts/teardown-gpu.sh --dry-run # show what would be destroyed

set -uo pipefail

source "${HOME}/.config/api-keys.env"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log() { echo "$(date +%H:%M:%S) [teardown] $*"; }

destroyed=0
failed=0

# ── Vast.ai Instance Teardown ───────────────────────────────────────────────
if [ -n "${VAST_API_KEY:-}" ]; then
    log "Checking Vast.ai instances..."
    instances=$(curl -s -H "Authorization: Bearer $VAST_API_KEY" \
        "https://console.vast.ai/api/v0/instances/?owner=me" 2>/dev/null)

    if [ -z "$instances" ] || echo "$instances" | grep -q '"error"'; then
        log "WARNING: Failed to list Vast.ai instances"
    else
        # Extract instance IDs (jq may not be installed — use python)
        ids=$(echo "$instances" | python3 -c "
import json, sys
data = json.load(sys.stdin)
instances = data.get('instances', data) if isinstance(data, dict) else data
for inst in (instances if isinstance(instances, list) else []):
    iid = inst.get('id')
    label = inst.get('label', inst.get('machine_id', 'unknown'))
    status = inst.get('actual_status', inst.get('status_msg', ''))
    print(f'{iid}|{label}|{status}')
" 2>/dev/null)

        if [ -z "$ids" ]; then
            log "No active Vast.ai instances found"
        else
            while IFS='|' read -r inst_id label status; do
                if $DRY_RUN; then
                    log "DRY RUN: Would destroy Vast.ai instance $inst_id ($label, $status)"
                else
                    log "Destroying Vast.ai instance $inst_id ($label, $status)..."
                    result=$(curl -s -X DELETE \
                        -H "Authorization: Bearer $VAST_API_KEY" \
                        "https://console.vast.ai/api/v0/instances/$inst_id/" 2>/dev/null)
                    if echo "$result" | grep -qi 'success\|true\|ok\|destroyed' 2>/dev/null || [ -z "$result" ]; then
                        log "  Destroyed: $inst_id"
                        ((destroyed++))
                    else
                        log "  WARNING: Destroy may have failed: $result"
                        ((failed++))
                    fi
                fi
            done <<< "$ids"
        fi
    fi
else
    log "WARNING: VAST_API_KEY not set — skipping Vast.ai teardown"
fi

# ── RunPod Pod Teardown ─────────────────────────────────────────────────────
if [ -n "${RUNPOD_API_KEY:-}" ]; then
    log "Checking RunPod pods..."
    pods=$(curl -s -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"query":"query { myself { pods { id name desiredStatus } } }"}' 2>/dev/null)

    if [ -z "$pods" ] || echo "$pods" | grep -q '"errors"'; then
        log "WARNING: Failed to list RunPod pods"
    else
        pod_ids=$(echo "$pods" | python3 -c "
import json, sys
data = json.load(sys.stdin)
pods = data.get('data', {}).get('myself', {}).get('pods', [])
for pod in pods:
    print(f'{pod[\"id\"]}|{pod.get(\"name\", \"unknown\")}|{pod.get(\"desiredStatus\", \"\")}')
" 2>/dev/null)

        if [ -z "$pod_ids" ]; then
            log "No active RunPod pods found"
        else
            while IFS='|' read -r pod_id name status; do
                if $DRY_RUN; then
                    log "DRY RUN: Would terminate RunPod pod $pod_id ($name, $status)"
                else
                    log "Terminating RunPod pod $pod_id ($name, $status)..."
                    result=$(curl -s -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
                        -H "Content-Type: application/json" \
                        -d "{\"query\":\"mutation { podTerminate(input: {podId: \\\"$pod_id\\\"}) }\"}" 2>/dev/null)
                    if echo "$result" | grep -q '"errors"' 2>/dev/null; then
                        log "  WARNING: Terminate may have failed: $result"
                        ((failed++))
                    else
                        log "  Terminated: $pod_id ($name)"
                        ((destroyed++))
                    fi
                fi
            done <<< "$pod_ids"
        fi
    fi
else
    log "WARNING: RUNPOD_API_KEY not set — skipping RunPod teardown"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
log "═══════════════════════════════════════"
if $DRY_RUN; then
    log "DRY RUN complete — no instances destroyed"
else
    log "Destroyed: $destroyed instances/pods"
    [ "$failed" -gt 0 ] && log "Failed: $failed (check logs)"
fi
log "═══════════════════════════════════════"

exit 0
