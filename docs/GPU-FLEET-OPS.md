# GPU Fleet Operations — Setup & Teardown

Quick reference for renting, configuring, running, and tearing down the translation pipeline's GPU fleet (Vast.ai + RunPod).

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Fleet Setup (End-to-End)](#fleet-setup-end-to-end)
4. [Day-to-Day Monitoring](#day-to-day-monitoring)
5. [Teardown (Always Do This)](#teardown-always-do-this)
6. [Troubleshooting](#troubleshooting)
7. [Config Reference](#config-reference)

---

## Overview

The translation pipeline (STT → DeepL → TTS) uses rented cloud GPUs to run Whisper transcription on the 202+ audiobooks in the library. GPUs are rented on demand, work is dispatched via SSH tunnels (Vast.ai) or HTTPS proxies (RunPod), then torn down when the queue empties.

**Cost model**: idle GPUs still bill. Always run `teardown-gpu.sh` after a backlog run. `AUTO_TEARDOWN_GPU=true` in config tears down automatically when the queue drains.

**Typical run** (default `WORKERS_PER_GPU=4`):
- 6× L40S fleet @ $0.53/hr ≈ $3.20/hr
- ~15 min STT per book per stream, 4 streams per L40S
- 1665 books ÷ (6 GPUs × 4 streams) × 15 min ≈ 17 hours ≈ $55

Pre-v8.2.3.5 (single-stream, `WORKERS_PER_GPU=1`) was ~70 hrs / $225 on the
same fleet. The speed-up is pure concurrency — no extra VRAM, no extra
instances.

---

## Prerequisites

### API keys (`~/.config/api-keys.env`)

```bash
# Vast.ai
export VAST_API_KEY="<your vast.ai API key>"

# RunPod
export RUNPOD_API_KEY="<your runpod API key>"
```

Permissions: `chmod 600 ~/.config/api-keys.env`.

### CLI tools

```bash
# Vast.ai CLI
pip install --user vastai
vastai set api-key "$VAST_API_KEY"

# RunPod — no CLI required; GraphQL via curl
```

### SSH key

```bash
# The key used for Vast.ai instance SSH tunnels
SSH_KEY="/home/bosco/.claude/ssh/id_ed25519"
# Public half must be registered in Vast.ai account: vastai set ssh-key "$(cat ~/.claude/ssh/id_ed25519.pub)"
```

### Site-local config

- Canonical path: `/etc/audiobooks/scripts/translation-env.sh`
- Template: `/hddRaid1/ClaudeCodeProjects/Audiobook-Manager/etc/translation-env.sh.example`
- Never overwritten by `upgrade.sh`

---

## Fleet Setup (End-to-End)

### 1. Rent Vast.ai instances

Search for verified L40S offers under $0.75/hr with decent bandwidth:

```bash
vastai search offers \
    'reliability > 0.98 gpu_name=L40S dph<0.75 inet_down>500 verified=true' \
    -o 'dph' | head -20
```

Create an instance from a chosen offer ID (use `nvidia/cuda:12.4.0-runtime-ubuntu22.04` or the Whisper-ready template):

```bash
vastai create instance <OFFER_ID> \
    --image nvidia/cuda:12.4.0-runtime-ubuntu22.04 \
    --disk 40 \
    --ssh \
    --label "whisper-<city>"
```

Wait for `actual_status=running`, then fetch SSH connection details:

```bash
vastai show instances
# Note: id, ssh_host (e.g. ssh5.vast.ai), ssh_port (e.g. 18388), label
```

Repeat for each desired GPU (typical fleet: 6–10 instances).

### 2. Rent RunPod pods (optional)

Via the [RunPod dashboard](https://www.runpod.io/console/pods): create a GPU Cloud pod with:
- GPU: L40S or A4000
- Container image: Whisper-capable (custom or pre-built)
- Expose HTTP port: 8000
- Volume: 40GB+

Once running, get the HTTPS proxy URL from **Pod Details → Connect → HTTP Service Port 8000**:

```
https://<pod_id>-8000.proxy.runpod.net
```

### 3. Bootstrap Whisper on each instance

SSH into each Vast.ai instance and install/start the Whisper HTTP server (one-time setup per rental):

```bash
ssh -i ~/.claude/ssh/id_ed25519 -p <ssh_port> root@<ssh_host>

# Inside the instance:
pip install faster-whisper fastapi uvicorn
# Copy your whisper_gpu_service.py (or pull from repo)
nohup python whisper_gpu_service.py --port 8000 --model large-v3 >/tmp/whisper.log 2>&1 &
```

The translation daemon's health check (`Tunnel X unhealthy — starting whisper server`) triggers this automatically on Vast.ai tunnels if the process dies, but the binary / model cache must already exist. For cold instances, a bootstrap SSH is required once.

For RunPod pods, the image should include Whisper pre-installed. Verify:

```bash
curl -s https://<pod_id>-8000.proxy.runpod.net/health
# Expected: {"status":"ok","model":"large-v3"}
```

### 4. Update `/etc/audiobooks/scripts/translation-env.sh`

Replace the `VASTAI_INSTANCES` and `RUNPOD_INSTANCES` arrays with the current fleet's endpoints:

```bash
VASTAI_INSTANCES=(
    # "local_port|ssh_port|ssh_host|label|compute_type"
    "8100|17828|ssh8.vast.ai|l40s-japan|float16"
    "8101|17934|ssh1.vast.ai|l40s-texas|float16"
    "8102|17936|ssh9.vast.ai|l40s-utah|float16"
)

RUNPOD_INSTANCES=(
    # "https_proxy_url|label"
    "https://abc123def456-8000.proxy.runpod.net|runpod-l40s-1"
    "https://xyz789ghi012-8000.proxy.runpod.net|runpod-l40s-2"
)

AUTO_TEARDOWN_GPU=true
GPU_API_KEYS_FILE="${HOME}/.config/api-keys.env"
```

`local_port` is an arbitrary free port on the daemon host; `ssh_port`/`ssh_host` come from `vastai show instances`.

### 5. Start the translation daemon

```bash
sudo systemctl start audiobook-translate.service
sudo systemctl start audiobook-translate-check.timer
sudo systemctl start audiobook-fleet-watchdog.timer
```

Timers and service:

| Unit | Role |
|------|------|
| `audiobook-translate.service` | Long-running daemon that manages SSH tunnels, dispatches jobs, tracks progress |
| `audiobook-translate-check.timer` | Periodic liveness check (every 5 min) — restarts daemon if its heartbeat is stale |
| `audiobook-fleet-watchdog.timer` | Detects fleets where the local daemon is healthy but remote GPUs are dead (zero new `chapter_subtitles` in 20 min while rows are `processing`). Restarts the daemon, which re-provisions tunnels. |

### 6. Resume the queue (if paused)

```bash
sudo /usr/local/bin/audiobook-translations resume
sudo /usr/local/bin/audiobook-translations status
```

---

## Day-to-Day Monitoring

### Watch progress live

```bash
sudo /usr/local/bin/audiobook-translations status --watch 5
# Keys: +/- adjust interval, a toggle aggressive (fire liveness check every tick), q quit
```

### Report completed translations

```bash
sudo /usr/local/bin/audiobook-translations report
sudo /usr/local/bin/audiobook-translations report --locale zh-Hans --since 2026-04-01
sudo /usr/local/bin/audiobook-translations report --csv > completed.csv
```

### Verify GPU health

```bash
# Vast.ai
vastai show instances

# RunPod
curl -s -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"query":"query { myself { pods { id name desiredStatus gpuCount costPerHr } } }"}' \
    | python3 -m json.tool
```

### Watch journal

```bash
sudo journalctl -u audiobook-translate.service -f
sudo journalctl -u audiobook-fleet-watchdog.service --since "1 hour ago"
```

---

## Teardown (Always Do This)

**Do not leave GPUs running idle. Period.** Per `feedback_gpu_instance_lifecycle.md`.

### Automatic (recommended)

Set `AUTO_TEARDOWN_GPU=true` in `/etc/audiobooks/scripts/translation-env.sh`. The daemon runs `teardown-gpu.sh` automatically when the queue empties.

### Manual

```bash
# Preview
sudo /opt/audiobooks/scripts/teardown-gpu.sh --dry-run

# Execute
sudo /opt/audiobooks/scripts/teardown-gpu.sh
```

This destroys every Vast.ai instance and terminates every RunPod pod owned by the account — not just ones listed in config. If you have unrelated rentals, use the dashboards directly.

### Verify teardown

```bash
vastai show instances
# Expected: empty table

curl -s -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"query":"query { myself { pods { id } } }"}'
# Expected: {"data":{"myself":{"pods":[]}}}
```

### Stop the daemon after teardown

```bash
sudo systemctl stop audiobook-translate.service audiobook-translate-check.timer audiobook-fleet-watchdog.timer
```

Leaving the daemon active with an empty fleet causes infinite SSH-tunnel restart loops against ghost hostnames (harmless but noisy).

---

## Troubleshooting

### "Tunnel X unhealthy — starting whisper server" in a loop

The SSH tunnel connects but the Whisper process isn't running. Either:
- The instance was freshly rented and never bootstrapped → SSH in and install Whisper + model (§3)
- The instance was terminated/expired → `vastai show instances` to confirm; remove from `translation-env.sh` and restart daemon

### Last completion timestamp is hours old

```bash
sudo sqlite3 /var/lib/audiobooks/db/audiobooks.db \
    "SELECT MAX(finished_at) FROM translation_queue WHERE state='completed';"
```

If stale (>1h with non-empty queue), the fleet is dead. Check `vastai show instances` and the RunPod pods query. Re-provision if empty, or bootstrap Whisper if instances exist but aren't serving.

### Daemon active but zero progress

`audiobook-fleet-watchdog` should catch this in ≤20 min and restart the daemon. Force it:

```bash
sudo /opt/audiobooks/scripts/fleet-watchdog.sh
```

### Balance running low

```bash
vastai show user | grep -i credit
```

Top up before starting long backlog runs. Instances auto-pause when credit hits the balance threshold.

### Resuming after reboot

The daemon, timers, and watchdog are enabled at install time. After reboot:

```bash
sudo systemctl status audiobook-translate.service
# If inactive:
sudo systemctl start audiobook-translate.service
sudo /usr/local/bin/audiobook-translations resume   # if queue was paused
```

---

## Config Reference

### `/etc/audiobooks/scripts/translation-env.sh`

| Variable | Purpose | Default |
|----------|---------|---------|
| `DB_PATH` | SQLite database | `/var/lib/audiobooks/db/audiobooks.db` |
| `LIBRARY_PATH` | Audiobook library root | `/srv/audiobooks/Library` |
| `VENV_PYTHON` | Python interpreter | `/opt/audiobooks/library/venv/bin/python` |
| `LOG_DIR` | Daemon log directory | `/var/log/audiobooks/translate` |
| `PID_FILE` | Daemon PID file | `/var/lib/audiobooks/.run/translate-daemon.pid` |
| `SSH_KEY` | SSH key for Vast.ai tunnels | *(required)* |
| `HEALTH_CHECK_INTERVAL` | Tunnel health poll interval (seconds) | 120 |
| `EMPTY_QUEUE_CHECKS` | Consecutive empty checks before auto-stop | 3 |
| `VASTAI_INSTANCES` | Vast.ai fleet config (see §4) | `()` |
| `RUNPOD_INSTANCES` | RunPod fleet config (see §4) | `()` |
| `AUTO_TEARDOWN_GPU` | Tear down when queue empties | `false` |
| `GPU_API_KEYS_FILE` | Path to API keys env file | `~/.config/api-keys.env` |
| `WORKERS_PER_GPU` | Parallel books per GPU (gthreads on remote + N local workers) | `4` |

### `~/.config/api-keys.env`

| Variable | Required by |
|----------|-------------|
| `VAST_API_KEY` | `teardown-gpu.sh`, provisioning workflow |
| `RUNPOD_API_KEY` | `teardown-gpu.sh`, provisioning workflow |

### Key scripts

| Path | Purpose |
|------|---------|
| `/opt/audiobooks/scripts/translation-daemon.sh` | Long-running daemon (service `audiobook-translate`) |
| `/opt/audiobooks/scripts/translation-check.sh` | Liveness heartbeat check (5-min timer) |
| `/opt/audiobooks/scripts/fleet-watchdog.sh` | Dead-fleet detector (timer — triggers restart if GPUs dead) |
| `/opt/audiobooks/scripts/teardown-gpu.sh` | Destroys Vast.ai instances + RunPod pods |
| `/usr/local/bin/audiobook-translations` | CLI: status, pause/resume, start/stop, report, export/import |

---

*Document Version: 8.2.3.5*
*Last Updated: 2026-04-15*
