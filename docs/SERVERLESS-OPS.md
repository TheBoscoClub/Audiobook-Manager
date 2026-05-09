# Serverless STT Operations — Setup & Health

Operator reference for the translation pipeline's serverless Whisper STT path.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Streaming/Backlog Endpoint Topology](#streamingbacklog-endpoint-topology)
4. [Configuration](#configuration)
5. [Routing & Provider Selection](#routing--provider-selection)
6. [Health & Monitoring](#health--monitoring)
7. [Cost & Teardown](#cost--teardown)
8. [Self-Hosted Fallback](#self-hosted-fallback)
9. [Relationship to the Streaming Pipeline](#relationship-to-the-streaming-pipeline)
10. [Config Reference](#config-reference)

---

## Overview

All STT traffic flows through serverless GPU endpoints on RunPod. There is no
fleet daemon, no SSH tunnel, no dedicated-instance rental, and no teardown
script. The provider manages worker lifecycle internally; scale-to-zero on
cold endpoints means idle cost is zero.

---

## Prerequisites

### API key (`~/.config/api-keys.env`)

```bash
# RunPod — serverless API key
AUDIOBOOKS_RUNPOD_API_KEY=<runpod-api-key>
```

Permissions: `chmod 600 ~/.config/api-keys.env`.

### Endpoints

Create two serverless Whisper endpoints in the RunPod dashboard:

- A **STREAMING** endpoint with `min_workers >= 1` (warm pool)
- A **BACKLOG** endpoint with `min_workers = 0` (cold pool)

The streaming/backlog split is the operational shape of this project's
workload — interactive listening needs a warm worker, batch backfill tolerates
a cold start in exchange for zero idle burn.

---

## Streaming/Backlog Endpoint Topology

| Endpoint role | `min_workers` | Used by | Why |
|---------------|---------------|---------|-----|
| **STREAMING** | `>= 1` (warm) | `scripts/stream-translate-worker.py`, per-segment playback translation | Latency-critical; a cold start mid-playback stalls the listener behind the 3-minute buffer |
| **BACKLOG** | `0` (cold) | `scripts/batch-translate.py`, inline API backfill | Batch work tolerates 10–30 s cold-start per chapter; scale-to-zero keeps idle cost at zero |

Asymmetric `min_workers` is the whole point of the split. Running backlog on a
warm pool burns money for no latency benefit; running streaming on a cold pool
means the first segment of every playback session waits for provider cold-start.

---

## Configuration

Set the endpoints your deployment uses in `/etc/audiobooks/audiobooks.conf` (or
`~/.config/api-keys.env` — either is read at startup):

```bash
# RunPod serverless
AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT=<runpod-streaming-endpoint-id>
AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT=<runpod-backlog-endpoint-id>
```

### Transitional single-endpoint fallback

`AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT` is retained for deployments that have not
yet split into streaming + backlog endpoints. When the streaming/backlog pair
is unset, the pipeline falls back to this single endpoint for both workloads.
New deployments should configure the pair directly.

---

## Routing & Provider Selection

`library/localization/pipeline.py::_remote_stt_candidates(workload)` performs
workload-aware dispatch:

- `WorkloadHint.STREAMING` → the STREAMING endpoint (warm, `min_workers>=1`)
- `WorkloadHint.LONG_FORM` / `WorkloadHint.ANY` → the BACKLOG endpoint
  (cold, `min_workers=0`)

`get_stt_provider(workload=...)` is the single call site. Explicit overrides
via `AUDIOBOOKS_STT_PROVIDER`:

- `whisper` — force the transitional RunPod single-endpoint path
- `local-gpu` — force the self-hosted `whisper-gpu` service (see below)

Auto mode (the default) is preferred. Explicit overrides are for debugging.

---

## Health & Monitoring

### Provider reachability

```bash
# RunPod — list endpoints and confirm a healthy pool exists
curl -s -H "Authorization: Bearer $AUDIOBOOKS_RUNPOD_API_KEY" \
    "https://api.runpod.ai/v2/$AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT/health" \
    | python3 -m json.tool
```

The RunPod dashboard shows recent request counts, cold-start rate, and spend.
Use that for at-a-glance health; the API response above is sufficient for
scripted checks.

### Application-side journal

```bash
# Streaming worker — inspects claim/process/callback cycle per segment
sudo journalctl -u audiobook-stream-translate.service -f

# Batch worker — chapter-level backlog processing (ad-hoc run via scripts/batch-translate.py)
sudo journalctl -t audiobook-batch-translate -f
```

### Database signals

- `streaming_segments.state='processing'` rows older than 10 minutes indicate a
  stuck segment — the worker re-claims them on the next poll
- `chapter_subtitles` MAX(created_at) shows the most recent completed chapter
  (batch or streaming); if stale during an active run, inspect the worker log

---

## Cost & Teardown

Serverless endpoints scale to zero automatically. The cold (BACKLOG) endpoint
charges only while a request is in-flight. The warm (STREAMING) endpoint holds
one or more workers resident — small ongoing cost proportional to `min_workers`.

There is no teardown script because there is nothing to tear down. To stop
spending entirely, set `min_workers=0` on the STREAMING endpoint in the
RunPod dashboard or delete the endpoints.

---

## Self-Hosted Fallback

For deployments with local AI-capable hardware, the project ships a self-hosted
Whisper service (`extras/whisper-gpu/`) that runs as a systemd unit on the app
host or a LAN peer. Configure via:

```bash
AUDIOBOOKS_WHISPER_GPU_HOST=<host>
AUDIOBOOKS_WHISPER_GPU_PORT=8765
```

See `docs/MULTI-LANGUAGE-SETUP.md#local-gpu-optional` for hardware compatibility
(NVIDIA + CUDA and enterprise AMD Instinct + ROCm are the supported classes).
Local GPU is automatically deprioritized for long-form work when the serverless
provider is configured.

---

## Relationship to the Streaming Pipeline

`scripts/stream-translate-worker.py` (run by `audiobook-stream-translate.service`)
is the consumer of `WorkloadHint.STREAMING`. It polls `streaming_segments` in
priority order, dispatches each 30-second segment to the STREAMING endpoint,
and posts results back to the coordinator API. See `docs/STREAMING-TRANSLATION.md`
for the full state-machine and priority model.

Batch backfill (`scripts/batch-translate.py`) uses `WorkloadHint.LONG_FORM` and
flows to the BACKLOG endpoint — cheap, cold-start-tolerant, chapter-at-a-time.

Both pipelines write to the same permanent cache (`chapter_subtitles`,
`chapter_translations_audio`), so a chapter translated once by either pipeline
serves free on all future playbacks.

---

## Config Reference

### Endpoints (`/etc/audiobooks/audiobooks.conf` or `~/.config/api-keys.env`)

| Variable | Purpose |
|----------|---------|
| `AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT` | RunPod warm (`min_workers>=1`) endpoint — streaming playback |
| `AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT` | RunPod cold (`min_workers=0`) endpoint — batch backfill |
| `AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT` | Transitional single-endpoint RunPod fallback — unset once the streaming/backlog pair is configured |
| `AUDIOBOOKS_WHISPER_GPU_HOST` | Self-hosted `whisper-gpu` service host (optional) |
| `AUDIOBOOKS_WHISPER_GPU_PORT` | Self-hosted `whisper-gpu` service port (default `8765`) |

### API keys (`~/.config/api-keys.env`)

| Variable | Required by |
|----------|-------------|
| `AUDIOBOOKS_RUNPOD_API_KEY` | All RunPod endpoint calls |

### Key files

| Path | Purpose |
|------|---------|
| `library/localization/pipeline.py` | `_remote_stt_candidates()` + `get_stt_provider()` — dispatches STREAMING vs BACKLOG |
| `library/localization/stt/whisper_stt.py` | `WhisperSTT` — RunPod serverless client |
| `library/localization/stt/local_gpu_whisper.py` | `LocalGPUWhisperSTT` — self-hosted `whisper-gpu` client |
| `scripts/stream-translate-worker.py` | Streaming segment worker (consumes STREAMING endpoints) |
| `scripts/batch-translate.py` | Batch chapter worker (consumes BACKLOG endpoints) |
| `systemd/audiobook-stream-translate.service` | Streaming worker unit |
