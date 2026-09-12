#!/bin/bash
# gpu-node.sh — Vast.ai GPU-node lifecycle for the translation pipeline.
#
# EXCEPTION (per .claude/rules/upgrade-consistency.md "New-Script Wiring
# Enforcement", clause 6): this is a stand-alone OPERATOR tool. It manages
# ephemeral rented cloud infrastructure (a Vast.ai GPU instance) that exists
# only for the duration of a translation batch. It is invoked by a human
# operator from the project tree, never by the installed application, and by
# design has:
#   - No systemd unit (the resource it manages is remote and ephemeral;
#     a unit on this host would outlive the thing it points at)
#   - No install.sh / upgrade.sh / install-manifest.sh entry (not part of
#     the installed application; runs from the project working tree)
#   - No dispatch hook (operator-invoked only)
#
# ---------------------------------------------------------------------------
# RUNBOOK (search -> up -> tunnel -> verify -> work -> down)
#
#   scripts/gpu-node.sh search                 # pick an offer (or trust "up")
#   scripts/gpu-node.sh up                     # rent best offer, bootstrap
#   scripts/gpu-node.sh status                 # state, $/hr, uptime, ports
#   scripts/gpu-node.sh tunnel                 # local 8000->vLLM, 8765->whisper
#   scripts/gpu-node.sh bootstrap-status       # vLLM models, whisper health, GPU
#   ... run the translation batch against http://127.0.0.1:8000 (vLLM
#       OpenAI-compatible API) and http://127.0.0.1:8765 (whisper) ...
#   scripts/gpu-node.sh tunnel --stop          # close the tunnel
#   scripts/gpu-node.sh down                   # DESTROY the instance (billing stops)
#
# COST EXPECTATIONS (measured 2026-09): a verified 1x H100 SXM ask runs
# ~1.74 dollars/hr (dph_total). Billing runs from create to DESTROY —
# a stopped instance still bills for storage. "down" is the only off switch.
#
# CREDENTIALS: the Vast.ai API key is NEVER printed and NEVER passed on a
# command line. The script expects VAST_API_KEY in its environment; when it
# is absent the script re-execs itself under the sanctioned injector:
#     ~/.claude/bin/with-secret VAST_API_KEY -- scripts/gpu-node.sh <cmd>
# (with-secret refuses env-dumping children and fails closed on a missing
# value.) The Authorization header is handed to curl via a 0600 temp config
# file so the key never appears in /proc/<pid>/cmdline.
#
# SSH: Vast instances accept the SSH public keys registered in your account
# (console.vast.ai -> Keys). If ssh/scp steps fail with "Permission denied",
# add your default public key (~/.ssh/id_*.pub) there. Connection is
# root@<ssh_host> -p <ssh_port> (usually a Vast SSH proxy that lands you
# inside the container).
#
# BOOTSTRAP SPLIT (why part onstart, part post-ssh):
#   - onstart (runs inside the container at boot, no files from us yet):
#       pip install whisper deps, then launch vLLM (already in the image).
#   - post-ssh (an "up" post-step once the instance reports running):
#       scp library/localization/stt/whisper_gpu_service.py to /workspace
#       and launch it. scp-after-running is simpler and more debuggable than
#       baking a base64 blob of the file into the onstart string, and it
#       always ships the current working-tree version of the service.
#
# QUALITY GATES: bash -n clean; shellcheck -S error clean; all network calls
# go through vast_curl() (key via temp config file); jq for all JSON.
#
# Environment overrides (all optional):
#   GPU_NODE_LABEL      instance label / ownership tag   (default: abm-translate)
#   GPU_NODE_GPU        gpu_name filter for search/up    (default: H100 SXM)
#   GPU_NODE_MAX_PRICE  max dph_total in dollars/hr      (default: unset = no cap)
#   GPU_NODE_IMAGE      docker image                     (default: vllm/vllm-openai:latest)
#   GPU_NODE_DISK_GB    instance disk, GB                (default: 80)
#   GPU_NODE_MODEL      vLLM model to serve              (default: Qwen/Qwen3-8B)
#   GPU_NODE_SSH_KEY    ssh -i identity file             (default: ssh default keys)
# ---------------------------------------------------------------------------

set -uo pipefail

API_BASE="https://console.vast.ai/api/v0"
LABEL="${GPU_NODE_LABEL:-abm-translate}"
GPU_NAME="${GPU_NODE_GPU:-H100 SXM}"
MAX_PRICE="${GPU_NODE_MAX_PRICE:-}"
IMAGE="${GPU_NODE_IMAGE:-vllm/vllm-openai:latest}"
DISK_GB="${GPU_NODE_DISK_GB:-80}"
VLLM_MODEL="${GPU_NODE_MODEL:-Qwen/Qwen3-8B}"
TUNNEL_PIDFILE="${TMPDIR:-/tmp}/gpu-node-tunnel-${LABEL}.pid"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHISPER_SERVICE_SRC="${SCRIPT_DIR}/../library/localization/stt/whisper_gpu_service.py"

die() { printf 'gpu-node: ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf 'gpu-node: %s\n' "$*"; }

# --- credential acquisition (never printed, never on a command line) --------
if [[ -z "${VAST_API_KEY:-}" ]]; then
    WITH_SECRET="${HOME}/.claude/bin/with-secret"
    [[ -x "$WITH_SECRET" ]] || WITH_SECRET="$(command -v with-secret || true)"
    [[ -n "$WITH_SECRET" ]] || die "VAST_API_KEY not in environment and with-secret not found.
Run: ~/.claude/bin/with-secret VAST_API_KEY -- $0 $*"
    # with-secret fails closed on a missing/empty value, so this cannot loop.
    exec "$WITH_SECRET" VAST_API_KEY -- "$0" "$@"
fi

command -v jq >/dev/null 2>&1 || die "jq is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

# Private scratch dir for the curl header config; removed on any exit.
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/gpu-node.XXXXXX")" || die "mktemp failed"
trap 'rm -rf "$WORKDIR"' EXIT
CURL_CONF="${WORKDIR}/hdr"
umask 077
printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
    "$VAST_API_KEY" > "$CURL_CONF"

# vast_curl METHOD PATH [json-body]
# The key rides in the 0600 config file, not argv.
vast_curl() {
    local method="$1" path="$2" body="${3:-}"
    local -a args=(-sS --fail-with-body --max-time 60 -K "$CURL_CONF" -X "$method")
    [[ -n "$body" ]] && args+=(--data-raw "$body")
    curl "${args[@]}" "${API_BASE}${path}"
}

# --- helpers ----------------------------------------------------------------

# All instances carrying our label, as a JSON array.
our_instances() {
    vast_curl GET "/instances/" \
        | jq --arg label "$LABEL" '[.instances[]? | select(.label == $label)]'
}

# Newest live labeled instance (exited/destroyed rows filtered), or "null".
our_live_instance() {
    our_instances | jq '[.[] | select((.actual_status // "") != "exited")] | sort_by(.id) | last'
}

build_search_query() {
    # Query JSON goes at the BODY TOP LEVEL of POST /bundles/ (verified live;
    # nesting it under "q" returns unfiltered results).
    local gpu="$1" max_price="$2" limit="$3"
    jq -n --arg gpu "$gpu" --argjson limit "$limit" '
        {
            gpu_name: {eq: $gpu},
            rentable: {eq: true},
            num_gpus: {eq: 1},
            verification: {eq: "verified"},
            type: "ask",
            order: [["dph_total", "asc"]],
            limit: $limit
        }' | if [[ -n "$max_price" ]]; then
            jq --argjson p "$max_price" '. + {dph_total: {lte: $p}}'
        else
            cat
        fi
}

onstart_script() {
    # Runs inside the container at boot. vLLM ships in the image; whisper deps
    # do not. whisper_gpu_service.py imports the `whisper` module
    # faster-whisper only: whisper_gpu_service.py was swapped to
    # faster-whisper's BatchedInferencePipeline in the same change set.
    cat <<EOF
#!/bin/bash
set -e
# vllm is a no-op on the vllm/vllm-openai image and a real install on
# generic CUDA images (vastai/pytorch fallback when proxy-SSH misbehaves)
pip install --no-cache-dir vllm faster-whisper flask >> /workspace/bootstrap.log 2>&1
nohup python3 -m vllm.entrypoints.openai.api_server \
    --model ${VLLM_MODEL} --port 8000 --max-model-len 8192 \
    > /workspace/vllm.log 2>&1 &
EOF
}

ssh_opts() {
    # Prints ssh option words, one per line, for mapfile consumption.
    printf '%s\n' -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
        -o ServerAliveInterval=30
    [[ -n "${GPU_NODE_SSH_KEY:-}" ]] && printf '%s\n' -i "$GPU_NODE_SSH_KEY"
    return 0
}

# Populates SSH_HOST/SSH_PORT/INSTANCE_ID from the live labeled instance.
require_live_instance() {
    local inst
    inst="$(our_live_instance)"
    [[ "$inst" != "null" && -n "$inst" ]] || die "no live '${LABEL}' instance found (run: $0 up)"
    INSTANCE_ID="$(jq -r '.id' <<<"$inst")"
    SSH_HOST="$(jq -r '.ssh_host // empty' <<<"$inst")"
    SSH_PORT="$(jq -r '.ssh_port // empty' <<<"$inst")"
    [[ -n "$SSH_HOST" && -n "$SSH_PORT" ]] || die "instance ${INSTANCE_ID} has no ssh endpoint yet (still booting?)"
}

# --- subcommands ------------------------------------------------------------

cmd_search() {
    local gpu="$GPU_NAME" max_price="$MAX_PRICE" limit=10
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --gpu) gpu="$2"; shift 2 ;;
            --max-price) max_price="$2"; shift 2 ;;
            --limit) limit="$2"; shift 2 ;;
            *) die "search: unknown option '$1'" ;;
        esac
    done
    local query resp
    query="$(build_search_query "$gpu" "$max_price" "$limit")"
    resp="$(vast_curl POST "/bundles/" "$query")" || die "offer search failed"
    printf '%-12s %-12s %-16s %-14s %s\n' "OFFER_ID" "PRICE(\$/hr)" "DOWN(Mbit/s)" "RELIABILITY" "GPU"
    jq -r '.offers[]? |
        [.id, (.dph_total * 1000 | round / 1000), (.inet_down | round),
         (.reliability2 * 10000 | round / 10000), .gpu_name] | @tsv' <<<"$resp" \
        | awk -F'\t' '{ printf "%-12s %-12s %-16s %-14s %s\n", $1, $2, $3, $4, $5 }'
    local n
    n="$(jq '.offers | length' <<<"$resp")"
    [[ "$n" -gt 0 ]] || info "no verified rentable offers matched (gpu='${gpu}'${max_price:+, max ${max_price} \$/hr})"
}

cmd_up() {
    local offer_id="" dry_run=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --offer) offer_id="$2"; shift 2 ;;
            --dry-run) dry_run=1; shift ;;
            *) die "up: unknown option '$1'" ;;
        esac
    done

    [[ -f "$WHISPER_SERVICE_SRC" ]] || die "whisper service not found at ${WHISPER_SERVICE_SRC}"

    # Refuse a second instance under our label — one node, one bill.
    local existing
    existing="$(our_live_instance)"
    if [[ "$existing" != "null" && -n "$existing" ]]; then
        die "a live '${LABEL}' instance already exists (id $(jq -r '.id' <<<"$existing"), state $(jq -r '.actual_status // "unknown"' <<<"$existing")). Use '$0 down' first."
    fi

    if [[ -z "$offer_id" ]]; then
        info "selecting best offer (gpu='${GPU_NAME}'${MAX_PRICE:+, max ${MAX_PRICE} \$/hr})..."
        local query
        query="$(build_search_query "$GPU_NAME" "$MAX_PRICE" 1)"
        offer_id="$(vast_curl POST "/bundles/" "$query" | jq -r '.offers[0].id // empty')"
        [[ -n "$offer_id" ]] || die "no matching offer found"
    fi

    local body
    # Create-instance body: client_id/image/disk/onstart/runtype/label are the
    # fields the vastai CLI "create instance" sends to PUT /asks/{id}/.
    # UNVERIFIED against a live create (task is read-only): "env" ({} accepted
    # as empty container env per CLI convention) and "label" placement — the
    # CLI sends label at top level; if create succeeds but status/down cannot
    # find the instance, check whether the API dropped the label.
    body="$(jq -n \
        --arg image "$IMAGE" \
        --arg onstart "$(onstart_script)" \
        --arg label "$LABEL" \
        --argjson disk "$DISK_GB" '
        {
            client_id: "me",
            image: $image,
            disk: $disk,
            label: $label,
            onstart: $onstart,
            runtype: "ssh",
            env: {}
        }')"

    if [[ "$dry_run" -eq 1 ]]; then
        info "DRY RUN — would PUT ${API_BASE}/asks/${offer_id}/ with body:"
        jq . <<<"$body"
        return 0
    fi

    info "creating instance from offer ${offer_id}..."
    local resp new_id
    resp="$(vast_curl PUT "/asks/${offer_id}/" "$body")" || die "create failed: $resp"
    new_id="$(jq -r '.new_contract // empty' <<<"$resp")"
    [[ -n "$new_id" ]] || die "create response had no new_contract: $resp"
    info "instance ${new_id} created — polling until running (this can take several minutes)..."

    local waited=0 state=""
    while (( waited < 1200 )); do
        state="$(our_instances | jq -r --argjson id "$new_id" \
            '.[] | select(.id == $id) | .actual_status // "provisioning"')"
        [[ "$state" == "running" ]] && break
        printf 'gpu-node:   %ss elapsed, state=%s\n' "$waited" "${state:-unknown}"
        sleep 15; waited=$(( waited + 15 ))
    done
    [[ "$state" == "running" ]] || die "instance ${new_id} not running after ${waited}s (state: ${state:-unknown}); check status / consider '$0 down'"

    require_live_instance
    info "instance running: id=${INSTANCE_ID} ssh=root@${SSH_HOST} port ${SSH_PORT}"

    # Post-ssh bootstrap step: ship and start the whisper service.
    local -a opts
    mapfile -t opts < <(ssh_opts)
    info "deploying whisper_gpu_service.py..."
    scp "${opts[@]}" -P "$SSH_PORT" "$WHISPER_SERVICE_SRC" \
        "root@${SSH_HOST}:/workspace/whisper_gpu_service.py" \
        || die "scp failed — is your SSH public key registered at console.vast.ai -> Keys?"
    ssh "${opts[@]}" -p "$SSH_PORT" "root@${SSH_HOST}" \
        "while pgrep -f 'pip install' >/dev/null; do sleep 10; done; WHISPER_MODEL=large-v3 nohup python3 /workspace/whisper_gpu_service.py --model large-v3 --port 8765 > /workspace/whisper.log 2>&1 & sleep 1; echo whisper launched" \
        || die "whisper service launch failed"

    info "up complete. Next: '$0 tunnel' then '$0 bootstrap-status'."
    info "REMINDER: billing (~1.74 \$/hr for verified H100 SXM) runs until '$0 down'."
}

cmd_status() {
    local rows
    rows="$(our_instances)"
    if [[ "$(jq 'length' <<<"$rows")" -eq 0 ]]; then
        info "no '${LABEL}' instances."
        return 0
    fi
    jq -r --arg now "$(date +%s)" '.[] |
        "id: \(.id)\n" +
        "  state:        \(.actual_status // "provisioning") (intended: \(.intended_status // "?"))\n" +
        "  price:        \(.dph_total // 0 | . * 1000 | round / 1000) $/hr\n" +
        "  uptime:       " +
            (if .start_date then
                ((($now | tonumber) - .start_date) as $s |
                 "\($s / 3600 | floor)h \(($s % 3600) / 60 | floor)m")
             else "n/a" end) + "\n" +
        "  ssh:          root@\(.ssh_host // "?") port \(.ssh_port // "?")\n" +
        "  gpu:          \(.gpu_name // "?")\n" +
        "  ports:        \(.ports // {} | to_entries |
            map("\(.key)->\(.value[0].HostPort // "?")") | join(", ") |
            if . == "" then "(none mapped)" else . end)"' <<<"$rows"
}

cmd_tunnel() {
    if [[ "${1:-}" == "--stop" ]]; then
        [[ -f "$TUNNEL_PIDFILE" ]] || { info "no tunnel pidfile (${TUNNEL_PIDFILE})"; return 0; }
        local pid
        pid="$(cat "$TUNNEL_PIDFILE")"
        if kill "$pid" 2>/dev/null; then
            info "tunnel (pid ${pid}) stopped."
        else
            info "tunnel pid ${pid} was not running."
        fi
        rm -f "$TUNNEL_PIDFILE"
        return 0
    fi
    [[ $# -eq 0 ]] || die "tunnel: unknown option '$1' (only --stop)"

    if [[ -f "$TUNNEL_PIDFILE" ]] && kill -0 "$(cat "$TUNNEL_PIDFILE")" 2>/dev/null; then
        die "tunnel already running (pid $(cat "$TUNNEL_PIDFILE")); '$0 tunnel --stop' first"
    fi

    require_live_instance
    local -a opts
    mapfile -t opts < <(ssh_opts)
    # Deliberately backgrounded with & rather than ssh -f: -f double-forks and
    # hides the final PID, which would leave nothing truthful to put in the
    # pidfile. & + $! gives the real, killable PID (same detached effect).
    ssh -N "${opts[@]}" -o ExitOnForwardFailure=yes -p "$SSH_PORT" \
        -L 8000:localhost:8000 -L 8765:localhost:8765 "root@${SSH_HOST}" &
    local pid=$!
    disown "$pid"
    sleep 2
    kill -0 "$pid" 2>/dev/null || die "tunnel exited immediately — check SSH key registration at console.vast.ai -> Keys"
    printf '%s\n' "$pid" > "$TUNNEL_PIDFILE"
    info "tunnel up (pid ${pid}): 127.0.0.1:8000 -> vLLM, 127.0.0.1:8765 -> whisper"
    info "stop with: $0 tunnel --stop"
}

cmd_down() {
    local yes=0
    [[ "${1:-}" == "--yes" ]] && yes=1
    local inst
    inst="$(our_live_instance)"
    [[ "$inst" != "null" && -n "$inst" ]] || { info "no live '${LABEL}' instance — nothing to destroy."; return 0; }
    local id dph start now
    id="$(jq -r '.id' <<<"$inst")"
    dph="$(jq -r '.dph_total // 0' <<<"$inst")"
    start="$(jq -r '.start_date // 0' <<<"$inst")"
    now="$(date +%s)"

    if [[ "$yes" -ne 1 ]]; then
        printf 'gpu-node: destroy instance %s (label %s, %s $/hr)? [y/N] ' "$id" "$LABEL" "$dph"
        local reply
        read -r reply
        [[ "$reply" == "y" || "$reply" == "Y" ]] || die "aborted"
    fi

    vast_curl DELETE "/instances/${id}/" >/dev/null || die "destroy failed for instance ${id}"
    info "instance ${id} destroyed."
    if [[ "$start" != "0" && "$start" != "null" ]]; then
        # Estimate only: elapsed runtime x hourly price. Vast bills storage
        # and bandwidth separately; the console has the authoritative number.
        awk -v s="$start" -v n="$now" -v d="$dph" 'BEGIN {
            h = (n - s) / 3600;
            printf "gpu-node: ran %.2f hours at %.3f $/hr -> estimated compute cost %.2f dollars (excludes storage/bandwidth)\n", h, d, h * d
        }'
    fi
    [[ -f "$TUNNEL_PIDFILE" ]] && info "note: tunnel pidfile still present — run '$0 tunnel --stop'"
    return 0
}

cmd_bootstrap_status() {
    require_live_instance
    local -a opts
    mapfile -t opts < <(ssh_opts)
    info "instance ${INSTANCE_ID} (root@${SSH_HOST} port ${SSH_PORT}):"
    ssh "${opts[@]}" -p "$SSH_PORT" "root@${SSH_HOST}" '
        echo "--- GPU (nvidia-smi) ---"
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1 || echo "nvidia-smi failed"
        echo "--- vLLM /v1/models (port 8000) ---"
        curl -sS --max-time 10 http://localhost:8000/v1/models 2>&1 || echo "(vLLM not answering — check /workspace/vllm.log; model download can take minutes)"
        echo
        echo "--- whisper /health (port 8765) ---"
        curl -sS --max-time 10 http://localhost:8765/health 2>&1 || echo "(whisper not answering — check /workspace/whisper.log)"
        echo
    ' || die "ssh failed — is your SSH public key registered at console.vast.ai -> Keys?"
}

usage() {
    cat <<EOF
Usage: $0 <subcommand> [options]

  search [--gpu NAME] [--max-price N] [--limit N]
                      List cheapest verified rentable 1-GPU offers
  up [--offer ID] [--dry-run]
                      Create instance (best or given offer), bootstrap vLLM +
                      whisper; refuses if a '${LABEL}' instance already exists
  status              Show our labeled instance(s): state, \$/hr, uptime, ports
  tunnel [--stop]     SSH tunnel 127.0.0.1:8000 -> vLLM, 127.0.0.1:8765 -> whisper
  down [--yes]        DESTROY our labeled instance (stops billing)
  bootstrap-status    ssh in: vLLM /v1/models, whisper /health, GPU name

Label override: GPU_NODE_LABEL (current: ${LABEL})
EOF
}

case "${1:-}" in
    search)            shift; cmd_search "$@" ;;
    up)                shift; cmd_up "$@" ;;
    status)            shift; cmd_status "$@" ;;
    tunnel)            shift; cmd_tunnel "$@" ;;
    down)              shift; cmd_down "$@" ;;
    bootstrap-status)  shift; cmd_bootstrap_status "$@" ;;
    ""|-h|--help|help) usage ;;
    *)                 usage >&2; die "unknown subcommand '${1}'" ;;
esac
