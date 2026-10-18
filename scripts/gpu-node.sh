#!/bin/bash
# gpu-node.sh — Vast.ai GPU-node lifecycle for the translation pipeline.
#
# TWO USES, ONE SCRIPT (per .claude/rules/upgrade-consistency.md "New-Script
# Wiring Enforcement"):
#   1. Operator CLI, run by hand from the project tree (runbook below).
#   2. Installed worker: install.sh / upgrade.sh copy it to
#      ${AUDIOBOOKS_HOME}/scripts/ (it is in install-manifest.sh
#      CANONICAL_WORKERS) and scripts/translation-drain.sh — run by
#      audiobook-translation-drain.service/.timer — calls up / tunnel /
#      tunnel --stop / down --yes under its own label (abm-drain).
# Either way it resolves its files relative to its own location
# (../library/localization/stt/whisper_gpu_service.py), which is the project
# tree in use 1 and the installed tree in use 2 — never the other one.
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
# command line. Resolution order:
#   1. VAST_API_KEY in the environment;
#   2. VAST_API_KEY_FILE — a pointer to a file holding the key (the installed
#      drain gets this from derive-service-secret at service start); a pointer
#      that is set but unreadable or empty is an error, never a fallthrough;
#   3. operator-CLI fallback only: re-exec under the sanctioned injector
#      ~/.claude/bin/with-secret VAST_API_KEY -- scripts/gpu-node.sh <cmd>
#      (refuses env-dumping children, fails closed on a missing value). The Authorization header is handed to curl via a 0600 temp config
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
#   GPU_NODE_IMAGE      docker image                     (default: vastai/pytorch)
#   GPU_NODE_DISK_GB    instance disk, GB                (default: 80)
#   GPU_NODE_MODEL      vLLM model to serve              (default: Qwen/Qwen3-8B)
#   GPU_NODE_SSH_KEY    ssh -i identity file             (default: ssh default keys)
#   GPU_NODE_WHISPER_SRC whisper_gpu_service.py to ship  (default: ../library/localization/stt/)
#   GPU_NODE_PIP_TIMEOUT max seconds to wait for on-node pip (default: 1200)
#   GPU_NODE_MAX_LIFETIME on-node dead-man switch, seconds (default: 0 = off;
#                        the drain always sets it — see dead_man_script)
#   VAST_API_BASE       API base URL                     (default: console.vast.ai; tests only)
#   VAST_API_KEY_FILE   file holding the API key         (see CREDENTIALS)
# ---------------------------------------------------------------------------

set -uo pipefail

API_BASE="${VAST_API_BASE:-https://console.vast.ai/api/v0}"
LABEL="${GPU_NODE_LABEL:-abm-translate}"
GPU_NAME="${GPU_NODE_GPU:-H100 SXM}"
MAX_PRICE="${GPU_NODE_MAX_PRICE:-}"
IMAGE="${GPU_NODE_IMAGE:-vastai/pytorch}" # vllm/vllm-openai breaks Vast proxy-SSH (hard entrypoint)
DISK_GB="${GPU_NODE_DISK_GB:-80}"
VLLM_MODEL="${GPU_NODE_MODEL:-Qwen/Qwen3-8B}"
# Bound on the post-ssh wait for the onstart `pip install` to finish (seconds).
# Unbounded, a wedged pip kept `up` — and the billing node — waiting forever.
PIP_WAIT="${GPU_NODE_PIP_TIMEOUT:-1200}"
[[ "$PIP_WAIT" =~ ^[0-9]+$ ]] || PIP_WAIT=1200
# Forwarded ports, defined once: the tunnel opens them and `tunnel --stop`
# sweeps for them by signature. Two literals would let the sweep silently stop
# matching the tunnel it is meant to find.
MT_LOCAL_PORT="${GPU_NODE_MT_PORT:-8000}"
WHISPER_LOCAL_PORT="${GPU_NODE_WHISPER_PORT:-8765}"
TUNNEL_PIDFILE="${TMPDIR:-/tmp}/gpu-node-tunnel-${LABEL}.pid"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHISPER_SERVICE_SRC="${GPU_NODE_WHISPER_SRC:-${SCRIPT_DIR}/../library/localization/stt/whisper_gpu_service.py}"

die() {
    printf 'gpu-node: ERROR: %s\n' "$*" >&2
    exit 1
}
info() { printf 'gpu-node: %s\n' "$*"; }

# --- credential acquisition (never printed, never on a command line) --------
if [[ -z "${VAST_API_KEY:-}" && -n "${VAST_API_KEY_FILE:-}" ]]; then
    [[ -r "$VAST_API_KEY_FILE" ]] || die "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is not readable"
    VAST_API_KEY="$(tr -d '[:space:]' <"$VAST_API_KEY_FILE")"
    [[ -n "$VAST_API_KEY" ]] || die "VAST_API_KEY_FILE (${VAST_API_KEY_FILE}) is empty"
fi
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
trap 'rm -rf "${WORKDIR:?}"' EXIT
CURL_CONF="${WORKDIR}/hdr"
umask 077
printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
    "$VAST_API_KEY" >"$CURL_CONF"

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

# Newest RUNNING labeled instance, or "null". Used by tunnel/bootstrap-status,
# which need a machine that can actually answer.
our_live_instance() {
    our_instances | jq '[.[] | select((.actual_status // "") != "exited")] | sort_by(.id) | last'
}

# Newest labeled instance in ANY state, or "null". `down` must use this:
# an instance whose container has exited is NOT free — its disk keeps
# billing until the instance is DESTROYED, and Vast auto-stops instances
# when credit runs out, so the exact moment `down` matters most is the
# moment the old filter hid the instance and reported "nothing to destroy"
# (observed 2026-09-12 on instance 50749707).
our_any_instance() {
    our_instances | jq 'sort_by(.id) | last'
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
            cuda_max_good: {gte: 13.0},
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

# On-node dead-man switch (empty when GPU_NODE_MAX_LIFETIME is 0/unset).
#
# The host-side guard and reaper cannot act while the HOST is down (power
# loss, kernel panic, network outage), and Vast keeps billing. This runs ON
# THE NODE, needs nothing from the host, and after GPU_NODE_MAX_LIFETIME
# seconds of container uptime ends the instance itself, in order:
#   1. DESTROY via the Vast API with the instance's own CONTAINER_API_KEY
#      (Vast injects CONTAINER_ID/CONTAINER_API_KEY into every container and
#      documents `vastai destroy instance $CONTAINER_ID` from inside it). Our
#      account key never goes to the node. Destroy ends ALL billing.
#   2. if that fails: STOP the instance via the same key — GPU billing ends,
#      the instance's DISK STORAGE KEEPS BILLING until it is destroyed (the
#      host-side reaper does that once the host is back).
#   3. if that fails too: signal the container's PID 1. BEST-EFFORT ONLY: the
#      kernel drops SIGTERM/SIGKILL sent to a PID-namespace init from inside
#      its own namespace unless init installed a handler, so this often does
#      nothing. Steps 1-2 are the real mechanism.
# The lifetime counts from the FIRST boot, persisted on the instance disk
# (/workspace/.dead-man-boot): Vast re-runs onstart on every container
# restart, and a restart must not re-arm a fresh full lifetime.
# The log (/workspace/dead-man.log) records which step succeeded.
# Live behaviour of 1-3 against Vast is UNVERIFIED by these unit tests; the
# dev-VM test plan checks it with a short lifetime.
dead_man_script() {
    local lifetime="${GPU_NODE_MAX_LIFETIME:-0}"
    [[ "$lifetime" =~ ^[0-9]+$ && "$lifetime" -gt 0 ]] || return 0
    cat <<EOF
# --- dead-man switch: end this instance after ${lifetime}s, host or no host ---
cat > /workspace/dead-man.sh <<'DEADMAN'
#!/bin/bash
LIFETIME=${lifetime}
API="https://console.vast.ai/api/v0"
BOOT_FILE=/workspace/.dead-man-boot
log() { echo "\$(date -u '+%F %T') dead-man: \$*" >> /workspace/dead-man.log; }
# First boot writes the epoch; a restart reuses it (remaining time only).
[ -s "\$BOOT_FILE" ] || date +%s > "\$BOOT_FILE"
BOOT=\$(cat "\$BOOT_FILE")
case "\$BOOT" in ''|*[!0-9]*) BOOT=\$(date +%s); echo "\$BOOT" > "\$BOOT_FILE" ;; esac
REMAINING=\$(( LIFETIME - (\$(date +%s) - BOOT) ))
[ "\$REMAINING" -lt 0 ] && REMAINING=0
log "armed: lifetime \${LIFETIME}s from boot \${BOOT}, \${REMAINING}s remaining"
sleep "\$REMAINING"
log "lifetime \${LIFETIME}s reached — ending this instance"
# Vast's variables are in PID 1's environment even if onstart's was trimmed.
for v in CONTAINER_ID CONTAINER_API_KEY; do
  if [ -z "\${!v:-}" ]; then
    export "\$v=\$(tr '\\0' '\\n' < /proc/1/environ 2>/dev/null | sed -n "s/^\$v=//p" | head -n1)"
  fi
done
if [ -n "\${CONTAINER_ID:-}" ] && [ -n "\${CONTAINER_API_KEY:-}" ]; then
  hdr="Authorization: Bearer \$CONTAINER_API_KEY"
  if curl -fsS --max-time 30 -X DELETE -H "\$hdr" "\$API/instances/\$CONTAINER_ID/" >> /workspace/dead-man.log 2>&1; then
    log "RESULT step 1: destroy accepted by the Vast API"; sleep 120
  fi
  log "step 1 (destroy) did not end this instance — trying stop"
  if curl -fsS --max-time 30 -X PUT -H "\$hdr" -H 'Content-Type: application/json' \\
      -d '{"state":"stopped"}' "\$API/instances/\$CONTAINER_ID/" >> /workspace/dead-man.log 2>&1; then
    log "RESULT step 2: stop accepted (GPU billing ends; storage bills until destroyed)"; sleep 120
  fi
  log "step 2 (stop) did not end this instance either"
else
  log "CONTAINER_ID/CONTAINER_API_KEY not available — cannot call the Vast API"
fi
log "step 3 (best effort): signalling PID 1 — often ignored inside a container"
kill -TERM 1; sleep 30; kill -KILL 1
log "RESULT: still running after every step — this instance needs the host-side reaper"
DEADMAN
chmod 700 /workspace/dead-man.sh
setsid nohup /workspace/dead-man.sh > /dev/null 2>&1 < /dev/null &
EOF
}

onstart_script() {
    # Runs inside the container at boot. vLLM ships in the image; whisper deps
    # do not. whisper_gpu_service.py imports the `whisper` module
    # faster-whisper only: whisper_gpu_service.py was swapped to
    # faster-whisper's BatchedInferencePipeline in the same change set.
    # The dead-man switch goes first: it must be armed even if pip fails.
    cat <<EOF
#!/bin/bash
$(dead_man_script)
set -e
# vllm is a no-op on the vllm/vllm-openai image and a real install on
# generic CUDA images (vastai/pytorch fallback when proxy-SSH misbehaves)
pip install --no-cache-dir vllm faster-whisper flask >> /workspace/bootstrap.log 2>&1
# FLASH_ATTN backend: flashinfer JIT-compiles CUDA kernels at startup and
# fails on hosts whose local nvcc mismatches (observed on instance 50695675).
# 0.55 VRAM cap leaves ~35 GB for the whisper service on the same GPU.
VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN \
nohup python3 -m vllm.entrypoints.openai.api_server \
    --model ${VLLM_MODEL} --port 8000 --max-model-len 8192 \
    --gpu-memory-utilization 0.55 \
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
            --gpu)
                gpu="$2"
                shift 2
                ;;
            --max-price)
                max_price="$2"
                shift 2
                ;;
            --limit)
                limit="$2"
                shift 2
                ;;
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
            --offer)
                offer_id="$2"
                shift 2
                ;;
            --dry-run)
                dry_run=1
                shift
                ;;
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
            runtype: "ssh_direct",
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
    while ((waited < 1200)); do
        state="$(our_instances | jq -r --argjson id "$new_id" \
            '.[] | select(.id == $id) | .actual_status // "provisioning"')"
        [[ "$state" == "running" ]] && break
        printf 'gpu-node:   %ss elapsed, state=%s\n' "$waited" "${state:-unknown}"
        sleep 15
        waited=$((waited + 15))
    done
    [[ "$state" == "running" ]] || die "instance ${new_id} not running after ${waited}s (state: ${state:-unknown}); check status / consider '$0 down'"

    require_live_instance
    info "instance running: id=${INSTANCE_ID} ssh=root@${SSH_HOST} port ${SSH_PORT}"
    # The offer search filters on MAX_PRICE, but an instance has billed above
    # its listing before (2026-09-12: $6.74/hr against $2.70). Refuse it here;
    # the caller's teardown destroys it (the drain's trap; `down` by hand).
    if [[ -n "$MAX_PRICE" ]]; then
        local billed
        billed="$(our_live_instance | jq -r '.dph_total // 0')"
        if awk -v b="$billed" -v m="$MAX_PRICE" 'BEGIN { exit !(b > m * 1.02) }'; then
            die "instance ${INSTANCE_ID} bills ${billed} \$/hr, above GPU_NODE_MAX_PRICE ${MAX_PRICE} — refusing it (run '$0 down')"
        fi
    fi

    # Post-ssh bootstrap step: ship and start the whisper service.
    local -a opts
    mapfile -t opts < <(ssh_opts)
    info "deploying whisper_gpu_service.py..."
    scp "${opts[@]}" -P "$SSH_PORT" "$WHISPER_SERVICE_SRC" \
        "root@${SSH_HOST}:/workspace/whisper_gpu_service.py" \
        || die "scp failed — is your SSH public key registered at console.vast.ai -> Keys?"
    ssh "${opts[@]}" -p "$SSH_PORT" "root@${SSH_HOST}" \
        "t=0; while pgrep -f '[p]ip install' >/dev/null; do [ \$t -ge ${PIP_WAIT} ] && { echo 'pip install still running after ${PIP_WAIT}s' >&2; exit 3; }; sleep 10; t=\$((t + 10)); done; WHISPER_MODEL=large-v3 nohup python3 /workspace/whisper_gpu_service.py --model large-v3 --port 8765 > /workspace/whisper.log 2>&1 & sleep 1; echo whisper launched" \
        || die "whisper service launch failed (or on-node pip install exceeded ${PIP_WAIT}s)"

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
        local pid stopped=0
        if [[ -f "$TUNNEL_PIDFILE" ]]; then
            pid="$(cat "$TUNNEL_PIDFILE")"
            if kill "$pid" 2>/dev/null; then
                info "tunnel (pid ${pid}) stopped."
                stopped=$((stopped + 1))
            else
                info "tunnel pid ${pid} from pidfile was not running."
            fi
            rm -f "${TUNNEL_PIDFILE:?}"
        else
            info "no tunnel pidfile (${TUNNEL_PIDFILE})"
        fi

        # Sweep by signature as well, because the pidfile is not authoritative.
        # It lives under $TMPDIR, which differs between invocations, so a
        # tunnel started in one shell writes a pidfile the next one never
        # looks at; and a respawning keeper leaves the recorded pid stale
        # while a live process carries on. On 2026-09-13 this printed "was not
        # running" about a tunnel that was running, which is the failure this
        # whole script exists to avoid: a stop that cannot report a miss.
        # Bracketed class so the pgrep never matches itself.
        local -a orphans=()
        mapfile -t orphans < <(pgrep -f -- "-L ${MT_LOCAL_PORT}:localhost:${MT_LOCAL_PORT}" 2>/dev/null || true)
        local orphan
        for orphan in "${orphans[@]}"; do
            [[ -n "$orphan" ]] || continue
            # Match on IDENTITY, not on text. `pgrep -f` searches the whole
            # command line, so any shell, editor or grep that merely MENTIONS
            # the forward also matches — while testing this sweep it killed
            # the test harness itself, whose only crime was quoting the
            # pattern. Only a process whose executable really is ssh is a
            # tunnel. Killing the wrong process is far worse than leaving a
            # harmless orphan, so this filter must stay.
            [[ "$(cat "/proc/${orphan}/comm" 2>/dev/null)" == "ssh" ]] || continue
            [[ "$orphan" != "$$" ]] || continue
            if kill "$orphan" 2>/dev/null; then
                info "stopped orphaned tunnel pid ${orphan} (not in any pidfile)."
                stopped=$((stopped + 1))
            fi
        done

        [[ $stopped -gt 0 ]] || info "no tunnel process found."
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
        -L "${MT_LOCAL_PORT}:localhost:${MT_LOCAL_PORT}" \
        -L "${WHISPER_LOCAL_PORT}:localhost:${WHISPER_LOCAL_PORT}" "root@${SSH_HOST}" &
    local pid=$!
    disown "$pid"
    sleep 2
    kill -0 "$pid" 2>/dev/null || die "tunnel exited immediately — check SSH key registration at console.vast.ai -> Keys"
    printf '%s\n' "$pid" >"$TUNNEL_PIDFILE"
    info "tunnel up (pid ${pid}): 127.0.0.1:8000 -> vLLM, 127.0.0.1:8765 -> whisper"
    info "stop with: $0 tunnel --stop"
}

cmd_down() {
    local yes=0
    [[ "${1:-}" == "--yes" ]] && yes=1
    # EVERY instance under our label, not just the newest: a second instance
    # (an `up` killed mid-create whose PUT still landed, a race, a retry) bills
    # exactly like the first, and `down` is the only off switch.
    local all count
    all="$(our_instances)" || die "could not list instances"
    count="$(jq 'length' <<<"$all")"
    if [[ "$count" -eq 0 ]]; then
        info "no '${LABEL}' instance — nothing to destroy."
        info "closing tunnel (no node to point at)"
        cmd_tunnel --stop
        return 0
    fi

    if [[ "$yes" -ne 1 ]]; then
        printf 'gpu-node: destroy %s instance(s) labelled %s (%s)? [y/N] ' "$count" "$LABEL" \
            "$(jq -r '[.[] | "\(.id) at \(.dph_total // 0) $/hr"] | join(", ")' <<<"$all")"
        local reply
        read -r reply
        [[ "$reply" == "y" || "$reply" == "Y" ]] || die "aborted"
    fi

    local now failed=0 row id state dph start
    now="$(date +%s)"
    while IFS= read -r row; do
        id="$(jq -r '.id' <<<"$row")"
        state="$(jq -r '.actual_status // "unknown"' <<<"$row")"
        dph="$(jq -r '.dph_total // 0' <<<"$row")"
        start="$(jq -r '.start_date // 0' <<<"$row")"
        [[ "$state" == "exited" ]] && info "instance ${id} has exited (container stopped) but its DISK STILL BILLS — destroying."
        if ! vast_curl DELETE "/instances/${id}/" >/dev/null; then
            printf 'gpu-node: ERROR: destroy failed for instance %s\n' "$id" >&2
            failed=1
            continue
        fi
        info "instance ${id} destroyed."
        if [[ "$start" != "0" && "$start" != "null" ]]; then
            # Estimate only: elapsed runtime x hourly price. Vast bills storage
            # and bandwidth separately; the console has the authoritative number.
            awk -v s="$start" -v n="$now" -v d="$dph" 'BEGIN {
                h = (n - s) / 3600;
                printf "gpu-node: ran %.2f hours at %.3f $/hr -> estimated compute cost %.2f dollars (excludes storage/bandwidth)\n", h, d, h * d
            }'
        fi
    done < <(jq -c '.[]' <<<"$all")
    # Close the tunnel rather than advising someone to. The node it points at
    # no longer exists, so every surviving forwarder is now an infinite
    # reconnect loop against a destroyed host — and it is silent, costs no
    # money, and therefore nothing makes anyone go and read the advice.
    # Measured 2026-09-15: three such loops outlived a teardown by 38, 50 and
    # 87 hours, logging 27,632 failed reconnects and 1.8 MB before anyone
    # noticed, and they were found only because the terminal refused to exit.
    # A destroyed node and a live tunnel to it is never a state worth keeping,
    # so the teardown owns both ends.
    info "closing tunnel (the node it pointed at is gone)"
    cmd_tunnel --stop
    [[ $failed -eq 0 ]] || die "one or more '${LABEL}' instances could not be destroyed"
    return 0
}

# Machine-readable spend facts for the drain's ledger; never prints the key.
#   credit=<account credit, dollars>
#   instances=<n> accrued=<sum over labelled instances of uptime x dph_total>
#     dph=<sum of dph_total over labelled instances>
cmd_billing() {
    local credit rows
    credit="$(vast_curl GET "/users/current/" | jq -r '.credit // empty')" \
        || die "could not read account credit"
    [[ -n "$credit" ]] || die "account response had no credit field"
    rows="$(our_instances)" || die "could not list instances"
    printf 'credit=%s\n' "$credit"
    jq -r --argjson now "$(date +%s)" '
        "instances=\(length) accrued=\([.[] | ((($now - (.start_date // $now)) / 3600) * (.dph_total // 0))] | add // 0) dph=\([.[] | (.dph_total // 0)] | add // 0)"' <<<"$rows"
}

# Number of instances under our label, in any state (exited still bills).
cmd_count() {
    local rows
    rows="$(our_instances)" || die "could not list instances"
    jq 'length' <<<"$rows"
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
  billing             credit=<dollars> / instances=<n> accrued=<dollars> (for the drain ledger)
  count               number of '${LABEL}' instances in any state
  onstart             print the onstart script 'up' would send (incl. dead-man switch)

Label override: GPU_NODE_LABEL (current: ${LABEL})
EOF
}

case "${1:-}" in
    search)
        shift
        cmd_search "$@"
        ;;
    up)
        shift
        cmd_up "$@"
        ;;
    status)
        shift
        cmd_status "$@"
        ;;
    tunnel)
        shift
        cmd_tunnel "$@"
        ;;
    down)
        shift
        cmd_down "$@"
        ;;
    bootstrap-status)
        shift
        cmd_bootstrap_status "$@"
        ;;
    onstart)
        # Print the onstart script `up` would send (no API call).
        onstart_script
        ;;
    billing)
        shift
        cmd_billing "$@"
        ;;
    count)
        shift
        cmd_count "$@"
        ;;
    "" | -h | --help | help) usage ;;
    *)
        usage >&2
        die "unknown subcommand '${1}'"
        ;;
esac
