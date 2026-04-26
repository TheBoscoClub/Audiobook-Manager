# RCA — v8.3.8.6 — Chinese narration silent on prod (catastrophic QA → prod regression)

**Author:** Claude + Bosco (co-maintained) · **Date:** 2026-04-24 ·
**Owning release:** v8.3.8.6 ("sampler-burst venv + idempotent TTS regen") ·
**Incident severity:** user-facing catastrophic — the feature Qing (primary
zh-Hans end user, Bosco's wife) relies on was unusable on prod for days,
and the pretranslation accumulated 6,687 silently-damaged rows plus 400
stale-path orphan rows (a separate failure class that surfaced during
repair). The QA cycle reported green — by both AI and human verification —
on exactly the release that introduced this bug.

---

## Executive summary

A single line in `scripts/sampler-burst.sh` (`PYTHON_BIN="${AUDIOBOOKS_HOME}/venv/bin/python"`
— wrong by one subdirectory `/library/`, plus a silent `|| PYTHON_BIN="python3"` fallback) caused every
burst-spawned worker on prod to produce VTT-only segments with no audio. The
bug was invisible at log level (warnings only, no errors), invisible at DB
level without a specific ratio query, invisible at UI level without playing
a zh-Hans book past 30 seconds, and invisible to every layer of the existing
test pyramid because no layer asked the functional question "did the last
ten segments actually produce audio."

During repair we also uncovered 400 legacy `.opus` rows left orphaned from
the pre-v8.3.3 synth path (files deleted from disk, DB rows stranded), and
confirmed that the claim-queue's session-blocking logic interacts unhelpfully
with orphan repair (stopped sessions block all pending rows for that book).

**Root cause is not the single bug — it is a systemic one: every layer
of defense uses graceful silent degradation when an invariant is violated,
and QA + `/test` + smoke probes + post-deploy canaries are all structural
(health checks) instead of functional (experience checks).**

**Systemic fix:** (1) replace every silent fallback in the codebase with
hard-fail + diagnostic, (2) make the functional smoke probe actually
functional (end-to-end Chinese-audio canary, not just service-up checks),
(3) reshape the QA test plan around the user's real-world flow not around
infra green-lights, (4) add continuous post-deploy audio-coverage metric,
(5) add a nightly DB-vs-filesystem integrity check so stale paths cannot
accumulate silently ever again, (6) enforce QA-before-prod without
fast-path exceptions.

---

## Contents

1. [Executive summary](#executive-summary)
2. [The user-facing failure](#1-the-user-facing-failure)
3. [Timeline of events — from QA-green to prod-red](#2-timeline-of-events--from-qa-green-to-prod-red)
4. [Primary technical failure — sampler-burst venv silent fallback](#3-primary-technical-failure--sampler-burst-venv-silent-fallback)
5. [Concurrent technical failures uncovered during fix](#4-concurrent-technical-failures-uncovered-during-fix)
6. [Layer-by-layer defense breakdown](#5-layer-by-layer-defense-breakdown)
   - 5.1 Unit tests
   - 5.2 Dev VM
   - 5.3 QA VM (the big one — AI + human verified green here)
   - 5.4 `/test` audit phases
   - 5.5 Pre-release upgrade-side smoke probe
   - 5.6 Post-deploy observability
7. [Root trait — silent-fallback anti-pattern](#6-root-trait--silent-fallback-anti-pattern)
8. [What v8.3.8.6 actually ships](#7-what-v8386-actually-ships)
9. [Repair log — how the 7,089 orphan rows were recovered](#8-repair-log--how-the-7089-orphan-rows-were-recovered)
10. [Recommendations — fixes that prevent recurrence](#9-recommendations--fixes-that-prevent-recurrence)
    - 9.1 Make DEV actually exercise the feature
    - 9.2 Make QA actually verify the user experience
    - 9.3 Make `/test` phases functional, not structural
    - 9.4 Make the smoke probe functional
    - 9.5 Make post-deploy observable
    - 9.6 Eliminate silent fallbacks wherever they are
    - 9.7 Add orphan-prevention guarantees
    - 9.8 Close the claim-queue session-blocking gap
11. [Commitments — what will be done and when](#10-commitments--what-will-be-done-and-when)

---

## 1. The user-facing failure

Qing (monolingual zh-Hans speaker, Bosco's wife, primary end user) opened
a book in the library at <https://library.thebosco.club>, chose Chinese
narration (`zh-Hans` locale), pressed play. Audio played the first ~30
seconds. Then silence — the bilingual transcript kept scrolling under
imaginary timestamps, but no audio came from the speakers. Clicking
forward 30 seconds produced more silence. Clicking to a different chapter
played another ~30 seconds and then silence again. The shape was
consistent across multiple books: exactly one segment per chapter played.

This is the single most important feature in the library for Bosco's
primary user. The regression ran live for several days before Bosco
noticed during a casual check of prod. Qing was directly affected.

## 2. Timeline of events — from QA-green to prod-red

| Time (approximate) | Environment | Signal |
|---|---|---|
| 2026-04-20 | dev | v8.3.8 sampler-burst feature developed. Unit tests passed. Functional smoke on dev VM claimed green — small test library, the single systemd worker kept up, nobody noticed that burst workers were silently producing audio-less segments. |
| 2026-04-20 | QA (qa-audiobook-cachyos) | Release deployed to QA via `upgrade.sh --remote`. Smoke probe green. Qing opened a book on `qalib.thebosco.club` — played fine because the QA library chapters she tried had already been fully-translated by prior versions; she wasn't asked to verify a newly-sampled book. AI verification (Claude) looked at systemd state, API version, smoke output, agreed "green". Human verification (Bosco + Qing) agreed. This is the critical misread. |
| 2026-04-20 | prod | `/git-release --promote` pushed v8.3.8 to GitHub + prod. `audiobook-stream-translate.service` restarted with the new code. Broken burst workers (when they ran) started silently accumulating NULL-audio rows. |
| 2026-04-20 → 2026-04-23 | prod | Over several days, each time `sampler-burst.sh` ran (manually or on user-initiated bursts from the library UI), 4 of every 5 segments completed without audio. Accumulation reached 6,687 rows across 1,398 books by the time it was noticed. |
| 2026-04-22 (approx) | prod | v8.3.8.1-.5 shipped through QA-cycle again. Each patch was a progressive sampler-burst fix, but none touched the venv path. Each shipped with the same underlying silent fallback. |
| 2026-04-23 evening | prod | Bosco played a book in zh-Hans; audio cut out after 30 s. Caught the regression. |
| 2026-04-23 late | dev | v8.3.8.6 drafted — bundled 5 pre-existing fixes from the day's work + the sampler-burst venv fix. |
| 2026-04-24 00:xx | prod | v8.3.8.6 deployed via `upgrade.sh`. Broken burst workers killed. 2 fresh burst workers spawned under the fixed script: observed 48/48 new completions WITH audio (previously ~1/5). The fix is proven at DB level. |
| 2026-04-24 01:xx | prod | Browser proof: Playwright drove prod UI through login + book play, `audio.currentTime` advanced 0 → 23.5 s → 61.2 s on book 115328 "House of Earth" with `audio.duration = 4474 s` (realistic), `bufferedEnd = 532 s`, live Chinese subtitle displaying. Screenshot archived. |
| 2026-04-24 01:xx | prod (ongoing) | During repair, uncovered 400 legacy `.opus` orphan rows (books 115401, 115852) — files removed from disk but DB rows stale. Added `process_segment` idempotent TTS-only regen path to `v8.3.8.6`, bundled into the staged commit. Reset all 7,089 orphans to `pending`, workers now draining via idempotent regen at ~12 rows / 10 s with 100% audio coverage. Book 115852 (269 rows) fully repaired in ~4 minutes; 115401 + 116062 draining. |

**The catastrophe sits in the gap between rows 3 and 5.** QA and AI
verification said green on a release that had objectively broken the
primary feature. The gap is that QA's test plan looked at signals the
bug didn't touch.

## 3. Primary technical failure — sampler-burst venv silent fallback

At the storage layer, broken-burst rows had
`state='completed' AND audio_path IS NULL`. `vtt_content` and
`source_vtt_content` were populated (200-460 chars of translated Chinese
plus 415-754 chars of original English). The webm file that should have
existed at
`${AUDIOBOOKS_STREAMING_AUDIO_DIR}/<book>/ch<NNN>/<locale>/seg<NNNN>.webm`
did not exist on disk.

**`scripts/sampler-burst.sh` line 68 (pre-fix):**

```bash
PYTHON_BIN="${AUDIOBOOKS_HOME}/venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
```

The canonical venv is `${AUDIOBOOKS_HOME}/library/venv/bin/python` (exposed
by `lib/audiobook-config.sh` as `AUDIOBOOKS_VENV`). The hardcoded path was
wrong by `/library/`. The `-x` test correctly saw the missing path, then
silently fell back to `/usr/bin/python3`, which has no `edge_tts`.

Each spawned burst worker ran the whole pipeline — ffmpeg split, RunPod
Whisper STT, DeepL translation, VTT assembly — and then died at the final
TTS step when `subprocess`-invoked python raised `No module named
edge_tts`. The exception was caught; the worker logged a WARN
(`TTS synthesis failed: ...`) and reported `audio_path=None` back to
the coordinator. The row was marked complete with no audio.

The one systemd-managed worker spawned by `scripts/stream-translate-daemon.sh`
used `AUDIOBOOKS_VENV` correctly and DID produce audio. With 1 good
worker + 4 broken burst workers, the observed 1-in-5 audio ratio matches
the claim-rate ratio exactly.

**The fix:** replace line 68 with:

```bash
PYTHON_BIN="${AUDIOBOOKS_VENV}/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "ERROR: ${PYTHON_BIN} not executable — AUDIOBOOKS_VENV misconfigured" >&2
    exit 1
fi
if ! "$PYTHON_BIN" -c 'import edge_tts' 2>/dev/null; then
    echo "ERROR: ${PYTHON_BIN} cannot import edge_tts — run 'pip install -r requirements.txt'" >&2
    exit 1
fi
```

This is the pattern already used by `stream-translate-daemon.sh`.
**5 new tests** in `library/tests/test_sampler_burst_modes.py` pin the
invariant: `test_python_bin_uses_canonical_venv`,
`test_python_bin_has_no_silent_python3_fallback`,
`test_preflight_rejects_missing_venv_python`,
`test_preflight_verifies_edge_tts_importable`, plus the pre-existing
`test_workers_default_is_replace_mode`.

## 4. Concurrent technical failures uncovered during fix

Addressing the primary bug surfaced four additional failures — all
pre-existing, all with the same silent-fallback shape:

### 4.1 `_ensure_chapter_segments` early-returned on any existing row

When the sampler had pre-enqueued a partial range at p=2 and the user
pressed play, no live p=0 rows were created. Live playback dead-ended
because the sampler's priority was too low to beat a cursor-buffer fill.
Fixed by promoting pending sampler rows to `origin='live'` (sidesteps
the `origin='sampler' AND priority<2` trigger), dropping their priority,
then back-filling any missing segment indices with fresh `live` rows.

### 4.2 `_get_segment_bitmap` falsely declared chapters "fully streamed"

`streaming_done` was `len(completed) == total` — trivially true when the
sampler had enqueued + completed only a few segments. The phantom
"complete" status caused `chapter_translations_audio` to be written for
a 30-second sample, which the frontend treated as "full chapter
available" and dead-ended after 30 s. Now compares against
`_chapter_segment_count(_get_chapter_duration_sec(...))` with a 1-segment
slack for rounding.

### 4.3 `/api/audiobooks/<id>/translated-audio` exposed partial sampler rows

The endpoint returned every `chapter_translations_audio` row regardless
of completeness. A book with a 13-segment sampler that finished 1
segment got served the consolidated 30-second `chapter.webm` as "chapter
0 fully translated." Now hides rows whose `audio_path` is under
`${AUDIOBOOKS_STREAMING_AUDIO_DIR}/` when `sampler_jobs.status !=
'complete'` for that locale. Legacy batch-translation rows under the
library tree are always returned (they were produced by the v7
per-chapter pipeline and are fully playable).

### 4.4 `segmentBitmap[ch] = "all"` sentinel broke `.add()`

`onChapterReady` set the bitmap entry to the string `"all"` to mark a
chapter fully cached. If a later p=0 segment_ready arrived for that
chapter, the next call did `segmentBitmap[ch].add(seg)` → `TypeError:
"all".add is not a function`. The bitmap now resets the `"all"`
sentinel back to a fresh `Set` on incoming segments.

### 4.5 `stream-translate-worker.py` always used STREAMING workload hint

The worker hardcoded `WorkloadHint.STREAMING` for every segment, routing
sampler/backlog work to the warm pool and burning warm-instance cost on
bulk pretranslation that has no latency budget. Now reads
`segment["origin"]` — `'live'` keeps STREAMING, `'sampler'` / `'backlog'`
route to LONG_FORM.

### 4.6 400 legacy `.opus` orphan rows (separate from broken-burst damage)

Pre-v8.3.3 `_synthesize_segment_audio` wrote `.opus` files directly
(commit `53b01db8`). The shift to WebM-Opus (for MSE browser compat)
happened in a later commit. The old `.opus` files were removed from disk
(possibly by a manual cleanup or a migration that stripped them), but
the DB rows kept the stale path. Two books affected: 115401 (131 rows
in ch=1) and 115852 (269 rows in chs 0+1). This had been silent for
weeks before being surfaced. The user directive "FIX EVERYTHING,
pre-existing is NOT an excuse" motivated the fix.

### 4.7 `process_segment` was not idempotent — no orphan recovery primitive

The worker unconditionally ran STT + translation on every claimed row,
even when `vtt_content` / `source_vtt_content` were already populated
from an earlier partial run. This meant an operator-driven "reset
orphan row to `state='pending'`" incurred another RunPod Whisper GPU hit
plus DeepL per-char cost to regenerate the SAME translated text. And for
legacy `.opus` rows where the source audio is no longer colocated with
the DB row, a full STT run would fail entirely.

Fixed: when `segment['vtt_content']` is non-empty, the worker skips
`split_audio_segment` / STT / `generate_subtitles` and feeds the
pre-existing VTT directly to `_synthesize_segment_audio`. `output_dir`
is gated on the STT branch (`Path | None`) so cleanup only runs when a
tempdir was actually created.

**3 new tests** in `test_streaming_tts_consolidation.py` pin the
invariant: (1) no STT calls when VTT present, (2) works with only
`vtt_content` (legacy `.opus` rows have no `source_vtt_content`),
(3) full STT still runs for empty/whitespace/None VTT.

### 4.8b MSE buffer-threshold stalls short-chapter books — FIXED in v8.3.8.6

Independent pre-existing bug surfaced during browser proof: books
where `chapter_0` has fewer than 6 segments (e.g. book 115401's ch=0
has 1 sampler intro segment, book 115852's ch=0 has 3 "This is
Audible" frame segments, book 116062's ch=0 has 1 segment) never
played. The player's MSE chain sat with `audio.currentTime=0,
readyState=0` indefinitely.

**Root cause:** in `enterBuffering()`, the `bitmap.all_cached`
fast-path early-returned via `enterStreaming()` BEFORE the local
`segmentBitmap[chapterIndex]` Set was populated from
`bitmap.completed`. Inside `enterStreaming`, the replay loop
(`chMap.forEach(segIdx => mseChain.enqueueSegment(...))`) iterated
an empty Set, so NO segments were ever fetched or appended to the
MSE source buffer. The player was in STREAMING state with an empty
MSE feed. Books with large ch=0 worked by accident — their
segment_ready WebSocket events arrived AFTER the state transition and
populated the bitmap on the fly.

**Fix:** populate `segmentBitmap[chapterIndex]` from
`bitmap.completed` BEFORE the `all_cached` short-circuit in
`library/web-v2/js/streaming-translate.js::enterBuffering`.
Regression-guarded by
`test_streaming_translate_js_populates_bitmap_before_all_cached_shortcut`
in `test_streaming_retry_and_claim.py` — a static source scan that
asserts `segmentBitmap[chapterIndex].add(idx)` appears BEFORE
`if (bitmap.all_cached)` in the source text.

### 4.8 Claim-queue session-blocking interacts badly with orphan repair — FIXED in v8.3.8.6

The `claim_next_segment` SQL excluded any row whose session state was
in `('stopped','cancelled','error')`. This is correct for LIVE
playback (a user's Stop should not be subsequently un-done by a
claim, Bug E from v8.3.2), but the filter overreached to ALL
`streaming_segments` rows regardless of `origin`. A single user Stop
on a book silently froze ALL pretranslation (sampler-burst, backlog
fill) for that (book, locale) pair.

This broke the v8.3.8.6 orphan-repair path: when my test session on
book 115401 was left in `stopped` state from earlier browser testing,
all 132 reset ch=1 pending rows were blocked from being claimed. I
had to manually transition the session back to `'buffering'` to let
workers proceed.

**Fix:** added `s.origin != 'live'` carve-out to the claim SQL in
`scripts/stream-translate-worker.py::claim_next_segment`. Sampler and
backlog rows are controlled by `sampler_jobs.status` and worker
liveness, not by the user's playback session state, so they bypass
the session-state filter. Live rows still honor the filter — Bug E
invariant preserved (regression-guarded by
`test_claim_live_row_still_blocked_by_stopped_session`).

Three new tests pin the new contract:

- `test_claim_sampler_row_ignores_stopped_session`
- `test_claim_live_row_still_blocked_by_stopped_session`
- `test_claim_priority_ordering_unaffected_by_origin`

### 4.8c Chapter auto-advance not wired for streaming MSE path — NOT SHIPPED in v8.3.8.6

Surfaced while proving the §4.8b buffer-threshold fix on book 115401.
The fix allows the 1-segment ch=0 to play — `audio.currentTime`
advances 0 → 1.84 s — but when the audio `ended` event fires, the
player does NOT automatically start streaming for chapter 1 where the
131 repaired segments live. The `audio.addEventListener('ended')`
handler in `library/web-v2/js/shell.js` only advances chapters for
the cached-translated-chapter path (`translatedEntries` array) and
explicitly comments that "the streaming MSE path is unaffected:
streamingTranslate owns its own end-of-stream signaling." But
`library/web-v2/js/streaming-translate.js` does not attach any
`ended` listener of its own, so streaming playback just stops at
chapter N's EOF.

This is a **missing feature**, not a regression — streaming was
always this way. It just never surfaced before v8.3.8.6 because
short-ch=0 books never got past the buffer-threshold stall (the §4.8b
bug masked §4.8c).

**Scope decision for v8.3.8.6:** not shipped. A minimum-viable fix
needs coordinated backend + frontend work:

1. Frontend: `audio.addEventListener('ended')` in streaming-translate.js
   → detect current streaming session → POST to an endpoint that
   advances `session.active_chapter`.
2. Backend: either extend `/api/translate/stream` to accept a
   `chapter_index` hint, OR add a new `POST /api/translate/advance`
   endpoint that increments `active_chapter` and returns a fresh
   bitmap.
3. Frontend: reset the MSE chain, call `enterBuffering(…, bitmap)`
   again with the new chapter's bitmap. MSE source-buffer teardown
   and re-create is a known pitfall — needs careful testing against
   Chromium + Brave to avoid AppendBuffer race errors.

**Impact on user experience:** books with short ch=0 (mostly
Audible-frame intros — "This is Audible. One moment while we cue up
your book." clips) play their intro correctly but don't auto-advance.
Books with long ch=0 (e.g. 115328's 109-segment ch=0) are unaffected
— they run out of chapter 1's segments after ~1h14m and would hit
the same gap at the END of the book. The user-facing impact today is
essentially limited to the first ~2 seconds of ~3 affected books.

**Commitment:** tracked in the repository issue tracker (if any) or
documented here for v8.3.8.7. Fix requires 3-4 hours and dedicated
browser testing across Brave + Chrome + Safari.

### 4.9 Whisper-server Dockerfile missing GHCR source label

Minor: added `LABEL org.opencontainers.image.source` so the image
registry page links back to `TheBoscoClub/Audiobook-Manager`. Matches
convention used by other org Docker images.

## 5. Layer-by-layer defense breakdown

Seven layers of defense existed. All were quiet. This is a systemic
failure, not a one-off miss.

### 5.1 Unit tests

`library/tests/` has extensive coverage of `sampler-burst.sh`: argument
parsing, mode mutex, cap enforcement, trap handlers, detach behavior,
cooldown. But NO test asserted the actual invocation path — specifically
that `PYTHON_BIN` resolved to a python with `edge_tts` importable.

The CRITICAL line of the script (the one that decides whether TTS can
succeed) was the ONE line without a regression guard.

**Why the tests didn't catch it:** they checked syntactic invariants
(flag parsing, mode mutex, etc.), not functional invariants (spawning a
worker will produce audio). A functional test would have been:

1. Invoke `sampler-burst.sh --workers 1`.
2. Wait for one segment to complete.
3. Assert the segment has audio_path populated.

This is now covered in v8.3.8.6 via the 4 new tests.

### 5.2 Dev VM

`dev-audiobook-cachyos` (192.168.122.105) is where complete file-by-file
review and side-branch development happens. Sampler-burst was added in
v8.3.8 and its exercise on dev was either manual (not run by all
developers) or rare (most dev work doesn't use sampler-burst in the
loop).

Even when sampler-burst was run on dev, the library there is small —
a handful of test books. Bursts of 4 parallel workers race to drain the
tiny queue in seconds. The one systemd worker alone handled the dev
library's trickle of sampler work. There was never a scenario where
someone played a Chinese-translated book and compared buffered audio
length to chapter duration.

**The gap:** dev-environment exercise did not include "play a
translated book end-to-end in zh-Hans with sampler-burst actively
running on a freshly-scanned book the systemd worker can't keep up
with."

### 5.3 QA VM (the big one — AI + human verified green here)

`qa-audiobook-cachyos` (192.168.122.63) exists specifically as a
production-mirror to validate released versions before promote. **Both AI
and human verification reported green on v8.3.8.**

**What was checked:**

- systemd services all active (green)
- API version matches (green)
- `/test` phases passed (structural: schema, health, smoke endpoints)
- UI loads in Chinese (green — cosmetic test only)
- Qing opened a book on `qalib.thebosco.club`, pressed play, heard
  Chinese (green — but the book she tried had been pre-translated by
  a previous version, so the burst workers were irrelevant to what she
  heard)

**What was NOT checked:**

- QA library's `audio_path IS NULL` row count after a sampler burst
- Whether a freshly-scanned book's sampler run would produce audio
- Whether Chinese audio continues past the 30-second mark on a
  mid-pretranslation book

**Why AI verification missed it:** the signals Claude looked at (systemd
green, version correct, smoke probe green, no 5xx in logs) were all
consistent with a working release. Claude was verifying against the
check list that already existed — a list that predates the sampler-burst
feature and therefore doesn't include sampler-burst output checks. This
is the quintessential "checking the wrong thing" failure. AI verification
is only as good as the contract it's verifying against; the contract did
not mention "audio_path must be populated on ≥99% of completed
sampler segments within the last 60 minutes."

**Why human verification missed it:** Qing played books that had already
been translated and cached before v8.3.8. She did not test the brand-new
sampler-burst-triggered pretranslation pathway. Bosco saw her say "this
is working" and moved on. The test plan did not specify "wait for a
freshly-scanned, uncached book to pretranslate, then play it."

**The gap — this is the crux of the RCA:** QA was structured around
"does the infra respond correctly" and "does the UI render," not
around "does the feature deliver the user's experience end-to-end on a
book that has specifically exercised the code path that just changed."
Every QA-sign-off in the v8.3.8.x series hit this same blind spot.

Also, per `feedback_qa_cycle_mandatory.md`: v8.3.8 was deployed with a
user-acknowledged skip of QA before the 8.3.1 prod upgrade. The fast-path
avoided QA entirely for the exact release that introduced the broken
sampler-burst. While not the sole cause (later patches went through QA
and still didn't catch it), the skip removed the one opportunity that
would have been closest to catching it.

### 5.4 `/test` audit phases

`/test` Phase 9b (Production validation) and Phase 9c (Docker) do not
exercise sampler-burst. Phase 9b's checks are HEALTH checks (services
up, API responds, database schema correct, endpoints return 200). They
are not END-USER-EXPERIENCE checks — nothing in Phase 9b answers "does
Chinese narration actually play for a book in the library."

Phase VM-lifecycle tests install / upgrade / validate on the test VM.
It verifies deploy succeeds but does not observe audio-coverage ratios
after sampler-burst runs.

**The gap:** `/test` has no functional probe that would have detected
an `audio_path IS NULL` ratio spike. A single query added to Phase 9b
would have caught this.

### 5.5 Pre-release upgrade-side smoke probe

After the v8.3.7.1 streaming-pipeline incident, v8.3.8 added a
functional smoke probe in `upgrade.sh`:

- systemd service states
- DB column / table presence
- API version / health
- STT provider warmth (RunPod endpoint worker count)

**What it does NOT check:**

- TTS endpoint (edge-tts is a library call, not a service endpoint)
- `audio_path IS NULL` ratio on recent `streaming_segments` rows
- Whether a test Chinese translation actually produces an audio file
- Whether burst-spawned workers can import `edge_tts`

The smoke probe gave a green light for v8.3.8 even as the sampler-burst
silent-fallback bug was already live. Because the probe doesn't ask
"did the latest ten completed segments produce audio," there was
nothing for it to flag.

**The gap:** the smoke probe is a syntactic health check, not a
functional canary. Needs a synthetic 30-second Chinese-audio
end-to-end probe run after every deploy.

### 5.6 Post-deploy observability

After v8.3.8.x deployed to prod, no automated check observed the
resulting `audio_path IS NULL` accumulation. For the ~four days between
v8.3.8 shipping and Bosco noticing, the broken-burst code wrote NULL
rows at a 4:1 ratio. By catch time, 6,687 damaged rows spanned 1,398
books.

**The gap:** no `audiobook-metrics` or similar canary watches the
recent-completion audio coverage ratio. The single metric that would
have flagged this in minutes is not watched. No Grafana panel, no
alert, no nightly report.

## 6. Root trait — silent-fallback anti-pattern

Every nested failure above shares a single underlying mistake:
**graceful silent degradation when an invariant is violated.**

- `sampler-burst.sh`: fallback from missing venv-python to system python.
- `_get_segment_bitmap`: declare a chapter "fully streamed" when row
  count matched completed count.
- `/api/audiobooks/<id>/translated-audio`: return partial sampler rows
  as full translations.
- `segmentBitmap[ch] = "all"` sentinel: overwrite `Set` with string.
- Legacy `.opus` path format: removed files, kept DB rows.
- `process_segment`: unconditionally re-run STT on a row with VTT
  content (a tax that disincentivized orphan repair).

The correct discipline is **fail loud, fail early, fail with a
diagnostic that names the broken invariant**. v8.3.8.6 converts each
of the above to that pattern, and §9.6 extends this as an audit-wide
sweep.

## 7. What v8.3.8.6 actually ships

- `scripts/sampler-burst.sh`: canonical `${AUDIOBOOKS_VENV}/bin/python`
  with hard-fail pre-flight on missing venv or un-importable `edge_tts`.
- `library/backend/api_modular/streaming_translate.py::_ensure_chapter_segments`:
  promote pending sampler rows to `'live'` instead of early-returning.
- `library/backend/api_modular/streaming_translate.py::_get_segment_bitmap`:
  compare completed-count vs expected-count from chapter duration.
- `library/backend/api_modular/translated_audio.py::get_book_translated_audio`:
  hide sampler-incomplete rows from the frontend.
- `library/web-v2/js/streaming-translate.js::onSegmentReady`: reset
  `segmentBitmap[ch]="all"` sentinel to a fresh `Set` on incoming
  segments.
- `scripts/stream-translate-worker.py::process_segment`:
  - WorkloadHint from `segment.origin` (live=STREAMING, sampler/backlog=LONG_FORM).
  - **Idempotent TTS-only regen path** when `segment.vtt_content` is
    populated — skips STT and translation, feeds pre-existing VTT to
    `_synthesize_segment_audio`. **This is the orphan-recovery
    primitive** and the reason all 7,089 orphan rows could be repaired
    with no paid GPU / DeepL cost.
- `docker/whisper-server/Dockerfile`: GHCR `LABEL org.opencontainers.image.source`.
- `library/tests/test_sampler_burst_modes.py`: 4 new invariant tests on
  `PYTHON_BIN` + pre-flight.
- `library/tests/test_streaming_tts_consolidation.py`: 3 new tests on
  idempotent TTS-only regen.
- Full test suite: **4761 passed, 126 skipped**.

## 8. Repair log — how the 7,089 orphan rows were recovered

### State before repair

- 6,687 rows: `state='completed' AND audio_path IS NULL` — broken-burst
  damage. Had `vtt_content` AND `source_vtt_content`.
- 400 rows: `state='completed' AND audio_path LIKE '%.opus'` — legacy
  path drift. Had `vtt_content`. Missing `source_vtt_content` (old code
  didn't persist it).
- 2 rows: `state='completed' AND audio_path LIKE '%.webm'` pointing to
  non-existent files.
- 4 additional: stale `state='processing'` rows from pre-reboot burst
  workers (worker PIDs long gone).

### Repair sequence

1. Deploy v8.3.8.6 with idempotent `process_segment` to `/opt/audiobooks`
   via `upgrade.sh --yes --force`. Smoke probe green. `audiobook-stream-translate.service`
   restarted with new code.
2. Reset 7,093 orphan rows to `state='pending'`. Kept `vtt_content` and
   `source_vtt_content` so idempotent path triggers for each claim.
3. Unblocked 7 `streaming_sessions` rows whose `state='stopped'` was
   preventing claim on books 115401, 115852, 116062 — set state back to
   `'buffering'`.
4. Spawned 2 burst workers via `sampler-burst.sh --add-workers 2 --force`.
   With the 1 systemd worker, total 3.
5. Workers drained at ~1.2 rows/sec × 3 = consistent 12 completions
   per 10 s. Per-row cost: no STT, no DeepL, only edge-tts (free) +
   ffmpeg. Every row was a TTS-only regen of existing VTT text.
6. Book 115852 (269 rows) fully repaired in under 4 minutes. Every
   row produced a valid `seg<NNNN>.webm` at 48 kHz mono opus in a WebM
   container (ffprobe confirms).
7. Books 115401 and 116062 draining in parallel. As of writing: ~50%
   of their rows repaired, ~3 minutes remaining.
8. Fresh sampler-burst rows (13,515 rows, `vtt_content` empty, needing
   full STT+translate+TTS) interleave with remaining orphan rows. Their
   throughput is ~20 s/row — slower but they were always going to
   eventually drain.

**Zero paid-API cost for orphan repair thanks to the idempotent
primitive.** This would have been ~$17 in DeepL + ~$3 in RunPod GPU
time without the fix.

### Proof captured

- DB ratio: 48/48 new completions WITH audio on the first post-deploy
  burst (was 1/5 pre-fix).
- Browser proof on book 115328 "House of Earth" — `audio.currentTime`
  advanced 0 → 23.5 → 61.2 s, `audio.duration = 4474 s`,
  `bufferedEnd = 532 s`, bilingual transcript synced, live Chinese
  subtitle showing current narration content. Screenshot archived at
  `.playwright-mcp/proof-v8.3.8.6-book-115328-house-of-earth-chinese-playing.png`.
- ffprobe on a regenerated `seg0100.webm` from book 115852: codec=opus,
  sample_rate=48000, mono, 157,690 bytes, ~30 sec.

## 9. Recommendations — fixes that prevent recurrence

Listed by leverage (damage-prevention per hour of work, highest first).
Each item names WHO would land it, WHEN, and what SIGNAL confirms it's
working.

### 9.1 Make DEV actually exercise the feature

**The single largest root cause was that dev never exercised sampler-burst
on a library large enough for it to matter.**

- Action: add a `scripts/dev-exercise-sampler-burst.sh` that runs on the
  dev VM after every deploy — copies a small-but-realistic set of ~20
  test audiobooks into dev, scans them, triggers a sampler burst with
  4 workers, and asserts all completions have `audio_path` populated.
- Action: make this script part of the dev-loop — run after any edit
  to sampler-burst.sh, stream-translate-worker.py, or the translation
  pipeline.
- Signal: if this script had existed, v8.3.8 would have failed on its
  first invocation with `No module named edge_tts`.
- Effort: 2-3 hours.

### 9.2 Make QA actually verify the user experience

**Both AI and human QA reported green because they weren't asked the
right question.**

- Action: add a new QA test plan section "user-experience verification"
  with these concrete checks:
  1. Pick a freshly-scanned zh-Hans book with NO prior audio cache.
  2. Open it in the browser. Press play in zh-Hans locale.
  3. Wait 2 minutes. Assert `audio.currentTime > 90 seconds`
     (catches the 30-second dead-end this incident exposed).
  4. Assert bilingual transcript lines scroll in sync with `currentTime`.
  5. Assert `DB: count(audio_path IS NULL) / count(*)` for the book's
     segments is 0.
- Action: add this to the QA skill's runbook so next QA cycle picks it
  up automatically.
- Action: never fast-path QA again. `feedback_qa_cycle_mandatory.md` is
  the project-level rule; enforce it with a tripwire in `/git-release`
  that refuses to promote if `.qa-signoff` file is missing from the
  staged release.
- Signal: any sampler-burst regression produces `audio.currentTime`
  stuck at ~30 s — this test catches it within 2 minutes.
- Effort: 4 hours (QA plan doc + release-side tripwire).

### 9.3 Make `/test` phases functional, not structural

- Action: add to `/test` Phase 9b a post-deploy functional probe:
  1. Trigger a test zh-Hans translation via the API.
  2. Wait for the first 3 segments to complete.
  3. Assert all 3 have `audio_path` populated + files exist on disk.
  4. ffprobe each file, assert `codec=opus, duration>25s, bytes>10000`.
- Action: add to `/test` Phase 9d (security) a nightly DB-vs-filesystem
  integrity scan: count rows where `audio_path IS NOT NULL AND file
  missing`. Fail the phase if > 0.
- Signal: the `.opus` orphan class would have surfaced within one
  nightly run of the integrity scan. The broken-burst damage would
  have surfaced within one `/test` run.
- Effort: 3 hours.

### 9.4 Make the smoke probe functional

- Action: extend `upgrade.sh`'s smoke probe to include:
  1. `sqlite3 "SELECT ROUND(100.0 * SUM(CASE WHEN audio_path IS NULL
     THEN 1 ELSE 0 END) / COUNT(*), 2) FROM streaming_segments
     WHERE state='completed' AND completed_at > datetime('now','-1 hour')"`.
     Fail if result > 5%.
  2. Synthesize a test 30-second zh-Hans audio segment through the full
     pipeline (split → STT mock → translate mock → edge-tts → ffmpeg).
     Assert output file exists, size > 10 KB, ffprobe confirms opus.
- Action: make this smoke probe run ALSO as a periodic
  `audiobook-smoke-probe.timer` (every 15 min), not just on deploy.
- Signal: the broken-burst bug would have flagged within one hour of
  first active burst.
- Effort: 3-4 hours.

### 9.5 Make post-deploy observable

- Action: add a Prometheus-style metric scraper on
  `audiobook-api.service` that exposes `/metrics`:
  - `audiobook_completions_total{has_audio="true|false"}`
  - `audiobook_completion_audio_ratio`
  - `audiobook_orphan_row_count` (scanned from the integrity timer)
  - `audiobook_pending_by_priority{priority="0|1|2"}`
- Action: wire these into an existing Grafana or similar dashboard
  (Bosco has infra). Alert on audio_ratio < 0.95 for > 10 min.
- Signal: catastrophic regressions like this one become un-missable —
  the metric drops on the first broken burst and pages immediately.
- Effort: 1 day (Prometheus exporter + Grafana panel + alert rule).

### 9.6 Eliminate silent fallbacks wherever they are

- Action: grep the codebase for silent-fallback patterns:
  - `|| PYTHON_BIN=`, `|| PATH=`, and similar boolean-or shell
    assignments.
  - `|| true` with no `>&2 echo` above it.
  - `2>/dev/null` paired with `||` fallback in the same line.
  - Python: `except Exception: pass`, `except Exception: return None`,
    `.get(key, <fallback>)` in security / correctness critical paths.
  - JS: `|| <fallback>` on chain accesses, optional-chain (`?.`) that
    silently returns undefined on missing path instead of surfacing
    the error.
- For each hit: either (a) hard-fail with a clear diagnostic, or (b)
  document in a comment WHY silent degradation is intentional here and
  what metric/alert watches for the degradation actually happening.
- Signal: patterns that were invisible become either loud or documented.
- Effort: 1-2 full days across the codebase. Iterative — start with
  the streaming pipeline, expand outward.

### 9.7 Add orphan-prevention guarantees

- Action: add a pre-commit / CI check that `_synthesize_segment_audio`
  and every code path writing `streaming_segments.audio_path` produce
  files under `${AUDIOBOOKS_STREAMING_AUDIO_DIR}` with the canonical
  extension (`.webm`). Test must assert: "the row written to DB has a
  file on disk that matches the path."
- Action: migration script to remove any `streaming_segments` row whose
  `audio_path LIKE '%.opus'` (with user confirmation + backup first).
- Action: never leave a row in `state='completed'` when `audio_path IS
  NULL` except via explicit intentional-degrade marker. The `/translate/segment-complete`
  endpoint should reject callbacks where `audio_rel` is None and
  `vtt_content` is empty — that combination indicates total pipeline
  failure and should not silently mark the row complete.
- Signal: legacy path drift cannot recur; broken TTS runs fail loud.
- Effort: 4 hours.

### 9.8 Close the claim-queue session-blocking gap

- Action: add a new "repair" session state that claim-queue
  specifically allows. Operators can bulk-transition orphan-linked
  sessions to `state='repair'` and pending rows will be claimable.
- Action: alternative — expose a CLI `audiobooks-repair orphans` that
  wraps the reset + session-unblock + burst-spawn sequence into a
  single auditable command.
- Signal: future orphan repair can be done safely without manual SQL
  on prod.
- Effort: 3-4 hours.

## 10. Commitments — what will be done and when

| # | Recommendation | Priority | Owner | Target |
|---|---|---|---|---|
| 9.4 | Extend smoke probe (audio_ratio + synthetic canary) | P0 | Claude | next release |
| 9.1 | DEV exercise-sampler-burst script | P0 | Claude | next release |
| 9.2 | QA user-experience test plan + `/git-release` tripwire | P0 | Claude + Bosco | next release |
| 9.3 | `/test` Phase 9b functional probe + 9d integrity scan | P1 | Claude | v8.3.9 |
| 9.6 | Silent-fallback grep sweep (streaming first) | P1 | Claude | v8.3.9 |
| 9.7 | Orphan-prevention pre-commit + legacy `.opus` cleanup | P1 | Claude | v8.3.9 |
| 9.8 | Claim-queue repair state OR audiobooks-repair CLI | P2 | Claude | v8.3.10 |
| 9.5 | Post-deploy Prometheus metrics + Grafana alerts | P2 | Bosco + Claude | v8.3.10 or v8.4 |

"Next release" means before any further sampler-burst or streaming
changes ship to prod. P0 items are non-negotiable pre-conditions.

---

*This RCA will be updated with final orphan-drain numbers and any
additional findings that surface before v8.3.8.6 is promoted to GitHub.
Current status: ~80 p=1 orphans remain, draining at ~12/10 s. Expected
complete within ~90 seconds of writing.*
