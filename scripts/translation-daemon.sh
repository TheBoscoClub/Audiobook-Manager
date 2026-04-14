#!/bin/bash
# translation-daemon.sh — Persistent translation pipeline daemon
#
# Manages SSH tunnels + batch-translate workers across Vast.ai and RunPod
# GPU instances. Designed to run as a systemd service that survives reboots.
#
# Features:
#   - Auto-restarts dead tunnels and workers every check cycle
#   - Stops cleanly when the translation queue is empty
#   - Logs per-worker and aggregate progress
#   - Graceful shutdown on SIGTERM/SIGINT
#
# Usage:
#   systemctl start audiobook-translate   # via systemd (preferred)
#   ./scripts/translation-daemon.sh       # manual foreground
#
# Stop:
#   systemctl stop audiobook-translate
#   kill $(cat /var/lib/audiobooks/.run/translate-daemon.pid)

set -uo pipefail

# ── Configuration Defaults ──────────────────────────────────────────────────
# These defaults work with a standard /opt/audiobooks installation.
# Override any of them in /etc/audiobooks/scripts/translation-env.sh
# (see etc/translation-env.sh.example for documentation).

# Resolve SCRIPT_DIR for finding sibling scripts (batch-translate.py, etc.)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DB_PATH="/var/lib/audiobooks/db/audiobooks.db"
LIBRARY_PATH="/srv/audiobooks/Library"
BATCH_SCRIPT="${SCRIPT_DIR}/batch-translate.py"
VENV_PYTHON="/opt/audiobooks/library/venv/bin/python"
SSH_KEY=""
LOG_DIR="/var/log/audiobooks/translate"
PID_FILE="/var/lib/audiobooks/.run/translate-daemon.pid"
HEALTH_CHECK_INTERVAL=120
EMPTY_QUEUE_CHECKS=3
AUTO_TEARDOWN_GPU=false
GPU_API_KEYS_FILE=""
NOTIFY_EMAIL=""
POST_COMPLETION_HOOK=""

# GPU instance arrays — empty by default, populated by site-local config
declare -a VASTAI_INSTANCES=()
declare -a RUNPOD_INSTANCES=()

# ── Load Site-Local Config ──────────────────────────────────────────────────
# Users customize their GPU infrastructure, paths, and notification settings
# in this file. It persists across upgrades (lives in /etc/audiobooks/).
TRANSLATION_ENV="${AUDIOBOOKS_TRANSLATION_ENV:-/etc/audiobooks/scripts/translation-env.sh}"
if [[ -f "$TRANSLATION_ENV" ]]; then
    # shellcheck source=/dev/null
    source "$TRANSLATION_ENV"
else
    echo "ERROR: No translation environment config found at $TRANSLATION_ENV" >&2
    echo "Copy etc/translation-env.sh.example to /etc/audiobooks/scripts/translation-env.sh" >&2
    echo "and configure your GPU instances. See the example file for documentation." >&2
    exit 1
fi

# Validate minimum config
if [[ ${#VASTAI_INSTANCES[@]} -eq 0 && ${#RUNPOD_INSTANCES[@]} -eq 0 && -z "${LOCAL_WHISPER_URL:-}" ]]; then
    echo "ERROR: No GPU instances configured in $TRANSLATION_ENV" >&2
    echo "Add VASTAI_INSTANCES, RUNPOD_INSTANCES, or LOCAL_WHISPER_URL entries." >&2
    exit 1
fi

# ── State ────────────────────────────────────────────────────────────────────
declare -A TUNNEL_PIDS
declare -A WORKER_PIDS
SHUTDOWN=false
EMPTY_COUNT=0

# ── Logging ──────────────────────────────────────────────────────────────────
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [daemon] $*"; }
log_worker() { echo "$(date '+%Y-%m-%d %H:%M:%S') [worker:$1] $2"; }

# ── Graceful Shutdown ────────────────────────────────────────────────────────
# cleanup_workers_tunnels stops workers and tunnels without exiting.
# Called by both the signal handler (immediate exit) and the normal
# completion path (which continues to verification/teardown after cleanup).
cleanup_workers_tunnels() {
    log "Stopping all workers and tunnels"
    SHUTDOWN=true

    # Kill workers first (they'll finish current chapter)
    for label in "${!WORKER_PIDS[@]}"; do
        local pid=${WORKER_PIDS[$label]}
        if kill -0 "$pid" 2>/dev/null; then
            log "Stopping worker $label (PID $pid)"
            kill "$pid" 2>/dev/null
        fi
    done

    # Wait up to 30s for workers to finish current chapter
    local waited=0
    while [ $waited -lt 30 ]; do
        local alive=0
        for pid in "${WORKER_PIDS[@]}"; do
            kill -0 "$pid" 2>/dev/null && ((alive++))
        done
        [ "$alive" -eq 0 ] && break
        sleep 2
        waited=$((waited + 2))
        log "Waiting for $alive workers to finish... (${waited}s)"
    done

    # Force kill any remaining
    for pid in "${WORKER_PIDS[@]}"; do
        kill -9 "$pid" 2>/dev/null
    done

    # Kill tunnels
    for pid in "${TUNNEL_PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done

    # Reset any stuck processing jobs back to pending
    sqlite3 "$DB_PATH" \
        "UPDATE translation_queue SET state = 'pending', started_at = NULL WHERE state = 'processing';" 2>/dev/null

    rm -f "$PID_FILE"
    log "Cleanup complete"
}

# Signal handler — immediate exit (skips post-completion pipeline)
shutdown_handler() {
    log "Shutdown signal received"
    cleanup_workers_tunnels
    exit 0
}
trap shutdown_handler SIGTERM SIGINT

# ── Tunnel Management ────────────────────────────────────────────────────────
start_tunnel() {
    local local_port=$1 ssh_port=$2 ssh_host=$3 label=$4

    # Check if tunnel is already alive
    local pid=${TUNNEL_PIDS[$label]:-0}
    if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    log "Starting SSH tunnel $label (localhost:$local_port -> $ssh_host:$ssh_port)"
    nohup ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -p "$ssh_port" -N -L "127.0.0.1:${local_port}:localhost:8000" \
        "root@${ssh_host}" > "$LOG_DIR/tunnel-${label}.log" 2>&1 &
    TUNNEL_PIDS[$label]=$!
    log "Tunnel $label started (PID $!)"
}

check_tunnel_health() {
    local local_port=$1 label=$2
    local health
    health=$(curl -s --connect-timeout 5 --max-time 15 "http://127.0.0.1:${local_port}/health" 2>/dev/null)
    if echo "$health" | grep -q '"ok"' 2>/dev/null; then
        return 0
    fi
    return 1
}

# ── Whisper Server Management ────────────────────────────────────────────────
ensure_whisper_server() {
    local ssh_port=$1 ssh_host=$2 label=$3 compute_type=$4

    # Check if server is running on remote
    local health
    health=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        -p "$ssh_port" "root@$ssh_host" "curl -s --max-time 10 http://localhost:8000/health" 2>/dev/null)

    if echo "$health" | grep -q '"ok"' 2>/dev/null; then
        return 0
    fi

    log "Whisper server not running on $label — starting it"

    # Upload server script
    local script="/tmp/whisper_server_${compute_type}.py"
    if [ ! -f "$script" ]; then
        create_whisper_script "$compute_type"
    fi
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        -P "$ssh_port" "$script" "root@${ssh_host}:/root/whisper_server.py" 2>/dev/null

    # Start gunicorn (separate from kill to avoid exit-code issues)
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        -p "$ssh_port" "root@$ssh_host" "pkill -9 gunicorn 2>/dev/null; echo killed" 2>/dev/null
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        -p "$ssh_port" "root@$ssh_host" \
        "cd /root; gunicorn -w 1 -b 0.0.0.0:8000 --timeout 600 whisper_server:app >> /var/log/whisper.log 2>&1 & echo PID=\$!" 2>/dev/null

    log "Whisper server started on $label — waiting for model load"
}

create_whisper_script() {
    local ct=$1
    cat > "/tmp/whisper_server_${ct}.py" << PYEOF
import tempfile, os, logging
from flask import Flask, request, jsonify
from faster_whisper import WhisperModel
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
model = WhisperModel('large-v3', device='cuda', compute_type='${ct}')
@app.route('/v1/audio/transcriptions', methods=['POST'])
def transcribe():
    audio_file = request.files.get('file')
    if not audio_file: return jsonify({'error': 'No file'}), 400
    language = request.form.get('language', 'en')
    with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as tmp:
        audio_file.save(tmp.name); tmp_path = tmp.name
    try:
        segments, info = model.transcribe(tmp_path, language=language, word_timestamps=True)
        words, text_parts = [], []
        for seg in segments:
            text_parts.append(seg.text)
            for w in (seg.words or []): words.append({'word': w.word, 'start': w.start, 'end': w.end})
        return jsonify({'text': ' '.join(text_parts), 'language': info.language, 'duration': info.duration, 'words': words})
    finally: os.unlink(tmp_path)
@app.route('/health')
def health(): return jsonify({'status': 'ok', 'model': 'large-v3'})
PYEOF
}

# ── Worker Management ────────────────────────────────────────────────────────
start_worker() {
    local host=$1 port=$2 label=$3

    # Check if worker is already alive
    local pid=${WORKER_PIDS[$label]:-0}
    if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    log "Starting worker $label (host=$host port=$port)"
    nohup env \
        AUDIOBOOKS_VASTAI_WHISPER_HOST="$host" \
        AUDIOBOOKS_VASTAI_WHISPER_PORT="$port" \
        AUDIOBOOKS_WHISPER_GPU_HOST=127.0.0.1 \
        AUDIOBOOKS_WHISPER_GPU_PORT=8765 \
        "$VENV_PYTHON" "$BATCH_SCRIPT" \
        --db "$DB_PATH" --library "$LIBRARY_PATH" \
        > "$LOG_DIR/worker-${label}.log" 2>&1 &
    WORKER_PIDS[$label]=$!
    log "Worker $label started (PID $!)"
}

# ── Queue Status ─────────────────────────────────────────────────────────────
get_queue_status() {
    sqlite3 "$DB_PATH" \
        "SELECT state, COUNT(*) FROM translation_queue GROUP BY state;" 2>/dev/null
}

get_subtitle_counts() {
    local en zh
    en=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM chapter_subtitles WHERE locale='en';" 2>/dev/null)
    zh=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM chapter_subtitles WHERE locale='zh-Hans';" 2>/dev/null)
    echo "en=$en zh-Hans=$zh"
}

# ── Main Loop ────────────────────────────────────────────────────────────────
main() {
    mkdir -p "$LOG_DIR" "$(dirname "$PID_FILE")"
    echo $$ > "$PID_FILE"

    log "═══════════════════════════════════════════════════════════════"
    log "Translation Daemon starting"
    log "Vast.ai instances: ${#VASTAI_INSTANCES[@]}"
    log "RunPod instances: ${#RUNPOD_INSTANCES[@]}"
    log "═══════════════════════════════════════════════════════════════"

    # Create whisper server scripts
    create_whisper_script "float16"
    create_whisper_script "auto"

    # Initial setup: start all tunnels and ensure whisper servers
    for instance in "${VASTAI_INSTANCES[@]}"; do
        IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"
        start_tunnel "$local_port" "$ssh_port" "$ssh_host" "$label"
    done

    # Wait for tunnels to establish
    sleep 5

    # Ensure whisper servers are running on all Vast.ai instances
    local -a whisper_pids=()
    for instance in "${VASTAI_INSTANCES[@]}"; do
        IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"
        ensure_whisper_server "$ssh_port" "$ssh_host" "$label" "$compute_type" &
        whisper_pids+=($!)
    done
    # Wait ONLY for the ensure_whisper_server jobs (not SSH tunnel background jobs)
    for pid in "${whisper_pids[@]}"; do
        wait "$pid" 2>/dev/null
    done

    # Wait for model loading
    log "Waiting 30s for whisper models to load..."
    sleep 30

    # Start workers for Vast.ai instances
    for instance in "${VASTAI_INSTANCES[@]}"; do
        IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"
        if check_tunnel_health "$local_port" "$label"; then
            start_worker "127.0.0.1" "$local_port" "$label"
        else
            log "WARNING: $label tunnel unhealthy — skipping worker"
        fi
    done

    # Start workers for RunPod instances
    for instance in "${RUNPOD_INSTANCES[@]}"; do
        IFS='|' read -r url label <<< "$instance"
        # Health check RunPod
        local health
        health=$(curl -s --connect-timeout 10 --max-time 30 "$url/health" 2>/dev/null)
        if echo "$health" | grep -q '"ok"' 2>/dev/null; then
            start_worker "$url" "0" "$label"
        else
            log "WARNING: $label not reachable — skipping"
        fi
    done

    log "Initial setup complete — entering monitoring loop"
    log "$(get_queue_status)"

    # ── Monitoring Loop ──────────────────────────────────────────────────────
    while ! $SHUTDOWN; do
        sleep "$HEALTH_CHECK_INTERVAL"
        $SHUTDOWN && break

        # Check queue
        local pending processing completed failed
        pending=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='pending';" 2>/dev/null)
        processing=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='processing';" 2>/dev/null)
        completed=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='completed';" 2>/dev/null)
        failed=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='failed';" 2>/dev/null)

        log "Queue: ${pending:-0} pending, ${processing:-0} processing, ${completed:-0} completed, ${failed:-0} failed | $(get_subtitle_counts)"

        # Check if queue is empty
        if [ "${pending:-0}" -eq 0 ] && [ "${processing:-0}" -eq 0 ]; then
            ((EMPTY_COUNT++))
            log "Queue empty (check $EMPTY_COUNT/$EMPTY_QUEUE_CHECKS)"
            if [ "$EMPTY_COUNT" -ge "$EMPTY_QUEUE_CHECKS" ]; then
                log "Queue confirmed empty — auto-stopping"
                break
            fi
        else
            EMPTY_COUNT=0
        fi

        # Health-check and restart dead tunnels. Three phases keep the
        # expensive work (whisper server restart + 15s model-load wait)
        # parallel while PID-map updates stay serial in the parent, because
        # bash subshells can't mutate parent associative arrays.
        #
        # Prior serial loop cost 8 × ~18s = ~144s per recovery pass; now the
        # slow phase collapses to ~18s regardless of instance count.

        # Phase 1 (serial, fast): spawn tunnel restarts for dead tunnels.
        # start_tunnel is non-blocking (nohup ssh &), so this is ms-scale.
        for instance in "${VASTAI_INSTANCES[@]}"; do
            IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"
            local tpid=${TUNNEL_PIDS[$label]:-0}
            if [ "$tpid" -gt 0 ] && ! kill -0 "$tpid" 2>/dev/null; then
                log "Tunnel $label died — restarting"
                start_tunnel "$local_port" "$ssh_port" "$ssh_host" "$label"
            fi
        done
        sleep 3  # single shared settle window for all tunnels

        # Phase 2 (parallel, slow): whisper health-check + model-load wait.
        # This is the old per-instance 15s sleep; now runs concurrently for
        # every unhealthy instance. Pure side-effect on the remote GPU — no
        # parent state mutated here.
        for instance in "${VASTAI_INSTANCES[@]}"; do
            IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"
            (
                if ! check_tunnel_health "$local_port" "$label"; then
                    log "Tunnel $label unhealthy — checking whisper server"
                    ensure_whisper_server "$ssh_port" "$ssh_host" "$label" "$compute_type"
                    sleep 15  # model load time
                fi
            ) &
        done
        wait

        # Phase 3 (serial, fast): restart dead workers. start_worker is also
        # non-blocking, and must update the parent's WORKER_PIDS map.
        for instance in "${VASTAI_INSTANCES[@]}"; do
            IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"
            local wpid=${WORKER_PIDS[$label]:-0}
            if [ "$wpid" -gt 0 ] && ! kill -0 "$wpid" 2>/dev/null; then
                log "Worker $label died — restarting"
                if check_tunnel_health "$local_port" "$label"; then
                    start_worker "127.0.0.1" "$local_port" "$label"
                fi
            fi
        done

        # Health-check RunPod workers
        for instance in "${RUNPOD_INSTANCES[@]}"; do
            IFS='|' read -r url label <<< "$instance"
            local wpid=${WORKER_PIDS[$label]:-0}
            if [ "$wpid" -gt 0 ] && ! kill -0 "$wpid" 2>/dev/null; then
                log "Worker $label died — restarting"
                local health
                health=$(curl -s --connect-timeout 10 --max-time 15 "$url/health" 2>/dev/null)
                if echo "$health" | grep -q '"ok"' 2>/dev/null; then
                    start_worker "$url" "0" "$label"
                else
                    log "WARNING: $label not reachable — skipping"
                fi
            fi
        done
    done

    log "═══════════════════════════════════════════════════════════════"
    log "Translation pipeline complete"
    log "Final: $(get_queue_status)"
    log "Subtitles: $(get_subtitle_counts)"
    log "═══════════════════════════════════════════════════════════════"

    # Stop workers and tunnels (but don't exit — run post-completion pipeline)
    cleanup_workers_tunnels

    # ── Post-Completion Pipeline ────────────────────────────────────────────
    # Run verification with proof
    log "Running translation verification..."
    local verify_script="${SCRIPT_DIR}/verify-translations.py"
    if [[ -f "$verify_script" ]]; then
        "$VENV_PYTHON" "$verify_script" --db "$DB_PATH" --json --fix 2>&1 | tee -a "$LOG_DIR/verify-$(date +%Y%m%d-%H%M%S).log"
        local verify_exit=$?
        if [[ $verify_exit -eq 0 ]]; then
            log "VERIFICATION PASSED — all translations verified with proof"
        else
            log "VERIFICATION FOUND FAILURES — failed books re-queued for retry"
            log "Check verification report: $(dirname "$DB_PATH")/translation-verification.json"
        fi
    else
        log "WARNING: verify-translations.py not found at $verify_script"
    fi

    # Email verification report (if NOTIFY_EMAIL configured)
    if [[ -n "$NOTIFY_EMAIL" ]]; then
        local email_script="${SCRIPT_DIR}/email-report.py"
        local report_json="$(dirname "$DB_PATH")/translation-verification.json"
        if [[ -f "$email_script" && -f "$report_json" ]]; then
            log "Emailing verification report to $NOTIFY_EMAIL..."
            "$VENV_PYTHON" "$email_script" \
                --to "$NOTIFY_EMAIL" \
                --report "$report_json" 2>&1 || log "WARNING: Email send failed"
        fi
    fi

    # Tear down GPU instances to stop billing (if AUTO_TEARDOWN_GPU enabled)
    if [[ "$AUTO_TEARDOWN_GPU" == "true" ]]; then
        local teardown_script="${SCRIPT_DIR}/teardown-gpu.sh"
        if [[ -f "$teardown_script" ]]; then
            log "Tearing down GPU instances to stop billing..."
            # Pass API keys file if configured
            local teardown_env=()
            if [[ -n "$GPU_API_KEYS_FILE" && -f "$GPU_API_KEYS_FILE" ]]; then
                teardown_env=(env GPU_API_KEYS_FILE="$GPU_API_KEYS_FILE")
            fi
            "${teardown_env[@]}" bash "$teardown_script" 2>&1 | tee -a "$LOG_DIR/teardown-$(date +%Y%m%d-%H%M%S).log"
        else
            log "WARNING: teardown-gpu.sh not found — GPU instances may still be running"
        fi
    fi

    # Run custom post-completion hook if configured
    if [[ -n "$POST_COMPLETION_HOOK" && -x "$POST_COMPLETION_HOOK" ]]; then
        log "Running post-completion hook: $POST_COMPLETION_HOOK"
        bash "$POST_COMPLETION_HOOK" 2>&1 || log "WARNING: Post-completion hook failed"
    fi
}

main "$@"
