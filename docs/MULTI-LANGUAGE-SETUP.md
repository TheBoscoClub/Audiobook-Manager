# Multi-Language Setup and Installation Guide

A comprehensive guide for adding multi-language support to your Audiobook Manager installation. This document covers architecture, provider configuration, cost expectations, and step-by-step instructions for adding new languages.

---

## Table of Contents

- [Overview and Scope](#overview-and-scope)
- [Architecture Overview](#architecture-overview)
- [Provider Setup Instructions](#provider-setup-instructions)
  - [Machine translation provider](#machine-translation-provider)
  - [GPU node: vLLM translation + faster-whisper STT (Vast.ai)](#gpu-node-vllm-translation--faster-whisper-stt-vastai)
  - [Scheduled drain (translate every new book automatically)](#scheduled-drain-translate-every-new-book-automatically)
  - [Serverless Whisper STT (RunPod, historical)](#serverless-whisper-stt-runpod-historical)
  - [Optional RunPod XTTS endpoint](#optional-runpod-xtts-endpoint)
  - [Local GPU (Optional)](#local-gpu-optional)
- [Turning audio translation off](#turning-audio-translation-off)
- [Configuration Reference](#configuration-reference)
- [Adding a New Language](#adding-a-new-language)
- [Cost and Time Investment](#cost-and-time-investment)
- [Translation Asset Portability](#translation-asset-portability)
- [Dependencies and Requirements](#dependencies-and-requirements)
- [Attribution](#attribution)
- [Troubleshooting](#troubleshooting)

---

## Overview and Scope

Audiobook Manager (v8.4.3) includes a full localization system that translates both the web interface and audiobook content itself. The system currently ships with English (`en`) and Simplified Chinese (`zh-Hans`), but the architecture supports adding more locales without code changes.

> ### Status: the GPU is rented by hand, per session
>
> The two halves of this system run on different footing, and both depend on a GPU node you start yourself:
>
> | Half | Providers | Status |
> |---|---|---|
> | **Text** — UI strings, book metadata, announcements | `VLLMTranslator` (Qwen3-8B on vLLM) | **Cached translations always serve.** New translations need `AUDIOBOOKS_MT_ENDPOINT` set, which is true only while a GPU node session is up; otherwise uncached strings fall back to English. |
> | **Audio** — subtitles (VTT), translated narration | `faster-whisper` STT + edge-tts / XTTS | **Needs a reachable STT service**: the whisper service on the GPU node, tunnelled to `127.0.0.1:8765`. |
>
> The reference deployment keeps no standing GPU. The operator rents a Vast.ai node with `scripts/gpu-node.sh`, runs a batch, and destroys the node (`gpu-node.sh down`); `scripts/gpu-budget-guard.sh` enforces a spend cap while it runs. Follow [GPU node: vLLM translation + faster-whisper STT (Vast.ai)](#gpu-node-vllm-translation--faster-whisper-stt-vastai). The earlier RunPod serverless account is **decommissioned** (historical), and DeepL was removed in v8.5.0.
>
> **What you will observe with no node up**: `audiobook-stream-translate.service` starts and idles; the sampler and live translation-monitor timers keep firing; and from v8.4.3 the sampler timer, which fires every 5 minutes, reports staleness rather than looking healthy. `scripts/translation-monitor-sampler.py` logs the line below only once the last completed `sampler_job` is 3 or more days old (`STALE_COMPLETION_ALERT_DAYS`); on a fresh install with no completed job it logs `no sampler_job has EVER reached 'complete'` instead:
>
> ```text
> SAMPLER STALE: no sampler_job has completed in N days (threshold 3).
> The timer is running; the work is not.
> ```
>
> That message is the intended behaviour of an install with no node session, not a bug. To turn the subsystem off instead, see [Turning audio translation off](#turning-audio-translation-off).

### What Is Translated

| Category | Examples | Mechanism |
|----------|----------|-----------|
| UI text | Navigation, buttons, labels, headings | Locale JSON files (1,093 keys per language) |
| Tooltips | All interactive elements | Locale JSON files |
| Book descriptions | Synopses, author bios, series info | Translation cache (machine translation) |
| Announcement banners | Admin-authored notices shown to patrons | Translation cache (machine translation) |
| Help pages | User-facing documentation | Locale JSON files |
| Error messages | User-visible errors and validation | Locale JSON files |
| Subtitles (VTT) | Per-chapter synchronized captions | STT pipeline (Whisper transcription + machine translation) |
| Translated audio narration | Full audiobook narration in target language | TTS pipeline (edge-tts or XTTS voice cloning) |

### What Is NOT Translated

| Category | Reason |
|----------|--------|
| Admin/backoffice UI (`utilities.html`) | Used exclusively by the system administrator. Translating admin-only pages adds maintenance burden with zero patron benefit -- no library user ever sees the backoffice. This is a deliberate design choice: 100% of user-facing content is translated, while the admin tools that only the operator uses remain in English. |
| System logs | Machine-readable, consumed by operators and log aggregators |
| API JSON responses | Structured data consumed by code, not end users |
| CLI output | Used only by the administrator at the terminal |
| Internal error messages | Developer-facing diagnostics in logs and stack traces |

### Current Language Support

| Locale Code | Language | Status |
|-------------|----------|--------|
| `en` | English | Default, complete |
| `zh-Hans` | Simplified Chinese | Complete (1,093 UI keys + book metadata + CJK search/sort) |

The architecture supports any BCP 47 locale code. Adding a new language requires no code changes -- only a locale file and configuration.

---

## Architecture Overview

The localization system is a three-stage pipeline that converts English audiobook content into translated text and narrated audio. Two modes of operation are available:

- **Batch translation** (described below): processes entire chapters in the background via a queue and timer. Ideal for pre-translating the library during off-hours.
- **Streaming translation** (v8.3.0+): on-demand, real-time translation triggered by playback. When a user presses play on an untranslated book, the system buffers 3 minutes of translated audio, then begins playback while the GPU stays ahead. For the complete streaming architecture, playback flow, state machine, and operational guide, see [STREAMING-TRANSLATION.md](STREAMING-TRANSLATION.md).

Both pipelines share the same permanent cache (`chapter_subtitles` table). A chapter translated by either pipeline serves instantly on future plays.

```text
Source Audio (English)
    |
    v
[STT] Speech-to-Text (Whisper)
    |  Transcribes English audio to timestamped text
    v
[Translation] Machine translation
    |  Translates English text to target language
    v
[TTS] Text-to-Speech (edge-tts or XTTS)
    |  Synthesizes translated text into narrated audio
    v
Translated Audio + VTT Subtitles
```

### Pipeline Components

The localization module lives in `library/localization/` (~4,900 lines of Python across 31 files) and is organized into subpackages:

| Subpackage | Purpose |
|------------|---------|
| `stt/` | Speech-to-text providers (`faster-whisper` service reached by `LocalGPUWhisperSTT`; RunPod serverless client, historical) |
| `translation/` | Provider abstraction (`TranslationProvider` protocol), provider factory, SQLite translation memory |
| `tts/` | Text-to-speech providers (edge-tts, XTTS via RunPod) |
| `subtitles/` | VTT subtitle generation and chapter synchronization |
| `glossary/` | Domain-specific translation glossaries for consistency |

### STT Providers (Speech-to-Text)

The STT layer uses the Whisper `large-v3` model, run through `faster-whisper` by `library/localization/stt/whisper_gpu_service.py`. When `AUDIOBOOKS_STT_PROVIDER` is set to `auto` (the default), the system selects a provider based on workload characteristics:

| Provider | Best For | Tradeoffs |
|----------|----------|-----------|
| **Local GPU Whisper (Vast.ai node via tunnel)** | The live path: `LocalGPUWhisperSTT` against `AUDIOBOOKS_WHISPER_GPU_HOST` / `AUDIOBOOKS_WHISPER_GPU_PORT` | Node rented per session with `scripts/gpu-node.sh`; billed from create to destroy. See [GPU node](#gpu-node-vllm-translation--faster-whisper-stt-vastai). |
| **RunPod Whisper (streaming, historical)** | Real-time per-segment inference for the live player | Warm pool (`min_workers>=1`) — first segment returns in seconds. Small ongoing hourly cost for the resident worker. |
| **RunPod Whisper (backlog, historical)** | Long-form audiobook transcription, sampler/backfill jobs | Cold pool (`min_workers=0`) — scales to zero, pay only when processing. 10-30 s cold start on the first request after idle. |
| **Local GPU Whisper (your own hardware)** | Testing, small batches, users with known-good AI hardware | Uses host GPU. Safe only on hardware classes designed for sustained AI inference (NVIDIA CUDA, enterprise AMD Instinct/ROCm). **Consumer AMD Radeon RDNA 2/3 + ROCm is known-unstable — see the cautionary tale in [Local GPU (Optional)](#local-gpu-optional).** |

The workload-aware selection system (`library/localization/selection.py`) distinguishes between short clips (prefer local to avoid cold-start latency) and long-form batch work (prefer the cold backlog endpoint for cheapest throughput).

### Translation Provider

Machine translation is `VLLMTranslator` (`library/localization/translation/vllm_mt.py`): Qwen3-8B served by vLLM, reached over the OpenAI-compatible `/v1/chat/completions` API. `library/localization/translation/base.py` defines the `TranslationProvider` protocol and `factory.py` returns `VLLMTranslator` when `AUDIOBOOKS_MT_ENDPOINT` is set, otherwise `None`. The translation layer includes:

- Batch processing to minimize provider calls
- Translation memory — the `string_translations` table is consulted before every call, so already-translated strings are served from the database with no provider at all

**Failure is announced, never silent.** When no provider is configured, or the configured provider cannot be reached or refuses a request, `translate()` sets `self.degraded` and counts the affected strings in `self.degraded_texts`, and logs at error level. Callers that **persist** the result must pass `strict=True`, which raises `TranslationUnavailableError` instead of returning the English source:

| Caller | Mode | Why |
|---|---|---|
| `localization/pipeline.py` (both VTT writers) | `strict=True` | Result is written to a `.{locale}.vtt` file on disk |
| Everything else | default (non-strict) | Transient English pass-through is recoverable; check `.degraded` |

The reason for the split is recoverability: an English string silently stored as though it were a Chinese translation is indistinguishable from a real one afterwards, so nothing downstream can ever repair it. A raised exception leaves the artifact unwritten and the job retryable.

### TTS Providers (Text-to-Speech)

| Provider | Quality | Cost | Use Case |
|----------|---------|------|----------|
| **edge-tts** (default) | High -- Microsoft Neural TTS voices | Free, no API key | Standard narration. Excellent quality for most languages. |
| **XTTS (Coqui)** | Highest -- preserves original narrator voice | GPU rental ($0.20-0.50/hr) | Voice cloning. Reproduces the original narrator's characteristics in the target language. Requires GPU (RunPod). |

### Fallback System

If the remote provider (RunPod) is unreachable, the system automatically falls back to a local provider for the current request. Local provider failures are not retried -- the error is real and propagates to the caller. This means a misconfigured remote provider degrades gracefully rather than blocking the entire pipeline.

---

## Provider Setup Instructions

### Machine translation provider

Text translation (UI strings, book metadata, subtitle text) uses a vLLM-served Qwen3-8B model on the same rented GPU node as the STT service; there is no third-party translation account to sign up for beyond Vast.ai itself. The setup is in [GPU node](#gpu-node-vllm-translation--faster-whisper-stt-vastai). The earlier hosted-API integration (DeepL) was removed in v8.5.0.

Existing translations continue to serve from the translation cache: the `string_translations` table (translation memory) is consulted before any provider call, so an installation with a populated cache keeps serving translated content with no node up. Strings not in the cache fall back to the English source while `AUDIOBOOKS_MT_ENDPOINT` is unset.

### GPU node: vLLM translation + faster-whisper STT (Vast.ai)

One rented node serves both jobs: vLLM (Qwen3-8B by default) for translation and the `faster-whisper` service for STT. `scripts/gpu-node.sh` has two uses: an operator CLI run by hand from the project tree (the workflow below, as documented in the script headers), and an installed worker that `install.sh` / `upgrade.sh` copy to `/opt/audiobooks/scripts/` for the [scheduled drain](#scheduled-drain-translate-every-new-book-automatically). It has no systemd unit of its own.

1. **Prerequisites**: `jq` and `curl`; a Vast.ai account with credit; your SSH public key registered at console.vast.ai under Keys. The script reads the Vast.ai API key from the `VAST_API_KEY` environment variable and never prints it or puts it on a command line. Export it for your shell session (`export VAST_API_KEY=<your-key>`); operators may instead inject it with their own secret tooling. When the variable is absent, the script tries to re-exec itself under a `with-secret` helper and exits with an error if it cannot find one.

2. **Rent and bootstrap the node**:

   ```bash
   scripts/gpu-node.sh search          # list the cheapest verified 1-GPU offers
   scripts/gpu-node.sh up              # BILLING STARTS HERE: rent the best offer, start vLLM, ship and start the whisper service
   scripts/gpu-node.sh status          # state, $/hr, uptime, ports
   ```

   Optional overrides: `GPU_NODE_GPU` (default `H100 SXM`), `GPU_NODE_MAX_PRICE`, `GPU_NODE_MODEL` (default `Qwen/Qwen3-8B`), `GPU_NODE_DISK_GB` (default `80`), `GPU_NODE_LABEL` (default `abm-translate`). Billing runs from create to destroy; a stopped instance still bills for storage.

3. **Start the spend guard immediately after `up`**, in a second terminal. It cannot be started first: it exits as soon as no `abm-translate` instance exists (`no '<label>' instances — guard exiting`). `--cap` is required; `--floor` and `--max-idle` are optional:

   ```bash
   scripts/gpu-budget-guard.sh --cap 35 --floor 64 --max-idle 15
   ```

   The guard destroys the instance at the cap, at the credit floor, or after the idle window, then re-queries the API to confirm the instance is gone. Add `--check` for a one-shot report with no action.

4. **Open the tunnel and verify**:

   ```bash
   scripts/gpu-node.sh tunnel            # 127.0.0.1:8000 -> vLLM, 127.0.0.1:8765 -> whisper
   scripts/gpu-node.sh bootstrap-status  # vLLM models, whisper /health, GPU name
   ```

   Model download on first boot can take minutes, so rerun `bootstrap-status` until both services answer.

5. **Point the application at the tunnel** in `/etc/audiobooks/audiobooks.conf`:

   ```bash
   AUDIOBOOKS_MT_ENDPOINT="http://localhost:8000"
   AUDIOBOOKS_MT_MODEL="Qwen/Qwen3-8B"
   AUDIOBOOKS_WHISPER_GPU_HOST=127.0.0.1
   AUDIOBOOKS_WHISPER_GPU_PORT=8765
   ```

   `factory.py` (`_endpoint()`) reads only the process environment, at call time; it does not read `audiobooks.conf` itself. A service's environment is fixed when it starts (the API unit through `EnvironmentFile=/etc/audiobooks/audiobooks.conf`, the stream-translate daemon through `lib/audiobook-config.sh`), so restart the services that translate after editing the file.

6. **Run the work, then tear down**:

   ```bash
   scripts/gpu-node.sh tunnel --stop
   scripts/gpu-node.sh down              # asks for confirmation, then DESTROYS the instance; billing stops (`down --yes` skips the prompt)
   ```

   Unset `AUDIOBOOKS_MT_ENDPOINT` afterwards so the factory returns to its no-provider state. `AUDIOBOOKS_WHISPER_GPU_HOST` can stay set: with the tunnel down, `LocalGPUWhisperSTT.is_available()` fails and the provider drops out of the candidate list.

### Scheduled drain (translate every new book automatically)

Every newly imported book is queued in `translation_queue` for each locale in `AUDIOBOOKS_SUPPORTED_LOCALES` except English, by the importer's post-insert hook. Nothing in the API process does queue work: a player bump (`/api/translation/bump`) only raises the book's priority, so the next drain does it first. Each queue row has two stages, tracked separately:

- **Subtitles**: speech-to-text plus machine translation. This needs a GPU and runs on a rented node.
- **Narration**: text-to-speech of the translated subtitles with edge-tts. This needs no GPU and runs on this host.

A row reaches `completed` only when both stages are done. Until then it stays `pending`, so unfinished work stays visible.

`audiobook-translation-drain.timer` runs `scripts/translation-drain.sh` once a day. The timer is **installed but not enabled**: `install.sh` and `upgrade.sh` copy the units and never switch them on, because every run that finds subtitle work rents a billed node.

Each run:

1. Takes a lock (`AUDIOBOOKS_DRAIN_LOCK`, shared with the reaper below), so two drains never overlap. Rows a dead earlier run left in `processing` go back to `pending`, as do rows claimed by an operator's hand-run `batch-translate.py` whose process is gone, or by any other owner with no progress for 6 hours.
2. Counts pending work for each stage. Books permanently excluded from translation and `quarantined` books do not count. With **no pending work in either stage it exits 0 without renting anything**. With narration work only, it skips straight to step 8 and rents nothing.
3. Refuses to rent unless the [reaper](#the-independent-reaper) is armed — its timer active and a heartbeat from a run that completed with the Vast.ai key, for this drain's label, within the last 30 minutes. It also refuses if no Vast.ai credential is configured, or if anything already listens on local port 8000 or 8765 (a tunnel you opened by hand would otherwise answer for a different node).
4. Checks the monthly ceiling (below). This run's cap is the smaller of `AUDIOBOOKS_DRAIN_BUDGET_USD` and what is left this month. Then it checks the Vast.ai account balance against that cap. Top-ups are manual: if the balance is below the cap, or cannot be read, the drain does not rent. It logs `Vast balance $X below run cap $Y — top up the account manually; queued books will wait` (or that the balance could not be read), leaves the queue pending, and exits non-zero so the unit fails and the operator is alerted. Otherwise the cap is written to the spend ledger as a reservation **before** renting.
5. Starts `gpu-budget-guard.sh --cap <that amount>` first, so the cap also covers the minutes while the node is being created. Then it runs `gpu-node.sh up` under this host's own label, `abm-drain-<short hostname>` (see [Labels](#labels)), so it never touches a node you rented by hand under `abm-translate` or another host's drain node. `up` also arms an **on-node dead-man switch** (below).
6. Opens the tunnel and waits up to `AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT` seconds for vLLM and whisper to answer.
7. Runs `batch-translate.py --stage subtitles --drain-mode`, bounded by `AUDIOBOOKS_DRAIN_MAX_MINUTES`:
   - Drain mode allows only the tunnelled node's whisper and vLLM. RunPod and any other paid provider are removed from the batch's environment, and it refuses to start if the endpoints are not the tunnel's.
   - The drain watches the tunnel process, the guard and a health probe. If the node or tunnel stops answering, the batch stops and the book it was working on goes back to `pending`, never `failed`.
8. Closes the tunnel and destroys **every** instance under its label, confirms through the API that none remain, and records the measured spend in the ledger. Only after that does it run the narration pass, `batch-translate.py --stage narration` (edge-tts), bounded by `AUDIOBOOKS_DRAIN_NARRATION_MAX_MINUTES`.

The teardown in step 8 also runs from an exit trap, so it runs after any failure and after SIGINT/SIGTERM, including a signal that arrives while `up` is still creating the node. A node whose ID was never learned is found by its label.

Any early end exits non-zero and leaves the unit `failed`: a step failing, a timeout, the wall-clock limit, the guard's cap firing, endpoints lost, a budget refusal, or a teardown it cannot confirm.

**Failing books.** A book that fails for a reason of its own (unreadable audio, a translation the quality guard refuses, a narration rejection) goes back to `pending` with the error recorded. It is tried again on the next run. After `AUDIOBOOKS_DRAIN_MAX_ATTEMPTS` consecutive failed runs (default 3) it moves to `quarantined` with the last error kept, and stops counting as work. One broken book therefore cannot make the drain rent a node every day just to fail again.

Failures that belong to the run rather than the book do not count toward that limit: losing the tunnel or node, a bootstrap timeout, a budget stop. One exception: a book whose audio crashes the whisper service makes the node's endpoints vanish on every attempt, which looks exactly like a flaky node. So when the endpoints are lost **while the same book is in flight** on `AUDIOBOOKS_DRAIN_MAX_SUSPECT` consecutive runs (default 3), that book is quarantined as `suspect:` on its own counter. It counts only when the node itself is still up, the tunnel is still up and the guard is still running — a node destroyed by our own guard, reaper or dead-man switch is not the book's fault. Losing the endpoints before a book starts counts against no book.

After fixing the cause, put a book back in the queue:

```bash
sudo -u audiobooks env PYTHONPATH=/opt/audiobooks/library \
    /opt/audiobooks/library/venv/bin/python -m localization.drain_queue \
    --db /var/lib/audiobooks/db/audiobooks.db requeue-quarantined --book-id <id>
```

Leave out `--book-id` to requeue every quarantined book.

**Monthly ceiling.** `AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD` (default 100 dollars) caps total drain spend per **calendar month in this host's local time**. The month boundary follows the host clock and time zone: a wrong clock or a time-zone change moves it, and two hosts in different time zones change month at different moments. Spend is recorded in an append-only ledger, `AUDIOBOOKS_DRAIN_LEDGER` (default `${AUDIOBOOKS_VAR_DIR}/translation-drain/spend-ledger.tsv`).

The ledger is a plain file rather than a database table on purpose. A database restore or a migration replay must never erase a month's spending and so re-authorise it.

How the ledger records a run:

- **Before renting**, it writes a reservation for the run's cap.
- **At the end**, it writes the measured spend: the larger of the account-credit drop and the instance's uptime multiplied by its hourly price.
- **If the run is killed** before it can write that, the full reservation counts. A crash can over-count the month, never under-count it.

A run is charged to the month in which it started. A run is refused when the month's spend has reached the ceiling, or when less than `AUDIOBOOKS_DRAIN_MIN_RUN_USD` (default 1 dollar) is left. A refusal leaves the queue pending and exits non-zero. When less than the per-run cap is left, the run goes ahead with the smaller cap. Read the ledger with `column -t -s $'\t' <ledger>`.

#### The independent reaper

The drain's guard runs inside the drain's own service, so anything that kills the drain without running its trap also kills the guard: SIGKILL, the OOM killer, a stop timeout. `audiobook-translation-reaper.timer` closes that gap. It runs every 10 minutes and is **enabled by default**, because it can only destroy instances, never rent one.

It destroys any instance under this host's drain label that meets one of these conditions:

- **(a)** no drain run currently holds the lock;
- **(b)** it is older than the drain's longest legitimate run, plus `AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES`;
- **(c)** it has accrued the per-run cap;
- **(d)** the month's ledger spend plus the live node's accrued cost has reached the monthly ceiling.

Conditions (a) to (c) need only the Vast.ai listing and are acted on first; the spend ledger is read only for (d). A damaged ledger therefore never stops an orphan from being destroyed; it is reported afterwards and the run exits non-zero.

It never touches any other label. A reaper that had to destroy something exits 2, so its unit shows failed: a firing is always worth knowing about. Each run that completes with the key writes a heartbeat; the drain will not rent without a recent one.

It takes the drain lock before it lists instances, so a drain that has just finished is never mistaken for an orphan.

Without a Vast.ai credential it exits 0 only on a host where the drain was never configured. Where the drain timer is enabled or a spend ledger exists, a missing key is an error (exit 1), because the reaper would otherwise be silently off. The key also cannot be blanked by accident: if `audiobooks.conf` sets `VAST_API_KEY_FILE=""` (which overrides the drop-in's pointer), the drain and the reaper still use the key the drop-in derived into their runtime directory.

#### The on-node dead-man switch

The reaper runs on this host, so it cannot act while the host is down: power loss, a kernel panic, a network outage. For that case, every drain node carries its own timer, set by the drain's `up` to the same maximum lifetime the reaper uses (`localization/drain_limits.py`; 20,800 seconds, 5 hours 47 minutes, at the defaults). The timer counts from the node's first boot, stored on its disk, so a container restart does not start it again. When it expires, the node ends itself without hearing from the host:

1. **Destroy.** It calls the Vast.ai API with the instance's own `CONTAINER_API_KEY`, which Vast injects into every container. This account's key never goes to the node. Destroying ends all billing.
2. **Stop**, if destroy fails. GPU billing ends; the instance's **disk storage keeps billing** until it is destroyed, which the reaper does once the host is back.
3. **Signal the container's PID 1**, if both API calls fail. Best effort only: inside a container the kernel ignores SIGTERM and SIGKILL sent to the namespace's PID 1 unless it installed a handler, so this step often does nothing.

The node's `/workspace/dead-man.log` records which step worked.

**Price limit.** With the host down only that timer ends a node, so an orphan can cost up to its hourly price × lifetime. The drain therefore caps the price: by default `GPU_NODE_MAX_PRICE` = the run's cap ÷ lifetime in hours (1.73 dollars per hour for a 10-dollar cap at the default lifetime), and offers above it are not rented. An instance that turns out to bill above it is refused and destroyed. If you set `GPU_NODE_MAX_PRICE` higher yourself, the node's lifetime shrinks to cap ÷ price and the batch's time limit shrinks with it, so price × lifetime still fits the cap; if too little batch time would be left (under 10 minutes), the run is refused.

The verified H100 SXM rate measured in 2026-09 was about 1.74 dollars per hour, just above the 1.73 default for a 10-dollar cap. Either raise `AUDIOBOOKS_DRAIN_BUDGET_USD` (11 dollars allows about 1.90 dollars per hour) or set `GPU_NODE_MAX_PRICE` (at 1.80 dollars per hour the batch limit drops from 4 hours to about 3 hours 47 minutes).

When a run's node disappears without the drain recording its spend (the host was down, or the reaper destroyed it), the next reaper or drain run charges that run its full reservation, never less.

The unit tests prove the switch is armed with the right lifetime and that it calls these three steps in order. Whether Vast honours each step on a live node is not yet verified; the dev-VM test plan checks it with a short lifetime.

#### Labels

The Vast.ai account is shared: production and the dev, test and QA VMs rent with the same key. Each host's reaper destroys instances under its label that it does not know to be alive, so a label shared between two hosts would let one host's reaper kill the other host's live node. The label therefore defaults to `abm-drain-<short hostname>`, and the drain and the reaper refuse any label not of the form `abm-drain-<name>` — including the old shared `abm-drain` and the operator's `abm-translate`.

Cloned VMs can share a hostname. Give each VM an explicit, distinct `AUDIOBOOKS_DRAIN_LABEL`.

**Settings** (`/etc/audiobooks/audiobooks.conf`; defaults in `lib/audiobook-config.sh`):

| Key | Default | Meaning |
| --- | --- | --- |
| `AUDIOBOOKS_DRAIN_BUDGET_USD` | `10` | Spend cap per run, in dollars, enforced by the guard |
| `AUDIOBOOKS_DRAIN_MONTHLY_BUDGET_USD` | `100` | Spend ceiling per calendar month (local time), in dollars |
| `AUDIOBOOKS_DRAIN_MIN_RUN_USD` | `1` | Do not rent when less than this many dollars is left this month |
| `AUDIOBOOKS_DRAIN_MAX_MINUTES` | `240` | Wall-clock limit for the GPU subtitle batch, in minutes |
| `AUDIOBOOKS_DRAIN_NARRATION_MAX_MINUTES` | `120` | Wall-clock limit for the host narration pass, in minutes |
| `AUDIOBOOKS_DRAIN_BOOTSTRAP_TIMEOUT` | `1800` | Seconds to wait for vLLM and whisper after `up` |
| `AUDIOBOOKS_DRAIN_MAX_ATTEMPTS` | `3` | Consecutive failed runs before a book is quarantined |
| `AUDIOBOOKS_DRAIN_LABEL` | `abm-drain-<short hostname>` | Vast.ai instance label the drain owns and the reaper polices; must be `abm-drain-<name>` |
| `AUDIOBOOKS_DRAIN_MAX_SUSPECT` | `3` | Consecutive runs that lose the node with the same book in flight before it is quarantined as suspect |
| `AUDIOBOOKS_DRAIN_REAPER_MARGIN_MINUTES` | `30` | Slack past the drain's longest legitimate run, in minutes — used by the reaper and the on-node dead-man switch; at least 10 |
| `AUDIOBOOKS_DRAIN_LEDGER` | under `AUDIOBOOKS_VAR_DIR` | The append-only spend ledger |
| `GPU_NODE_SSH_KEY`, `GPU_NODE_MAX_PRICE`, `GPU_NODE_GPU`, `GPU_NODE_PIP_TIMEOUT` | (see `gpu-node.sh`) | Offer selection, the SSH identity the `audiobooks` user connects with, and the limit on how long `up` waits for the node's `pip install` |

At about 1.74 dollars per hour for a verified H100 SXM (measured 2026-09), the 10-dollar per-run default covers roughly 5 hours 45 minutes of node time. That is more than the 4-hour batch limit plus bootstrap. The 100-dollar month is about ten full-length runs.

**Enabling it** (an operator decision):

1. **Credential.** On hosts with `/usr/bin/derive-service-secret`, install and upgrade put drop-ins at `/etc/systemd/system/audiobook-translation-{drain,reaper}.service.d/derive-secrets.conf`. At each start, these derive `VAST_API_KEY` into the unit's private runtime directory and point `VAST_API_KEY_FILE` at it. Elsewhere, put the key in a 0600 file owned by `audiobooks` and set `VAST_API_KEY_FILE` to its path in `audiobooks.conf`. The key is never logged.
2. **SSH key.** The drain runs as `audiobooks`, so that user needs an SSH key registered at console.vast.ai under Keys:
   - generate it: `sudo -u audiobooks ssh-keygen -t ed25519 -f /var/lib/audiobooks/.ssh/id_ed25519 -N ''`;
   - register the `.pub` file at console.vast.ai under Keys;
   - set `GPU_NODE_SSH_KEY` to the private key path.
3. **Supervised first run.** Start one run by hand and watch it: `sudo systemctl start audiobook-translation-drain.service` and `journalctl -u audiobook-translation-drain -f`.
4. **Enable the timer**: `sudo systemctl enable --now audiobook-translation-drain.timer`. To stop scheduled drains again: `sudo systemctl disable --now audiobook-translation-drain.timer`. No [`disabled-units`](#the-disabled-units-file) entry is needed: install and upgrade never enable this timer, so both a disable and an enable survive every upgrade.

### Serverless Whisper STT (RunPod, historical)

> **Not in use in the reference deployment.** The maintainer's RunPod account is decommissioned and no endpoints exist. The client code, config variables, and tests below all remain in the tree and are unchanged, but this path is **no longer exercised end-to-end by the maintainer** — treat the instructions as a starting point to verify yourself, not as a tested recipe. The only GPU path in use is the hand-rented Vast.ai node described above.

STT runs through serverless Whisper endpoints on RunPod. Endpoints scale to zero automatically, so idle cost on cold pools is $0. For the full operator reference, see `docs/SERVERLESS-OPS.md`.

The pipeline uses a **streaming/backlog endpoint split**: a STREAMING pool (warm, `min_workers>=1`) for per-segment playback translation, and a BACKLOG pool (cold, `min_workers=0`) for batch chapter translation. Create both in the RunPod dashboard.

1. **Sign up** at [runpod.io](https://www.runpod.io/) and add credits.

2. **Deploy the endpoint pair** (Whisper / `faster-whisper` template, `large-v3` model):
   - **STREAMING**: `min_workers=1` — always-warm worker for live playback
   - **BACKLOG**: `min_workers=0` — scale-to-zero for chapter batch runs

3. **Configure API key** in `~/.config/api-keys.env`:

   ```bash
   # RunPod — serverless API key
   AUDIOBOOKS_RUNPOD_API_KEY=your-runpod-api-key
   ```

4. **Configure endpoint IDs** in `~/.config/api-keys.env` or `/etc/audiobooks/audiobooks.conf`:

   ```bash
   # RunPod serverless Whisper endpoints
   AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT=your-streaming-endpoint-id
   AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT=your-backlog-endpoint-id
   ```

   Configure either, both, or neither. The pipeline picks the right tier per workload (streaming vs. backlog) and falls through to whatever else is configured (single-endpoint legacy mode, local GPU host) on transient failure.

5. **Cost**: Approximately $0.00026/second of GPU time on RunPod. STREAMING at `min_workers=1` incurs a small ongoing hourly cost for the resident worker; BACKLOG at `min_workers=0` is $0 while idle. Cold starts on BACKLOG add 10-30 seconds of latency on the first request after a period of inactivity — acceptable for batch work, which is why streaming uses a warm pool.

### Optional RunPod XTTS endpoint

RunPod also offers a serverless XTTS path for voice cloning:

```bash
AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT=your-xtts-endpoint-id
```

### Local GPU (Optional)

If your host machine has a GPU that is **known-good for sustained AI inference**, you can run Whisper locally instead of (or in addition to) a remote provider.

> **This section covers a GPU in a machine you own.** The maintainer's path is the rented Vast.ai node above, which uses the same `LocalGPUWhisperSTT` client and the same `whisper_gpu_service.py`; a card in your own hardware is not exercised by the maintainer. Local GPU remains opt-in, and its safety depends entirely on your hardware class — read the cautionary tale below before enabling it.
>
> ⚠️ **Hardware compatibility matters. Not all GPUs are safe for AI workloads.**

#### Hardware compatibility matrix

| Hardware | Status | Notes |
|----------|--------|-------|
| NVIDIA consumer/workstation (RTX 30xx, 40xx, A-series, L-series) + CUDA | ✅ Expected to work | Mature CUDA stack, production-grade for AI inference. Same silicon class as RunPod's nodes. |
| NVIDIA data center (H100, A100, L40S) + CUDA | ✅ Expected to work | Designed for sustained AI workloads. |
| Enterprise AMD Instinct (MI-series / CDNA) + ROCm | ✅ Expected to work | Purpose-built for compute; ROCm is first-class on this class. |
| Apple Silicon (M-series) + MPS | ⚠️ Not integrated | Whisper runs on MPS via PyTorch, but this project's local-GPU path targets Linux + CUDA/ROCm. |
| **Consumer AMD Radeon (RDNA 2 / RDNA 3) + ROCm** | ⚠️ **KNOWN UNSTABLE** | Well-documented instability under sustained AI inference. **See cautionary tale below.** |
| Integrated GPUs, low-VRAM (<8 GB), pre-Pascal NVIDIA | ❌ Not recommended | Models won't fit or will thrash. Use CPU fallback or remote GPU. |

#### Maintainer's cautionary tale

The maintainer attempted this pipeline on an **AMD Radeon 6800 XT (RDNA 2) + ROCm** on CachyOS/Arch Linux. During a Whisper transcription job, the host **crashed catastrophically**: the system became unresponsive, on reboot the UEFI/BIOS configuration had been wiped to defaults, and the project's working tree on local disk was corrupted beyond recovery. The project was only recoverable because it had been pushed to GitHub. This is consistent with the well-documented history of retail Radeon + ROCm instability under AI workloads (driver resets, VRAM corruption, kernel panics, and — in this case — firmware-adjacent damage).

The maintainer **does not have and cannot afford** a GPU that is known-good for local AI inference. Consequently:

- The rented Vast.ai node ([GPU node](#gpu-node-vllm-translation--faster-whisper-stt-vastai)) is the **path the maintainer uses**; RunPod serverless is historical.
- Local GPU remains available in the codebase for users whose hardware actually supports sustained AI workloads.
- If you have retail AMD Radeon RDNA 2 or RDNA 3 hardware: **do not assume it will work**. Short test jobs first, monitor GPU reset counts (`dmesg | grep amdgpu`), keep your project under version control pushed to a remote, and have filesystem/BIOS backups.

#### Setup (hardware on the "expected to work" list)

1. **Install packages** (Arch/CachyOS examples — adapt to your distro):

   - NVIDIA + CUDA: `nvidia` + `cuda` + `python-pytorch-cuda` + `python-openai-whisper`
   - Enterprise AMD + ROCm: `rocm-hip-runtime` + `python-pytorch-opt-rocm` + `python-openai-whisper`

2. **Start the service**:

   ```bash
   cd extras/whisper-gpu
   sudo ./setup.sh
   ```

3. **Configure** (only required when the GPU service runs on a different host from the app, e.g. app inside a VM, GPU on the host):

   ```bash
   # Local GPU — Whisper service on a reachable host (same-box, LAN, or libvirt bridge for VMs)
   AUDIOBOOKS_WHISPER_GPU_HOST=<your-whisper-host>
   AUDIOBOOKS_WHISPER_GPU_PORT=8765
   ```

4. **Operational notes**:
   - Long transcription jobs load the GPU hard — avoid sharing the GPU with interactive desktop/display tasks during a batch
   - Local GPU is automatically deprioritized for long-form work when remote providers are configured
   - Useful as a testing and development tool; for full-library translation the maintainer rents a GPU node per batch

---

## Turning audio translation off

If you want text translation but not audio translation, leave every STT
and TTS variable unset and record your intent in `/etc/audiobooks/disabled-units`:

```bash
sudo tee -a /etc/audiobooks/disabled-units >/dev/null <<'EOF'
audiobook-stream-translate.service
audiobook-translation-monitor-live.timer
audiobook-translation-monitor-sampler.timer
EOF
```

That file is all you need — the next `install.sh` or `upgrade.sh` run disables
and stops anything named in it. To apply it immediately without waiting for an
upgrade, disable the units by hand as well:

```bash
sudo systemctl disable --now audiobook-stream-translate.service
sudo systemctl disable --now audiobook-translation-monitor-live.timer
sudo systemctl disable --now audiobook-translation-monitor-sampler.timer
```

This is safe. `audiobook.target` declares all three with `Wants=`, not
`Requires=` (see `systemd/audiobook.target`), so a disabled or failed unit
cannot prevent the target — or any other service — from starting. Text
translation, the library, the player, and the API are unaffected.

### The `disabled-units` file

One unit name per line. Blank lines and `#` comments are ignored, and inline
comments and surrounding whitespace are fine:

```text
# Audio translation — off while no GPU node session is in use
audiobook-stream-translate.service
audiobook-translation-monitor-live.timer     # 30s live tier
audiobook-translation-monitor-sampler.timer  # 5min sampler tier
```

Two properties worth knowing:

- **Absence means "no opinion", not "enable".** Units you never mention behave
  exactly as before, so a unit shipped by a future release still gets enabled
  normally on upgrade. This is why the file lists what to keep off rather than
  what to turn on.
- **The list is authoritative, not advisory.** A listed unit that is currently
  running is stopped and disabled, so writing a name into the file *is* the
  operation. Removing the name and upgrading turns the unit back on.

> ### Before v8.4.3, `systemctl disable` did not survive an upgrade
>
> `upgrade.sh` re-enabled these units every run, from three separate places —
> the `Wants=` parse of the installed `audiobook.target`, a `standalone_units`
> array, and an unconditional "belt-and-suspenders" enable **and start** of
> `audiobook-stream-translate.service`. `install.sh` did the same.
>
> An operator-side `systemctl disable` therefore survived only until the next
> upgrade and was then silently reverted, with the timers firing again as though
> nothing had happened. This is not hypothetical: the v8.4.2.5 deploy re-enabled
> and restarted all three units on the reference deployment, undoing a
> deliberate mitigation that had been in place since 2026-08-26.
>
> `systemctl is-enabled` cannot express intent — it reports `disabled` both for a
> unit the operator switched off and for one that has simply never been enabled —
> which is why intent is recorded in a file instead of inferred from unit state.

## Configuration Reference

All localization settings are environment variables, read from `/etc/audiobooks/audiobooks.conf` or `~/.config/api-keys.env`. API keys should always go in `~/.config/api-keys.env` (permissions `600`, owner-only).

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_DEFAULT_LOCALE` | `en` | Default locale for the UI when no user preference is set |
| `AUDIOBOOKS_SUPPORTED_LOCALES` | `en,zh-Hans` | Comma-separated list of enabled locale codes |

### STT (Speech-to-Text)

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_STT_PROVIDER` | `auto` | Provider selection: `auto`, `whisper` (RunPod single-endpoint transitional path, historical), or `local-gpu`. Auto is the default and builds its candidate list from any configured RunPod endpoints (historical) plus the `AUDIOBOOKS_WHISPER_GPU_HOST` service when it is reachable. |

### TTS (Text-to-Speech)

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_TTS_PROVIDER` | `edge-tts` | Provider: `edge-tts` or `xtts-runpod` |
| `AUDIOBOOKS_TTS_VOICE_ZH` | `zh-CN-XiaoxiaoNeural` | Microsoft Neural TTS voice for Chinese narration (edge-tts) |

For additional languages, set `AUDIOBOOKS_TTS_VOICE_<LANG>` where `<LANG>` is the uppercase language subtag. For example, `AUDIOBOOKS_TTS_VOICE_JA` for Japanese, `AUDIOBOOKS_TTS_VOICE_KO` for Korean.

To list all available edge-tts voices:

```bash
edge-tts --list-voices
```

### API Keys

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_RUNPOD_API_KEY` | (none) | RunPod serverless API key |

### Provider Endpoints

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT` | (none) | RunPod warm (`min_workers>=1`) Whisper endpoint — streaming playback |
| `AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT` | (none) | RunPod cold (`min_workers=0`) Whisper endpoint — batch backfill |
| `AUDIOBOOKS_RUNPOD_WHISPER_ENDPOINT` | (none) | Transitional single-endpoint RunPod fallback — unset once the streaming/backlog pair is configured |
| `AUDIOBOOKS_RUNPOD_XTTS_ENDPOINT` | (none) | RunPod serverless XTTS endpoint ID |
| `AUDIOBOOKS_WHISPER_GPU_HOST` | (none) | Whisper service host: `127.0.0.1` through the `gpu-node.sh tunnel`, or a machine you own (unset disables the local-GPU path) |
| `AUDIOBOOKS_MT_ENDPOINT` | (none) | vLLM OpenAI-compatible URL for machine translation, e.g. `http://localhost:8000` through the tunnel; unset = no MT provider |
| `AUDIOBOOKS_MT_MODEL` | `Qwen/Qwen3-8B` | Model name sent to the vLLM endpoint |
| `AUDIOBOOKS_WHISPER_GPU_PORT` | `8765` | Whisper service port |

### Metadata Enrichment

| Variable | Default | Description |
|----------|---------|-------------|

---

## Adding a New Language

Adding a new language requires no code changes. Follow these steps:

### Step 1: Create the Locale File

Copy the English locale file and translate all keys:

```bash
cp library/locales/en.json library/locales/<locale>.json
```

For example, to add Japanese:

```bash
cp library/locales/en.json library/locales/ja.json
```

The locale file contains 1,093 keys organized by UI section. Each key maps to a translated string:

```json
{
  "nav.library": "Library",
  "nav.collections": "Collections",
  "nav.settings": "Settings",
  "player.play": "Play",
  "player.pause": "Pause",
  ...
}
```

You can translate the file manually, use a machine translation API programmatically, or use any translation tool of your choice. Every key must have a translation -- missing keys fall back to the English value at runtime.

### Step 2: Register the Locale

Add your new locale to `AUDIOBOOKS_SUPPORTED_LOCALES` in `/etc/audiobooks/audiobooks.conf`:

```bash
AUDIOBOOKS_SUPPORTED_LOCALES="en,zh-Hans,ja"
```

Restart the API service after changing this value:

```bash
sudo systemctl restart audiobook-api.service
```

### Step 2a: Backfill the 6-minute sampler for the new locale

Adding a new locale doesn't automatically retro-translate existing books'
opening samples. Run the sampler reconciler to enqueue a 6-minute pretranslation
for every book × the new locale:

```bash
# Target a single locale (recommended — bounded cost per locale):
sudo -u audiobooks python3 /opt/audiobooks/scripts/sampler-reconcile.py --locale ja

# Or preview first:
sudo -u audiobooks python3 /opt/audiobooks/scripts/sampler-reconcile.py --locale ja --dry-run

# Or cap the batch size if you're watching RunPod budget:
sudo -u audiobooks python3 /opt/audiobooks/scripts/sampler-reconcile.py --locale ja --max-books 50
```

The reconciler is idempotent — safe to re-run. Books with an existing
completed `sampler_jobs` row are skipped. Samplers enqueue at priority `p2`,
so they never compete with live-playback work (the DB trigger enforces this).
Newly added books after this point get their sampler enqueued automatically
via the scan-time hook. See `docs/SAMPLER.md` for the full design.

### Step 3: Configure a TTS Voice

If you want translated audio narration, set the TTS voice for your language. First, find available voices:

```bash
edge-tts --list-voices | grep ja-JP
```

Example output:

```text
ja-JP-NanamiNeural
ja-JP-KeitaNeural
```

Then set the voice in `/etc/audiobooks/audiobooks.conf`:

```bash
AUDIOBOOKS_TTS_VOICE_JA="ja-JP-NanamiNeural"
```

### Step 4: Test

1. Log in to the web UI
2. Open user preferences (profile settings)
3. Select your new locale from the language dropdown
4. Verify all UI text renders in the target language
5. Check that book descriptions translate on demand (requires cached translations or a configured machine-translation provider)

### Step 5: CJK Considerations

If your new language uses CJK characters (Chinese, Japanese, Korean), no additional work is needed. Audiobook Manager already includes:

- **CJK-aware search**: Full-text search handles CJK character boundaries correctly
- **Collation sort**: Library sorting uses locale-appropriate ordering (e.g., pinyin for Chinese, stroke order, etc.)
- **Font rendering**: The web UI loads CJK-capable font stacks

---

## Cost and Time Investment

This section provides honest numbers from the project's real-world development experience so prospective admins can make informed decisions.

### Total Project Investment (November 2025 -- April 2026)

Audiobook Manager is a substantial engineering project. The localization system is one component of a much larger whole:

| Category | Estimate | Notes |
|----------|----------|-------|
| Developer time (total project) | ~1,000+ hours | Architecture, coding, testing, debugging, infrastructure. Includes all features, not just localization. |
| Human labor value | ~$70,000 | At a senior *NIX engineer's rate (~$70/hour based on $145k/year salary) |
| **Total project cost** | **~$70,000** | Overwhelmingly human time |

### Localization-Specific Costs

The translation/multilingual subsystem represents a meaningful fraction of the total effort:

| Category | Estimate | Notes |
|----------|----------|-------|
| Developer time on localization | 150-250 hours | i18n architecture, STT/TTS pipeline, locale files, testing, provider integration, subtitle generation |
| Developer labor value | ~$10,500-17,500 | At ~$70/hour |
| Machine translation API | $0-50 | A free tier (500k chars/month) was sufficient. 1,038 UI strings + book descriptions consumed a fraction of it. |
| GPU rental (STT + TTS) | $150-450 | For a library of 600-800 audiobooks (~2,000-4,000 hours of audio). Varies by GPU pricing and audio length. |
| **Total localization cost** | **~$10,650-18,000** | Mostly developer time |

### GPU Cost Breakdown

For a library of ~600-800 audiobooks:

| Task | GPU Hours | Cost Range | Notes |
|------|-----------|------------|-------|
| STT transcription (Whisper) | 50-150 hrs | $10-75 | Depends on audio length and GPU speed. An A100 transcribes ~10x real-time. |
| TTS narration (edge-tts) | 0 | $0 | Free. Microsoft Neural TTS, no API key, no GPU required. |
| TTS narration (XTTS voice cloning) | 200-600 hrs | $40-300 | Only if you want narrator voice preservation. Much more compute-intensive than edge-tts. |
| **Total GPU cost** | -- | **$10-375** | edge-tts is free; XTTS adds significant cost |

### What a New Admin Should Expect

| Scenario | Time | Cost |
|----------|------|------|
| **Using a shipped language** (en or zh-Hans) with existing locale files | Hours | $0 (edge-tts) or $150-450 (XTTS) in GPU for audio narration |
| **Adding a new language** with a machine-translation provider + edge-tts | 1-3 days | $0-50 (free-tier MT + free TTS). GPU for STT: $10-75. |
| **Adding a new language** with XTTS voice cloning | 1-3 days + GPU processing time | $150-450 depending on library size |
| **Building this from scratch** (as this project did) | Months of engineering | $10,000+ in developer time alone |

The key takeaway: the expensive part is building the infrastructure, not using it. Once the pipeline exists, adding a language is a configuration task, not an engineering project.

---

## Translation Asset Portability

Translation work (VTT subtitles, TTS audio files, metadata translations) costs real money in GPU time. The `audiobook-translations` CLI tool lets you export and import these assets between environments (dev, test, QA, production) without re-translating.

### Export

Export all translation assets to a portable tarball:

```bash
audiobook-translations export -o translations.tar.gz
```

Export only a specific locale:

```bash
audiobook-translations export -o zh-translations.tar.gz --locale zh-Hans
```

The export bundles:

- VTT subtitle files (English source + translated)
- TTS audio files
- Database rows (metadata translations, collection translations, string translations)
- A manifest mapping audiobook IDs to titles for cross-environment matching

### Import

Import a translation archive into a different environment:

```bash
audiobook-translations import -a translations.tar.gz
```

The import process:

1. Reads the manifest from the archive
2. Matches books by title between source and target databases
3. Extracts VTT and audio files to the correct book directories
4. Inserts/replaces translation database rows
5. Marks imported books as completed in the translation queue

Books that exist in the archive but not in the target database are skipped with a warning.

### Custom Database Path

Both commands accept `--db` to specify a non-default database path:

```bash
audiobook-translations export --db /path/to/audiobooks.db -o export.tar.gz
audiobook-translations import --db /path/to/audiobooks.db -a export.tar.gz
```

If `--db` is not specified, the tool uses `$AUDIOBOOKS_DATABASE` from your configuration.

---

## Dependencies and Requirements

### Required

| Dependency | Minimum Version | Purpose |
|------------|----------------|---------|
| Python | 3.12+ (3.14 recommended) | Localization module runtime |
| ffmpeg | 7.0+ | Audio conversion, chapter detection, format transcoding |
| SQLite | 3.38+ (with JSON1) | Translation metadata storage |
| `edge-tts` | 7.0+ | Default TTS provider (Microsoft Neural TTS) |
| `requests` | 2.33+ | HTTP client for GPU provider APIs and all remote calls |
| `pypinyin` | 0.55+ | Mandarin pinyin conversion for CJK sort and search (zh-Hans locale) |

### Optional

| Dependency | Purpose |
|------------|---------|
| `openai-whisper` | Local CPU fallback for STT (very slow but always available) |
| CUDA toolkit | NVIDIA GPU acceleration for local Whisper (see [Local GPU (Optional)](#local-gpu-optional) for supported hardware) |
| ROCm | AMD GPU acceleration for local Whisper — **enterprise AMD Instinct only**; consumer Radeon RDNA 2/3 is known-unstable, see [Local GPU (Optional)](#local-gpu-optional) |

### Python Package Installation

The localization dependencies are included in the project's `requirements.txt` and installed automatically during `install.sh` or `upgrade.sh`. For manual installation:

```bash
pip install edge-tts requests pypinyin
```

For local Whisper fallback (optional):

```bash
pip install openai-whisper
```

---

## Attribution

The localization system was built using the following open-source and commercial services:

| Component | Role | License/Terms |
|-----------|------|---------------|
| [OpenAI Whisper](https://github.com/openai/whisper) | Speech-to-text transcription | MIT License |
| [Microsoft Edge TTS](https://github.com/rany2/edge-tts) | Neural text-to-speech synthesis | MIT License (library); Microsoft terms (service) |
| [XTTS / Coqui TTS](https://github.com/coqui-ai/TTS) | Multilingual voice cloning | MPL-2.0 License |
| [RunPod](https://www.runpod.io/) | Serverless GPU platform | Commercial |
| [Hugging Face](https://huggingface.co/) | Model hosting (Whisper, XTTS models) | Various open licenses |

---

## Troubleshooting

### Provider Connection Issues

**Symptom**: Translation jobs fail with connection errors or timeouts.

| Provider | Common Cause | Fix |
|----------|-------------|-----|
| RunPod (historical) | Serverless cold start timeout | First request after idle period takes 10-30 seconds. Increase client timeout or send a warm-up request. |
| Local GPU / Vast.ai node | Service not started, or tunnel down | Run `scripts/gpu-node.sh status` and `scripts/gpu-node.sh bootstrap-status`; reopen with `scripts/gpu-node.sh tunnel`. |

**Fallback behavior**: When a remote provider fails, the system falls back to local processing once per request. If you see "falling back to local" in logs, your remote provider is unreachable but translation is still proceeding (slowly).

### GPU Cold Starts

**Symptom**: First transcription request takes 30-120 seconds before processing begins.

- **RunPod serverless (historical)**: Cold start is inherent to serverless -- the GPU spins up on demand. Subsequent requests within the keep-alive window are fast. The streaming endpoint at `min_workers>=1` keeps a worker warm to avoid this on live playback.
- **Mitigation**: For batch processing, send a short test file first to warm the instance before queuing long audiobooks.

### CJK Font Rendering

**Symptom**: Chinese/Japanese/Korean characters display as boxes or tofu in the web UI.

- The web UI uses system font stacks with CJK fallbacks. If your browser or OS lacks CJK fonts, install them:

  ```bash
  # Arch/CachyOS
  sudo pacman -S noto-fonts-cjk

  # Debian/Ubuntu
  sudo apt install fonts-noto-cjk

  # Fedora
  sudo dnf install google-noto-sans-cjk-fonts
  ```

- Clear browser cache after font installation.

### Subtitle Sync Issues

**Symptom**: VTT subtitles are out of sync with audio playback.

- Subtitle timestamps come from Whisper's word-level alignment. Sync quality depends on:
  - Audio quality (clean recordings sync better than noisy ones)
  - Whisper model size (larger models produce better timestamps)
- **Fix**: Re-transcribe with a larger Whisper model or a higher-quality GPU provider.

### Translation Quality

**Symptom**: Translated text reads unnaturally or contains errors.

- Machine translation quality varies by provider and language pair. English-to-Chinese is generally excellent.
- **Glossary support**: Add domain-specific terms to `library/localization/glossary/` to override default machine translations for proper nouns, series names, and specialized vocabulary.
- **Manual correction**: Edit translated strings directly in the locale JSON file or the database. Manual edits are preserved across re-translations.

### edge-tts Voice Issues

**Symptom**: TTS audio sounds robotic or uses the wrong voice.

- List available voices: `edge-tts --list-voices | grep <language-code>`
- Different voices have different quality levels. `XiaoxiaoNeural` (Chinese) and `NanamiNeural` (Japanese) are among the highest quality.
- Set the voice explicitly in your configuration rather than relying on defaults.

### Import Fails with "No matching books found"

**Symptom**: `audiobook-translations import` reports 0 matched books.

- The import process matches books by title between the source and target databases. If titles differ (e.g., due to re-scanning or metadata updates), no match is found.
- **Fix**: Ensure both environments have the same audiobooks scanned before importing. The tool prints which books were unmatched.
