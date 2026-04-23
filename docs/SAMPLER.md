# 6-Minute Pretranslation Sampler

**Status**: v8.3.8 — released in local staged mode (not promoted to prod at time of writing).
**Audience**: operators, contributors, and anyone diagnosing why a book's 🎧 sample button is/isn't showing.

## Purpose (three-at-once)

The sampler pretranslates the opening of every book — once, per enabled non-EN locale, bounded to about 6 minutes. It serves three goals simultaneously:

1. **Cost control.** Only books that users actually commit to listening to past the sample incur full-book translation cost. A library of 300 books in `zh-Hans` costs ~$1.50–$6 once for samples; full-book translation happens on demand.
2. **Library-wide discovery.** Any non-EN listener can browse the entire library and preview any title in their language with zero GPU wait. They decide whether to commit before the system spends money on their behalf.
3. **GPU cold-start runway.** When a user does commit, the live-translation pipeline needs ~60s to warm a cold GPU and another ~2 min to fill the buffer ahead of the cursor. The sample gives the user something to listen to during that runway, so they never see the "排队中" spinner again.

## What gets translated

For each `(audiobook_id, locale)` where `locale` is a non-EN entry in `AUDIOBOOKS_SUPPORTED_LOCALES`, the sampler covers **at least 6 minutes** of audio starting at chapter 0 segment 0. The exact scope depends on chapter boundaries:

| Chapter 0 duration | Result |
|---|---|
| Shorter than 6 min | Keep going into chapter 1 (and beyond) until 6 min total is reached. Apply the same boundary rule to the last chapter we touch. |
| 6 min exactly | Just that chapter (12 segments). Done. |
| 6 min < ch0 ≤ 9 min | Extend to chapter end for a cohesive sample (we were going to take most of it anyway). |
| ch0 > 9 min | Hard stop at exactly 6 min (12 segments). Don't cut deep into a long chapter. |

"9 min" is the sum of `SAMPLER_MIN_SECONDS` (360s = 6 min) and `SAMPLER_MAX_EXTEND_SECONDS` (180s = 3 min slack) — the extend budget past the 6-min mark. The pure scope algorithm lives in `library/localization/sampler.py::compute_sampler_range` and is covered by 11 trace-case tests.

## Priority invariant — live playback always wins

The streaming worker orders pending segments by `priority ASC`. To guarantee the sampler never starves the user who is actually listening right now:

- **p0** = live cursor buffer, current book only
- **p1** = live forward chase (buffer ahead of cursor), current book only
- **p2** = sampler work
- **p3** = backlog / all other bulk work

The DB enforces this mechanically via a trigger:

```sql
CREATE TRIGGER streaming_segments_sampler_priority_ins
BEFORE INSERT ON streaming_segments
WHEN NEW.origin = 'sampler' AND NEW.priority < 2
BEGIN
    SELECT RAISE(ABORT, 'sampler rows must have priority >= 2 (p0/p1 reserved for live playback)');
END;
```

A matching UPDATE trigger catches attempts to demote a sampler row after the fact. These are the same invariant restated in SQL — any INSERT or UPDATE that would violate it is aborted by the DB engine itself, independent of what any application code thinks it's doing.

## Adaptive buffer-fill threshold

When a user plays a cached sample in `zh-Hans`, we need to fire the live-translation pipeline early enough that the buffer catches up before the sample ends. Too early and we waste GPU money on casual browsers; too late and the GPU cold-start extends past the end of the sample and Qing sees a spinner.

We resolve this adaptively based on **current STT provider warmth** — aggregated across every STT backend the operator has configured (RunPod, Vast.ai, self-hosted whisper-gpu, etc.):

| State | Threshold | Runway |
|---|---|---|
| Cold (no provider has ready workers) | Fire at segment 3 (90s in) | 4.5 min to end of sample — safest, covers worst-case 60s cold-start |
| Warm (at least one provider has ≥1 ready worker) | Fire at segment 4 (120s in) | 4 min to end of sample — more cost-aware, user is more committed |

The frontend queries `GET /api/translate/warmth` to learn the current threshold, then calls `POST /api/translate/sampler/activate` once playback passes that segment. The server creates p0/p1 segments from `cursor+1` forward. The worker picks them up ahead of any p2 sampler work on other books.

The warmth response includes a `providers` array (`[{"name", "ready", "endpoint_id"}, ...]`) so the UI can surface per-provider state when helpful. Warm = **any** provider ready, so a single warm farm is enough to shorten the runway; this matches real behavior, where the dispatcher will pick the warm farm for the first call.

Warmth probe is cached server-side for 60s to bound provider /health traffic. Default on probe failure (timeout, no key configured): assume cold (safer).

### Provider options

The project treats STT backend choice as an **operator deployment decision**, not a project contract. Any of the following work:

- **RunPod serverless** — pay-per-second, no minimum spend, `AUDIOBOOKS_RUNPOD_*` keys
- **Vast.ai serverless** — cheaper on some GPU classes, `AUDIOBOOKS_VASTAI_SERVERLESS_*` keys
- **Self-hosted GPU Whisper service** — CUDA/ROCm/Apple Silicon machine on the LAN, `AUDIOBOOKS_WHISPER_GPU_HOST` + `AUDIOBOOKS_WHISPER_GPU_PORT`
- **CPU-only `faster-whisper`** — no GPU, slower but zero operating cost; advanced deployment (requires wiring in the provider, see `library/localization/stt/`)

Multiple backends can be configured at once. The pipeline **round-robins** across configured candidates by default (`AUDIOBOOKS_STT_DISTRIBUTION=round_robin`), so parallel workers spread the load across farms. Modes: `round_robin` (default), `random`, `primary` (pre-8.3.8 legacy — always picks the first candidate).

See `docs/SERVERLESS-OPS.md` for endpoint provisioning and `docs/MULTI-LANGUAGE-SETUP.md` for the full configuration reference.

### Backfill acceleration — sampler-burst.sh

For backfills where many books need their 6-min sample translated at once (e.g., after adding a new locale to a 300-book library), `scripts/sampler-burst.sh` spawns N parallel `stream-translate-worker.py` processes that exit automatically when the queue drains. Combined with dual-provider round-robin, a 4-worker burst across 2 STT farms gives ~4× wall-clock speedup on a cold library:

```bash
# After a locale-addition reconcile, spawn 4 parallel workers to drain
sudo systemctl stop audiobook-stream-translate.service   # free the queue
sudo -u audiobooks /opt/audiobooks/scripts/sampler-burst.sh --workers 4
# Or run reconcile + burst in one step
sudo -u audiobooks python3 /opt/audiobooks/scripts/sampler-reconcile.py --burst 4
```

## Triggers — when a sampler is enqueued

### Scan-time (automatic)

When the scanner imports a new book (`library/scanner/utils/db_helpers.py::insert_audiobook`), the `sampler_hook.enqueue_sampler_for_new_book` call fires for each enabled non-EN locale. Failures are logged and swallowed — sampler is enrichment, never blocks book ingestion.

### Locale-addition (manual)

When you add a new locale to `AUDIOBOOKS_SUPPORTED_LOCALES`, existing books don't automatically get sampled — you must run the reconciler:

```bash
sudo -u audiobooks python3 /opt/audiobooks/scripts/sampler-reconcile.py
# Or with constraints:
sudo -u audiobooks python3 scripts/sampler-reconcile.py --locale zh-Hans --max-books 50
sudo -u audiobooks python3 scripts/sampler-reconcile.py --dry-run
```

The reconciler iterates every book, checks `sampler_jobs` for the targeted locale(s), and enqueues only for missing pairs. Idempotent — safe to re-run; already-complete jobs are untouched.

### Admin API (testing / targeted retry)

```bash
curl -X POST http://localhost:5001/api/translate/sampler/prefetch \
     -H 'Content-Type: application/json' \
     -d '{"audiobook_id": 42, "locale": "zh-Hans"}'
```

Returns the `sampler_jobs` row. Idempotent: re-hitting with an already-complete (book, locale) returns `status=complete` without side effects.

## Status reporting

### Single-book

```bash
curl http://localhost:5001/api/translate/sampler/status/42/zh-Hans
```

Returns `{"status": "none|pending|running|complete|failed", "progress": 0.0..1.0, ...}`.

### Bulk (used by library browse)

```bash
curl 'http://localhost:5001/api/translate/sampler/batch-status?locale=zh-Hans&ids=1,2,3,4,5'
```

Returns `{"1": "complete", "2": "none", ...}`. Library UI chunks in batches of 100.

## UI surface

In non-EN locales, the library-browse grid shows a 🎧 sample button on each book card whose `sampler_jobs.status == 'complete'`. Clicking it triggers the same `shellPlay` flow as the normal play button — playback starts instantly from the cached chapter-0 audio. Once the user crosses the buffer-fill threshold, the frontend activates the live pipeline transparently.

No special "upgrade to full translation" UI — the transition from sample to full is driven by position crossing the 6-min mark during playback; the live buffer has been filling since the threshold and is ready.

## Cost envelope

Per `(book, locale)`:

- 12 segments × (Whisper STT ~$0.0003 + DeepL 1-sentence ~$0.0001 + edge-tts free) ≈ **$0.005**
- For a 300-book library × 1 locale = **~$1.50**, one-time.
- Adding a second locale: same math again.

Books users don't touch past the sample never incur full-book cost. This is the "cost control" goal making itself concrete.

## Failure modes & recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| Sample button never appears on any card | No sampler has been enqueued for your locale. | Run `scripts/sampler-reconcile.py --locale X` |
| Status stays `pending` forever | Streaming worker dead / start-limit-hit. | `systemctl status audiobook-stream-translate`; check recent journal |
| Status stays `running` for hours | Worker can't reach any configured STT backend (network / key wrong / no providers configured). | Check worker logs for HTTP 401/timeout against whichever backends the operator configured (api.runpod.ai, run.vast.ai, self-hosted whisper-gpu host, etc.) |
| Status = `failed` | Worker hit an error it couldn't recover from on any segment. | Look at `sampler_jobs.error`; re-enqueue via the admin prefetch endpoint |
| Sample plays but live buffer never fills | Adaptive threshold never fired — maybe JS error. | Browser dev tools: look for POST `/api/translate/sampler/activate` after segment 3 or 4 |

## Related docs

- `docs/STREAMING-TRANSLATION.md` — how the live streaming pipeline works end-to-end
- `docs/SERVERLESS-OPS.md` — STT backend endpoint provisioning (RunPod, Vast.ai, self-hosted GPU recipes)
- `docs/MULTI-LANGUAGE-SETUP.md` — enabling new locales
