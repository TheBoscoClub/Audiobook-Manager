# Streaming Translation Pipeline

On-demand, real-time translation triggered by playback. When a user presses play
on an untranslated audiobook, the system dispatches chapter-level work to GPU
workers, buffers three minutes of translated audio, then begins playback.
Pre-translated books serve instantly from cache.

## Why Streaming Exists

The library contains 1,861 audiobooks. Batch-translating all of them upfront
(STT + DeepL + TTS for every chapter in every locale) would cost hundreds of
dollars in GPU time. As of v8.3.0, the batch pipeline has pre-translated 327
books (5,245 chapters). The remaining 1,534 books sit untranslated.

Streaming solves this by paying only for what a listener actually plays.

## Two Pipelines, One Cache

| Pipeline | Trigger | Processing | Output |
|----------|---------|-----------|--------|
| **Batch** (`batch-translate.py`) | Timer + queue | Entire chapters, background | Permanent VTT + TTS audio |
| **Streaming** (`streaming_translate.py`) | Playback | 30-second segments, real-time | Segments → consolidated VTT |

Both pipelines write to the same permanent cache (`chapter_subtitles` and
`chapter_translations_audio` tables). Once a chapter is translated by either
pipeline, future plays are free. The system self-heals: listening patterns
gradually fill the cache, and batch fills the rest during idle time.

## End-to-End Playback Flow

### Phase 1 — App Open (GPU Warm-Up)

When the app opens and the user's locale is not English, the frontend sends
`POST /api/translate/warmup`. This writes a hint to the database so the
translation daemon can proactively spin up a GPU instance, reducing cold-start
latency from ~60 seconds to near-zero.

### Phase 2 — Press Play

`shell.js` calls `streamingTranslate.check(bookId, locale)`, which sends
`POST /api/translate/stream` to the coordinator:

```text
Player → Coordinator API → Database lookup:
  ├── chapter_subtitles exists? (batch cache)
  ├── chapter_translations_audio exists? (batch TTS cache)
  │
  ├── Both exist → { state: "cached" } → instant playback
  │
  └── Missing → { state: "buffering", session_id, segment_bitmap }
```

### Phase 3 — Buffering

The frontend state machine transitions from `IDLE` to `BUFFERING`:

1. A **visual overlay** slides up above the player bar — gold-themed progress
   bar showing segment completion (e.g., "3 / 6")
2. A **localized audio notification** plays via pre-generated edge-tts clips
   (e.g., zh-Hans: *"请稍候，正在为您翻译本书。字幕和语音朗读即将开始。"*)
3. The **main audio pauses** — no point playing English narration during the wait

The coordinator simultaneously:

- Creates `streaming_segments` rows for the active chapter (priority 0)
- Creates rows for the next chapter (priority 1 = prefetch)
- Each row represents one 30-second segment:
  `(audiobook_id, chapter_index, segment_index, locale, state='pending')`

### Phase 4 — GPU Worker Processing

`stream-translate-worker.py` polls the `streaming_segments` table in priority
order and processes each segment:

```text
1. Atomically claim next pending segment (ORDER BY priority, chapter, segment)
2. ffmpeg stream-copy → extract 30-second audio slice from the chapter
3. STT (faster-whisper on GPU) → raw English transcript
4. Translation (DeepL API) → translated text
5. Generate VTT with timestamps
6. Offset timestamps for segment position within the chapter
7. POST /api/translate/segment-complete → report inline VTT content
```

Active chapters (priority 0) are processed segment-by-segment for low-latency
streaming. Prefetch chapters (priority 1) can be processed as a single batch
unit for efficiency.

### Phase 5 — Real-Time Push

When the coordinator receives a segment completion callback, it:

1. Updates the segment state to `completed` in the database
2. Broadcasts `segment_ready` via WebSocket to all connected clients
3. Broadcasts `buffer_progress` with completed/total counts

The frontend receives these events and updates the progress bar in real time.

### Phase 6 — Buffer Threshold Met

Once 6 segments are complete (3 minutes of audio), the state machine transitions
from `BUFFERING` to `STREAMING`:

- Overlay hides
- Notification audio stops
- Main audio **resumes** with translated subtitles available
- GPU workers continue processing remaining segments ahead of the cursor

### Phase 7 — Seek Handling

| Action | Behavior |
|--------|----------|
| ±30 seconds within buffer | Instant — segment already cached, no interruption |
| Jump beyond cached range | `POST /api/translate/seek` → reprioritize from new position → re-enter buffering |
| Jump to batch-cached chapter | Instant — already in permanent cache |

The seek endpoint deprioritizes all pending segments (priority 2) and promotes
the 6 segments starting from the seek target to priority 0.

### Phase 8 — Consolidation

When all segments of a chapter complete, `_consolidate_chapter()`:

1. Reads VTT content from all segment rows
2. Strips duplicate `WEBVTT` headers, merges into a single file
3. Writes to `subtitles/{audiobook_id}/ch{N}.{locale}.vtt`
4. Inserts into `chapter_subtitles` — the same permanent cache used by batch

After consolidation, the chapter is indistinguishable from a batch-translated
one.

## Architecture Diagram

```text
┌───────────────────────────────────────────────────────────────────┐
│                        WEB PLAYER                                  │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ shell.js │──►│ streaming-   │──►│ Buffering Overlay        │  │
│  │ playBook │   │ translate.js │   │ (progress bar + audio)   │  │
│  │ + seek   │   │ state machine│   └──────────────────────────┘  │
│  └──────────┘   └──────┬───────┘                                  │
│                         │                                          │
│            ┌────────────┼────────────┐                             │
│            │ WebSocket  │  REST API  │                             │
│            │ events     │  calls     │                             │
└────────────┼────────────┼────────────┼─────────────────────────────┘
             │            │            │
             ▼            ▼            ▼
┌───────────────────────────────────────────────────────────────────┐
│                     COORDINATOR API                                │
│                                                                    │
│  POST /api/translate/stream         Request streaming translation  │
│  POST /api/translate/seek           Handle seek to uncached pos    │
│  POST /api/translate/warmup         Pre-warm GPU on app open       │
│  GET  /api/translate/segments/…     Segment completion bitmap      │
│  GET  /api/translate/session/…      Session state                  │
│  POST /api/translate/segment-complete   Worker callback            │
│  POST /api/translate/chapter-complete   Worker callback (prefetch) │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │ WebSocket Manager: broadcasts segment_ready,             │     │
│  │   chapter_ready, buffer_progress to all clients          │     │
│  └──────────────────────────────────────────────────────────┘     │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│                        DATABASE                                    │
│                                                                    │
│  streaming_sessions     Active session tracking, GPU warm-up       │
│  streaming_segments     Per-segment state (pending/processing/     │
│                         completed/failed), priority, inline VTT    │
│  chapter_subtitles      Permanent cache (shared with batch)        │
│  chapter_translations_audio  Permanent TTS cache (shared)          │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│                     GPU WORKER FLEET                                │
│                                                                    │
│  stream-translate-worker.py                                        │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │ Poll streaming_segments (priority order)                 │      │
│  │  → ffmpeg: extract 30s audio segment                    │      │
│  │  → faster-whisper: STT on GPU                           │      │
│  │  → DeepL API: translate transcript                      │      │
│  │  → Generate VTT with offset timestamps                  │      │
│  │  → POST /api/translate/segment-complete                 │      │
│  └─────────────────────────────────────────────────────────┘      │
│                                                                    │
│  Runs on: Vast.ai L40S, RunPod instances, or self-hosted GPU       │
└───────────────────────────────────────────────────────────────────┘
```

## Design Constants

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Segment duration | 30 seconds | L40S processes in ~2-3s; small enough for low latency |
| Buffer threshold | 6 segments (3 min) | Enough runway for continuous playback while GPU stays ahead |
| Prefetch ahead | Next chapter | Seamless chapter transitions without re-buffering |
| Active priority | 0 | Processed first — what the user is hearing now |
| Prefetch priority | 1 | Processed after active segments complete |
| Deprioritized | 2 | Segments behind the seek cursor |

## State Machine

```text
                    ┌──────────────────────────────┐
                    │                              │
                    ▼                              │
    ┌────────┐   check()   ┌────────────┐   threshold   ┌────────────┐
    │  IDLE  │────────────►│ BUFFERING  │──────────────►│ STREAMING  │
    │        │  (not cached)│            │   (6 segs)    │            │
    └────────┘             │ • overlay  │               │ • playing  │
        ▲                  │ • audio    │               │ • subs on  │
        │                  │ • paused   │               │            │
        │                  └─────┬──────┘               └─────┬──────┘
        │                        │                             │
        │                   seek beyond                   seek beyond
        │                   cached range                  cached range
        │                        │                             │
        │                        ▼                             │
        │                  ┌────────────┐                      │
        │                  │ BUFFERING  │◄─────────────────────┘
        │  all cached      │ (from seek)│
        │  or English      └────────────┘
        │                        │
        └────────────────────────┘
```

## Controlling Batch Translation

The batch pipeline is independent and controllable:

**Automatic mode** (default): The `audiobook-translate-check.timer` fires every
5 minutes. It queries `translation_queue` for pending rows. If any exist and the
daemon is not running, it starts `audiobook-translate.service`, which provisions
GPU instances, processes the queue, tears down GPUs, and exits.

**Manual mode**: Disable the timer to stop auto-processing:

```bash
# Stop automatic batch translation
sudo systemctl stop audiobook-translate-check.timer

# Start a batch run at any time
sudo systemctl start audiobook-translate.service

# Re-enable automatic mode
sudo systemctl start audiobook-translate-check.timer
```

The daemon manages the full GPU lifecycle — instances spin up on start and tear
down when the queue empties. You are billed only for GPU hours consumed.

**Wedge detection**: If a worker stops making progress for 60 minutes (e.g.,
crashed SSH tunnel), `translation-check.sh` detects the stale heartbeat, restarts
the daemon, and resets stuck rows to `pending`.

## Files

| File | Purpose |
|------|---------|
| `library/backend/api_modular/streaming_translate.py` | Coordinator API (7 endpoints) |
| `library/web-v2/js/streaming-translate.js` | Frontend state machine |
| `library/web-v2/css/shell.css` | Buffering overlay styles |
| `library/web-v2/shell.html` | Overlay markup |
| `scripts/stream-translate-worker.py` | GPU worker (segment processing) |
| `scripts/translation-daemon.sh` | Batch daemon (GPU lifecycle) |
| `scripts/batch-translate.py` | Batch worker (chapter processing) |
| `scripts/translation-check.sh` | Timer-driven batch starter |
| `systemd/audiobook-translate.service` | Batch daemon service unit |
| `systemd/audiobook-translate-check.timer` | 5-minute batch check timer |
| `library/localization/pipeline.py` | Shared STT → translate → VTT pipeline |
| `library/web-v2/audio/translation-buffering-*.mp3` | Localized notification clips |

## Database Schema (Migration 004)

```sql
CREATE TABLE streaming_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id    INTEGER NOT NULL,
    locale          TEXT NOT NULL,
    state           TEXT DEFAULT 'buffering',    -- buffering, streaming, completed, warmup
    active_chapter  INTEGER DEFAULT 0,
    buffer_threshold INTEGER DEFAULT 6,
    gpu_warm        INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE streaming_segments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id    INTEGER NOT NULL,
    chapter_index   INTEGER NOT NULL,
    segment_index   INTEGER NOT NULL,
    locale          TEXT NOT NULL,
    state           TEXT DEFAULT 'pending',      -- pending, processing, completed, failed
    priority        INTEGER DEFAULT 1,           -- 0=active, 1=prefetch, 2=deprioritized
    worker_id       TEXT,
    vtt_content     TEXT,                         -- inline VTT for completed segments
    audio_path      TEXT,
    started_at      DATETIME,
    completed_at    DATETIME,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale)
);
```

## Security

All route handlers validate inputs at the boundary:

- **Locale**: `_sanitize_locale()` enforces `^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$` —
  rejects path traversal (`../`) and log injection (newlines, control characters)
- **Integer IDs**: `audiobook_id`, `chapter_index`, `segment_index` are coerced to
  `int` before any database query or filesystem operation
- **Worker callbacks** (`segment-complete`, `chapter-complete`): internal-only
  endpoints called by GPU workers, not exposed to browser clients
