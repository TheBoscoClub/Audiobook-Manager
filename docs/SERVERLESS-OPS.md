# Serverless STT Operations — Setup & Health

Operator reference for the translation pipeline's serverless Whisper STT path (RunPod, historical). The live GPU path is a hand-rented Vast.ai node driven by `scripts/gpu-node.sh`; see `docs/MULTI-LANGUAGE-SETUP.md` ("GPU node: vLLM translation + faster-whisper STT").

> ## RunPod serverless is historical
>
> The maintainer's RunPod account is **decommissioned** and no endpoints exist.
> The old Vast.ai *serverless* client was removed in v8.3.10.6, which is not the
> same as Vast.ai being unused: since v8.6.0 the GPU is a Vast.ai node rented by
> hand with `scripts/gpu-node.sh` (guarded by `scripts/gpu-budget-guard.sh`),
> running `faster-whisper` behind an SSH tunnel at `127.0.0.1:8765` and Qwen3-8B
> on vLLM. The RunPod client code, config variables, and tests described below
> remain in the tree and are unchanged, but **no serverless endpoint is
> provisioned**, so nothing completes unless a node session is up.
>
> The three systemd units - `audiobook-stream-translate.service`,
> `audiobook-translation-monitor-live.timer`,
> `audiobook-translation-monitor-sampler.timer` - are **enabled and running**,
> and produce nothing while no node session is up. The sampler tier reports this honestly every 5 minutes:
>
> ```text
> SAMPLER STALE: no sampler_job has completed in N days (threshold 3).
> The timer is running; the work is not.
> ```
>
> `upgrade.sh` and `install.sh` re-enable all three on every run, so
> `systemctl disable` is **not a durable mitigation** - see "Turning audio
> translation off" in `docs/MULTI-LANGUAGE-SETUP.md`.
>
> Treat everything below as a design record and a starting point to verify
> yourself, not a tested recipe.

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

STT was **designed** to flow through serverless GPU endpoints on RunPod. In that
design there is no fleet daemon, no SSH tunnel, no dedicated-instance rental, and
no teardown script: the provider manages worker lifecycle internally, and
scale-to-zero on cold endpoints means idle cost is zero. The current deployment
is the opposite shape (a rented node, an SSH tunnel, an explicit teardown), covered
in `docs/MULTI-LANGUAGE-SETUP.md`.

In the reference deployment none of this is provisioned, so actual STT traffic
is zero and actual spend is $0. The rest of this document describes how to stand
it up yourself.

**Related docs this one does not cover**: the sampler is by far the largest STT
consumer (the great majority of `streaming_segments` rows are
`origin='sampler'`) - see `docs/SAMPLER.md`. Stuck-claim reset and the
staleness alert are owned by the two monitor timers - see
`docs/TRANSLATION-MONITOR.md`.

---

## Prerequisites

### API key (`~/.config/api-keys.env`)

```bash
# RunPod — serverless API key
AUDIOBOOKS_RUNPOD_API_KEY=<runpod-api-key>
```

Permissions: `chmod 600` on the key file.

`resolve_secret()` also accepts a `*_FILE` pointer, so the key can live outside
the config: set `AUDIOBOOKS_RUNPOD_API_KEY_FILE` to a `0600` file containing it.
An inline value wins over the pointer.

### Endpoints

If you are standing this up yourself (the maintainer no longer runs it), create two serverless Whisper endpoints in the RunPod dashboard:

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
yet split into streaming + backlog endpoints.

**It is not a fallback.** `pipeline.py` appends this endpoint as an *additional*
candidate whenever both it and the API key are set - it is **not** gated on the
streaming/backlog pair being unset. With all three configured you get a
multi-candidate pool that `_select_from_candidates` round-robins across. Unset
it once you have split the pair, or it will absorb round-robin traffic.

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
- `local` — **recognised but rejected**; raises a migration `ValueError`. Use
  `local-gpu`

`WorkloadHint.SHORT_CLIP` also routes to the BACKLOG endpoint, alongside
`LONG_FORM` and `ANY`.

When multiple candidates are configured, `AUDIOBOOKS_STT_DISTRIBUTION` selects
how they are spread: `round_robin` (default), `random`, or `primary`
(always the first candidate).

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

```

`scripts/batch-translate.py` has **no journal identifier** — it logs to stdout
under the logger name `batch-translate`, so `journalctl -t audiobook-batch-translate`
returns `-- No entries --`. Redirect its output, or run it under
`systemd-cat -t audiobook-batch-translate` if you want a journal tag.

### Database signals

- `streaming_segments.state='processing'` rows older than **60 seconds** (live)
  or **2 hours** (sampler/backlog) indicate a stuck segment. **The worker does
  not reclaim them** — it has no age predicate at all. Resets are performed by
  `audiobook-translation-monitor-live.service` (timer, 30 s cadence) and
  `audiobook-translation-monitor-sampler.service` (5 min), with a retry cap of 3
  and an aged-segment operator alert at 120 s. See `docs/TRANSLATION-MONITOR.md`
- `sampler_jobs` with `status='failed'` carry a reason in `error`; a
  `SAMPLER STALE` journal line means nothing has completed in ≥3 days
- `chapter_subtitles` MAX(created_at) shows the most recent completed chapter
  (batch or streaming); if stale during an active run, inspect the worker log

---

## Cost & Teardown

Serverless endpoints scale to zero automatically. The cold (BACKLOG) endpoint
charges only while a request is in-flight. The warm (STREAMING) endpoint holds
one or more workers resident — small ongoing cost proportional to `min_workers`.

There is no teardown script for the serverless path because there is nothing to
tear down, and nothing is provisioned on RunPod, so spend there is **$0**. The
Vast.ai node is the opposite: billing runs from create to destroy, and
`scripts/gpu-node.sh down` is the only off switch. Run
`scripts/gpu-budget-guard.sh --cap <dollars>` beside every session. If you
provision RunPod endpoints yourself and want to stop spending entirely, set
`min_workers=0` on the STREAMING endpoint in the RunPod dashboard, or delete the
endpoints.

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
`LocalGPUWhisperSTT` is appended **last** for every workload - not specifically
long-form - and only when `is_available()` succeeds, so a configured serverless
provider is tried first. The rented Vast.ai node uses this same client: with
`gpu-node.sh tunnel` up, set `AUDIOBOOKS_WHISPER_GPU_HOST=127.0.0.1` and the
service on the node answers on port 8765. The standalone `extras/whisper-gpu/`
systemd unit is a separate, untested-by-the-maintainer way to host the same
service on your own hardware.

---

## Relationship to the Streaming Pipeline

`scripts/stream-translate-worker.py` (run by `audiobook-stream-translate.service`)
is the consumer of `WorkloadHint.STREAMING`. It polls `streaming_segments` in
priority order, dispatches each 30-second segment, and posts results back to the
coordinator API. It routes by `segment["origin"]`: `'live'` rows go to the
STREAMING pool, while `'sampler'` and `'backlog'` rows are dispatched as
`LONG_FORM` to the BACKLOG pool. That distinction carries most of the traffic -
the great majority of segment rows are sampler-origin. See `docs/STREAMING-TRANSLATION.md`
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
