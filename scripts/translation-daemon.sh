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

# ── Configuration ────────────────────────────────────────────────────────────
DB_PATH="/var/lib/audiobooks/db/audiobooks.db"
LIBRARY_PATH="/hddRaid1/Audiobooks/Library"
BATCH_SCRIPT="/hddRaid1/ClaudeCodeProjects/Audiobook-Manager/scripts/batch-translate.py"
VENV_PYTHON="/opt/audiobooks/library/venv/bin/python"
SSH_KEY="/home/bosco/.claude/ssh/id_ed25519"
LOG_DIR="/var/log/audiobooks/translate"
PID_FILE="/var/lib/audiobooks/.run/translate-daemon.pid"
HEALTH_CHECK_INTERVAL=120  # seconds between tunnel/worker health checks
EMPTY_QUEUE_CHECKS=3       # consecutive empty checks before auto-stop

# ── GPU Instance Definitions ─────────────────────────────────────────────────
# Format: "local_port|ssh_port|ssh_host|label|compute_type"
# Add/remove instances here. Daemon auto-adapts.
declare -a VASTAI_INSTANCES=(
    "8100|17828|ssh8.vast.ai|v100-japan|float16"
    "8101|17934|ssh1.vast.ai|p100-texas|auto"
    "8102|17936|ssh9.vast.ai|a4000-utah|float16"
    "8103|17940|ssh6.vast.ai|rtx5060ti-denmark|float16"
    "8104|18388|ssh5.vast.ai|rtx4060ti-china|float16"
    "8105|18390|ssh6.vast.ai|a4000-poland|float16"
    "8106|18998|ssh5.vast.ai|v100-minnesota|float16"
    "8107|18394|ssh6.vast.ai|rtx4080s-california|float16"
)

# RunPod instances use HTTPS proxy URLs (no SSH tunnel needed)
declare -a RUNPOD_INSTANCES=(
    "https://jykskbbews0xb2-8000.proxy.runpod.net|runpod-a4000-1"
    "https://yw1moek45rczqc-8000.proxy.runpod.net|runpod-a4000-2"
    "https://m0mtwgl4ukqdqo-8000.proxy.runpod.net|runpod-a4000-3"
)

# ── State ────────────────────────────────────────────────────────────────────
declare -A TUNNEL_PIDS
declare -A WORKER_PIDS
SHUTDOWN=false
EMPTY_COUNT=0

# ── Logging ──────────────────────────────────────────────────────────────────
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [daemon] $*"; }
log_worker() { echo "$(date '+%Y-%m-%d %H:%M:%S') [worker:$1] $2"; }

# ── Graceful Shutdown ────────────────────────────────────────────────────────
shutdown_handler() {
    log "Shutdown signal received — stopping all workers and tunnels"
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
    sudo -u audiobooks sqlite3 "$DB_PATH" \
        "UPDATE translation_queue SET state = 'pending', started_at = NULL WHERE state = 'processing';" 2>/dev/null

    rm -f "$PID_FILE"
    log "Shutdown complete"
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
    nohup sudo -u audiobooks \
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
    sudo -u audiobooks sqlite3 "$DB_PATH" \
        "SELECT state, COUNT(*) FROM translation_queue GROUP BY state;" 2>/dev/null
}

get_subtitle_counts() {
    local en zh
    en=$(sudo -u audiobooks sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM chapter_subtitles WHERE locale='en';" 2>/dev/null)
    zh=$(sudo -u audiobooks sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM chapter_subtitles WHERE locale='zh-Hans';" 2>/dev/null)
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
        pending=$(sudo -u audiobooks sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='pending';" 2>/dev/null)
        processing=$(sudo -u audiobooks sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='processing';" 2>/dev/null)
        completed=$(sudo -u audiobooks sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='completed';" 2>/dev/null)
        failed=$(sudo -u audiobooks sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM translation_queue WHERE state='failed';" 2>/dev/null)

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

        # Health-check and restart dead tunnels
        for instance in "${VASTAI_INSTANCES[@]}"; do
            IFS='|' read -r local_port ssh_port ssh_host label compute_type <<< "$instance"

            # Restart dead tunnel
            local tpid=${TUNNEL_PIDS[$label]:-0}
            if [ "$tpid" -gt 0 ] && ! kill -0 "$tpid" 2>/dev/null; then
                log "Tunnel $label died — restarting"
                start_tunnel "$local_port" "$ssh_port" "$ssh_host" "$label"
                sleep 3
            fi

            # Check tunnel health, restart whisper if needed
            if ! check_tunnel_health "$local_port" "$label"; then
                log "Tunnel $label unhealthy — checking whisper server"
                ensure_whisper_server "$ssh_port" "$ssh_host" "$label" "$compute_type"
                sleep 15  # model load time
            fi

            # Restart dead worker
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

    # Clean shutdown
    shutdown_handler

    # Run verification with proof
    log "Running translation verification..."
    local verify_script
    verify_script="$(dirname "$BATCH_SCRIPT")/verify-translations.py"
    if [ -f "$verify_script" ]; then
        "$VENV_PYTHON" "$verify_script" --db "$DB_PATH" --json --fix 2>&1 | tee -a "$LOG_DIR/verify-$(date +%Y%m%d-%H%M%S).log"
        local verify_exit=$?
        if [ $verify_exit -eq 0 ]; then
            log "VERIFICATION PASSED — all translations verified with proof"
        else
            log "VERIFICATION FOUND FAILURES — failed books re-queued for retry"
            log "Check verification report: $(dirname "$DB_PATH")/translation-verification.json"
        fi
    else
        log "WARNING: verify-translations.py not found at $verify_script"
    fi

    # Email verification report
    local email_script
    email_script="$(dirname "$BATCH_SCRIPT")/email-report.py"
    local report_json
    report_json="$(dirname "$DB_PATH")/translation-verification.json"
    if [ -f "$email_script" ] && [ -f "$report_json" ]; then
        log "Emailing verification report to bosco@thebosco.club..."
        "$VENV_PYTHON" "$email_script" \
            --to bosco@thebosco.club \
            --report "$report_json" 2>&1 || log "WARNING: Email send failed"
    fi

    # Tear down GPU instances to stop billing
    local teardown_script
    teardown_script="$(dirname "$BATCH_SCRIPT")/teardown-gpu.sh"
    if [ -f "$teardown_script" ]; then
        log "Tearing down GPU instances to stop billing..."
        bash "$teardown_script" 2>&1 | tee -a "$LOG_DIR/teardown-$(date +%Y%m%d-%H%M%S).log"
    else
        log "WARNING: teardown-gpu.sh not found — GPU instances may still be running"
    fi
}

main "$@"
