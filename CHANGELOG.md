# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [8.3.8.14] - 2026-04-27

### Fixed

- **UFW preset diverged between Dev and QA, silently breaking `devdocker.thebosco.club`**: 2026-04-26 cloudflared on the local host was returning 404 for `devdocker.thebosco.club` even though the tunnel ingress was correctly configured (after API update) and the Dev VM's Caddy was listening on 8085. Root cause: Dev's UFW had ports 22, 5001, 8443, 8090, 8080, 8084 open but **not 8085**, while QA's UFW had 8085 from a manual fix. Cloudflared's outbound TCP from the host to `192.168.122.105:8085` was silently dropped by Dev's firewall. `install.sh` and `upgrade.sh` now reconcile UFW rules for the canonical port set (`5001/tcp 8090/tcp 8080/tcp 8443/tcp 8084/tcp 8085/tcp`) on every install AND every upgrade — `ufw allow` is idempotent, and the `upgrade.sh` block uses `ufw status | awk` to skip already-allowed ports so existing installs catch up cleanly without log spam. Gated on `command -v ufw` and `ufw status | grep -q active` so non-UFW hosts (or UFW-inactive hosts) are skipped silently. Block lives right after the Caddy reverse-proxy install in `install.sh:1853` and right after the Caddy reload in `upgrade.sh` (in the systemd-reload neighborhood)
- **In-app upgrade UI showed "Restoring services after upgrade failure..." despite the upgrade actually succeeding** — `upgrade.sh`'s post-upgrade smoke probe (line 2928 release-tarball branch, line 3278 from-project branch) ran unconditionally as long as `DRY_RUN != true`, but `scripts/upgrade-helper-process` invokes `upgrade.sh --skip-service-lifecycle --yes` and the helper's own service-stop happens BEFORE upgrade.sh runs and service-start happens AFTER upgrade.sh exits. The smoke probe therefore ran while services were still stopped, found `audiobook-redirect.service: inactive (ExecMainStatus=15)`, `audiobook-mover.service: inactive (ExecMainStatus=15)`, `audiobook-api: not running`, and `API health endpoint unreachable at http://127.0.0.1:5001/api/system/health` — declared "4 FAILURE(S), 2 warning(s)", and exited with code 1. The helper saw exit-1, branched into its failure-recovery path at `scripts/upgrade-helper-process:516`, wrote the status `"Restoring services after upgrade failure..."`, then proceeded to start services (Step 7) and the actual deployment was already correct (VERSION = 8.3.8.13, all services started cleanly). The user saw a misleading "failure" UI on a successful upgrade. Reproduced on prod 2026-04-26 during the v8.3.8.13 in-app upgrade. Fix: gate both smoke-probe blocks in `upgrade.sh` on `SKIP_SERVICE_LIFECYCLE != "true"` in addition to the existing `DRY_RUN != "true"` gate. The helper does its own post-start verification (Step 8: poll `/api/system/health` for up to 30s); the redundant pre-service-start probe in upgrade.sh was always wrong for the in-app flow and the gate was the missing piece. CLI `./upgrade.sh` invocations (without `--skip-service-lifecycle`) continue to run the smoke probe as before — that path manages services itself, so the probe runs after `start_services`. Comment blocks on both upgrade.sh smoke-probe sites updated to document the helper-driven service lifecycle and why the gate exists

## [8.3.8.13] - 2026-04-26

### Fixed

- **iOS Chrome portrait player-clipping survived v8.3.8.8/v8.3.8.9 because `theme-art-deco.css` was forcing body to `min-height: 100vh`** (`Audiobook-Manager-g9f`): Qing's iPhone 17 Pro Chrome iOS still showed the bottom row of player controls (`1x`/`倍速`/`CC`/transcript/lang/`×`) clipped behind Chrome's persistent bottom action bar even after v8.3.8.9 capped `<html>` at `min(100svh, var(--app-height, 100svh))`. Live diagnostic on Qing's actual device — added via `?debug=viewport` instrumentation in `library/web-v2/js/shell.js::setupViewportDebugOverlay` (gated behind the query param, invisible to all other users) — produced ground truth: `100svh = 676`, `100lvh = 788`, `documentElement.cH = 676` (correct cap), but `body.getBoundingClientRect().height = 788` (broken). Chrome iOS reports `100svh` and `visualViewport.height` correctly; the cap on `<html>` was working. The body was 112 px taller than `<html>` because `theme-art-deco.css:186` declared `body { min-height: 100vh }` — and `theme-art-deco.css` loads after `shell.css`, so its `min-height: 100vh` (= 788 px on iOS Chrome portrait) won the cascade and forced the body box to the layout viewport. Per CSS spec `min-height` overrides `max-height` when they conflict, so v8.3.8.9's body cap was a no-op there. Same root cause produced the landscape-collections-won't-scroll bug: in landscape the iframe's bottom-of-body region was sized to the layout viewport too, leaving content unreachable. Fix: `library/web-v2/css/shell.css` body rule changed to `html body` selector (specificity 0,0,2) with explicit `min-height: 0` to override the theme's 0,0,1 rule; `height` and `max-height` both clamped to `min(100svh, var(--app-height, 100svh))` as belt-and-suspenders. Belt-and-suspenders defense added on the mobile player too: `max-height: min(50svh, calc(var(--app-height, 100svh) * 0.5))` plus `overflow-y: auto; -webkit-overflow-scrolling: touch` so even if a future browser update breaks the body cap again, the player can never extend past half the visible viewport. Verified end-to-end on Qing's actual iPhone 17 Pro Chrome iOS portrait (body rect = 676, content-frame = 628 top=47 bot=676, all four player rows visible above Chrome's action bar) AND landscape (body rect = 338, collections scroll restored). The viewport diagnostic overlay (`setupViewportDebugOverlay`) ships permanently as a maintenance tool because Apple does not allow remote DevTools inspection of Chrome iOS — when the next iOS-Chrome viewport regression appears, hitting `?debug=viewport` on any environment immediately surfaces `100vh`/`100svh`/`100dvh`/`100lvh`, `visualViewport.*`, `--app-height`, `safe-area-inset-*`, and rendered rects of `<body>`/`#shell-header`/`#content-frame`/`#shell-player`. Documentation: new "Viewport Diagnostic Overlay" section added to `docs/CSS-CUSTOMIZATION.md`. Audit-driven side fixes that landed in the same release window (separate commits): 4 `_fake_run` mock signatures in `library/tests/test_tts_factory.py` updated to accept `**kwargs` after v8.3.8.10 added `encoding="utf-8", errors="replace"` to `subprocess.run()` in `edge_tts_provider.py` (CI red since the v8.3.8.10 push); `ffmpeg` added to `.github/workflows/ci.yml` apt install (was missing, causing `test_streaming_tts_consolidation.py` to fail in CI environments only); 5 `scripts/*.py` (`batch-translate`, `email-report`, `verify-translations`, `embed-cover-art`, `sampler-reconcile`) wired into `scripts/install-manifest.sh` with documented exception comments per the project's "new-script wiring enforcement" rule; `scripts/stream-translate-worker.py` added to `upgrade.sh` (was in install.sh but missing from upgrade.sh)

## [8.3.8.12] - 2026-04-25

### Added

- **Version-update banner — `library/web-v2/js/version-poller.js`**: addresses the gap that bit Qing's iPhone Chrome on 2026-04-25, where her tab stayed open with cached HTML referencing the old `?v=` cachebust stamps and continued running the broken pre-v8.3.8.9 CSS for hours after the prod hot-patch was live. The shell now polls `/api/system/version` every 60 s (only while the document is visible — `Page Visibility API` defers polling on backgrounded tabs to spare battery and API load), and when it detects a version different from the one captured at page load, renders an Art Deco banner pinned just above the player. The banner has two buttons: a `Reload` action and a close (`×`) — **both** trigger `location.reload()`. There is intentionally no "stay on the old version" escape hatch: once a deploy is detected, any acknowledgement (action button or close) refreshes (per explicit user decision 2026-04-25). The reload uses a plain `location.reload()` — NO cookie wipe, NO storage wipe, NO `Clear-Site-Data` header. The HTML's `Cache-Control: no-cache` and the `?v=` cachebust rotation handle invalidation surgically; auth cookies and accessibility preferences in localStorage are intentionally preserved. i18n via existing `t()` infrastructure: `update.available` / `update.reload` / `update.dismiss` keys added to `library/locales/en.json` and `library/locales/zh-Hans.json`; banner re-renders on `localeChanged` event so a mid-session locale switch updates the banner text live (Qing's app uses zh-Hans, this MUST work). Pre-existing cachebust automation (`scripts/bump-cachebust.sh` rewrites `?v=` stamps on every deploy) handles the actual cache invalidation; this banner closes the loop for already-open tabs. Regression-guarded by `test_version_poller_wired_into_shell` and `test_update_i18n_keys_present_in_both_locales` in `library/tests/test_shell_css.py`

## [8.3.8.11] - 2026-04-25

### Fixed

- **Whisper non-speech markers (`¶`, `♪`, lone punctuation) crashed edge-tts and bypassed the silent-segment fallback**: 3 sampler segments held in `state='failed'` on prod after v8.3.8.10 because their VTT bodies were `¶¶ ¶¶` or just `.` — Whisper's convention for instrumental music or sound effects that produce no real speech. `_vtt_to_plain` returned the markers as plain text (5 chars in the worst case), `_synthesize_segment_audio`'s `if not text.strip()` check happily passed them along to edge-tts, which crashed because the input wasn't synthesizable Mandarin. Fix: in `_synthesize_segment_audio`, after `_vtt_to_plain`, strip whitespace + Unicode punctuation (`unicodedata.category().startswith("P")`) + the explicit non-speech markers `¶♪♫♩♬`. If nothing remains, return `None` and fall through to the silent-segment fallback added in v8.3.8.10. Catches the legitimate "Whisper transcribed music" case at the same boundary as the legitimate "empty VTT" case

## [8.3.8.10] - 2026-04-25

### Fixed

- **`subprocess.run(..., text=True)` crashed on non-UTF-8 stderr from ffmpeg/ffprobe/edge-tts**: 91 sampler segments accumulated in `state='failed'` with `UnicodeDecodeError: 'utf-8' codec can't decode bytes ... invalid continuation byte`. Root cause: source-file metadata (chapter titles, ID3 tags) authored in legacy single-byte encodings was being echoed by ffmpeg/ffprobe to stderr; Python's strict UTF-8 decode (the default for `text=True`) raised before the worker could even inspect the exit code — the segment opus had been written successfully, but the decode crash caused the bounded retry handler to flip the row to `state='failed'` after 3 attempts. Fix: replace `text=True` with `encoding="utf-8", errors="replace"` at all `subprocess.run` sites that capture output: `scripts/stream-translate-worker.py::split_audio_segment` (line 310), `_get_chapter_audio_and_timing`'s ffprobe fallback (line 605), `library/localization/chapters.py::_chapters_from_ffprobe` and `extract_chapter`, and `library/localization/tts/edge_tts_provider.py::synthesize`. Decode replacement keeps stderr legible for the error message without crashing on bytes that don't matter
- **Music / silence segments stalled MSE playback by leaving holes in the audio chain**: 47 segments (8 with VTT cues but no spoken text + 39 with `ValueError: No speech detected` from Whisper) accumulated on prod with either `state='completed', audio_path=NULL` or `state='failed'`. The frontend's MSE chain has no path that handles a "completed" segment with missing audio — it stalls indefinitely waiting for bytes that will never arrive. Fix: when `_synthesize_segment_audio` returns `None` (its documented contract for empty translated VTT), generate a silent WebM-Opus matching the source segment's duration via `_synthesize_silent_segment_audio` (new helper). Container/codec/sample-rate match the spoken-segment helper exactly so Task 10 chapter consolidation can `ffmpeg -c copy` across mixed spoken/silent segments without re-encoding. Subtitle cues remain accurate (their timestamps are unaffected). Also: `process_segment` now catches `ValueError("No speech detected ...")` from `generate_subtitles` and falls through with empty VTT, letting the silent-segment fallback complete the segment with `audio_path` set instead of bouncing through the retry handler. Regression-guarded by `test_synthesize_silent_segment_audio_creates_valid_webm` and `test_process_segment_no_speech_falls_back_to_silent_segment` in `library/tests/test_streaming_tts_consolidation.py`

## [8.3.8.9] - 2026-04-25

### Fixed

- **`stream-translate-worker.py` silently swallowed TTS exceptions, leaving "completed" rows with NULL audio_path** (`Audiobook-Manager-g9f`): the inner TTS try/except at lines 420-442 caught `Exception` from `_synthesize_segment_audio`, logged `WARNING`, and reported `audio_path=None` to the coordinator. The coordinator wrote `state='completed', audio_path=NULL` — the MSE chain on the frontend reads `state='completed'` as "done", tries to fetch the per-segment audio, hits 404, and stalls indefinitely. 20 orphan rows accumulated in 24h of prod operation (1 live priority p=0 blocking Pronto playback for Qing on 2026-04-25, 18 sampler rows scattered across multiple books — exact pattern of "translation never happened, waited 30+ minutes"). The "VTT alone is still useful, so a TTS failure downgrades to text-only" rationalization in the prior comment was the silent-fallback anti-pattern from the v8.3.8.6 sampler-burst venv RCA — the VTT-only fallback was never actually reachable by the player, the MSE chain has no path that handles a missing-audio "completed" segment. Fix: let TTS exceptions propagate to the outer retry handler at line 485, which already has bounded retry (cap=3), error column persistence, and the v8.3.8.6 idempotent re-run that skips STT+translation when `vtt_content` is already populated — turning a transient edge-tts blip into a sub-second retry instead of a permanent broken segment. The legitimate `_synthesize_segment_audio` → None case (empty VTT — music/silence segments per the function docstring) is preserved unchanged. `library/tests/test_streaming_tts_consolidation.py::test_process_segment_tts_failure_does_not_silently_complete` flips the prior `test_process_segment_tts_failure_degrades_to_text_only` test (which had been *enforcing* the bug) to assert the opposite invariant: TTS exception → `result is False`, segment-complete callback never invoked, retry_count incremented, error column populated, worker_id/started_at cleared for re-claim
- **iOS Chrome mobile player still clipped after v8.3.8.8** (`Audiobook-Manager-g9f`): Qing's iPhone 17 Pro Chrome continued to show only the player's cover + title row, with all four wrapped control rows hidden behind Chrome's persistent bottom nav, even after v8.3.8.8 bumped `--player-height` 100 → 200 px. Root cause: the player wasn't being clipped by its own `min-height` — the entire `<body>` was extending behind the toolbar. `library/web-v2/css/shell.css` sized `<html>` to `var(--app-height, 100dvh)`, where `--app-height` is set by `setupViewportFix()` from `window.visualViewport.height`. On iOS Chrome `visualViewport.height` matches the layout viewport (i.e. INCLUDES the area behind the persistent bottom nav), and `100dvh` aliases to `100lvh` on the same engine — so `<html>` was always taller than the visible area. The body's flex column placed the player at the bottom edge of `<html>`, which sat behind Chrome's nav. v8.3.8.8's height bump made the bug *more visible* (200 px of clipped player vs 100 px) but did not address the source. Fix: cap `<html>` height at `min(100svh, var(--app-height, 100svh))`. `100svh` is the small viewport (with all UI showing) and on iOS Chrome correctly excludes the persistent bottom nav; the `min()` lets `--app-height` win when smaller (on-screen keyboard shrinks the visual viewport) and caps it when larger (the iOS Chrome bug case). Comment block on `html` and the mobile `@media` block updated to document why `100svh` is mandatory and why `100dvh` is forbidden. `shell.js::setupViewportFix` comment header expanded to record that on iOS Chrome the computed `bottomChrome` evaluates to `0` and iframe consumers should not depend on it
- **Regression guard for `<html>` height cap**: `library/tests/test_shell_css.py::test_html_height_capped_at_100svh` asserts the exact `min(100svh, var(--app-height, 100svh))` formulation is present and that `var(--app-height, 100dvh)` does not return. Belt-and-braces against future "simplification" replacing `svh` with `dvh`, since `dvh` was the original setting that hid this bug from desktop dev

## [8.3.8.8] - 2026-04-24

### Fixed

- **Mobile player controls hidden behind iOS Chrome bottom nav bar**: Qing's iPhone (Chrome iOS) showed the player's cover + title row but ALL controls — play/pause, -30/+30, scrubber, speed, CC, close — were clipped. Audio + subtitles worked, but she had no way to pause, seek, or close the player. Root cause: on narrow-mobile (`@media max-width: 768px`) the player flex-wraps its 5 child bars into 4 rows totaling ~184 px of content, but `min-height: calc(100px + safe-area-inset-bottom)` only reserved ~134 px. `body { overflow: hidden }` clipped the overflow; iOS Chrome's bottom nav bar (which, unlike iOS Safari, does NOT collapse on scroll) occluded whatever escaped. The v8.3.6 "mobile player bottom clipping" fix with `--player-height: 100px` worked on Safari because Safari's bottom chrome DOES retract — iOS Chrome exposes the gap. Fix: bump `--player-height` to 200 px at the mobile breakpoint so `min-height` reserves enough vertical space for all four wrapped rows (row 1: cover + info ~56 px · row 2: controls ~44 px · row 3: extras ~44 px · row 4: progress ~40 px = 184 px + internal padding). `height: auto` still lets the bar shrink on breakpoints that need less. Affects `library/web-v2/css/shell.css` only — no JS or HTML changes

## [8.3.8.7] - 2026-04-24

### Fixed

- **`streaming-translate.js` did not auto-advance chapters on MSE end-of-stream**: the streaming player loaded the active chapter's segments into MediaSource, played them, and then sat silent at `audio.currentTime == audio.duration` with no next-chapter transition. Affected books whose ch=0 is a short Audible frame ("This is Audible." clip of 1-3 segments) — e.g. 115401, 115852, 116062 — which played their ~1-3 second intro and stopped, even though the actual content in ch=1 was fully cached. Root cause: `shell.js`'s `audio.addEventListener('ended', …)` only advances chapters for the LEGACY cached-chapter path (`translatedEntries`) and explicitly comments that "the streaming MSE path is unaffected: streamingTranslate owns its own end-of-stream signaling" — but `streaming-translate.js` had no `ended` listener of its own. Fix: installs a chapter-advance listener inside `enterStreaming()` that, on `audio.ended` while `state === STREAMING`, tears down the current `MseAudioChain`, removes the listener, POSTs `/api/translate/stream` with the incremented `chapter_index`, and re-enters buffering so the replay loop can feed the new chapter's segments. Listener is also removed in `enterIdle()` and on book-switch so dead-session callbacks can't fire. `totalChapters` is captured from the `/translate/stream` response (see backend fix below) so the advance handler knows when to stop at end-of-book
- **`/api/translate/stream` buffering response was missing `total_chapters`**: the `_fully_cached_response` branch already returned it, but the `buffering` branch did not, meaning the chapter-advance client could not tell when it had reached the last chapter without a separate book-metadata fetch. Now both branches return `total_chapters`. Covered by `test_buffering_response_includes_total_chapters` in `test_streaming_translate.py`
- Regression-guarded by `test_streaming_translate_js_has_chapter_advance_on_ended` in `test_streaming_retry_and_claim.py` — a static source scan that asserts the `advanceChapter` function exists, an `ended` listener is installed and removed, the POST body references `nextChapter`, and `totalChapters` is tracked
- **`_fully_cached_response` was missing `segment_bitmap`**: chapter-advance POST to `/api/translate/stream` for a fully-cached chapter returned `{state: "cached"}` without a bitmap, so the frontend's `enterBuffering` received `undefined` and no-oped its populate-and-transition block — the player sat in BUFFERING after a successful advance call. Now both the cached and buffering response branches return `segment_bitmap` built from `_get_segment_bitmap`. Covered by `test_cached_response_includes_segment_bitmap` in `test_streaming_translate.py`
- **`get_book_translated_audio` sampler-incomplete filter overreached on stuck `sampler_jobs.status`**: v8.3.8.6 added a filter that hides sampler-origin audio when `sampler_jobs.status != 'complete'` to prevent dead-ends from partial samples. But `sampler_jobs.status` drifts — orphan-repair and ad-hoc `UPDATE streaming_segments` flows deliver all audio without touching the sampler_jobs row, leaving books with status='running' forever even though every segment has `audio_path` and every consolidated `chapter.webm` exists on disk. The filter then hid fully-playable audio (book 115852 returned `[]` from `/translated-audio` despite 269/269 segments complete + both chapter.webm files valid, 325 KB and 40 MB). Authoritative signal is the actual segment state: sampler is incomplete iff `COUNT(*) FROM streaming_segments WHERE state != 'completed' OR audio_path IS NULL > 0` for that (book, locale). Falls back to `sampler_jobs.status` only when streaming_segments has zero rows. Also includes a one-off data repair: `UPDATE sampler_jobs SET status='complete' WHERE status='running' AND no streaming_segments rows are pending or audio-less` — 14 rows repaired on prod
- **`streaming-translate.js::MseAudioChain` never signalled end-of-stream**: the chain appended segments but never called `mediaSource.endOfStream()`, so the `<audio>` element reached its last buffered timestamp and sat there with `readyState=2, ended=false`, paused silently. That prevented the chapter-advance `ended` handler from firing. Added `markEndOfStream()` + in-flight-append watchdog to `_drain()` — when the caller signals no more segments will be enqueued AND the queue is empty AND no fetches are in flight, the MediaSource transitions to `'ended'` and the `<audio>` element fires `ended`. Called from `enterStreaming` when `chapterIsFullyKnown(currentChapter)` and from `onSegmentReady` when the final segment for a chapter arrives. Per-chapter segment totals are tracked in `chapterTotals[ch]` (populated from `bitmap.total` in `enterBuffering`)

## [8.3.8.6] - 2026-04-24

### Fixed

- **`sampler-burst.sh` spawned workers with system `python3` (no `edge_tts`)**: line 68 hardcoded `${AUDIOBOOKS_HOME}/venv/bin/python` — wrong by `library/`. The canonical venv is `${AUDIOBOOKS_HOME}/library/venv/bin/python` (set by `audiobook-config.sh` as `AUDIOBOOKS_VENV`). The broken-path `[[ -x ]]` check then silently fell back to system `/usr/bin/python3`, which has no `edge_tts` module — every TTS synthesis call failed with `No module named edge_tts`. Net effect on prod: 4 of every 5 segments completed with VTT only, no `audio_path`, no `.webm` on disk. The user clicked play and heard 30 s of audio (the one segment from the systemd worker) followed by silence. Now uses `AUDIOBOOKS_VENV` like `stream-translate-daemon.sh` does, and **hard-fails** if the venv python isn't executable or can't `import edge_tts` — silent fallback class of bug closed
- **`_ensure_chapter_segments` skipped chapters with existing sampler rows**: pre-fix the function early-returned when ANY row existed for `(book, chapter, locale)`, so when the sampler had pre-enqueued a partial range at p=2 and the user pressed play, no live p=0 rows were created and live playback dead-ended. Now promotes pending sampler rows to `origin='live'` (sidesteps the `origin='sampler' AND priority<2` trigger), drops their priority, then back-fills any missing segment indices with fresh `live` rows
- **`_get_segment_bitmap` falsely declared chapters "fully streamed" from a partial sampler**: `streaming_done` was `len(completed) == total` — trivially true when the sampler had enqueued + completed only a few segments. The phantom "complete" status caused `chapter_translations_audio` to be written for a 30 s sample, which the frontend treats as "full chapter available" and dead-ends after the sample. Now compares against `_chapter_segment_count(_get_chapter_duration_sec(...))` with a 1-segment slack for rounding — the chapter is "done" only when the full expected segment count is present
- **`/api/audiobooks/<id>/translated-audio` exposed partial sampler rows as full translations**: the endpoint returned every `chapter_translations_audio` row regardless of completeness. A book with a 13-segment sampler that finished 1 segment got served the consolidated 30-second `chapter.webm` as "chapter 0 fully translated" — the player loaded it, played 30 s, and stopped. Now hides rows whose `audio_path` is under `${AUDIOBOOKS_STREAMING_AUDIO_DIR}/` when `sampler_jobs.status != 'complete'` for that locale. Legacy batch-translation rows under the library tree are always returned (they were produced by the v7 per-chapter pipeline and are fully playable)
- **`segmentBitmap[ch] = "all"` sentinel broke subsequent `.add()` calls**: `onChapterReady` set the bitmap entry to the string `"all"` to mark a chapter fully cached. If a later p=0 segment_ready arrived for that chapter (e.g. a freshly-activated chapter after the sampler-only chapter was promoted), the next call did `segmentBitmap[ch].add(seg)` → `TypeError: "all".add is not a function`. The bitmap now resets the `"all"` sentinel back to a fresh `Set` on incoming segments — a chapter is manifestly NOT fully cached if the worker is still emitting new segments for it
- **`stream-translate-worker.py` always used `STREAMING` workload hint**: the worker hardcoded `WorkloadHint.STREAMING` for every segment, routing sampler/backlog work to the warm pool and burning warm-instance cost on bulk pretranslation that has no latency budget. Now reads `segment["origin"]` — `'live'` keeps STREAMING, `'sampler'` / `'backlog'` route to LONG_FORM (Vast.ai scale-to-zero + RunPod backlog), giving dual-farm throughput for bulk work
- **`whisper-server` Docker image missing GHCR package linkage**: added `LABEL org.opencontainers.image.source` so the image registry page links back to `TheBoscoClub/Audiobook-Manager` (matches the convention used by other org Docker images)
- **`stream-translate-worker.py::process_segment` was not idempotent**: the worker unconditionally ran STT + translation on every claimed row, even when `vtt_content` / `source_vtt_content` were already populated from an earlier partial run. Before this change, an operator-driven "reset orphan row to `state='pending'`" incurred another RunPod Whisper GPU hit plus DeepL per-char cost to regenerate the SAME translated text. It also meant there was no recovery primitive for rows with a valid VTT but a missing/bad `audio_path` — e.g. the 400 legacy `.opus` rows (books 115401, 115852) left behind by the pre-v8.3.3 synth path and the 6,687 `audio_path IS NULL` rows left behind by the sampler-burst venv bug above. Now: if `segment['vtt_content']` is non-empty, the worker skips `split_audio_segment` / STT / `generate_subtitles` entirely and feeds the pre-existing VTT directly to `_synthesize_segment_audio`. `output_dir` is gated on the STT branch (`Path | None`) so the cleanup block only runs when a tempdir was actually created. Covered by 3 new tests in `library/tests/test_streaming_tts_consolidation.py` pinning (1) no STT calls when VTT present, (2) works with only `vtt_content` (legacy `.opus` rows have no `source_vtt_content`), (3) full STT still runs for empty/whitespace/None VTT
- **`streaming-translate.js::enterBuffering` MSE-starvation on short chapters**: books whose ch=0 had fewer than `BUFFER_THRESHOLD (6)` segments stalled forever at "正在准备…… N / M" with `audio.currentTime=0, readyState=0`. Root cause: the `bitmap.all_cached` fast-path early-returned via `enterStreaming()` BEFORE `segmentBitmap[chapterIndex]` was populated from `bitmap.completed`. Inside `enterStreaming`, the replay loop iterated an empty `Set`, never called `mseChain.enqueueSegment()`, and the MSE source buffer stayed empty indefinitely. Observed during v8.3.8.6 orphan-repair browser proof on books 115401 (1-seg ch=0 Audible intro), 115852 (3-seg ch=0), 116062 (1-seg ch=0). Fix: populate `segmentBitmap[chapterIndex]` from `bitmap.completed` BEFORE the `all_cached` short-circuit. Regression test `test_streaming_translate_js_populates_bitmap_before_all_cached_shortcut` in `test_streaming_retry_and_claim.py` enforces the ordering via static source scan
- **`claim_next_segment` session-state filter overreached to sampler/backlog rows**: the `sess.state NOT IN ('stopped','cancelled','error')` guard was designed to prevent live-playback rows from being silently resumed after a user presses Stop (Bug E from v8.3.2). But the filter applied to ALL `streaming_segments` rows regardless of `origin`, which meant a single user Stop on a book froze all pretranslation work (sampler-burst, backlog fill) for that (book, locale) pair. This exact pattern blocked the v8.3.8.6 orphan repair for books 115401, 115852, and 116062 until the operator manually transitioned their `streaming_sessions.state='stopped'` rows back to `'buffering'`. Fix: added `s.origin != 'live'` carve-out to the claim SQL so the session-state block applies ONLY to live rows. Sampler and backlog rows are controlled by `sampler_jobs.status` and worker liveness, not the user's playback session. 3 new tests in `test_streaming_retry_and_claim.py`: (1) sampler + backlog rows claimable under stopped session, (2) live rows STILL blocked under stopped session (regression guard on the original Bug E invariant), (3) priority ordering honored when live-blocked rows coexist with sampler-eligible rows in the same queue

### Documentation

- **`docs/RCA-v8.3.8.6-chinese-audio-silence.md`**: comprehensive RCA covering the catastrophic QA-to-prod regression. Executive summary, timeline of QA-green → prod-red, primary technical failure (sampler-burst venv silent fallback), six concurrent pre-existing failures uncovered during fix (including 400 legacy `.opus` orphan rows, MSE buffer-threshold stalls on short-chapter books, claim-queue session-blocking interactions), layer-by-layer breakdown of why seven independent layers of defense all missed it (unit tests, dev VM, QA VM where AI + human both reported green, `/test` phases, pre-release smoke probe, post-deploy observability), root-trait analysis of the silent-fallback anti-pattern, repair log for the 7,089 orphan rows recovered via idempotent TTS regen at zero paid-API cost, eight concrete recommendations with owners + timelines to ensure DEV and QA actually work as intended going forward

## [8.3.8.5] - 2026-04-24

### Changed

- **`sampler-burst` default is now DETACH (interactive-friendly)**: previous behavior blocked the terminal on a drain-polling loop — so the operator couldn't close their terminal without `Ctrl+Z` / `bg` / `disown` gymnastics, and a `Ctrl+C` would fire the `EXIT` trap and SIGTERM the workers they'd just spawned. New default: after spawning, the script prints a monitoring hint (`sqlite3`, `pgrep`, `tail -f`) and returns exit 0 immediately. The workers are backgrounded children; non-interactive bash doesn't `huponexit`, so they keep running after the shell detaches. The `EXIT`/`INT`/`TERM` traps that kill children are now gated on `--wait` — they only install when the caller explicitly asks to stay attached

### Added

- **`--wait` flag** for cron/CI callers that want the old drain-polling behavior plus a non-zero exit code on `--timeout` (exit 3). The traps that SIGTERM workers on script exit only install under `--wait`. Paired with `--timeout DUR`, `--wait` gives scripted callers the same deterministic semantics as v8.3.8.4

## [8.3.8.4] - 2026-04-24

### Added

- **`sampler-burst --workers N` / `--add-workers N` semantics**: the previous version only stacked new workers on top of whatever was already running. Now:
  - **`--workers N`** (default, **REPLACE** semantics): any existing burst workers are gracefully `SIGTERM`ed so they finish their current segment (~30–60s on cold GPU, 90s grace window), then N fresh workers spawn. Gives predictable pool-size control without orphaning workers
  - **`--add-workers N`** (**ADD** semantics): existing burst workers keep running; N new workers stack on top. For when you want to ramp throughput incrementally
  - **Cap enforcement**: total `stream-translate-worker.py` count across the host is capped at `MAX_WORKERS_TOTAL=16` including the systemd on-demand worker. Requested counts that would push past the cap are clamped to the remaining slot budget with a user-visible `note:` line showing what was skipped and why. Mutually exclusive; the two flags can't both be given
  - **Existing-worker discovery** filters the systemd unit's `MainPID` out of the `pgrep -f stream-translate-worker.py` set. Works whether the prior burst parent is alive, exited, or re-parented to init via `nohup`
  - Regression suite in `library/tests/test_sampler_burst_modes.py` (16 tests) pins every invariant: arg-parsing mutex, default=replace, cap math, TERM-before-KILL, grace=90s, systemd exclusion from burst count
- **Test bypass for the user-gate helpers**: `AUDIOBOOKS_SKIP_USER_GATE=1` env var makes both shell (`lib/audiobook-config.sh`) and Python (`library/config.py`, inline copies in `email-report.py` / `embed-cover-art.py`) helpers no-op. `library/tests/conftest.py` sets it before any script module is imported, so all 4729 tests see a no-op gate regardless of the CI runner's uid. Production scripts never set this

## [8.3.8.3] - 2026-04-23

### Added

- **Shared `require_audiobooks_user()` helper**: new function in both `lib/audiobook-config.sh` (shell) and `library/config.py` (Python). Every script that reads/writes the audiobook DB, reads the operator config at `/etc/audiobooks/audiobooks.conf`, or spawns worker subprocesses now fails fast with a clear `sudo -u audiobooks <script>` diagnostic when invoked as any other account. The DB and config are `0640 audiobooks:audiobooks` by design; running as root or an interactive user bypasses the permission model the systemd units rely on. Applied to 13 scripts: `sampler-burst.sh`, `stream-translate-daemon.sh`, `audiobook-status`, `audiobook-purge-cache`, `audiobook-save-staging`, `audiobook-save-staging-auto`, `audiobook-translations`, `sampler-reconcile.py`, `stream-translate-worker.py`, `batch-translate.py`, `verify-translations.py`, `embed-cover-art.py`, `email-report.py` (last two use an inline copy of the helper — no `library/` `sys.path` setup in those)

### Fixed

- **`sampler-burst.sh` silent-drain bug**: `_pending_count()` used `sqlite3 ... || echo 0` — so a transient DB-query failure (e.g. running as a user without group membership) was interpreted as "queue drained" and the main loop exited immediately with `Queue drained after 0s` even with thousands of pending rows. Now returns `999999` on query failure so the loop keeps polling until its wall-clock timeout fires; the new user-gate catches the common case (wrong user) before spawning workers anyway
- **`sampler-burst.sh` worker log path collision**: `/tmp/sampler-burst-N.log` was flat and unscoped, so a second user (or second invocation) hit `Permission denied` trying to overwrite files owned by whoever ran first. Now writes to `/tmp/sampler-burst-$$/worker-N.log` — per-invocation directory, no collisions
- **Missing `/usr/local/lib/audiobooks` symlink on existing deployments**: `install.sh` creates `/usr/local/lib/audiobooks → ${target}/lib` on fresh installs, but three existing deployments were discovered without it (probably wiped by a historical partial uninstall). Without the symlink, any script that sources `audiobook-config.sh` from the canonical `/usr/local/lib` path fails with "file not found" on startup. Added defensive create in `upgrade.sh::audit_and_cleanup` so subsequent upgrades self-repair

## [8.3.8.2] - 2026-04-23

### Fixed

- **`sampler-burst.sh` spawned workers with no provider credentials**: the script sourced `/usr/local/lib/audiobooks/audiobook-config.sh` (canonical DEFAULTS) but never sourced `/etc/audiobooks/audiobooks.conf` (operator OVERRIDES + STT/DeepL keys). Workers inherited a bare-defaults environment — on any deployment with a library path override (e.g. `AUDIOBOOKS_LIBRARY=/hddRaid1/Audiobooks/Library`), every segment failed at file resolution; without `AUDIOBOOKS_RUNPOD_*` / `AUDIOBOOKS_DEEPL_API_KEY` exported, workers raised `RuntimeError("No STT provider configured")` on every dispatch. Now mirrors the systemd pattern (`Environment=` then `EnvironmentFile=`): sources defaults first, then wraps `audiobooks.conf` in `set -a` / `set +a` so every assignment is exported to worker subprocesses. Honors `$AUDIOBOOKS_CONFIG` override
- **`stream-translate-daemon.sh` same latent defect**: even though it only runs under systemd today, anyone invoking it manually (debug, cron, future wrappers) would hit the same missing-env issue. Added the same defensive `set -a` / source `audiobooks.conf` / `set +a` block. Idempotent under systemd where `EnvironmentFile=` already populated those vars
- **Missing CLI symlinks for sampler admin tools**: `sampler-burst` was only accessible by full `/opt/audiobooks/scripts/sampler-burst.sh` path despite being a documented user-facing admin command; `sampler-reconcile` (referenced by `docs/SAMPLER.md` and `MULTI-LANGUAGE-SETUP.md`) had no short-name at all. Both now registered in `SCRIPT_ALIASES` (install.sh + upgrade.sh — duplicated but kept in sync) so `refresh_bin_symlinks` auto-creates `/usr/local/bin/sampler-burst` and `/usr/local/bin/sampler-reconcile` on every install/upgrade
- **Broken-symlink sweep missed `sampler-*`**: `upgrade.sh::audit_and_cleanup` glob was `audiobook*` only, so if a future release removed a sampler-* CLI, the stale symlink would linger. Extended to `\( -name "audiobook*" -o -name "sampler-*" \)`
- **CodeQL `py/log-injection` defense in `sampler.py::enqueue_sampler`**: callers already validate `locale` at the boundary (sampler_hook iterates env-whitelisted locales; admin API rejects non-canonical slugs), but the log line used raw `str(locale)` / `str(scope)`. Added private `_safe_log()` helper (mirrors `_safe_log_value` in `streaming_translate.py`) that strips CR/LF/null/control chars and truncates to 200. CodeQL alert #522 dismissed as false-positive with the caller-validation + explicit-sanitizer reasoning documented on the alert

## [8.3.8.1] - 2026-04-23

### Fixed

- **Release tarball missing critical directories**: `create-release.sh` used an explicit allowlist that had drifted from the project layout. The v8.3.8 GitHub tarball shipped at 1.2M instead of 2.4M — missing `data-migrations/`, `config-migrations/`, `caddy/`, `Dockerfile` + `docker-compose.yml` + `docker-entrypoint.sh`, `docs/`, `ACKNOWLEDGEMENTS.md`, and `bootstrap-install.sh`. Impact: any `--from-github` install or upgrade silently skipped every data migration (including 008 which ships the `sampler_jobs` table and `streaming_segments.origin` column), silently skipped config migrations, and omitted the Docker deployment artifacts. Allowlist expanded to cover all runtime-required directories plus `ACKNOWLEDGEMENTS.md` and user-facing `docs/`. Library + docs rsyncs now exclude Claude session artifacts (`.claude*`, `*.jsonl`) so nothing internal leaks in
- **Release-requirements gate didn't catch whole-table features**: `scripts/release-requirements.sh` only declared required DB columns — migrations that add a whole new table (like `sampler_jobs` in 8.3.8) slipped past. Added `REQUIRED_DB_TABLES` array plus matching `missing_tables` report path; `scripts/smoke_probe.sh` extended to probe required tables. Both use `declare -p` guard so older envs running pre-8.3.8.1 release-requirements.sh stay backward-compatible

## [8.3.8] - 2026-04-23

### Added

- **6-minute pretranslation sampler**: every book gets its opening 6 minutes pretranslated per enabled non-EN locale at scan time. Library-wide discovery for non-EN listeners (preview any book in their locale without GPU wait) and runway for the live pipeline when a user commits. Scope algorithm in `library/localization/sampler.py::compute_sampler_range`: ≥ 6 min, extend to chapter boundary if within `SAMPLER_MAX_EXTEND_SECONDS` (3 min) slack, otherwise hard-stop at 6 min. See `docs/SAMPLER.md`
- **Sampler priority invariant enforced at the DB layer**: new `streaming_segments.origin` column (`live` | `sampler` | `backlog`) plus `BEFORE INSERT/UPDATE` triggers that `RAISE(ABORT)` when `origin='sampler' AND priority < 2`. Makes it physically impossible for sampler work to starve live-playback cursor (p0) or buffer-ahead (p1) work
- **`sampler_jobs` table** tracking per-`(audiobook, locale)` status + progress; driven by `segment-complete` callback; drives the library-browse "🎧 6-min Sample" affordance
- **Scan-time sampler hook** `library/scanner/utils/sampler_hook.py` — fires on every new book insert for each enabled non-EN locale. Failures logged and swallowed; sampler is enrichment, never blocks ingestion
- **Locale-addition reconciler** `scripts/sampler-reconcile.py` — enqueues sampler jobs for every `(book, locale)` pair missing a `sampler_jobs` row; idempotent; `--dry-run`, `--locale`, `--max-books` flags
- **Sampler API endpoints** in `api_modular/streaming_translate.py`:
  - `POST /api/translate/sampler/prefetch` — admin-triggered enqueue
  - `GET /api/translate/sampler/status/<id>/<locale>` — single-book status + chapter audio URLs when complete
  - `GET /api/translate/sampler/batch-status?ids=&locale=` — bulk-query up to 100 books at once; used by library browse
  - `POST /api/translate/sampler/activate` — called by frontend when user crosses the adaptive buffer-fill threshold; creates p0/p1 segments from cursor forward
  - `GET /api/translate/warmth` — returns aggregate STT-provider warmth across every configured backend (RunPod, Vast.ai, self-hosted whisper-gpu) plus adaptive `buffer_fill_threshold` and a per-provider `providers: [{name, ready, endpoint_id}, ...]` array for richer diagnostics
- **Adaptive buffer-fill threshold (provider-agnostic)** — segment 3 when NO configured STT provider has ready workers (4.5-min runway), segment 4 when at least one provider is warm (4-min runway). Warmth probe iterates every configured provider family (RunPod `api.runpod.ai`, Vast.ai `run.vast.ai`) and caches 60s. Default on probe failure / no provider configured: assume cold (safer)
- **Dual-farm STT round-robin** (`library/localization/pipeline.py::_select_from_candidates`) — when 2+ STT backends are configured, parallel workers rotate across farms instead of hammering whichever happens to be first in the candidate list. Modes tunable via `AUDIOBOOKS_STT_DISTRIBUTION`: `round_robin` (default, process-wide atomic counter), `random` (uniform), `primary` (pre-8.3.8 legacy — always picks `remote[0]`). Gives N× throughput on backfills when paired with `sampler-burst.sh`
- **`scripts/sampler-burst.sh`** — parallel worker fan-out for sampler backfills. Spawns N (default 4, max 16) `stream-translate-worker.py` processes, waits for `streaming_segments` queue to drain, gracefully TERM+KILL cleanup on SIGINT/EXIT. Refuses to run alongside active `audiobook-stream-translate.service` without `--force` (prevents N+1 worker competition). Integer-validated `--workers` and duration-parsed `--timeout` reject shell-injection inputs. Exit codes: 0 drained, 2 bad arg, 3 timeout. Also wired into `sampler-reconcile.py --burst N` for enqueue-then-drain in one command
- **"🎧 6-min Sample" book-card affordance** — `library.js::applySamplerAvailability` chunks rendered book IDs in batches of 100, queries `/api/translate/sampler/batch-status`, flips `.btn-sample` visibility on cards whose sampler is complete. Sapphire-bordered to distinguish from the full-play button. Curated zh-Hans translation for the label and tooltip
- **Release-requirements manifest** `scripts/release-requirements.sh` — declarative `REQUIRED_CONFIG_KEYS`, `REQUIRED_SYSTEMD_UNITS`, `REQUIRED_DB_COLUMNS`. Upgrade/install emit actionable remediation snippets for missing entries
- **Post-upgrade functional smoke probe** `scripts/smoke_probe.sh` — probes `systemctl is-active` (with expected-inactive whitelist for timer-triggered units), `/api/system/health` response, DB schema coverage, and **STT provider readiness for whichever backend(s) the operator configured** via the provider-agnostic `_probe_stt_providers` path (RunPod, Vast.ai, self-hosted GPU Whisper — multiple peers supported; no configured provider prints INFO instead of failing). Wired into BOTH `upgrade.sh` success paths AND `install.sh`; hard-fails the workflow when the system isn't actually functional post-upgrade
- **Schema migration 024** (`library/backend/migrations/024_streaming_origin_and_sampler.sql`) + matching data migration `data-migrations/008_streaming_origin_and_sampler.sh` — idempotent; verified on scratch SQLite
- **install.sh streaming config stubs are now provider-agnostic**: fresh `audiobooks.conf` lists commented stanzas for the translation backend (`AUDIOBOOKS_DEEPL_API_KEY` — DeepL is currently the only supported translator) alongside peer STT backend options the operator can pick ONE OR MORE of — RunPod serverless (`AUDIOBOOKS_RUNPOD_*`), Vast.ai serverless (`AUDIOBOOKS_VASTAI_SERVERLESS_*`), self-hosted GPU Whisper (`AUDIOBOOKS_WHISPER_GPU_HOST`/`_PORT`), and a note about CPU-only `faster-whisper` as an advanced option. Named providers are recipes, not requirements — see `docs/SERVERLESS-OPS.md`

### Service-account UID/GID is now auto-matched and configurable

Prior releases hardcoded `AUDIOBOOKS_CANONICAL_UID=935` and
`AUDIOBOOKS_CANONICAL_GID=934` in `install.sh` and the `Dockerfile`,
which fails on hosts where either slot is already claimed by an unrelated
account (e.g., hosts where GID 935 holds an `empower` or similar group).
v8.3.8 fixes both sides of this:

- **`install.sh` auto-probes for a free matched pair** (UID == GID),
  starting at `AUDIOBOOKS_PREFERRED_UID=935 AUDIOBOOKS_PREFERRED_GID=935`
  (both overridable via environment variables) and walking upward to the
  first matched number that is free on this host. Matched UID:GID
  simplifies Docker bind-mount portability and mirrors how mainstream
  container images assign service identifiers.

- **`upgrade.sh` detects UID != GID** on existing installs and
  interactively offers to realign via `scripts/migrate-audiobooks-uid.sh`.
  The prompt is skipped in `--yes` and `--dry-run` modes so unattended
  automation isn't blocked. Operators running attended upgrades just
  answer `y` and the script takes care of `usermod`/`groupmod` +
  chowning every audiobook-owned path under `/opt/audiobooks`,
  `/etc/audiobooks`, `/var/lib/audiobooks` (including the Docker
  `docker-data` bind-mount), and `/srv/audiobooks`.

- **`scripts/migrate-audiobooks-uid.sh`** now accepts `--uid N --gid N`
  explicitly, and auto-probes for a free matched pair when called with
  no args.

- **`Dockerfile`** accepts `AUDIOBOOKS_UID` and `AUDIOBOOKS_GID` as
  `ARG` build-args (both defaulting to a matched `935:935`). Operators
  who want the Docker image to match a non-default host UID can build
  with `docker build --build-arg AUDIOBOOKS_UID=1042 --build-arg AUDIOBOOKS_GID=1042 ...`
  and the in-container `audiobooks` user will match exactly. `install.sh`
  exports the resolved host values so this step is straightforward.

### Operator action required (one-time, only if upgrading pre-v8.3.8 with UID != GID)

If your host has an existing `audiobooks` account with mismatched UID/GID
(e.g. 951:949 from older installs) AND you want the new matched convention,
`upgrade.sh` will prompt interactively. Answer `y`; the script handles
everything.

To skip the prompt but match later manually:

```bash
sudo bash /opt/audiobooks/scripts/migrate-audiobooks-uid.sh            # auto-pick matched pair
sudo bash /opt/audiobooks/scripts/migrate-audiobooks-uid.sh --uid 1042 --gid 1042
sudo bash /opt/audiobooks/scripts/migrate-audiobooks-uid.sh --dry-run  # preview only
```

If you keep your existing mismatched UID/GID, everything continues to
work — the `Dockerfile` build-args let you align the container image to
match your host when you rebuild the Docker image.

- **Cachebust stamp automation** `scripts/bump-cachebust.sh` — one stamp per deploy, atomically rewrites every `?v=<stamp>` in `web-v2/*.html`. Wired into both `upgrade.sh` (after HTML sync, before service restart) and `install.sh`. Replaces the manual `?v=` bumping every JS/CSS change required — root cause of the recurring "user runs stale JS after a deploy" class of incident (v8.3.4 qalib 2000-ID URL-overflow 400 was one). Stamp validation rejects shell-injection inputs. 8 regression tests pin the rewrite contract + shellcheck cleanliness + upgrade/install wiring
- **Profile preference live-apply** — `account.js::saveBrowsingPref` now dispatches `audiobooks:preference-changed` CustomEvent after persisting each preference. `library.js::_wirePreferenceLiveApply` listens and routes by key: `view_mode` → CSS class toggle (instant), `sort_order`/`items_per_page`/`content_filter` → re-apply + reload audiobooks. No hard browser refresh required. (Deferred since the Localization-RND merge, finally un-gated.)
- **`docs/EMAIL-SETUP.md`** — end-to-end SMTP configuration guide covering Resend (recommended for thebosco.club deployments — cleanroom SES, no PGP wrap), Gmail (app-password requirement), Microsoft 365 / Outlook.com, Protonmail Bridge (with explicit callout of why it breaks Apple mail via `554 5.7.1 [CS01]`), generic MTA relay, mailx/s-nail smoke-test wrappers. STARTTLS vs implicit-SSL vs plaintext decision matrix. Common failure-mode table with diagnoses

### Fixed

- **upgrade.sh version-ordering bug (SEVERE — root cause of the v8.3.7.1 prod regression)**: `do_upgrade()` was writing the new `VERSION` file before `apply_data_migrations` ran, so the migration dispatcher's gate read the new version, saw `installed > MIN_VERSION`, and silently skipped every data migration. This is why v8.3.7.1 landed on prod with three missing columns (`streaming_segments.retry_count`, `streaming_segments.source_vtt_content`, `audiobooks.chapter_count`) despite the dispatcher being invoked — and why the streaming worker kept crashing with `sqlite3.OperationalError: no such column`. Fixed by capturing `_DO_UPGRADE_PRE_VERSION` at the top of `do_upgrade` before any file write, exporting for `apply_data_migrations`. Belt-and-suspenders: migration gate now treats empty/unknown `installed_version` as "must run"
- **upgrade.sh `audit_and_cleanup` orphan-systemd wipe (SEVERE)**: the orphan-unit check fell back to `${SCRIPT_DIR}/systemd` when `${target}/systemd` was missing. When upgrade.sh was copied to `/tmp/` for a `--from-github` bootstrap, `SCRIPT_DIR=/tmp` and `/tmp/systemd` didn't exist — every installed `audiobook-*.service` appeared "orphaned" and was `rm -f`'d. Fixed with three-tier source resolution (`$project/systemd` → `$target/systemd` → `$SCRIPT_DIR/systemd`), each candidate only accepted when it contains `audiobook*.service` files, safety gate that skips the destructive loop entirely when no trusted source exists
- **Install / upgrade now validate they produced a functional system** — both paths run `release-requirements.sh::validate_release_requirements` + `smoke_probe.sh` before printing success. A passing `rsync` + `systemctl enable` is no longer treated as proof of a functional release. Dry-run safely skips the gates
- **Ruff format drift eliminated across `library/` and `scripts/`** — 46 files reformatted to the project's canonical style. `except (A, B):` lines annotated with `# fmt: skip` to preserve explicit tuple-parens (the `test_no_py2_except_comma.py` regression guard bans unparenthesized multi-exception excepts; ruff 0.15.11 would otherwise collapse them)
- **Shfmt drift eliminated** on `install.sh`, `upgrade.sh`, `scripts/bump-cachebust.sh`, `scripts/migrate-audiobooks-uid.sh`, `scripts/release-requirements.sh`
- **Bandit B108 /tmp in tests**: `library/tests/test_batch_translate.py::test_required_flags` now uses `tmp_path` fixture instead of hardcoded `/tmp/x.db`. `library/tests/test_streaming_translate.py:757` annotated `# nosec B108` — the `/tmp/../etc/hosts` string is a security test input exercising the path-traversal rejection logic, never touches the filesystem
- **`scripts/bump-cachebust.sh` documented as an intentional wiring exception** per `upgrade-consistency.md` "New-Script Wiring Enforcement" — build-time helper shelled out by `install.sh` + `upgrade.sh`, not a service, not a user-facing CLI
- **Prod `audiobook-enrichment.service` hot-patched** on the local host — the installed unit at `/etc/systemd/system/audiobook-enrichment.service` still referenced the non-existent `${AUDIOBOOKS_HOME}/venv/bin/python` path; replaced with the v8.3.8 `${AUDIOBOOKS_HOME}/library/venv/bin/python` path, `daemon-reload`, `reset-failed`. Unit is now `inactive` (healthy steady state for timer-triggered service) instead of `failed`
- **`docs/EMAIL-SETUP.md` sample SMTP key is now an obvious placeholder** — the previous Resend-prefix sample was replaced with `YOUR_RESEND_API_KEY_HERE`. Eliminates any chance of grep-for-prefix collisions mistaking the docs for a real key
- **`caddy/maintenance.html:8` semgrep `missing-integrity` false positive suppressed** with an inline `<!-- nosemgrep: missing-integrity -->` comment. The flagged `<link rel="icon">` is an inline `data:image/svg+xml` URI — SRI is N/A for data URIs (no network fetch to validate)
- **`.vulture_whitelist.py` added** to the repo root — 555 entries generated by `vulture library/ --make-whitelist`. Near-universal false positives (Flask route handlers dispatched via `@bp.route`, pytest fixtures resolved by parameter name, dataclass fields consumed by serialization). Header documents regeneration procedure and whitelist-aware invocation

### Security

- **Bandit B608 suppression comments correctly anchored**: `scripts/scan_supplements.py`, `scripts/enrichment/__init__.py`, and `backend/api_modular/streaming_translate.py` previously placed `# nosec B608` on the `cursor.execute(...)` line, but bandit tracks B608 on the f-string construction line. Moved each f-string onto a single annotated assignment (`_sql_* = f"..."  # nosec B608 ...`) and added matching `# nosemgrep` suppression on the execute call. All three queries were already safe — placeholder interpolation is `?,?,?` only (int-validated values), column names are validated against a hardcoded allowlist (`_SCALAR_COLUMNS`), and locale is `_sanitize_locale`-validated
- **Semgrep SSRF warning on STT warmth probe suppressed**: `streaming_translate.py::_probe_stt_warmth` builds `https://{api.runpod.ai,run.vast.ai}/v2/{endpoint}/health` from a hardcoded scheme/host and admin-only env vars (`AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT`, `AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT`); no user input reaches the URL, and every call is authenticated with a Bearer API key. Added `# nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected` with an inline rationale comment
- **19 CodeQL alerts dismissed as false-positive after per-alert review** — 8 `py/path-injection` + 9 `py/log-injection` + 1 `py/reflective-xss` in `streaming_translate.py`, plus 1 `py/log-injection` in `docker/whisper-server/whisper_server.py`. Every flagged sink is already protected by the in-file sanitizers `_sanitize_locale` (regex `^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$` — forbids traversal and HTML metacharacters), `_validate_audio_path` (absolute-path containment check under `_streaming_audio_root`), `_safe_join_under` (re-resolves and re-validates containment), and `_safe_log_value` (strips CRLF/NUL/control chars via `re.sub(r"[\r\n\x00-\x1f\x7f]", "_", ...)`). Each dismissal was written via the GitHub code-scanning API with a concrete rationale identifying the relevant sanitizer by name and line

### Changed

- **`streaming_segments.priority` schema comment** updated from `0=P0 cursor, 1=P1 chase, 2=back-fill` to `0=P0 cursor, 1=P1 chase, 2=sampler, 3=backlog`, reflecting the four-tier priority model
- **`segment-complete` callback** now captures segment `origin` before the state update. For `origin='sampler'` segments, increments `sampler_jobs.segments_done` and flips status to `'complete'` once `segments_done >= segments_target`

## [8.3.7.1] - 2026-04-22

### Fixed

- **`upgrade.sh` no longer prints "Upgrade complete!" after a mid-run sudo failure**: both `do_upgrade` call sites (local-project path at `upgrade.sh:2992` and GitHub-release path at `upgrade.sh:2704`) discarded the function's return code. Because `do_upgrade` runs `set +e` internally (its flow control relies on nonzero returns from helpers like `compare_versions`), a sudo-credential prompt in a non-interactive shell, rsync permission error, or out-of-disk condition would cause `do_upgrade` to `return 1` — which the caller ignored, continuing straight to `start_services` + `validate_auth_post_upgrade` + `echo "Upgrade complete!"`. VERSION on disk remained at the OLD version while the UI and admin saw success. Surfaced during v8.3.7 prod upgrade in this release cycle when `sudo -v` prompted for a password the non-interactive shell couldn't supply: `sudo: a terminal is required to read the password`, `Error: Sudo access required` — followed immediately by the script cheerfully restarting services and declaring "Upgrade complete!" with 8.3.1 still on disk. Now both call sites capture `upgrade_rc=$?`, emit a loud red `=== Upgrade FAILED ===` banner when nonzero, still restart services so the machine doesn't stay offline, and `exit "$upgrade_rc"` so callers (release automation, systemd invocations, operators reading the log) see the real outcome

## [8.3.7] - 2026-04-21

### Added

- **In-flight VTT stitched from `streaming_segments` (#68)**: `/api/audiobooks/<id>/subtitles` now merges cached `chapter_subtitles` rows with a `streaming_segments` index (deduped by `(chapter_index, locale)`), so a chapter whose VTT file has not yet been finalized on disk still appears in the manifest the moment the first completed segment lands — `subtitles.js` polling can discover live-streaming tracks without waiting for end-of-chapter consolidation. The companion route `/api/audiobooks/<id>/subtitle/<chapter>/<locale>` falls through to a stitched VTT built from `streaming_segments` when no cached file exists (or exists in DB but is missing on disk); stitching strips per-segment `WEBVTT` headers and emits a single `WEBVTT` + concatenated cues in `segment_index` order. For `locale='en'` the stitcher pulls `source_vtt_content` (Whisper transcript is locale-agnostic); other locales pull `vtt_content` where `streaming_segments.locale` matches. Stitched VTT is never cached on disk — always rebuilt from segment rows so late-arriving segments appear on next fetch. Error discrimination preserved: cached row with missing file still returns `VTT file missing on disk` (404); no row at all returns `Subtitle not found` (404)

### Changed

- **Inaccurate GPU cold-start copy replaced for edge-tts path (#54)**: `library/backend/translation/generate_translated_audio.py` and `user_request_translated_audio.py` were emitting `Waking up the GPU server. Cold starts can take a minute or two…` under `phase='gpu_spinup'` — copy carried over from the XTTS-on-Vast.ai era. Current TTS provider for zh-Hans is Microsoft `edge-tts` (API service, no GPU, no cold start), and the sub-second operation surfaced a minute+ wait to Qing that never happened. Both sites now emit `Starting voice synthesis…`. The phase string (`'gpu_spinup'`) is preserved — downstream subscribers key off the phase, not the message; renaming the phase is a follow-up
- **Comprehensive docs sync to v8.3.7 surface area**: `docs/ARCHITECTURE.md` (footer → 8.3.7, new subsections on in-flight VTT stitching, `_safe_join_under` / `_validate_audio_path` / log-injection helpers, deferred legacy-queue state, nested-arcname `transfer.py`, canonical UID=935/GID=934 in install flow, `streaming_segments.source_vtt_content` / `retry_count` columns); `docs/INSTALLER-ARCHITECTURE.md` (new "Canonical Service-Account UID/GID" section + `scripts/migrate-audiobooks-uid.sh` row in file-by-file map); `docs/STREAMING-TRANSLATION.md` + `docs/STREAMING-TRANSLATION.zh-Hans.md` (schema adds `source_vtt_content` / `audio_path` / `retry_count`, new "In-flight VTT Stitching" and "Deferred Legacy-Queue State" sections); `docs/MULTI-LANGUAGE-SETUP.md` + `docs/SERVERLESS-OPS.md` + `SECURITY.md` (version footers → 8.3.7 / 2026-04-22); `docs/TROUBLESHOOTING.md` (new section 13 covering Docker cold-boot UID mismatch, stale 字幕生成失败 toast, transcript scroll snap-back, missing in-flight track, `transfer.py` segment collisions); `library/web-v2/help.html` (new "Real-Time Streaming Translation" and "Mobile Transcript Layout" subsections, picked up by the existing `string_translations` overlay for zh-Hans)
- **CI pytest job now installs `requirements-dev.txt`**: `.github/workflows/ci.yml` previously only `pip install`-ed `requirements.txt`, so dev-only pins (pylint/astroid, bandit, mypy stubs) were absent from the CI environment — any test exercising a dev-only import would `ImportError` in CI while passing locally. Added an optional `pip install -r requirements-dev.txt` block guarded by `[ -f ... ]` so CI stays green on branches without the dev manifest

### Fixed

- **Visible `.sp-error` banner surfaces silent playback failures (#65 defensive layer)**: `library/web-v2/js/shell.js`'s player bar now renders an i18n-translated error under the author line whenever `Audio.play()` rejects or the `<audio>` element raises a `MediaError`. Maps `DOMException.name` (`NotAllowedError → player.error.gestureLost`, `NotSupportedError → player.error.codecUnsupported`) and `MediaError.code` 1–4 (`aborted` / `networkFailed` / `decode` / `codecUnsupported`) to 7 new flat-dotted catalog keys in `en.json` + `zh-Hans.json`. `Dockerfile` now `COPY`s `library/locales → /app/locales` so the containerized `i18n.py` (`/app/backend/i18n.py`, `parent.parent = /app`) can resolve the catalog inside the image — without this, `t()` would have echoed raw dotted keys like `player.error.gestureLost` to Qing's screen. BrowserStack repro harness gains `--search` to filter the lazy-rendered library grid before looking for a specific `--book-id` (iOS viewport virtualizes off-screen cards). `shell.css` / `shell.js` cachebusts bumped to `v=1776743877`
- **Announcement banner + library toolbar compacted for phone viewports (#67, #69)**: `library/web-v2/css/feature-announce.css` adds a `≤768px` media query that collapses the announcement to a one-line bilingual headline plus dismiss — the full banner ate ~570px on a 412px-wide Pixel 10 Pro XL (virtually a whole viewport) and pushed book cards below two full scrolls. Mobile now shows only the EN + zh-Hans headlines (~`0.85rem` / `0.95rem`), hides the bilingual body, feature-pill row, gold rule lines, and vertical dividers, and gives dismiss a larger tap target — recovers ~430–490px so the library grid is reachable in the first viewport after header+marquee. `library/web-v2/css/responsive.css` hides `.author-controls` / `.narrator-controls` pill groups on phone portrait, flex-wraps the author+narrator search rows side-by-side, and compacts `#top-pagination` to prev / current / next only (ellipses and middle numbers hidden). Cachebusts `library.css` (for `responsive.css` import) and `index.html` (for `library.css` + `feature-announce.css`) to `v=1776742848`
- **`MagicMock.call_args` race in `wait_for_thread_completion`**: CPython's `MagicMock._increment_mock_call` sets `self.called = True` *outside* the internal `NonCallableMock._lock` but assigns `self.call_args` *inside* the lock. Under concurrent load (daemon worker thread writing the mock vs. polling test thread, coverage instrumentation amplifying GIL contention), a poller reading `.called` and `.call_args` back-to-back can observe `.called == True` while `.call_args` is still `None`. The helper returned `True` in that window and the caller's next line — `mock.complete_operation.call_args[0][1]` — crashed with `TypeError: 'NoneType' object is not subscriptable`. Surfaced as `test_genre_sync_dry_run_would_update` failing ~40% of the time under the full 4626-test suite while passing 100% in isolation. `wait_for_thread_completion` now requires BOTH `.called` AND `.call_args is not None` before declaring completion — matches the invariant every caller relies on when dereferencing `call_args[0][1]` on the next line. Also adds an `expect=("complete"|"fail"|None)` parameter that converts the prior silent either-or race (which hid the underlying failure as an unrelated `TypeError`) into a loud diagnostic `AssertionError` naming which tracker method the worker actually invoked. Mechanically annotated 72 call sites across four extended test modules based on each test's subsequent assertion. Verified: 8 consecutive full-suite runs (4626 tests each) pass 100%; `P(8/8 green under prior 40% flake rate) = 0.6^8 = 0.017`
- **Proxy log noise from `BrokenPipeError` / `ConnectionResetError`**: `library/web-v2/proxy_server.py` treated mid-response client disconnects (iOS Safari backgrounding, tab close, navigation) as unhandled `Exception`s — full traceback logged, then `send_error(500)` attempted on a dead socket, polluting `journalctl -u audiobook-proxy` and triggering false-alarm 500 cascades in background monitors. Both exception classes are now caught explicitly before the generic handler: a single info line is logged without traceback and no response write is attempted on the closed socket
- **Behavioral proof of v8.3.6 streaming threshold-cap fix**: iOS autoplay policy prevents Selenium-driven BrowserStack harnesses from exercising `HTMLMedia.play()` (synthetic `dispatchEvent` clicks do not satisfy WebKit's user-activation gate), leaving the v8.3.6 cap at `library/web-v2/js/streaming-translate.js:534` without behavioral proof on real iOS. `library/tests/test_streaming_threshold_cap.py` loads the shipped JS source, extracts the threshold expression, and runs it through a Node subprocess with synthetic `(completed, total, rawThreshold)` inputs — asserts Qing's regression case (`total=5, raw=6 → threshold=5 → STREAMING`), edge cases (tiny chapters, unknown totals), and `grep`-asserts the cap at lines 273/339 remains in source to catch future rollbacks. 8 tests, 0.16s
- **Bilingual `pairVttCues` test aligned with v8.3.3 src-stub synthesis**: `test_target_only_is_dropped` asserted that empty-src + orphan-tgt returned `[]`, but v8.3.3 task #25 intentionally changed `pairVttCues` to synthesize empty-text source stubs so pre-v8.3.2 chapters missing `source_vtt_content` still render the 双语文字记录 panel. Replaced with `test_target_only_synthesizes_source_stubs` (explicit proof that each orphan target cue produces a synthetic source stub with matching timing and empty text) and `test_both_empty_still_empty` (double-empty input returns `[]`)
- **Stray `f""` with no placeholders in `playwright_ios_repro`**: the `TOTP generated (rotates every 30s)` log line was an `f`-string with no placeholders — ruff `F541` / pylint `W1309`. Replaced with a plain string literal
- **Bilingual transcript side-panel — target language on top, auto-scroll anchors on target, user scroll no longer snaps back**: `library/web-v2/css/i18n.css` flips the mobile media query `@media (max-width: 720px)` from `flex-direction: column` to `flex-direction: column-reverse` so the target-language column (中文) renders visually above the source column (原文) without changing DOM order — on mobile single-column the reader's primary language leads, matching the desktop side-by-side layout's implicit Y-alignment. `library/web-v2/js/subtitles.js::highlightTranscriptCue` now prefers the `.col-target` match as the `scrollIntoView` anchor (both columns share the same Y-offset on desktop so either works; on mobile this centers the reader's language instead of the source). A `_userScrolledAt` timestamp captured on `touchstart` / `wheel` / `pointerdown` inside `#transcript-content` suppresses auto-scroll for `USER_SCROLL_PAUSE_MS` (4 s) after any user-initiated scroll, so reading ahead or looking back no longer snaps to the playhead on the next `timeupdate`. Cachebusts `i18n.css` + `subtitles.js` to `v=1776891943` in `shell.html` and `index.html`
- **Bandit B608 + Semgrep ERROR suppressions relocated to correct line per tool scoping**: 17 Bandit B608 MEDIUM findings and 5 Semgrep ERROR findings persisted despite existing `# nosec B608` / `# nosemgrep` comments because both tools scope suppressions to the exact source line of the flagged AST node — the f-string literal — not the surrounding `.execute(` call. Moved all suppressions to the f-string line with justifications pointing at the allowlist source (code-defined dict `_RELATED_TABLES`, literal tuple `("authors","narrators")`, `VALID_STATUSES`/`VALID_PRIORITIES` regex validation, `ALLOWED_LOOKUP_TABLES` whitelist check) plus proof that every value is parameter-bound via `?`. Files touched: `admin_activity.py`, `admin_authors.py`, `roadmap.py`, `utilities_crud.py`, `import_to_db.py`, `migrations/migrate_to_normalized_authors.py`, `scanner/utils/db_helpers.py`, `scripts/cleanup_audiobook_duplicates.py`, `scripts/find_duplicates.py`, `scripts/populate_sort_fields.py`. Post-fix: Bandit 0 HIGH / 0 MEDIUM, Semgrep 0 ERROR
- **Broken MD051 link fragments in `docs/MULTI-LANGUAGE-SETUP.md`**: TOC entries at lines 13–14 referenced stale anchors `#vastai-gpu-for-whisper-stt-and-xtts-tts` and `#runpod-serverless-gpu` — the actual `###` headings were renamed to `Serverless Whisper STT (RunPod and Vast.ai — peer providers)`, `Vast.ai XTTS (Voice-Cloning TTS)`, and `Optional RunPod XTTS endpoint`. TOC now matches the slugified heading IDs; `markdownlint` is silent on this file
- **Python 3 `except A, B:` alias-binding bug purged codebase-wide (59 sites across 38 files)**: Python 3 silently parses `except ValueError, TypeError:` as `except ValueError as TypeError:` — it binds the exception object to a local variable named `TypeError`, shadowing the built-in class. The `TypeError` exception is NOT also caught; any real `TypeError` raised inside the `except` block escapes unhandled. This is Py2 syntax that Python 3 accepts with different semantics — no warning, no error. Phase 6 of the prior audit caught and fixed 2 sites in `streaming_translate.py` (discovered via CodeQL on that file); a follow-up audit pass surfaced **59 more sites** across `library/backend/api_modular/*` (admin_activity × 6, user_state × 5, utilities_system × 6, utilities_db × 2, translations × 3, maintenance, `__init__`, duplicates, auth_registration, utilities_conversion × 2, maintenance_tasks/db_vacuum, utilities_ops/{hashing, library, `_subprocess`}), `library/scanner/metadata_utils` × 3, `library/web-v2/proxy_server`, `library/common`, `library/localization/stt/local_gpu_whisper`, `library/backend/migrations/backfill_asins`, `library/scripts/*` (populate_sort_fields, librivox_downloader, google_play_processor × 3, backfill_enrichment, verify_metadata × 2, enrich_single × 3, enrich_from_isbn, enrich_from_audible, enrichment/{provider_audible, provider_google, provider_openlibrary}), and two `library/tests/*` files (conftest, test_auth_email_and_config, test_player_navigation_persistence). All converted to the correct `except (A, B):` parenthesized-tuple form. New `library/tests/test_no_py2_except_comma.py` guards against regression with three checks: a whole-tree scan that fails on any new `except A, B:` outside tuple form, plus two meta-tests that verify the regex matches known-bad patterns (including dotted namespaces like `subprocess.TimeoutExpired` and `urllib.error.URLError`) and doesn't false-match the correct parenthesized form. Behavior delta on existing tests: **zero** — because Python 3 was already silently catching only the first exception, and no test exercised the (unreachable) fallthrough from the silently-alias-bound second class. Full pytest suite went 4,691 → 4,694 (+3 guard tests), 88 skipped unchanged, 0 failed, 0 errors
- **`audiobooks` service account UID/GID canonicalized to 935/934 across all environments**: `install.sh` previously created the `audiobooks` user with `useradd --system` (no explicit UID), letting each distro pick from its `--system` range — yielding UID 935 on the prod host, UID 951 on QA/dev VMs, and UID 1000 baked into the `Dockerfile`. When the Docker container bind-mounts host volumes (`/var/lib/audiobooks/docker-data`, `/srv/audiobooks/Library`, `/srv/audiobooks/Supplements`), the UID mismatch makes the container treat existing host files as alien — triggering the Dockerfile's scanner init path on every restart (~45-min cold-boot against a 2,000-book library), and producing new files the host service account can't read back. `install.sh` now creates the user/group with explicit `--uid 935 --gid 934` (emitting a WARN-and-continue instead of failing if an existing install has a different UID, since renumbering files in-place is a separate migration), and refuses to proceed if UID 935 or GID 934 are claimed by a different account on the host. `Dockerfile` hardcodes the same `--uid 935 --gid 934` so container-side bind mounts inherit host chown stamps without translation. New helper `scripts/migrate-audiobooks-uid.sh` realigns existing installs to canonical: stops `audiobook.target`, runs `usermod -u` + `groupmod -g`, `chown -R` every file in `/opt/audiobooks`, `/etc/audiobooks`, `/var/lib/audiobooks`, `/srv/audiobooks` from the old UID/GID to the canonical values, then restarts services. Supports `--dry-run` for preview. QA (UID 951 → 935), dev VM (UID 951 → 935), and the Docker container's next build (UID 1000 → 935) all realign via this script after upgrade
- **`transfer.py` export/import preserves streaming-audio + chapter-audio file contents across books**: `library/localization/transfer.py::_write_tarball` previously used flat arcnames (`audio/{basename}` for chapter audio, `audio/streaming/{basename}` for segments) — every book's consolidated `chapter.webm` collided to one tar entry and thousands of streaming segments named `seg0000.webm` / `seg0001.webm` across books all overwrote each other in the tarball. On a real QA export (1,465 `completed` streaming segments), only ~232 distinct files reached the tar; the other ~85% were silently discarded. Fixed: arcnames now nest by `(audiobook_id, chapter_index, locale, segment_index)` — `audio/streaming/<id>/ch<NNN>/<locale>/seg<NNNN>.<ext>` and `audio/chapter/<id>/ch<NNN>/<locale>/chapter.<ext>`. Import reconstructs the nested arc key, extracts to the target env's streaming-audio dir at a path using the **remapped** audiobook_id (`<streaming-audio-dir>/<new_id>/ch<NNN>/<locale>/seg<NNNN>.<ext>`), and writes that prod-local path into `streaming_segments.audio_path`. Extensions (`.opus` vs `.webm`) preserved from source so codec detection keeps working on the target. Import also accepts legacy flat-format archives (falls back to `audio/streaming/{basename}` when the nested arcname isn't present) so in-flight tarballs from pre-fix `transfer.py` still import correctly — just with fewer files surviving. `test_localization_transfer.py` adds 3 regression tests: `test_streaming_segments_no_basename_collisions` (12-segment matrix across 2 books × 2 chapters × 3 segments, asserts all 12 distinct tar entries + all 12 files arrive on target with correct contents and remapped audiobook_ids), `test_chapter_audio_no_basename_collisions` (two books each with `chapter.webm` both survive), `test_import_accepts_legacy_flat_arcnames` (backward compat)
- **CodeQL path-injection + log-injection defense in streaming pipeline**: `library/backend/api_modular/streaming_translate.py` adds a general `_safe_join_under(base, *parts)` helper that resolves the target and verifies `is_relative_to(base)` before returning — used at the chapter-audio consolidation output (`<streaming-audio-root>/<book_id>/ch<NNN>/<locale>/chapter.webm`). The `/api/translate/chapter-complete` worker callback now runs `translated_vtt_path` and `source_vtt_path` through a local resolver that rejects any path outside `_streaming_subtitles_root` with HTTP 400, matching the pre-existing `_validate_audio_path` pattern — prevents a compromised worker from persisting DB rows that point at arbitrary filesystem locations. All `logger.*` call sites that interpolated tainted-flow values now cast ints with `int(...)` and route strings through the existing `_safe_log_value`, satisfying CodeQL `py/log-injection`. Also fixes two latent exception-handler bugs where `except ValueError, OSError:` / `except OSError, ValueError, subprocess.TimeoutExpired:` were parsed as alias bindings (Py2 syntax), not tuples — parenthesized so real `OSError` / `subprocess.TimeoutExpired` are actually caught. `docker/whisper-server/whisper_server.py:94` wraps `size` in `int(...)` for the same reason. `library/web-v2/proxy_server.py` partial-SSRF flagged at line 414 dismissed as a false positive — host is hardcoded `127.0.0.1:$API_PORT`, path is CRLF/null-stripped and allowlist-matched, netloc is re-verified against a hardcoded expected value before `urlopen`. Test fixture path regression fixed in `library/tests/test_streaming_priority_queue.py` (relative `open("library/backend/schema.sql")` → `Path(__file__).parent.parent / "backend" / "schema.sql"` so it works under `pytest library/`); `test_streaming_translate.py::test_chapter_insert_writes_subtitles_and_audio` updated to use VTT paths inside `AUDIOBOOKS_STREAMING_SUBTITLES_DIR` instead of `/tmp/*.vtt`
- **Coverage restored for phase-6 security helpers**: the path-injection + log-injection hardening added `_safe_join_under` and expanded `_validate_audio_path` / `chapter-complete` with new rejection branches but shipped with zero direct unit tests — `streaming_translate.py` coverage dropped from 89% to 82% after phase 6. Added `TestSafeJoinUnder` (10 tests: happy-path single/multi/numeric components, parent traversal, nested traversal, absolute-path reset, null-byte in first/later component, dotted filename, resolved-path identity), `TestValidateAudioPath` (6 tests: `None` passthrough, `_streaming_audio_root=None` rejection, absolute/relative inside-root acceptance, outside-root rejection, traversal rejection via `monkeypatch`), and 4 new `TestChapterComplete` tests covering HTTP-400 rejection of `audio_path` outside root, `translated_vtt_path` outside root, `source_vtt_path` outside root, and null-byte VTT paths that raise `OSError` during `Path.resolve()`. Net +20 tests; `_safe_join_under` goes from 0% → 100% coverage; chapter-complete path-rejection branches now exercised
- **Stale legacy translation-queue failures no longer surface as "字幕生成失败" toasts on first book-open**: `library/localization/queue.py::get_book_translation_status` now collapses `pending` / `processing` / `failed` rows on non-English locales to a new `{"state": "deferred", "reason": "streaming_pipeline"}` payload, masking pre-streaming-era batch-pipeline crashes from the UI. The legacy worker hasn't drained the queue for months — all 1,844 `translation_queue` rows on QA were stamped `failed` with `"No STT provider configured"` on 2026-04-19 — so every first-open of an untranslated zh-Hans book was rendering that stale error via `subtitles.js::renderGenStatus`'s `phase === "error"` branch. `subtitles.js::showGenBanner` + `startGenPoll` add explicit `state === "deferred"` handlers that call `hideGenBanner()` without polling or auto-queueing; `streaming-overlay.js` remains the canonical progress surface for streaming-supported locales. Completed legacy rows still pass through unchanged (legitimate VTT-on-disk cases). `'en'` locale is exempt — STT failures for English are real, not stale. `test_localization_queue_coverage.py` adds 4 regression tests covering failed / pending / completed non-en and completed en passthrough. Cachebusts `subtitles.js` to `v=1776894450` in `shell.html`
- **Docker: `docker-compose.yml` image tag synced to 8.3.7**: `ghcr.io/theboscoclub/audiobook-manager` was pinned to `8.3.1` — users pulling the compose file would receive a four-patch-release-old image instead of the current release
- **Docker: `python:3.14-slim` base digest refreshed + residual CVEs documented**: `Dockerfile` `FROM` digest updated from `sha256:bc389f7d` (2026-04-16) to `sha256:3989a23f` (2026-04-22); base-image CVE delta: **15 CRIT / 62 HIGH → 0 CRIT / 6 HIGH**. Full application image delta (python base + ffmpeg/mesa/mbedtls apt deps): 15 CRIT / 62 HIGH → 15 CRIT / 60 HIGH. Remaining 15 CRIT are in `ffmpeg` (`CVE-2026-40962` — fix deferred by Debian), Mesa libgbm (`CVE-2026-40393` — no GPU in container), and mbedTLS (`CVE-2026-34873`, `CVE-2026-34875` — not our TLS stack; OpenSSL is). All residuals are `fix_deferred` or `affected` with no Debian Trixie patch available as of 2026-04-22; full acceptance rationale documented in `Dockerfile` comment block. `ARG APP_VERSION` default updated from stale `8.3.2` → `8.3.7`

## [8.3.6] - 2026-04-20

### Added

- **On-device debug overlay for Chrome iOS / Safari iOS diagnostics**: `library/web-v2/js/debug-overlay.js` surfaces a fixed-position QA panel (activated by `?debug=1`, persists in `localStorage.debugOverlay`) that mirrors DevTools signals end users on iOS cannot see otherwise — MSE codec support matrix, `window.streamingTranslate` state snapshot, `<audio>` element readyState/networkState/currentSrc, and the tail of a rolling event log fed by `window.__debugLog(kind, payload)`. Carries `translate="no"` so Chrome iOS's Google Translate overlay doesn't mangle the snapshot text on Qing's screenshots. Loaded from `shell.html` with `data-cfasync="false"` so Cloudflare Rocket Loader can't defer it past the moments we need to capture

### Fixed

- **Streaming stuck on spinner for chapters with fewer than 6 segments**: `library/web-v2/js/streaming-translate.js::onBufferProgress` compared `completed >= threshold` using an uncapped `threshold` (`data.threshold || BUFFER_THRESHOLD` where `BUFFER_THRESHOLD = 6`). For any chapter whose segment count is less than 6 (e.g. a short opener at ~150 s yielding `total: 5`), the condition `completed >= 6` can never be true — the BUFFERING → STREAMING transition only fires via `onChapterReady` (full consolidation), and until then the user sees 排队中 / "queueing" for the full chapter. Capped `threshold = total > 0 ? Math.min(rawThreshold, total) : rawThreshold` to match the cap pattern already in place at `updateProgress()` and the phase/message branch. Surfaced on Qing's zh-Hans prod demo (Book with `total=5, threshold=6` → 5-minute spinner before consolidation kicked it loose)
- **Cachebust stamps bumped** on `shell.html`/`index.html`/`utilities.html` `?v=` query strings so the patched `streaming-translate.js` and new `debug-overlay.js` bypass browser/CDN caches on upgrade. Manual bump pending Task #51 (automated bump in `upgrade.sh`/`install.sh`)

## [8.3.2] - 2026-04-20

### Added

- **Streaming translation worker systemd unit**: new `audiobook-stream-translate.service` runs `scripts/stream-translate-worker.py` continuously as `audiobooks:audiobooks`. Wired into `audiobook.target`, `scripts/install-manifest.sh`, `install.sh`, and `upgrade.sh`. Enforced by `library/tests/test_stream_translate_wiring.py` per `.claude/rules/upgrade-consistency.md`
- **Three-tier priority queue for seek-beyond-buffer**: P0 fills ~3 min forward of cursor, P1 continues forward chase to chapter end, P2 back-fills the gap between prior translated tail and cursor. `handle_seek_impl` and `stop_streaming_impl` implement the model; worker claim order already orders by priority
- **Per-segment Chinese TTS** (edge-tts, `zh-CN-XiaoxiaoNeural`, CPU): worker produces `streaming-audio/<book>/ch<NNN>/<locale>/seg<NNNN>.opus` per segment; chapter-level consolidation concatenates segments into `chapter_translations_audio` via ffmpeg concat demuxer
- **MSE-based player audio swap**: `MediaSource` + `SourceBuffer` chain feeds per-segment opus audio for seamless playback; English `<audio>` element pauses when the Chinese pipeline is live
- **`/streaming-audio/<book>/<ch>/<seg>/<locale>` API route** with path-traversal guard, locale whitelist, and `Range` request support
- **Phase reporting**: `_derive_phase()` returns `idle|warmup|gpu_provisioning|buffering|streaming|error`; included in session response and `buffer_progress` WebSocket events
- **Polling fallback** when WebSocket disconnects or misses events (3 s cadence)
- **Bilingual navigable side panel**: source (en) + target (zh-Hans) cue pairs side-by-side; click-to-seek; current cue highlighted on `timeupdate`. Panel evolved from the existing `#transcript-panel` into a two-column layout; pairs cues by monotonic time-window overlap (`pairVttCues`) to handle 1:1, 1:n, and n:1 translation merges/splits
- **Dual-provider serverless STT dispatch**: `library/localization/pipeline.py::_remote_stt_candidates(workload)` routes transcription calls to RunPod and/or Vast.ai serverless endpoints with asymmetric min_workers (STREAMING warm pool `min_workers>=1` for real-time latency, BACKLOG cold pool `min_workers=0` for cost). Providers are peers — selected per availability/price, not primary+fallback. `WorkloadHint` enum propagates the routing hint from callers (streaming worker, `batch-translate.py`) through the pipeline
- **i18n keys** for `streaming.phase.*` and `streaming.bilingual.*` in the two targeted locales (`en`, `zh-Hans`)
- **`streaming_segments.audio_path` consumed end-to-end**: the column was added by migration `data-migrations/003_streaming_segments.sh` in v8.3.1 but had no writer or reader; v8.3.2 is the first release where the worker writes per-segment opus paths and the `/streaming-audio` route reads them
- **`streaming_segments.source_vtt_content` column** (`library/backend/migrations/022_streaming_source_vtt.sql` + matching `data-migrations/006_streaming_source_vtt.sh`): persists the English (source) VTT next to the translated VTT for every streaming segment so chapter consolidation can write the bilingual pair into `chapter_subtitles`. Migration is idempotent (`PRAGMA table_info` check), boundary-gated (`MIN_VERSION="8.3.2"`), and runs automatically via `upgrade.sh::apply_data_migrations`
- **`POST /api/translate/segment-complete` accepts `source_vtt_content`**: worker callback now persists both VTTs in a single round-trip; backwards-compatible (the field is optional, legacy workers continue to populate translated-only)
- **`streaming_segments.retry_count` column** (`library/backend/migrations/023_streaming_retry_count.sql` + `data-migrations/007_streaming_retry_count.sh`, `MIN_VERSION="8.3.2"`, idempotent `PRAGMA table_info` guard): tracks per-segment retry attempts so transient STT/DeepL failures can be recovered instead of dead-lettered on first error

### Fixed

- **Streaming translation collapsed every book to a single chapter**: `streaming_translate.py::_get_chapter_count` resolved chapter counts via `translation_queue.total_chapters`, which is `0` for any book that has never been queued for translation. Downstream callers used `count or 1`, so a 24-hour book like *Sapiens* dispatched 1836 segments to chapter 0 with no chapter boundaries — the entire library became a single virtual chapter for streaming purposes. Replaced with `_resolve_chapter_count(db, audiobook_id)` whose resolution order is (1) in-process memo, (2) `audiobooks.chapter_count` column, (3) `ffprobe -show_chapters` on `audiobooks.file_path` with the result `UPDATE`d into the column for future calls. Chapters are a property of the audio file, not of a translation attempt — `chapter_count` now lives on the `audiobooks` table where it belongs. `request_streaming_translation` and `handle_seek` wrap the resolver in `try/except ValueError` and return HTTP 500 with sanitized logging when resolution fails (missing book row, missing `file_path`, ffprobe reports zero chapters)
- **`audiobooks.chapter_count` column** added by `data-migrations/004_audiobook_chapter_count.sh` (idempotent `ALTER TABLE` guarded by `PRAGMA table_info` check, `MIN_VERSION=8.3.2`). Scanner populates it at ingest via `metadata_utils.run_ffprobe()`'s new `-show_chapters` flag and the new `chapter_count` field on the metadata dict consumed by both `import_to_db.import_audiobooks` (bulk import) and `db_helpers.insert_audiobook` (single import). Existing rows backfill lazily on first streaming request — one `ffprobe` call per book, then never again
- **Upgrade installed new systemd units but never enabled them**: `upgrade.sh::enable_new_services` was gated on `MAJOR_VERSION=true`, so patch upgrades (e.g., 8.3.1 → 8.3.2) shipped new units (`audiobook-stream-translate.service`) that were copied into `/etc/systemd/system/` but never symlinked into `audiobook.target.wants/`. Services started this boot but evaporated after host reboot — `qalib.thebosco.club` returned Cloudflare 502 because nothing started at boot. Gate removed: `enable_new_services` now runs on every upgrade (it is idempotent). The function was also expanded to enable standalone units outside the target's `Wants=` set (`audiobook-enrichment.timer`, `audiobook-shutdown-saver.service`). `install.sh`'s enable loop gained `audiobook-shutdown-saver.service` for symmetry (latent reboot-time staging-flush bug fixed as collateral). Regression locked in by `library/tests/test_service_enablement_unconditional.py` (unconditional call, Wants= parser intact, standalone-timer coverage, install.sh parity, target Wants= census, unit-file sanity)
- **Caddy reverse-proxy template routed every hostname to Docker**: `caddy/audiobooks.conf` shipped a single `:8084` site pointing at `https://localhost:8443` (the Docker app port), so any Cloudflare tunnel ingress entry on any hostname silently landed on the Docker container rather than the native app — the opposite of the documented production architecture. Template rewritten to two sites (`:8084` → `__NATIVE_PORT__`, `:8085` → `__DOCKER_PORT__`) with `handle_errors 502 503` on both so a dead upstream returns the maintenance page instead of a raw connection error. `install.sh` and `upgrade.sh` substitute both placeholders from `AUDIOBOOKS_WEB_PORT` (native, default `8443`) and `AUDIOBOOKS_DOCKER_PORT` (Docker, default `8443`). Dual-stack hosts (QA) override `AUDIOBOOKS_WEB_PORT=8090` in `audiobooks.conf` so the two sites diverge (native `:8090`, Docker `:8443`); single-stack hosts keep both at `8443` with no behavioral change. Discovered when `qalib.thebosco.club` (meant to mirror production's native-app experience) was proven by curl fingerprinting to be hitting the Docker container all along
- **Streaming translation module never imported**: 7 `except ValueError, TypeError:` sites in `library/backend/api_modular/streaming_translate.py` used Py2 tuple syntax and raised `SyntaxError` at module load. Every `/api/translate/*` endpoint silently 404'd. All sites rewritten to `except (ValueError, TypeError):` with a regression test that asserts the module imports AND that no Py2 except-tuple syntax exists anywhere in the file
- **Upgrade left retired scripts on disk**: `upgrade.sh::upgrade_application` copies `scripts/` with a per-file `cp` loop (no `rsync --delete`), and `audit_and_cleanup` only had a hand-maintained allowlist in section (d) — not a diff against project `scripts/`. When Phase 3 retired `fleet-watchdog.sh`, `translation-check.sh`, and `translation-daemon.sh`, the files persisted in `/opt/audiobooks/scripts/` after upgrade on every existing install. Same class of bug as the systemd-enablement gap above: drift between what the project ships and what survives on installed systems. New section (g) in `audit_and_cleanup` diffs `${target}/scripts/` against `${PROJECT_DIR}/scripts/` and removes any file not present in the project (mirroring section (c) for systemd units), with an allowlist for root-level scripts that get copied into `target/scripts/` from the project root (`upgrade.sh`, `migrate-api.sh`). Regression locked in by `library/tests/test_upgrade_orphan_script_cleanup.py` (5 tests: diff mechanism, PROJECT_DIR consultation, root-level allowlist, per-removal logging, retired-script sanity)
- **Filesystem reconciler ran in report mode on every install and upgrade**: `install.sh` and `upgrade.sh` both invoked `scripts/reconcile-filesystem.sh` with `RECONCILE_MODE="${RECONCILE_MODE:-report}"`, so drift the reconciler was explicitly authored to remove (entries in `PHANTOM_PATHS`, legacy config overrides matched by `CONFIG_CANONICAL_DEFAULTS`, stale `__pycache__` trees) was only ever logged, never cleaned. QA surfaced this as two lingering empty phantom cover directories (`/opt/audiobooks/library/web-v2/covers`, `/opt/audiobooks/library/covers`) that survived every 8.x upgrade despite being in `PHANTOM_PATHS` since the manifest landed. Both invocation sites now default to `enforce` — the whole point of those manifest arrays is "this must not survive" so report-only was never the right default. Operators can still opt in to a read-only audit via `RECONCILE_MODE=report ./upgrade.sh ...`. The reconciler itself keeps defaulting to `report` when invoked directly (`bash scripts/reconcile-filesystem.sh`) so ad-hoc debugging runs don't mutate state. Regression locked in by `library/tests/test_install_manifest_reconciler.py::test_invoker_defaults_to_enforce_mode` parametrized over both scripts
- **Translated playback halted at end of chapter 0**: `library/web-v2/js/shell.js`'s `<audio>`-`ended` handler stopped at the first translated chapter because it only knew about a single `translated-audio` URL. `playBook` now sorts the full `translatedEntries` payload by `chapter_index` and stores it on the controller, and the `ended` handler walks `translatedChapterIdx` forward, swaps `audio.src` to `/translated-audio/{next}/{locale}`, reloads subtitles for the new chapter, and `await play()`s. `subtitles.load(...)` is invoked with the actual first `chapter_index` from the sorted list rather than a hard-coded `0`, so books whose first translated chapter is non-zero (delayed translation, partial coverage) load the right cues
- **Bilingual transcript panel (双语文字记录) rendered empty after streaming consolidated**: the streaming worker only ever wrote translated VTT into `streaming_segments.vtt_content`, so when `_consolidate_chapter` promoted a fully-streamed chapter into `chapter_subtitles`, the source-language ('en') row was never created and the bilingual side panel had nothing to pair against the translated cues. Fix is two-sided: (1) backend captures both VTTs (`scripts/stream-translate-worker.py` returns `(source_vtt, translated_vtt)`, `streaming_segments.source_vtt_content` stores English alongside the translation, `_consolidate_chapter` builds two consolidated VTTs via a `_merge_segment_vtts(column)` inner helper and writes both translated + 'en' rows mirroring the chapter-complete prefetch pattern at `streaming_translate.py` lines 1217-1223); (2) frontend defends against legacy chapters (consolidated before this fix shipped) where the 'en' row may still be missing — `library/web-v2/js/subtitles.js::pairVttCues` synthesizes blank source stubs `{startMs, endMs, text: ""}` per target cue when `src.length === 0 && tgt.length > 0` so the panel renderer creates both columns and click-to-seek still works on the translated side
- **Streaming bitmap returned `all_cached: true` alongside `total: 0`**: `library/backend/api_modular/streaming_translate.py::_get_segment_bitmap` short-circuited on cached subtitles before counting `streaming_segments` rows, so progress consumers received a contradictory shape (player code defensively read `bitmap.completed.length` from a string `"all"`, polling fallback couldn't decide whether to keep watching). Refactored to always count rows, then derive `streaming_done = (total > 0 and len(completed) == total)` and `batch_cached = _has_cached_subtitles(...)` independently. New `cache_source` field — `"batch"` / `"streaming"` / `"both"` / `"none"` — gives operators a clear diagnostic of which pipeline produced the playable artifacts. `all_cached = batch_cached or streaming_done` keeps the existing frontend contract (`streaming-translate.js:321` only reads `all_cached`) intact while making the underlying counts honest. Locked in by `library/tests/test_streaming_translate.py::TestGetSegmentBitmap` (6 tests covering each `cache_source` combination)
- **Reverse proxy swallowed upstream 500s with no traceback**: `library/web-v2/proxy_server.py` had a bare `except Exception as e: self._send_json_error(500, "Internal Server Error", str(e))` that returned a generic 500 to the client and logged nothing — when a proxied API endpoint raised, journalctl showed only the request line, never the upstream cause. Both `URLError` (503 Service Unavailable) and bare `Exception` (500) handlers now emit a `[PROXY]`-prefixed `log_message` with `traceback.format_exc()` for the unhandled case before sending the JSON response, so silent 500s become diagnosable in `journalctl -u audiobook-proxy`
- **Failed streaming segments never retried**: `scripts/stream-translate-worker.py`'s exception handler flipped a segment straight from `state='processing'` to `state='failed'` on the first error — a transient RunPod timeout or DeepL 503 permanently dead-lettered that window of audio, leaving a gap in the cursor buffer the user could never recover from without re-requesting the entire session. Worker failure path now uses an atomic SQL `CASE` — if `retry_count + 1 < 3`, requeue to `state='pending'` with `retry_count = retry_count + 1` and clear `worker_id` + `started_at` so another worker can claim it; if already at 3, promote to `state='failed'`. `claim_next_segment` skips rows where `COALESCE(retry_count, 0) >= 3` via `LEFT JOIN` defense-in-depth, so exhausted rows can't re-enter the claim pool even if a stale worker somehow flips them back
- **`streaming_segments.error` column never populated**: the column existed since v8.3.1 but no code path ever wrote to it — operators debugging a stuck session had to grep journalctl for worker logs, which rotate. Worker exception handler now persists `f"{type(e).__name__}: {e}"[:500]` into the column in the same `UPDATE` that decides requeue-vs-dead-letter, so post-mortem of any failed segment is one `SELECT id, error, retry_count FROM streaming_segments WHERE state='failed'` away
- **Frontend showed buffering spinner on terminal error**: `library/web-v2/js/streaming-translate.js::onBufferProgress` ignored `data.phase === "error"` (the backend signals this via `_derive_phase()` when `failed > 0`), so after a session's segments exhausted retries the UI kept spinning indefinitely with no user-visible indication anything had gone wrong. New branch in `onBufferProgress` surfaces `t("streaming.phase.error")` as the overlay message, updates the progress meter one last time, then collapses to `IDLE` after 3 s via `setTimeout(enterIdle, 3000)`. Copy updated in `library/locales/en.json` and `library/locales/zh-Hans.json` from the old optimistic "retrying…" text to terminal-semantics "Translation error — please try again" / "翻译出错——请重试" (the old copy accurately described `phase=error` as transient back when retries didn't exist; now that phase=error is reached only after three exhausted attempts, the copy had to match the new semantics)
- **Tab close / player close / book switch did not drain streaming session**: streaming sessions owned GPU warm-pool time and DeepL character quota — if a user closed the tab, clicked the player's `sp-close` button, triggered MediaSession stop, or switched to a different book, the backend worker kept claiming pending segments for a session no user would ever consume. New `drainStreaming(useBeacon)` helper in `streaming-translate.js` posts to `/api/translate/stop` via `navigator.sendBeacon` (unload path, survives page tear-down) or `fetch({keepalive:true})` (interactive path). `window` listeners (`pagehide`, `beforeunload`) fire the beacon variant; `shell.js::close()` and `shell.js::playBook()` (on book-id change) fire the fetch-keepalive variant. Exposed as `window.streamingTranslate.drain` for future callers
- **`/api/translate/stop` did not actually stop the worker**: `library/backend/api_modular/streaming_translate.py::stop_streaming_impl` flipped pending rows from `priority=1` to `priority=2` — the worker's `ORDER BY priority ASC` claim would still pick them up just as readily, the downgrade was a no-op dressed as a stop. Replaced with `DELETE FROM streaming_segments WHERE session_id=? AND state='pending'` so the rows are physically gone from the claim pool. In-flight `state='processing'` rows are left alone (the worker will finish or fail them organically; the retry-count logic now handles the fail case correctly). Defense-in-depth: `claim_next_segment`'s `LEFT JOIN streaming_sessions` filters out rows whose session `state IN ('stopped', 'cancelled', 'error')` — even if something somehow re-inserts pending rows under a stopped session, the worker will skip them
- **`data-migrations/*.sh` function-pattern scripts silently no-op'd during upgrade and fresh install**: migrations 003 (`streaming_segments`), 006 (`streaming_source_vtt`), and 007 (`streaming_retry_count`) each define a `run_migration()` bash function and rely on the dispatcher to invoke it after sourcing. Both `upgrade.sh::apply_data_migrations` and `install.sh`'s data-migration loop did `source "$migration"` without calling the function — `source` runs top-level commands but does NOT call defined functions. Result: QA upgrades ran the dispatcher, logged "Applied: N/N migrations", and left the `streaming_segments` table missing the expected columns. First streaming request after the upgrade crashed the worker with `sqlite3.OperationalError: no such column: s.retry_count`, dead-lettering the entire session. Both dispatchers now `declare -F run_migration` after sourcing; if the function exists they invoke it and `unset -f run_migration` to prevent cross-migration contamination (if migration N defines the function but N+1 doesn't, N+1 must not inherit N's logic). Idempotent: migrations already use `PRAGMA table_info` guards, so re-running a successful migration is a no-op. Also supports the legacy top-level-command pattern (migrations 001, 002, 004, 005) unchanged — if `run_migration` isn't defined, only the `source` side runs
- **`upgrade.sh` no-updates branch did not run data migrations**: when `check_for_updates` reported "already at target version", the early-exit path ran `apply_schema_migrations` but skipped `apply_data_migrations`, so a re-run upgrade couldn't recover from a prior silent no-op. Early-exit path now runs both dispatchers — re-running `./upgrade.sh` against the same version is now a recovery mechanism that brings the DB fully into spec
- **Test harnesses couldn't source `upgrade.sh` without triggering arg-parsing**: added `UPGRADE_SH_SOURCE_ONLY=1` guard that returns (when sourced) or exits 0 (if invoked directly) before the arg-parser runs, enabling white-box testing of individual functions. Used by the new regression test `library/tests/test_upgrade_data_migration_dispatch.py` which forges a pre-fix `streaming_segments` shape, invokes `apply_data_migrations`, and asserts `retry_count` is actually added. Mutation-proven: the test fails on a pre-fix snapshot of the dispatcher and passes on the current code
- **`apply_data_migrations` now honors `DB_PATH` env var**: precedence is explicit `DB_PATH` > `/etc/audiobooks/audiobooks.conf::AUDIOBOOKS_DATABASE` > `${AUDIOBOOKS_VAR_DIR}/db/audiobooks.db` fallback. Lets test harnesses and operators target a specific DB without editing the host conf

### Removed

- **Dedicated-instance Vast.ai Whisper path**: retired `library/localization/stt/vastai_whisper.py` (`VastaiWhisperSTT`), `AUDIOBOOKS_VASTAI_WHISPER_HOST`/`PORT` env vars, `scripts/translation-daemon.sh`, `scripts/fleet-watchdog.sh`, `scripts/translation-check.sh`, `scripts/teardown-gpu.sh`, `systemd/audiobook-translate.service`, `systemd/audiobook-translate-check.{service,timer}`, `systemd/audiobook-fleet-watchdog.{service,timer}`, `etc/translation-env.sh.example`, `docs/GPU-FLEET-OPS.md`, and the `--vastai-host` flag on `scripts/batch-translate.py`. STT now runs exclusively through the dual-provider serverless pipeline (RunPod + Vast.ai serverless endpoints). Operators migrating from dedicated instances should configure `AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY` + STREAMING/BACKLOG endpoint pairs — see `docs/SERVERLESS-OPS.md`. Explicit `STT_PROVIDER=vastai` now raises a migration error pointing at `vastai-serverless`. The `audiobook-translations` CLI wrapper no longer exposes `start`/`stop`/`check` subcommands (there is no dedicated translation daemon to manage); it retains pause/resume/status/report/export/import against `audiobook-stream-translate.service` and the `translation_queue` table
- **`audiobook-translations` daemon-lifecycle subcommands**: removed `start`, `stop`, `check`, `--aggressive`, and `--no-ensure` — the retired dedicated-instance topology they managed no longer exists. Backlog drainage is now on-demand via `scripts/batch-translate.py` against serverless STT endpoints

### Security

- **`/streaming-audio` path-traversal guard**: resolve candidate via `Path.resolve()`, require the configured audio root to appear in `candidate.parents` or return `403`

## [8.3.1] - 2026-04-16

### Added

- **Local-GPU hardware compatibility guidance and cautionary tale**: `README.md` "Optional: Local GPU Transcription" and `docs/MULTI-LANGUAGE-SETUP.md` "Local GPU (Optional)" now carry a hardware compatibility matrix (✅ NVIDIA + CUDA, ✅ enterprise AMD Instinct/ROCm, ⚠️ consumer AMD Radeon RDNA 2/3 + ROCm known-unstable, ❌ integrated/low-VRAM) and the maintainer's first-person cautionary tale — an AMD Radeon 6800 XT + ROCm Whisper inference job crashed the host, wiped UEFI/BIOS configuration, and corrupted the on-disk working tree (recovered only because the project was pushed to GitHub). The maintainer does not have and cannot afford a known-good local AI GPU, so remote GPU (Vast.ai / RunPod) is the only path tested end-to-end
- **RDNA 2/3 runtime warning in `extras/whisper-gpu/setup.sh`**: the setup script now inspects the detected GPU name via `torch.cuda.get_device_name(0)` and, if it matches consumer Radeon RDNA 2/3 patterns (RX 66xx–69xx, 77xx–79xx, 7900 variants), prints the cautionary tale and requires an explicit `y` confirmation before installing the systemd service. The warning points at `docs/MULTI-LANGUAGE-SETUP.md` for the full context
- **Hardware Requirements section in `README.md`**: new top-level section with minimum/recommended CPU/RAM/disk/network/OS matrix and an "Optional: local GPU transcription" sub-matrix (NVIDIA, consumer AMD with RDNA 2/3 cautionary tale, enterprise AMD, Apple Silicon, CPU-only). Explicitly frames the maintainer's rig as "one possible configuration" rather than a minimum or recommendation
- **Hardware Requirements section in `docs/ARCHITECTURE.md`**: mirrors the README matrix at architectural depth, and adds a "The maintainer's rig" paragraph that points to `docs/reference-system.yml` and the in-app About page. Renumbered the document's TOC to accommodate the new section between Prime Directive and Component Architecture
- **Reference System snapshot (`docs/reference-system.yml`)**: a script-generated, honest snapshot of the exact machine the project is developed and smoke-tested on. Shipped in the repo and copied alongside `VERSION` by `install.sh` and `upgrade.sh` so live installs can serve it without shelling out to detection tools
- **`scripts/collect-reference-system.sh`**: reproducible generator for `docs/reference-system.yml`. Collects CPU/RAM/storage/GPU/OS via `/proc`, `lscpu`, `lsblk`, `lspci`, and `/etc/os-release`, emits valid YAML, and is idempotent so the file can be regenerated on hardware changes
- **`GET /api/system/reference-system` endpoint (`library/backend/api_modular/utilities_system.py`)**: serves the installed copy of `docs/reference-system.yml` as `text/plain`, with a 404 fallback if the file is missing (fresh install without the optional snapshot). Enables the in-app About page to fetch and display the snapshot
- **About page "Reference System" section (`library/web-v2/about.html`, `css/about.css`)**: hidden by default; the page fetches `/api/system/reference-system` on load and reveals the section only on a successful fetch. Raw YAML is rendered in an Art Deco styled `<pre>` block (monospace body, gold left border) framed explicitly as "one possible configuration, not a minimum or recommendation", with a pointer back to the README Hardware Requirements
- **Containerized Whisper server (`docker/whisper-server/`)**: `Dockerfile` (NVIDIA CUDA 12.2 + cuDNN 8 base, `faster-whisper==1.0.3`, `large-v3` model pre-downloaded into the image so cold starts don't re-pull 5 GB) and `whisper_server.py` (Flask/Gunicorn endpoint used by the streaming-translation pipeline). Pairs with the streaming translation pipeline introduced in v8.3.0 so remote GPU workers can be spun up from an OCI image rather than hand-assembled on each Vast.ai/RunPod instance
- **Streaming translation design docs as PDFs**: `docs/STREAMING-TRANSLATION.pdf` (English) and `docs/STREAMING-TRANSLATION.zh-Hans.pdf` (Simplified Chinese) — the v8.3.0 streaming pipeline design rendered via pandoc + xelatex for sharing and archival

### Changed

- **`extras/whisper-gpu/setup.sh` is vendor-neutral**: header comments and prerequisite-install hints now cover both NVIDIA + CUDA and enterprise AMD + ROCm, not ROCm-only. The `torch.cuda.is_available()` check works for both stacks (PyTorch exposes ROCm through the `cuda` API on AMD)
- **`library/localization/stt/local_gpu_whisper.py` docstring is vendor-neutral**: removed hardcoded "AMD Radeon GPU" assumption — points at `docs/MULTI-LANGUAGE-SETUP.md` for supported hardware
- **`docs/ARCHITECTURE.md` STT provider table**: `Local GPU` row now cites supported hardware classes and flags consumer Radeon RDNA 2/3 as unsupported, linking to `docs/MULTI-LANGUAGE-SETUP.md#local-gpu-optional`
- **Test suite fail-fast on missing `VM_HOST` (`library/tests/conftest.py`, `test_backoffice_integration.py`, `test_auth_integration.py`, `test_auth_ui_e2e.py`, `test_player_navigation_persistence.py`)**: `VM_HOST` and `VM_NAME` no longer default to a specific maintainer VM — they default to empty and any test that requires a VM skips cleanly without one. `--vm` CLI help and `deploy_to_vm` docstring now reference the `VM_HOST` env var instead of a specific hostname. `pytest.ini` integration marker description updated to match
- **`vm-test-manifest.json` is placeholder-driven**: `default_vm`, `test_environments[0]`, and `qa_vm` blocks now carry `<test-vm-name>` / `<test-vm-ip>` / `<qa-vm-name>` / `<qa-vm-ip>` / `<qa-vm-baseline-snapshot>` placeholders with a new `_example_note` key in each block explaining the replacement pattern. `python_version` requirement loosened from `3.14` to `3.11+` to match realistic installer expectations
- **QA test modules parameterized (`test-audiobook-manager-qa-app.md`, `test-audiobook-manager-qa-docker.md`, `test-audiobook-manager-qa-all.md`)**: VM host, VM name, SSH user/key, snapshot name, Docker container/image/mount paths all read from `vm-test-manifest.json` via `jq` at runtime — no hardcoded maintainer values. SSH credentials sourced from `QA_SSH_KEY` / `QA_SSH_USER` env vars

### Fixed

- **Personal IP/hostname/path scrub across the tree**: removed the maintainer's personal storage paths (e.g. `/hddRaid1/Audiobooks`, `/dasRaid0/...`), libvirt VM IPs (`192.168.122.{63,104,105}`), and VM hostnames (`{test,qa,dev}-audiobook-cachyos`) from tracked files. Live code now uses `${AUDIOBOOKS_DATA}` / `${AUDIOBOOKS_LIBRARY}` / `${AUDIOBOOKS_SOURCES}` indirection (including `install-manifest.json` path entries and the "Library directory readable" health check) and documentation uses placeholders like `<project-dir>`, `<test-vm-ip>`, `<qa-vm-name>`. Historical CHANGELOG entries, audit reports, and superpowers plans were genericized without losing descriptive intent
- **`upgrade.sh` copies `docs/reference-system.yml` to the install root**: parallel to the existing `VERSION` copy, so `/api/system/reference-system` works after an upgrade without requiring a fresh install or manual file placement
- **Auth blueprint split into six focused submodules**: `library/backend/api_modular/auth.py` (was ~4800 lines, Radon MI sprawling B) now delegates to `auth_admin.py`, `auth_account.py`, `auth_email.py`, `auth_recovery.py`, `auth_registration.py`, and `auth_webauthn.py`. All submodules read the module-level `_auth_db` via `LOAD_GLOBAL` at call time so monkeypatching `backend.api_modular.auth._auth_db` in tests works across every extracted route. `auth.py` is now 1351 lines with a clean Radon MI A (25.45). No behavior change — the blueprint, routes, decorators, and exports are identical
- **`translated_audio.py` Bandit B110 silent-exception fixes** (`library/backend/api_modular/translated_audio.py:331,646`): two `ffprobe` duration probes used `except: pass`, which swallowed genuine failures silently. Both sites now log at debug level (`logger.debug("ffprobe duration probe failed (non-fatal): %s", e)`) so the fallback path remains intentional but is observable in logs. Dropped Bandit findings 62 → 60
- **Shell script indentation re-normalized to project config** (50+ `.sh` files): a prior `shfmt -i 2` run violated `.editorconfig`'s `indent_size = 4` for `*.sh`. Re-ran `shfmt -i 4 -ci -sr -w` across every modified shell script (`upgrade.sh`, `install.sh`, `uninstall.sh`, all `config-migrations/*.sh`, `data-migrations/*.sh`, `scripts/*.sh`, `library/scripts/**/*.sh`, `dev/*.sh`, `docker-entrypoint.sh`, `launch.sh`, `migrate-api.sh`, `create-release.sh`, `library/setup.sh`, `library/launch*.sh`, `library/scanner/*.sh`). All scripts pass `bash -n` and `shellcheck -S warning`. Diff shrank 6122 → 1058 lines (legitimate canonicalization only)
- **`pypinyin` install + test monkeypatch clarification**: `pypinyin>=0.55.0` is pinned in `library/requirements.txt:27` but was absent from the maintainer's dev host. Four tests failed with `ModuleNotFoundError: pypinyin` (`test_translations_api`, `test_translations_helpers_coverage` x2, `test_utilities_ops_audible_extended`). Install + `library/tests/test_multi_session.py` monkeypatch clarification (point `"backend.api_modular.auth._auth_db"` at the module-level variable, not a submodule attribute) bring the suite back to 4513 passed / 0 failed
- **`logging` import dedup in `library/backend/api_modular/__init__.py`**: removed redundant inner `import logging` inside the exception handler (already imported at line 19)
- **Round 10 type/naming residuals**: `library/backend/migrations/migrate_to_normalized_authors.py` — renamed shadowed `db_path` to `cli_db_path` in the CLI entry point so it no longer collides with the module-level constant. `library/scripts/enrichment/__init__.py` — added `from typing import Any` and typed the accumulator as `dict[str, Any]`, plus `assert row_id is not None` for the optional branch. `library/scripts/enrichment/provider_local.py` — prefixed the unused `_book` arg and typed the result accumulator as `dict[str, str | float]`. `library/scripts/{find_duplicates,generate_hashes,populate_genres,google_play_processor,update_metadata_from_source}.py` — prefixed unused args with `_` to satisfy ruff ARG rules without changing signatures
- **`library/requirements-dev.txt`**: pinned `astroid>=4.0.4,<4.1` so pylint's AST parser stays on the tested minor across dev hosts

## [8.3.0.1] - 2026-04-16

### Fixed

- **Asset cache-busting timestamps updated**: stale `?v=` timestamps on CSS/JS/iframe references in `shell.html` and `index.html` caused Chromium's disk cache to serve old flex-layout stylesheets after the v8.3.0 deploy, resulting in a blank library grid that didn't resize on desktop browsers. Updated all cache-buster timestamps to force fresh asset loads
- **CodeQL security fixes in streaming translation API**: added input validation to all 6 route handlers in `streaming_translate.py` — `_sanitize_locale()` rejects path traversal and log injection via strict regex (`^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$`), and `audiobook_id`/`chapter_index`/`segment_index` are coerced to `int` at route boundaries. Resolves 6 `py/log-injection` and 2 `py/path-injection` CodeQL alerts

## [8.3.0] - 2026-04-16

### Added

- **Streaming translation pipeline**: on-demand, real-time translation triggered by playback — when a user presses play on an untranslated audiobook, the system dispatches chapter-level work to GPU workers (Vast.ai/RunPod), buffers 3 minutes of translated audio, then begins playback. Pre-translated books (batch pipeline) serve instantly from cache
  - New state machine: IDLE → BUFFERING (overlay + audio notification) → STREAMING (playing with pipeline ahead)
  - WebSocket push events: `segment_ready`, `chapter_ready`, `buffer_progress` for real-time player updates
  - Segment bitmap tracking — player knows instantly which 30-second segments are cached for seamless seek vs re-buffer decisions
  - Seek/skip handling: ±30s within buffer = seamless; beyond buffer = re-enter buffering with overlay + notification
  - GPU warm-up on app open for non-English locales to reduce cold-start latency
  - Consolidation: streaming segments merge into permanent `chapter_subtitles` entries so future plays are free
- **Streaming coordinator API** (`library/backend/api_modular/streaming_translate.py`): `POST /api/translate/stream`, `GET /api/translate/segments/<id>/<ch>/<locale>`, `GET /api/translate/session/<id>/<locale>`, `POST /api/translate/warmup`, `POST /api/translate/seek`, `POST /api/translate/segment-complete`, `POST /api/translate/chapter-complete`
- **Streaming worker script** (`scripts/stream-translate-worker.py`): chapter-level GPU worker that polls `streaming_segments` for pending work, splits chapter audio into 30-second segments, processes each through STT → Translation → VTT, and reports completion via HTTP callbacks
- **Buffering overlay UI**: visual progress bar with spinner, animated slide-up above the player bar, gold-themed to match the existing design — shows segment count progress (e.g., "3 / 6")
- **Localized buffering notification audio**: pre-generated edge-tts audio clips played during buffering state — `zh-Hans` (XiaoxiaoNeural) and `en` (AriaNeural) fallback
- **Database migration 004**: `streaming_segments` table (per-segment state tracking with priority, worker assignment, inline VTT content) and `streaming_sessions` table (active session tracking with GPU warm-up signal)

## [8.2.3.6] - 2026-04-15

### Fixed

- **`localization/fallback.py` now retries the remote provider before falling
  back to local**: a single transient `requests.exceptions.RequestException` /
  `OSError` / `TimeoutError` from a RunPod HTTPS proxy used to trigger an
  immediate permanent CPU fallback, dropping that worker's throughput to
  ~1/40th of GPU for the rest of the chapter. `with_local_fallback()` now
  retries up to `REMOTE_MAX_ATTEMPTS=4` times with `(2s, 5s, 15s)` exponential
  backoff and only falls back to local after all retries fail. Local-provider
  failures are still not retried — real errors propagate. Observed impact:
  prevents silent throughput collapse across the 6×L40S fleet during RunPod
  proxy hiccups, which were the primary cause of the 2026-04-14 stalled
  backlog. Covered by `library/tests/test_stt_runtime_fallback.py`
- **`scripts/audiobook-translations` `+` / `-` key handling in the queue
  manager TUI**: tty was not in cbreak mode, so single-key concurrency
  adjustments required pressing Enter. Now reads one char at a time

## [8.2.3.5] - 2026-04-15

### Added

- **Multi-book-per-GPU concurrency**: new `WORKERS_PER_GPU` env var
  (default 4) spawns N parallel `batch-translate.py` workers per Vast.ai/RunPod
  tunnel. All N share a single `faster-whisper` model instance on the GPU (no
  extra VRAM) — the remote gunicorn now runs `-k gthread --threads N`, and
  CUDA releases the GIL so N transcribes run truly concurrently. On L40S
  (48GB) this cuts per-book wall time ~4× and collapses the ~1665-book
  backlog from ~70 hrs / $225 to ~18 hrs / $60 on the same 6-GPU fleet.
  Configurable via `/etc/audiobooks/scripts/translation-env.sh`; set to 1 to
  restore pre-change single-stream behavior

### Changed

- **Atomic job claim in `batch-translate.py::next_pending_job`**: replaced the
  SELECT-then-UPDATE pair (race window where two workers could claim the same
  `translation_queue` row) with a single `UPDATE ... RETURNING` statement.
  Required for correctness under `WORKERS_PER_GPU > 1`; harmless with 1.
  Requires SQLite ≥ 3.35 (shipped 2021-03; Arch/CachyOS 3.51, Debian 13 3.46
  — all supported targets satisfy it)

## [8.2.3.4] - 2026-04-15

### Security

- **`project_root` info-leak avoided**: The 8.2.3.3 attempt populated
  `project_root` directly on the unauthenticated `/api/system/version` endpoint,
  which previously had project_root removed on purpose (filesystem-path
  disclosure to anonymous callers). Reverted that change and added a new
  admin-gated `/api/system/install-info` endpoint (`@admin_or_localhost`) that
  returns only the install path. `utilities.js::loadVersionInfo()` now calls
  both endpoints — anonymous callers see version only, admins see the install
  path

### Fixed

- **Upgrade modal truncated tweak version**: `scripts/upgrade-helper-process` version regex (`[0-9]+\.[0-9]+\.[0-9]+`) dropped the optional 4th segment, so the modal showed `8.2.3 → 8.2.3` instead of `8.2.3 → 8.2.3.2`. Extended both `current_version` (line 342) and `available_version` (line 345) parsers with `(\.[0-9]+)?` to preserve `x.y.z.w`. `new_version`/`old_version` parsers at lines 551–552 already had the fix
- **APPLICATION VERSION panel — INSTALL PATH "-"**: the utilities page was reading `data.project_root` from the public version endpoint, where it wasn't (and shouldn't be) populated. Moved to the new `/api/system/install-info` admin endpoint
- **Stale JS cache-buster on `utilities.html`**: `utilities.js?v=1775585227` was not bumped when the new `loadVersionInfo()` two-call pattern shipped, so browsers with the utilities page cached kept running the old JS and INSTALL PATH stayed "-" even after upgrade. Bumped to `?v=1776270600` so clients re-fetch on next page load

## [8.2.3.3] - 2026-04-15 [WITHDRAWN]

Release withdrawn — CI failed on `test_utilities_system.py::TestGetVersion::test_returns_version_from_file`. The 8.2.3.3 attempt added `project_root` to the unauthenticated `/api/system/version` response, which violated the pre-existing security contract (no filesystem-path disclosure to anonymous callers). See 8.2.3.4 for the corrected fix.

## [8.2.3.2] - 2026-04-15

Re-release of 8.2.3.1 — the v8.2.3.1 GitHub release was created immutable with no
tarball asset and could not accept uploads, blocking the upgrade path. Content is
identical to the intended 8.2.3.1 release.

## [8.2.3.1] - 2026-04-15

### Added

- **`docs/GPU-FLEET-OPS.md`**: end-to-end reference for renting, bootstrapping,
  monitoring, and tearing down the Vast.ai + RunPod Whisper STT fleet used by
  the translation pipeline. Covers SSH tunnel setup, `translation-env.sh`
  configuration, `audiobook-translate` / `audiobook-fleet-watchdog` timers,
  and `teardown-gpu.sh` usage. Cost model and troubleshooting included

### Changed

- **Dependency bumps (minor)**: `gevent` 25.9.1 → 26.4.0, `filelock` 3.25.2 →
  3.28.0 (CVE floor updated), `marshmallow` 4.2.4 → 4.3.0, `pytest` 9.0.2 →
  9.0.3 (dev), `mypy` 1.20.0 → 1.20.1 (dev), plus transitives. `requirements.txt`
  and `requirements-docker.txt` floors raised accordingly
- **Complexity reduction — 10 D/E/F functions refactored to A-grade**: extracted
  cohesive blocks (validation, API calls, mapping, persistence, state transitions)
  into private `_`-prefixed helpers. Public signatures, return shapes, log
  messages, exceptions, and side-effects are bit-identical. Affects
  `library/backend/api_modular/translations.py` (`batch_translate` F/50 → A/2,
  `on_demand_translate` E/33 → A/2, `_translate_missing` D/28 → A/2,
  `translate_strings` D/21 → A/3), `library/scripts/enrichment/` (`AudibleProvider.enrich`
  E/31 → A/3, `GoogleBooksProvider.enrich` D/21 → A/3, `enrich_book` D/28 → A/4),
  `library/localization/transfer.py` (`import_translations` E/31 → A/5,
  `export_translations` D/25 → A/1), and `library/localization/translation/deepl_translate.py`
  (`DeepLTranslator.translate` D/22 → A/5). `radon cc library/ -nc -e 'library/tests/*'`
  now reports zero D/E/F grades

## [8.2.3] - 2026-04-14

### Added

- **Systemd hardening across remaining units**: `audiobook-fleet-watchdog.service`,
  `audiobook-translate.service`, `audiobook-translate-check.service`, and
  `audiobook-shutdown-saver.service` now set `NoNewPrivileges=yes`,
  `ProtectSystem=full`, and `ProtectHome=read-only`. `PrivateTmp=yes` added
  everywhere except `audiobook-shutdown-saver.service` (intentionally omitted —
  needs real `/tmp` to flush staging files before shutdown)

### Changed

- **`upgrade.sh` normalizes ownership + permissions on every run**: the
  `verify_installation_permissions()` helper now unconditionally resets
  `audiobooks:audiobooks` ownership and canonical mode bits across the entire
  install tree, venv `bin/` entries, TLS key (`0640`), `auth.key` (`0600`), and
  `auth.db` (`0640`). Prevents recurrences of the 2026-04-14 outage where
  `/opt/audiobooks` was left `bosco:bosco` `0700` after an interactive rebuild
  and blocked the service account from reading its own install. Paths are
  resolved via `${AUDIOBOOKS_CERTS}` / `${AUDIOBOOKS_VAR_DIR}` from
  `lib/audiobook-config.sh` (no hardcoded paths)

### Fixed

- **Permission normalizer now chmods extension-less shebang wrappers**:
  the first pass of the unconditional normalizer only handled `*.sh` and
  `launch*.sh`, which silently reset `/opt/audiobooks/scripts/audiobook-*`
  (extension-less entry points) to `0644`. The `/usr/local/bin/audiobook-*`
  symlinks then failed the `-x` test and `reconcile-filesystem.sh` reported
  20 "missing wrapper" drift items on every upgrade. The normalizer now
  detects `#!` shebang headers under `$target/scripts` and chmods those files
  to `0755`. Fix applied in `lib/audiobook-config.sh`, `install.sh`, and
  `upgrade.sh`. Verified on the test VM: drift count dropped
  from 20 to 0 after re-deploy
- **markdownlint cleanup**: `CHANGELOG.md`, `README.md`, and
  `docs/MULTI-LANGUAGE-SETUP.md` now pass `markdownlint-cli2` with zero errors.
  Wrapped long CHANGELOG bullets, added blank lines around fenced code blocks
  and lists, tagged bare fences with `text` language
- **`docs/ARCHITECTURE.md` line-length fix**: the localization-subsystem
  intro paragraph on line 710 was 681 chars; wrapped to ≤100 cols so
  `markdownlint-cli2 MD013` passes clean
- **Bandit B608 suppression moved to the reported line**: in
  `library/backend/import_to_db.py::get_enriched_books_in_library`, the
  `# nosec B608` comment was on the `cursor.execute(` line, but bandit
  reports the offense at the f-string that follows. The suppression now
  sits on the closing `"""` line where bandit actually flags it, bringing
  bandit MEDIUM count from 1 to 0
- **Release workflow no longer fails on immutable-release update**: the
  `v8.2.2.1` Release run failed because `softprops/action-gh-release`
  attempts to patch `target_commitish` on a release that `/git-release
  --promote` already created, and GitHub rejects that on immutable
  releases (`target_commitish cannot be changed when release is
  immutable`). Replaced the action with a `gh` CLI block that detects
  whether the release exists — uploads assets with `--clobber` if so,
  otherwise creates the release with `--generate-notes`. Works identically
  for tag-pushed-then-promoted and direct tag-push flows
- **Auth integration test timeouts raised to 45s**: `TIMEOUT = 15` in
  `library/tests/test_auth_integration.py` and `timeout=10` in
  `test_player_navigation_persistence.py::_get_auth_session` were below
  the ~20s SQLCipher-backed `/auth/login` response time observed on the
  test VM under load, causing 3 lifecycle tests to error as `ReadTimeout`
  and 5 Playwright tests to error at the auth setup step. Raised both to
  `45` — restores 3871-test baseline, Playwright tests skip cleanly
  instead of erroring when browser isn't available on the host
- **CI coverage config split**: new `.coveragerc.ci` omits the
  localization subsystem's GPU and external-service modules (Whisper
  STT, DeepL, Douban metadata, translation API routes) that cannot run
  in GitHub CI without live GPU/credentials. Strict `.coveragerc`
  remains the default for local/dev runs where external GPU resources
  (RunPod/Vast.ai) are reachable. `.github/workflows/ci.yml` now passes
  `--cov-config=../.coveragerc.ci` so CI sees 88.87% coverage instead
  of 82.88% with the localization subsystem included
- **Version drift in release artifacts**: `Dockerfile` `ARG
  APP_VERSION` was still `8.0.4` and `install-manifest.json`'s
  `"version"` was `8.0.4.1` — two and three minor versions behind the
  canonical `VERSION` file. Bumped both to `8.2.3` so the Docker
  image's `LABEL version` and the install manifest agree with every
  other version reference. Single-source-of-truth rule: packaging
  artifacts must move in lockstep with `VERSION`
- **AI self-promotion removed from user-facing docs**: the "AI
  development partner" / "Claude Code" attributions in
  `library/README.md` and `docs/MULTI-LANGUAGE-SETUP.md` were
  release blockers per the project's AI self-promotion prohibition
  (`.claude/rules/testing.md`). Removed both entries

## [8.2.2.1] - 2026-04-14

### Added

- **`audiobook-translations report`**: on-demand historical report of all
  completed translations, runnable anytime against the live DB. Unlike
  `status` (live daemon dashboard), `report` is an audit — "what has the
  pipeline actually delivered to date?" — useful for release notes, cost
  attribution, and verifying a locale rollout finished. Shows per-book
  locale, chapter count (from `chapter_subtitles`), wall-clock duration
  (`finished_at − started_at`), and finish timestamp, with a totals line.
  Options: `--locale LOCALE` to filter, `--since DATE` to window by finish
  date, `--csv` for spreadsheet export, `--no-summary` to suppress totals

## [8.2.2] - 2026-04-14

### Added

- **Three-layer cost-safety watchdog architecture** to prevent paying for
  idle GPU compute when the translation pipeline hangs. Prior behavior: a
  wedged daemon or dead remote fleet could silently burn hours of rented
  GPU time while `systemctl is-active` reported "running". Each layer
  catches a distinct failure mode:
  - **Layer 1 — daemon heartbeat** (`library/localization/queue.py`,
    `scripts/batch-translate.py`, `scripts/translation-check.sh`): new
    `translation_queue.last_progress_at` column bumped at pickup,
    per-chapter `_on_progress`, step transitions, and finish.
    `translation-check.sh` treats any `processing` row whose heartbeat is
    older than 15 minutes as wedged — restarts `audiobook-translate.service`
    and resets the row to `pending` so a fresh worker re-picks it
  - **Layer 2 — fleet utilization** (`scripts/fleet-watchdog.sh`,
    `systemd/audiobook-fleet-watchdog.service/.timer`): new oneshot running
    every 5 minutes. When the daemon is active AND `translation_queue` has
    `processing` rows AND zero new `chapter_subtitles` rows have been
    inserted in 20 minutes, the fleet is dead — restart the daemon to
    trigger re-provisioning and flip stuck rows back to `pending`. Catches
    the case where Layer 1's heartbeat is fresh (daemon is happily retrying)
    but the remote GPU instances died
  - **Layer 3 — remote dead-man TTL** (`scripts/translation-daemon.sh`
    whisper-server heredoc): embedded daemon thread in the Flask whisper
    server calls `shutdown -h now` after `IDLE_SHUTDOWN_SEC` (default 1800s)
    without a request. Each Vast.ai/RunPod instance halts itself if the
    local daemon stops talking to it, so a local crash can't leave remote
    GPUs burning
- **Per-book progress display in `audiobook-translations status`**: replaces
  the uninformative "5h12m ELAPSED" column with `X/Y (NN%)` showing chapter
  progress against the real total, plus a new `IDLE` column (seconds since
  last heartbeat) that colors yellow at ≥15min and red at ≥1h. Total
  chapter count persists on `translation_queue.total_chapters` via the same
  heartbeat path, so the denominator survives worker restarts
- **Data migration `003_translation_heartbeat.sh`** (MIN_VERSION=8.2.2):
  idempotent `ALTER TABLE` adding `last_progress_at` and `total_chapters`
  to existing queue rows, backfills `last_progress_at` from
  `started_at`/`created_at`, creates `idx_tq_last_progress`. Also applied
  in-place at daemon startup via `PRAGMA table_info` guard so upgrade paths
  that skip migrations still converge

### Changed

- **`install.sh` now explicitly enables `audiobook-translate-check.timer` and `audiobook-fleet-watchdog.timer`** instead of relying on `audiobook.target` auto-wanting. Matches the other timer-enable pattern in the installer and ensures watchdog coverage is active from first boot

## [8.2.1.1] - 2026-04-14

### Added

- **Interactive `--watch` controls in `audiobook-translations`**: live
  refresh now supports `+`/`-` to tune the tick interval (±5s, min 1s), `a`
  to toggle aggressive mode (fires the liveness check every tick instead of
  only when the daemon is idle), `r` to refresh immediately, and `q` to
  quit. Status output shows the current tick number, observed gap since the
  previous tick, active mode label, and an annotation on the `Timer:` line
  that explains how `--watch` is relating to the 5-minute systemd cadence
  (override, will-trigger-if-idle, or read-only)
- **`audiobook-translations check` subcommand**: one-shot invocation of `audiobook-translate-check.service` so operators can force the daemon-liveness check from the CLI without waiting for the 5-minute timer

### Changed

- **`translation-daemon.sh` health-check loop is now parallelized**: reorganized the per-instance recovery path into three phases — serial tunnel restart, parallel whisper-server probe and model-load, serial worker restart — so eight Vast.ai instances needing attention collapse from ~144s of back-to-back work into ~18s. Parent-process PID-map mutations stay in the serial phases so backgrounded subshells never race the parent's bookkeeping

### Fixed

- **ffmpeg chapter-split timeout now scales with chunk duration** (`library/localization/chapters.py`): the fixed 60s cap would kill stream-copy on large opus files with sparse sync points (e.g., a 10-hour chunk from a 266-hour Dostoyevsky collection). Timeout is now `max(120s, 60s + 5% × chunk_seconds)` — long chunks get room to finish, short chapters still bounded at 120s
- **Malformed UTF-8 bytes no longer crash TTS and glossary reads** (`library/localization/queue.py`, `library/localization/translation/glossary.py`): a single bad byte in an STT-provider-written VTT transcript or glossary file used to raise `UnicodeDecodeError` and fail the whole translation job. Both reads now use `errors="replace"` so isolated bad bytes become `U+FFFD` and downstream TTS skips them harmlessly

## [8.2.1] - 2026-04-13

### Added

- **Massively parallel translation pipeline** (`scripts/translation-daemon.sh`, `scripts/batch-translate.py`, `scripts/verify-translations.py`): Multi-GPU translation engine supporting simultaneous Vast.ai and RunPod instances with automatic GPU lifecycle management (provision → translate → teardown). Includes batch metadata translation, verification with quality scoring, and email reporting — all orchestrated through three new systemd units (`audiobook-translate.service`, `audiobook-translate-check.service/.timer`)
- **Translation queue CLI** (`scripts/audiobook-translations`): Extended with `status`, `pause`, `resume`, `start`, `stop` subcommands for operational control. Pause/resume uses DB state transitions (`pending` ↔ `paused`) so workers drain naturally when no pending jobs remain
- **Site-local translation configuration** (`etc/translation-env.sh.example`): GPU instance definitions (Vast.ai/RunPod IDs, SSH ports, API keys) now live in `/etc/audiobooks/scripts/translation-env.sh` — a site-local config file that survives upgrades. Ships with documented example template
- **GPU teardown and health-check scripts** (`scripts/teardown-gpu.sh`, `scripts/translation-check.sh`): Automated GPU instance lifecycle management and translation progress monitoring with email notifications

### Changed

- **Translation daemon reads site-local config**: `scripts/translation-daemon.sh` now sources `/etc/audiobooks/scripts/translation-env.sh` instead of embedding hardcoded GPU instance definitions, making the pipeline upgrade-safe

### Fixed

- **Hardcoded `/opt/audiobooks` path** in `scripts/audiobook-translations` help output: Replaced with `${AUDIOBOOKS_HOME}` config variable — was flagged by the no-hardcoded-paths test and CI
- **Email template tests depended on ephemeral patch file** (`library/tests/test_email_templates.py`): Tests loaded i18n keys from `/tmp/i18n_patch_v81_email.json`, a development-time shim that doesn't exist in CI. Removed the patch-file fixture since all email translation keys now live in the real locale catalogs (`library/locales/{en,zh-Hans}.json`)
- **CodeQL false positive dismissed** (alert #447, `py/sql-injection` in `audiobooks.py:599`): SQL query construction uses `_SORT_MAPPINGS` allowlist dict and `_FILTER_SPECS` hardcoded list — all user input goes through parameterized `?` placeholders
- **Conftest `i18n` module resolution**: Added `library/backend/` to `sys.path` in `tests/conftest.py` so the new `backend/api_modular/i18n_routes.py` can resolve `from i18n import ...` during test collection

### Security

- **Closed all semgrep ERROR and bandit MEDIUM+ findings**: Annotated 54 semgrep raw-SQL / tainted-SQL findings across 21 production files and 59 bandit MEDIUM findings in 14 test files with inline `# nosec B608` and `# nosemgrep:` suppressions plus rationale. Column/table names in DDL come from hardcoded allowlists; values use parameterized `?` placeholders
- **XSS hardening in `library/web-v2/js/{utilities,library}.js`**: Refactored 2 UNSAFE attribute-injection sinks to `document.createElement` / `textContent`; 4 UNSAFE-LITE template interpolations now escape format/duration/quality via existing `escapeHtml()` helper
- **Added `library/requirements-dev.txt`**: Pinned test and security tooling (pytest, ruff, mypy, bandit, pip-audit, playwright) for consistent dev environments across contributors

## [8.2.0.2] - 2026-04-13

### Fixed

- **Schema drift in auth database**: `preferred_locale` column was added to
  `schema.sql` (users and access_requests tables) but missing from
  `database.py` ALTER TABLE migrations, causing `SELECT *` queries to return
  columns in wrong positional order. Replaced all `SELECT *` in
  `UserRepository` and `AccessRequestRepository` with explicit column-list
  constants (`_USER_SELECT`, `_AR_SELECT`) that guarantee deterministic
  positional order regardless of physical table layout. Added missing ALTER
  TABLE migration for `preferred_locale` in users table
- **Schema version bump to 10**: Updated `SCHEMA_VERSION` constant and all test assertions to match `schema.sql` version 10 (was stuck at 9)
- **Missing `credentials: "include"` on i18n fetch calls** in `shell.js`: Two new fetch calls added by the localization feature (translated-audio lookup and translation bump) were missing auth cookie forwarding, which would fail silently when `AUTH_ENABLED=true`
- **Dry-run mode calling backfill script**: Data migration `001_podcast_detection.sh` attempted to execute `backfill_enrichment.py` even in `--dry-run` mode, triggering upgrade.sh's ERR trap when the script wasn't present in the target directory
- **CodeQL security alerts in i18n code**: Sanitized log injection vectors in `translations.py`, `search_cjk.py`, `i18n.py`, `i18n_routes.py`, and `whisper_gpu_service.py` — replaced user-controlled `str(e)` in error responses with generic messages, added regex validation for locale path construction, added `_sanitize_log()` helper for safe logging of user input
- **MIME-encoded email test assertions**: Updated 7 email tests to decode multipart/alternative MIME bodies (base64-encoded by i18n changes) before checking content
- **DeepL Pro quota handling** (`library/localization/translation/quota.py`): `QuotaTracker` was treating DeepL Pro's unlimited plan (`character_limit=0`) as falsy, leaving the default 500K cap in place and blocking translations for Pro users. Fixed by treating `0` as unlimited (sentinel: 1 trillion). Raised default `char_limit` from 500K to 1T for new installs
- **Batch translate endpoint now includes descriptions, series, and pinyin sort keys** (`library/backend/api_modular/translations.py`): Previously only translated titles and authors. Descriptions translated in sub-batches of 10 to stay within API limits. `db_path` passed to `DeepLTranslator` so translation memory (TM) cache is used
- **CVE-2026-39892 (cryptography)**: Bumped `cryptography` minimum from `>=46.0.6` to `>=46.0.7` in `requirements.txt` to resolve security advisory
- **Ruff format and additional CodeQL dismissals**: Applied `ruff format` across 55 files — `translated_audio.py`, `subtitles.py`, `email_templates.py`, `chapters.py`, `translations.py`, and test suite. Dismissed remaining CodeQL alerts for intentional patterns with documented rationale

## [8.2.0.1] - 2026-04-13

### Added

- **Multi-language setup and installation guide** (`docs/MULTI-LANGUAGE-SETUP.md`): Comprehensive documentation covering provider setup (DeepL, Vast.ai, RunPod, local GPU), configuration reference for all 16 environment variables, step-by-step guide for adding new languages, real-world cost and time investment breakdown (~$70k+ total project, ~$150-450 GPU costs for full library translation), translation asset portability, dependency matrix, attribution for all AI services, and troubleshooting guide
- **Admin exclusion rationale in all relevant docs**: Explained in README, ARCHITECTURE.md, About page, and Help page why the admin/backoffice UI is intentionally not translated — 100% of user-facing content is translated while admin-only tools used solely by the operator remain in English to avoid doubling maintenance for pages no patron ever sees

### Fixed

- **Untranslated tooltip title attributes** across all user-facing pages: Added `data-i18n-title` attributes to 102 interactive elements (buttons, links, inputs, selects) in `shell.html`, `index.html`, `help.html`, `about.html`, `claim.html`, `login.html`, and `register.html`. Added 63 new tooltip translation keys to both `en.json` and `zh-Hans.json` (1,039 keys total). The `i18n.js` engine already supported `[data-i18n-title]` selectors — the HTML elements were simply missing the attribute bindings
- **Unused import in localization pipeline** (`library/localization/pipeline.py`): Removed `Chapter` import that was flagged by ruff F401 and breaking CI

## [8.2.0] - 2026-04-13

### Added

- **Bilingual feature announcement banner** (`library/web-v2/js/feature-announce.js`, `css/feature-announce.css`): Art Deco styled one-time dismissible banner announcing Chinese language support — shows EN + ZH headlines, body text, and four feature highlight cards (Multi-Language, Subtitles, Transcript, CJK Search). Dismissed state persists in localStorage; safe DOM construction only (createElement + textContent)
- **Translation asset export/import CLI** (`scripts/audiobook-translations`): Portable transfer tool for moving subtitles, TTS audio, and metadata translations between environments without re-translating. Supports `export`, `import`, and `list` subcommands with configurable data directory
- **Localization documentation**: README updated with 8 localization feature bullets, ARCHITECTURE.md gains full "Localization & Translation Pipeline" section (STT → Translation → TTS architecture, provider tables, DB schema, CJK search/sort), help.html adds "Language & Translation" section with 4-card feature grid and 4 FAQ entries

### Fixed

- **Translation worker not starting on user-triggered locale bump** (`library/backend/api_modular/i18n_routes.py`): When a user changed their locale preference, the background translation worker was not spawned to process the new locale's pending translations

## [8.1.2] - 2026-04-12

### Added

- **Chapter-by-chapter subtitle generation** (`library/localization/chapters.py`): Audiobooks are now split into chapters via embedded ffprobe metadata (with Audible `chapters.json` sidecar fallback) and each chapter transcribed individually on the GPU, producing per-chapter VTT files with offset-adjusted timestamps. Replaces the prior whole-book-at-once architecture that blocked the GPU for 30–60+ minutes on long audiobooks
- **Chapter progress reporting**: Frontend subtitle generation banner now shows "Chapter 3 of 42: Title" with a teal progress bar during transcription. Status API returns `chapter_index`, `chapter_total`, and `chapter_title` fields for real-time polling. i18n keys added for en and zh-Hans
- **On-demand TTS generation banner** (`library/web-v2/css/i18n.css`): Styled banner for text-to-speech generation with spinner, progress text, phase labels, and error/done states — mirrors the subtitle generation banner design
- **TTS request endpoint and job tracking**: User-facing `/api/user/tts/request` endpoint with per-user cooldown, background thread generation, and status polling via `/api/tts/status/<book_id>/<locale>`
- **Local GPU Whisper service** (`library/localization/stt/whisper_gpu_service.py`): Self-hosted Whisper transcription service for ROCm AMD GPUs with 2 GB max content length
- **Data migrations framework** (`data-migrations/`): Version-gated data-state migrations that run during `upgrade.sh` when an upgrade crosses a declared version boundary. Each script declares a `MIN_VERSION` and is idempotent — safe to re-run but only triggered when the installed version is below the boundary. First migration: `001_podcast_detection.sh` (boundary v8.0.3)

### Changed

- **Gunicorn worker timeout**: Increased from 120s to 1800s with 60s graceful timeout — the prior value killed workers during GPU transcription of long audiobook chapters
- **Subtitle generation backend refactored**: Eliminated code duplication between admin and user endpoints by extracting shared `_start_generation()` function that handles background thread setup, chapter-by-chapter progress callbacks, and database writes

### Fixed

- **Gunicorn worker killed during GPU transcription** (prior session incident): `--timeout 120` caused SIGKILL of the worker process during long Whisper transcription requests, destroying the daemon thread that held the GPU connection. Manifested as a 400 error in GPU service logs (actually a broken pipe from the killed worker). Fixed by increasing timeout to 1800s
- **STT auto-selection preferred DeepL over Whisper**: Auto mode now correctly prefers Whisper for long-form audiobook work — DeepL's transcription endpoint rejects payloads above ~100 MB, and audiobooks routinely exceed that
- **STT provider priority favored unstable backends**: Auto mode now prefers Vast.ai (dedicated instance, reliable throughput) over RunPod (frequently resource-constrained) over local GPU (system instability risk under heavy Whisper loads). Previously local GPU was first, causing either system crashes or silent fallback to CPU transcription

## [8.1.1] - 2026-04-11

### Added

- **Installer architecture documentation**
  (`docs/INSTALLER-ARCHITECTURE.md`): Source-of-truth reference for
  `install.sh`, `uninstall.sh`, `upgrade.sh`, the manifest, and the
  reconciler — documents the 2026-04 drift incident, the canonical-defaults
  pairing rule (`library/config.py` + `lib/audiobook-config.sh` must agree),
  the subset-preservation invariant, and six rules for future installer
  changes
- **Content classification drift documentation**
  (`docs/CONTENT-CLASSIFICATION-DRIFT.md`): Explains how
  `audiobooks.content_type` gets stale when scans predate classification
  fixes, how to detect drift via cross-DB ASIN comparison, how to repair
  with a surgical ASIN-based rewrite, and why
  `library/scripts/backfill_enrichment.py --podcast-detection` must be run
  after any DB import on a Phase-0+ install. Documents the 2026-04-07 dev
  VM incident (scan preceded Phase 0 podcast detection commits `ccb863e` +
  `c10b335` by ~21 hours, leaving 101 rows mis-labeled as `Podcast` that
  should have been `Show`, `Episode`, or `Product`)
- **End-to-end uninstall preservation tests**
  (`library/tests/test_uninstall_keep_data.py`): Four pytest cases run
  `uninstall.sh --user --force` against a scratch `$HOME` and assert that
  `--keep-data` preserves DB, `auth.db`, `auth.key` (mode 0600 after
  restore), covers cache, and `audiobooks.conf`; `--delete-data` wipes
  them; preservation tolerates absent optional items; script stays
  executable
- **Manifest entry for flat-database drift**: `scripts/install-manifest.sh` `CONFIG_CANONICAL_DEFAULTS` now also lists `AUDIOBOOKS_DATABASE|${STATE_DIR}/audiobooks.db` so the reconciler strips the legacy flat-file override that pre-dated the `db/` subdirectory layout

### Changed

- **`config-migrations/002_strip_legacy_path_overrides.sh`** (renamed from
  `002_strip_legacy_covers_override.sh` via `git mv`): Now strips BOTH
  `AUDIOBOOKS_COVERS` (legacy `library/web-v2/covers` and `library/covers`)
  and `AUDIOBOOKS_DATABASE` (legacy `/var/lib/audiobooks/audiobooks.db`)
  via a shared `_strip_key` helper that only removes exact legacy defaults
  and preserves any user customization. `AUDIOBOOKS_VENV`/`AUDIOBOOKS_CERTS`
  deliberately excluded — their canonical defaults still live under
  `${AUDIOBOOKS_HOME}/library/*`
- **`etc/audiobooks.conf.example`** — all path keys commented out:
  `AUDIOBOOKS_DATA`, `LIBRARY`, `SOURCES`, `SUPPLEMENTS`, `HOME`,
  `DATABASE`, `COVERS`, `RUN_DIR`, `CERTS`, `LOGS`, `VENV`, `CONVERTER`,
  `AUTH_DATABASE`, `AUTH_KEY_FILE`, `DATA_DIR`. Added preamble explaining
  that hardcoding defaults in this template is the exact drift mechanism
  that caused the 2026-04 cover-art 404 / split-DB incident. Users
  uncomment only the keys they actually want to override

### Fixed

- **`uninstall.sh --keep-data` silently wiped user state** (2026-04
  incident): The helper preserved only
  `/srv/audiobooks/{Library,Sources,Supplements}` and happily deleted
  `/var/lib/audiobooks` (DB, `auth.db`, covers cache) plus
  `/etc/audiobooks/audiobooks.conf` and `auth.key`. New
  `stage_preserved_state` stages DB dir, `auth.db`, `auth.key`, covers
  cache, and `audiobooks.conf` to a `mktemp -d` with an `EXIT` trap;
  `restore_preserved_state` replays the staging dir after the wipe,
  re-applies `chmod 0600` to `auth.key`, and re-chowns everything to the
  service account. `--delete-data` short-circuits staging entirely
- **`install.sh --fresh-install` lost new config keys**: Because uninstall now restores `audiobooks.conf`, a fresh install path would keep the OLD config and never pick up new default keys. `do_fresh_install` Step 3b now deletes the restored `audiobooks.conf` so `install.sh` writes a fresh default, and Step 5 merges the user's non-default overrides back on top from `fresh_backup_dir`
- **`do_fresh_install` auth.db staging path**: Now uses canonical `${state_src}/auth.db` with a fallback to legacy `${state_src}/db/auth.db` for pre-v8 installs that still kept the auth DB under `db/`
- **`uninstall.sh` user-mode crash on /tmp/audiobook\* artifacts**: In user mode, `remove_runtime_files` would try to unlink `/tmp/audiobook-staging` owned by the `audiobooks` service account and crash on the sticky-bit. New `_can_touch_runtime` helper gates removal on ownership (`[[ -O "$target" ]]` — not `-w`, which lies on group-writable sticky-bit dirs) and skips anything the invoking user doesn't own, logging the skip instead of failing
- **Dev VM content classification drift** (2026-04-07 incident): The dev VM's
  audiobooks DB was bulk-imported on 2026-04-07 19:20:17 — all 1,844 rows
  landed in a one-second window, 21 hours before commits `ccb863e` and
  `c10b335` added Phase 0 podcast detection (publisher/author heuristics +
  backfill sweep). Because `content_type` is set at insertion time and never
  recomputed, dev rows stayed mis-labeled while prod's later scan correctly
  classified the same titles. Total **118 rows reclassified** in four passes:
  (1) 101-row ASIN-JOIN cross-sync from prod's TSV — 70 `Podcast`→`Show`,
  21 `Podcast`→`Episode`, 10 `Podcast`→`Product` false-positive corrections;
  (2) 1 Brian Cox `Meditation` singleton → `Product`; (3) 10 Wondery ad-free
  rows caught by `backfill_enrichment.py --podcast-detection` publisher
  heuristics (America's Coup in Iran, Encore: Enron, The Osage Murders);
  (4) 6 residual rows — 3 Michelle Obama "The Light Podcast" episodes and
  3 Stephen Fry "Ep." episodes — caught only by a secondary
  cross-classification author-match query after an initial "resolved" claim
  was disproven by the user's browser screenshots. The 6-row blind spot
  existed because the ASIN-JOIN requires prod and dev to share ASINs; these
  titles had different ASINs on prod, so the JOIN never linked them, and
  their authors weren't in `_PODCAST_PUBLISHERS`. Root-cause prevention (run
  `backfill_enrichment.py --podcast-detection` after any DB import on a
  Phase-0+ install) and the new cross-classification author-analysis
  detection pattern are now documented in
  `docs/CONTENT-CLASSIFICATION-DRIFT.md`. Final state verified via Playwright
  against `devlib.thebosco.club`: API `total_count: 1160`, zero Michelle
  Obama / Stephen Fry podcast episodes in the library view, matching the
  user's independent browser check

## [8.1.0] - 2026-04-11

### Added

- **Internationalization (i18n) — Simplified Chinese (zh-Hans)**: Full localization pipeline. Catalog-based UI strings (`library/locales/en.json`, `library/locales/zh-Hans.json`) cover the static chrome (header, navigation, player controls, modals, account screens, error messages). On-demand DeepL translation overlay handles dynamic and admin-authored content
- **On-demand DeepL translation cache** (`library/backend/api_modular/translations.py`): `/api/translations/strings` (POST hash-based lookup) and `/api/translations/by-locale/{locale}` (GET full snapshot) endpoints. Backend hashes source strings with short SHA-256 and caches per-locale translations in `string_translations`. Frontend `i18n.js` mirrors the hash so the JS and Python sides agree on cache keys
- **Cross-frame locale sync**: Locale changes propagate from `shell.html` into the embedded `iframe#content-frame` (and vice versa) via `postMessage`, plus a global `localeChanged` event so individual modules (marquee, tutorial, sidebar, modal, maintenance banner) can re-render without a full page reload
- **DeepL overlay coverage**: Book card titles, author names, narrator names, series names, sidebar collection names, marquee new-book titles, sort options, tour step titles + descriptions, help.html and about.html headings, book detail modal title/author/narrator, accessibility panel button labels, and admin-authored maintenance panel + notification banner content all flow through the DeepL pipeline
- **Speech-to-text (STT) provider abstraction** (`library/localization/stt/`): Pluggable Whisper backends — DeepL transcription (legacy), self-hosted Vast.ai Whisper server, and an `auto` mode that prefers Whisper over DeepL when both are configured. `VastaiWhisperSTT` accepts both `faster-whisper` (top-level `words[]`) and `whisper.cpp` (`segments[].words[]`) response shapes, plus string-typed timestamps and `text`/`word` field aliases
- **Text-to-speech (TTS) provider factory** (`library/localization/tts/factory.py`): Config-driven backend selection via `AUDIOBOOKS_TTS_PROVIDER`. Supported: `edge-tts` (CPU default), `xtts-runpod` (RunPod serverless GPU), `xtts-vastai` (self-hosted Vast.ai XTTS v2 server). New `VastaiXTTSProvider` mirrors the Vast.ai Whisper architecture
- **On-demand subtitle generation banner** (`library/web-v2/js/subtitle-banner.js`): When a translated subtitle track is requested but missing, the player shows a GPU-aware progress banner while the backend generates it (job status surfaced via `/api/localization/jobs/{id}`). Tracks both Whisper STT and DeepL translation phases independently
- **Translated audio (XTTS) endpoint** (`library/backend/api_modular/translated_audio.py`): Generates translated voice tracks via the configured TTS provider, transcodes WAV (XTTS) or MP3 (edge-tts) to Opus, and records the actual provider name in `chapter_translations_audio.tts_provider`
- **About page Localization & AI Services attribution** (`library/web-v2/about.html`): Acknowledges DeepL, OpenAI Whisper, Coqui XTTS, Hugging Face, Vast.ai, RunPod, FFmpeg, and other vendors used in the translation pipeline
- **TTS provider factory test coverage** (`library/tests/test_tts_factory.py`): 12 unit tests covering provider selection, credential validation, language stripping (`zh-Hans` → `zh`), `synthesize()` request shape, and error paths — no GPU required
- **Vast.ai Whisper response-shape test coverage** (`library/tests/test_vastai_whisper_response_shapes.py`): 6 unit tests locking in both `faster-whisper` and `whisper.cpp` parser branches plus four edge cases (string timestamps, empty word entries, `text`/`word` aliases, duration fallback)
- **STT runtime fallback** (`library/localization/pipeline.py::_transcribe_with_fallback`): Wraps the per-request `provider.transcribe()` call so any `requests.exceptions.RequestException`, `OSError`, or `TimeoutError` from a remote STT provider (Vast.ai, RunPod, DeepL) triggers a one-shot retry against in-process `LocalWhisperSTT` (faster-whisper). Local provider failures are not retried — the error is real. Covered by 6 new unit tests in `library/tests/test_stt_runtime_fallback.py`
- **Workload-aware STT/TTS selection** (`library/localization/selection.py`, `pipeline.py`, `tts/factory.py`): New `WorkloadHint` enum (`SHORT_CLIP`, `LONG_FORM`, `ANY`) lets callers express intent. Long-form audiobook work prefers RunPod serverless → Vast.ai → local; short interactive clips prefer local to avoid cold-start. Both STT and TTS share the same `with_local_fallback()` helper (`library/localization/fallback.py`) so network errors retry locally exactly once
- **TTS runtime fallback** (`library/localization/tts/factory.py::synthesize_with_fallback`): Mirrors the STT fallback shape — any network error from an XTTS backend retries once against `edge-tts` (always-available local fallback, no cold-start, no GPU)
- **End-to-end subtitle pipeline test** (`library/tests/test_subtitle_pipeline_e2e.py`): Stubs `STTProvider` and `DeepLTranslator` so `generate_subtitles()` runs the full STT → sentence segmentation → alignment → VTT chain in ~0.1s without loading Whisper. Five tests cover source-only, dual-language, target-equals-source, silent-audio error, and VTT format sanity
- **Localized guest email templates**
  (`library/backend/api_modular/email_templates.py`): Magic-link, approval,
  denial, reply, invitation, and activation emails now render from the
  JSON catalogs via a new `render_email(template_name, locale, **vars)`
  helper. Adds `preferred_locale` columns to `users` and `access_requests`
  (migration `library/auth/migrations/010_user_locale.sql`) so senders
  pick the recipient's language. HTML scaffold lives in Python, all copy
  lives in the catalog; HTML bodies escape user-supplied values via
  `str.format_map` with an `_EscapedMapping` wrapper. 18 tests in
  `library/tests/test_email_templates.py`
- **Pinyin-based Chinese sort order**
  (`library/backend/api_modular/search_cjk.py`, `audiobooks.py`,
  `grouped.py`): Adds `pinyin_sort` column to `audiobook_translations`
  (migration `021_audiobook_translations_pinyin_sort.sql` + idempotent
  `backfill_pinyin_sort.py`), populated via `pypinyin` on every
  translation write and on demand for existing rows. Title grids for
  `locale=zh*` sort via `LEFT JOIN audiobook_translations ON locale` and
  `ORDER BY COALESCE(NULLIF(pinyin_sort, ''), audiobooks.title)`, so
  untranslated rows fall back to the English title instead of floating
- **CJK bigram search** (`library/backend/api_modular/search_cjk.py`):
  Queries containing any CJK character swap FTS `MATCH` for LIKE-based
  bigram matching against `audiobooks.title`, `audiobooks.author`, and
  `audiobook_translations.title`. Works around SQLite `unicode61`
  tokenizer dropping CJK characters entirely. 18 tests in
  `library/tests/test_chinese_sort.py`
- **DeepL quota + glossary + translation memory**
  (`library/localization/translation/quota.py`, `glossary.py`,
  `deepl_translate.py`): `QuotaTracker` persists monthly character usage
  in a new `deepl_quota` table (migration `020_deepl_quota.sql`) with
  soft warning at 90% and hard stop at 99%. `GlossaryManager` pushes a
  YAML-driven en→zh glossary (`library/localization/glossary/en-zh.yaml`,
  16 domain terms) to DeepL's `/v2/glossaries` endpoint once per process
  and caches the glossary ID by content hash. `DeepLTranslator.translate()`
  now checks `string_translations` (SHA-256 cache keys) before hitting
  the API, bills only unique misses, and writes results back to the TM.
  Admin endpoint `GET /api/admin/localization/quota` returns the
  snapshot. 5 tests in `library/tests/test_deepl_quota.py`
- **Document chrome localization**: All 11 user-facing HTML pages (`shell`, `index`, `about`, `help`, `register`, `login`, `contact`, `claim`, `verify`, `401`, `403`) tagged with `data-page-title-key` on `<html>`, rewritten on `localeChanged` via a new `applyChrome()` helper in `i18n.js`. Titles follow a consistent em-dash + localized-brand convention (`关于 — 图书馆` / `About — The Library`)
- **JavaScript string sweep**: 45 new keys wrap hardcoded English in `account.js`, `library.js`, `shell.js`, `subtitles.js`, `utils.js`, and `webauthn.js` (alerts, confirms, thrown `Error` messages, player bar fallbacks, relative-time helpers, WebAuthn diagnostics). Admin-only JS (`utilities.js`, `suggestions-admin.js`, `maint-sched.js`) intentionally excluded per i18n scope rules

### Changed

- **STT auto-mode prefers Whisper over DeepL**: When `AUDIOBOOKS_STT_PROVIDER=auto` and both Whisper (Vast.ai/RunPod) and DeepL are configured, the pipeline now picks Whisper for higher accuracy. DeepL remains the fallback when Whisper is unavailable
- **Backoffice scope stripped from translation**: `data-i18n="shell.backOffice"` removed from `shell.html`, orphan key removed from `en.json` and `zh-Hans.json`. Admin pages (`admin.html`, `utilities.html`) have no `data-i18n` tags and remain English-only — admin/back-office UI is fully excluded from end-user locales
- **Cache busters** for `library/web-v2/js/*.js` and `css/*.css` bumped to force browser reload of i18n-aware modules
- **Live connection state labels** in the player relabeled for clarity
- **Mobile play/pause button** tap target enlarged to meet WCAG touch-size guidelines

### Fixed

- **whisper.cpp `verbose_json` response shape parser**: `VastaiWhisperSTT` was assuming `faster-whisper`'s top-level `words[]` and crashed on `whisper.cpp`'s `segments[].words[]` shape. Now walks both shapes and falls back to `text`/`word` field aliases
- **Subtitle banner absolute-import path** (`subtitle_banner.py`): Was using a project-relative import that failed under the installed app's `sys.path`
- **STT job status tracking**: Subtitle generation jobs now correctly transition through `pending → transcribing → translating → complete` instead of jumping straight to `complete`
- **a11y panel button labels** translated via i18n (previously hardcoded English)
- **i18n locale fetch over CDNs**: Switched from `fetch(...)` defaults to `cache: "no-store"` so Cloudflare doesn't serve a stale `en.json` to a `zh-Hans` user
- **Marquee on-demand translation fallthrough**: New-book marquee titles now fall through to the DeepL pipeline when no catalog entry exists, instead of showing the English title to a Chinese user
- **Marquee re-fetches translations on locale change**: Was caching the English titles for the lifetime of the page
- **Book detail modal title/author/narrator translation**: Modal opens via `library.showBookDetail(id)` from inside the iframe — `_overlayModalTranslation` now swaps in DeepL'd title + author from the per-locale snapshot
- **Sidebar collection name translation**: Series and curated collection labels now translate on-demand via DeepL
- **Sort/marquee/narrator endpoint**: Translation lookup moved from POST to GET so it can be served from a CDN cache when available
- **zh-Hans review issues**: Multiple corrections to the Simplified Chinese catalog after first-pass review
- **Hardcoded SSH keys, users, and credentials**: Removed from `install.sh`, `upgrade.sh`, and helper scripts. All credentials now read from `audiobooks.conf` or environment variables

### Security

- **No hardcoded credentials in scripts**: Audit + cleanup of `install.sh`, `upgrade.sh`, and helper scripts to ensure no SSH keys, usernames, or passwords are baked into the repository

## [8.0.4.1] - 2026-04-08

### Added

- **Narrator backfill mode**: `--narrator-backfill` flag for `backfill_enrichment.py` re-enriches books that have ASINs but still show "Unknown Narrator" — fills in real narrator data from the Audible API
- **Phase 0 podcast detection**: Backfill script now runs a pre-enrichment scan that reclassifies items whose author or publisher matches known podcast networks (Wondery, Gimlet, Parcast, etc.) from `content_type='Product'` to `'Podcast'` — catches items without ASINs that the enrichment pipeline cannot reach

### Fixed

- **JS console errors in iframe context**: `account.js` threw TypeError when loaded inside the iframe (index.html) because `my-account-btn` only exists in shell.html — added null guards to `showAuthenticatedState`, `showSignInState`, and the DOMContentLoaded handler
- **WebSocket heartbeat always reported idle**: `websocket.js` referenced `audio-player` but the actual element ID is `audio-element` — heartbeat now correctly reports streaming/paused/idle state
- **Narrator enrichment sort_name constraint**: `_apply_narrators()` was inserting into the `narrators` table without `sort_name`, violating the NOT NULL constraint — now uses `generate_sort_name()` from `name_parser`
- **Narrator junction table cleanup**: Enrichment now handles both "Unknown" and "Unknown Narrator" placeholder entries in `book_narrators` — replaces either variant when real narrator data arrives from Audible
- **SQL nosemgrep annotation in f-string**: Two `cursor.execute(f"""  # nosemgrep:` calls in `audiobooks.py` embedded the `#` comment inside the SQL string, causing `sqlite3.OperationalError: unrecognized token` on `/api/filters` — restructured to place annotations on the `cursor.execute(` line
- **Podcast episodes in main library**: Wondery podcast episodes with `content_type='Product'` and no ASIN were not caught by the enrichment pipeline's publisher detection — Phase 0 now handles this at backfill time

## [8.0.4] - 2026-04-08

### Added

- **Enrichment provider chain**: Tiered metadata enrichment pipeline with four providers — Local (ASIN/series from files), Audible API (series, ratings, categories, editorial reviews), Google Books (ISBN, description, language, publisher), and Open Library (fallback series, subjects). Each provider fills only empty fields, never overwrites existing data
- **Enrichment orchestrator** (`library/scripts/enrichment/__init__.py`): Merge-only-empty semantics, column name validation, side-table writes for `audible_categories` and `editorial_reviews`, backward-compatible result format
- **Backfill enrichment script** (`library/scripts/backfill_enrichment.py`): Two-phase operation — Phase 1 recovers ASINs from `.voucher` files, Phase 2 runs the provider chain on all un-enriched books. CLI supports `--dry-run`, `--limit`, `--asin-only`
- **ASIN extraction from voucher files**: `extract_asin()` in `scanner/metadata_utils.py` now checks three sources in order: `chapters.json`, `.voucher` files in Sources directory, and source filenames
- **Systemd enrichment timer**: `audiobook-enrichment.timer` runs nightly at 3:00 AM with 10-minute random delay, `PartOf=audiobook.target`. The companion `audiobook-enrichment.service` runs the backfill script as a oneshot
- **`enrichment_source` column**: Tracks which provider enriched each book (local, audible, google_books, open_library)

### Fixed

- **Novel regex false positive**: Title pattern "X: A Novel" was extracting "A" as series name — fixed by requiring ≥2 characters in the series name capture group

## [8.0.3.2] - 2026-04-07

### Fixed

- **Mobile card checkbox placement**: Moved the hide/unhide checkbox from upper-left to upper-right on mobile book cards so tapping it selects the book without accidentally opening the detail view
- **Marquee title interaction**: New Books marquee titles are now clickable — tapping a title plays the book directly. Titles glow white on hover and the marquee pauses for easy selection
- **Project directory picker in upgrade UI**: The folder icon button implied a native file picker but only triggered an API scan against nonexistent allowlisted paths. Replaced with a "Scan" button, added `AUDIOBOOKS_PROJECT_DIR` config option, auto-populate and auto-scan when switching to project source, auto-select single results, and accept admin-typed paths directly
- **Path validation on project scan API**: Added null-byte check, `isabs()`, and `isdir()` validation for user-supplied `base_path` in `/api/system/projects` endpoint — resolved 3 CodeQL `py/path-injection` alerts
- **Shell script formatting**: Applied `shfmt` formatting to 12 shell scripts — standardized `case` statement indentation

### Changed

- **CI dependency bump**: Updated `docker/login-action` from 4.0.0 to 4.1.0 (Dependabot #30)
- **Security dependency upgrades**: Updated `requests` to 2.33.1 (CVE-2026-25645), `Pygments` to 2.20.0 (CVE-2026-4539), `pip` to 26.0 (CVE-2026-1703)

## [8.0.3.1] - 2026-04-03

### Changed

- **Error handling hardened across 11 API modules**: Replaced bare `except` clauses with specific exception types and logged tracebacks in `utilities_ops`, `utilities_system`, `library_ops`, `conversion_ops`, `enrichment_ops`, `maintenance_tasks`, `collection_ops`, `preference_ops`, `download_ops`, `auth`, and `admin`
- **Standardized module-level logging**: All API modules now use consistent `logger = logging.getLogger(__name__)` patterns
- **Updated 10 outdated pip dependencies**: Bumped `certifi`, `charset-normalizer`, `idna`, `Jinja2`, `MarkupSafe`, `packaging`, `pip`, `setuptools`, `urllib3`, `Werkzeug` to latest versions

### Security

- **Resolved 10 CodeQL security alerts**: Fixed log-injection vulnerabilities (unsanitized user input in log statements) and path-injection vulnerabilities (unsanitized path construction) across API modules
- **Bulk operation mode validation**: `mode` parameter in bulk operations now validated against an explicit allowlist to prevent injection

### Fixed

- **Documentation sync**: Updated README changelog section, ARCHITECTURE upgrade workflow documentation

## [8.0.3] - 2026-03-31

### Added

- **Logrotate configuration**: `config/logrotate-audiobooks` added to project and installed to `/etc/logrotate.d/audiobooks` by both `install.sh` and `upgrade.sh`; `uninstall.sh` removes it on teardown. Prevents `/var/log/audiobooks/` from growing unbounded.

### Fixed

- **Database path consistency**: `install.sh` was initializing the database at `/var/lib/audiobooks/audiobooks.db` instead of `/var/lib/audiobooks/db/audiobooks.db` (the canonical path used by all other components). Now creates the `db/` subdirectory and places the database there.
- **Systemd service cleanup**: Removed legacy `ExecStartPre` from `audiobook-api.service` that attempted to create `/opt/audiobooks/library/data/` at service start — this conflicted with `ProtectSystem=strict` which makes the filesystem read-only at runtime.
- **User preferences not applied on page load**: View mode (grid/list) and items per page were saved to the server but never loaded or applied when the library page loaded — only sort order was being restored. Added preference loading from both localStorage (instant) and server API (cross-device sync) for all browsing preferences
- **List view mode**: Added full list-view CSS layout — single-column grid with horizontal card layout (cover, title/author, actions), responsive mobile breakpoints
- **Items per page option mismatch**: Shell.html preferences modal offered 20/24/50/100 while the library page selector offered 25/50/100/200; unified to 25/50/100/200 across both locations
- **Default items_per_page**: Fixed backend and frontend defaults from "24" (not a valid option) to "50"
- **Accessibility settings not applied in iframe pages**: `a11y-consumer.css` was created with font-size, line-spacing, contrast, color temperature, and panel darkness rules, but was never linked in any iframe-loadable page (index, utilities, admin, help, about, contact). The shell's `accessibility.js` injected CSS custom properties into the iframe DOM on every load, but without the consuming stylesheet they had zero visual effect

## [8.0.2.2] - 2026-03-31

### Fixed

- **Mypy type errors resolved**: Fixed 47 type errors across 5 api_modular files — `get_db()` calls now use `Path` instead of `str`/`None`, added None guards for unconfigured database paths, removed implicit `None` returns from Flask route handlers
- **Dependabot auto-merge workflow**: Updated `dependabot/fetch-metadata` from invalid SHA (v2.4.0) to `v3.0.0` — workflow was failing on every Dependabot PR due to unresolvable action reference

## [8.0.2.1] - 2026-03-31

### Changed

- **Favicon updated to smile-book variant**: Replaced all 8 favicon/PWA assets (SVG, ICO, 16/32px PNG, apple-touch-icon, Android Chrome 192/512px) with upward-flipped book chevron ("smile") design. Updated Caddy maintenance page inline SVG data URI to match.

## [8.0.2] - 2026-03-31

### Fixed

- **Live Connections race condition**: Shell and iframe both opened competing WebSocket connections with the same session cookie.
  When the iframe's WS replaced the shell's in ConnectionManager, the shell's finally-block unregistered by session_id —
  nuking the iframe's connection too, resulting in "Live Connections: 0" even with active users. Server-side `unregister()`
  is now ownership-aware (only removes if WS object matches). Client-side iframe now receives events via `postMessage`
  bridge from the parent shell instead of opening a duplicate WebSocket.

## [8.0.1.5] - 2026-03-31

### Added

- **Favicon and PWA icons**: Custom headphones+book SVG favicon with full browser coverage — SVG, ICO, 16/32px PNG, apple-touch-icon (180px), Android Chrome icons (192/512px), and web app manifest for PWA home screen shortcuts. All 13 standalone HTML pages include favicon link tags. Caddy maintenance page uses inline data URI for self-contained favicon display.

## [8.0.1.4] - 2026-03-30

### Changed

- **Cyclomatic complexity refactoring**: Extracted nested Flask route handlers to module level across 14 `api_modular` files; decomposed complex functions into focused helpers. 1353 functions scanned — zero at C-grade or worse. All route handlers are now registered at module level with `@blueprint.route()` decorators; `init_*_routes()` functions are thin wrappers that set the module-level `_db_path` variable. No API changes.

### Fixed

- **Sort order persistence across sessions**: Sort selection now survives iframe navigation (back office, collections) and full page reloads; localStorage is the browser-local source of truth with server API as cross-device sync; profile modal sort dropdown aligned with main page values; all preference PATCHes use `keepalive` to complete even during page unload

## [8.0.1.3] - 2026-03-30

### Fixed

- **Proxy PATCH method**: Added missing `do_PATCH` handler to `proxy_server.py` — previously returned HTTP 501 for all PATCH requests (e.g., admin settings toggle)
- **Proxy PATCH body forwarding**: `_read_request_body` now includes PATCH alongside POST/PUT — previously dropped the JSON body, causing Flask to return HTTP 400
- **CORS PATCH support**: Added PATCH to `Access-Control-Allow-Methods` header in OPTIONS preflight responses

### Changed

- **Download timer interval**: Reduced from 6h to 5min; boot delay reduced from 30min to 2min for faster initial download checks
- **Edison bulb indicator**: Enlarged (28×49 → 40×70px), brighter glow colors, and pulsing scale animation for better visibility in Back Office header

### Fixed

- **Sort order persistence**: Sort preference now persists via localStorage (instant) and server API (cross-device); previously the async API call could race with page load or fail silently for guests; clear-search also syncs sort reset
- **Mobile horizontal overflow**: Changed `.letter-groups` `flex-wrap: nowrap` → `wrap` so letter-group buttons flow to multiple rows on narrow viewports instead of overflowing; added `overflow-x: hidden` on body; added 480px breakpoint for filter actions, compact letter buttons, pagination wrap, and vertically-stacked results info
- **Shellcheck fixes**: SC2162 (add `-r` to `read`), SC2181 (direct exit code check), SC2086 (quote variables), SC2317 (remove unreachable exit), SC2012 (replace `ls` with `find`), SC2001 (use parameter expansion), SC2029 (document intentional client-side expansion) across `launch.sh`, `setup.sh`, `start-dev.sh`, `config-migrations/001_add_run_dir.sh`, `create-release.sh`, `install.sh`, `upgrade.sh`
- **Back Office upgrade panel**: Removed hardcoded dev path placeholder from upgrade source field

## [8.0.1.2] - 2026-03-30

### Added

- **Multi-session login**: Admin-controllable toggle for concurrent device logins — global default setting plus per-user override (yes/no/default) in Back Office
- **Edison bulb indicator**: Animated Edison bulb in Back Office header lights up when there are unread user suggestions
- **Reading glasses icon**: Accessibility button in shell header uses reading glasses icon with label; all shell header buttons now have visible labels

### Changed

- **Shell header restructure**: Shell header reorganized per user feedback — navigation and controls reordered for improved usability
- **`index.html` redirect**: Direct access to `index.html` now redirects to shell wrapper; all shell navigation links target the iframe
- **Complexity refactoring**: Refactored 84 functions from C/D/E/F-grade cyclomatic complexity down to A/B-grade across 47 files — decomposed using helper extraction, table-driven dispatch, and guard clauses for improved maintainability, readability, and testability
- **Architecture documentation**: Added "Code Quality & Complexity Management" section to ARCHITECTURE.md documenting the complexity policy, refactoring patterns used, and enforcement via `/test` audits

### Fixed

- **Claim token hash mismatch**: Fixed PendingRegistration storing hash of full 32-char token while validate used hash of 16-char truncated token — passkey user creation → claim → login round trips now work correctly
- **Accessibility font size**: Font size setting now applies to `html` element (not `body`), ensuring changes propagate into iframe content
- **Accessibility iframe propagation**: Accessibility settings (font size, contrast, reduced motion) now visually affect iframe content via `a11y-consumer.css`
- **Shell navigation links**: Shell header links now correctly target the iframe instead of the top-level window
- **Sort preference persistence**: Sort preference now persists correctly in shell header; Help and Back Office buttons restored
- **Audit fixes**: SC2024 sudo redirect corrections, script permissions, markdown formatting, and Caddy config formatting
- **Bandit B608 cleanup**: Removed 12 stale `nosec B608` annotations from non-f-string lines; added proper suppression to 40 false-positive SQL f-strings using module constants
- **Converter service**: Added `LimitNOFILE=65536` to `audiobook-converter.service` matching API service configuration
- **Audit pass 2**: Reformatted 36 Python files with ruff; fixed 26 mypy type errors across 15 files (type annotations, Optional guards, urllib.parse.quote import); fixed 19 markdownlint issues in tracked rule and template files
- **Audit pass 3**: Fixed TOTP secret mismatch in `test_player_navigation_persistence.py` (5 test errors);
  sanitized control characters in log messages (`audiobooks.py`, CodeQL py/log-injection #380/#381);
  added content-type allowlist for cover image proxy responses (`proxy_server.py`, CodeQL py/http-response-splitting #382);
  fixed `/api/covers/default.jpg` → `/covers/default.jpg` in `utilities.js` (was always 404);
  replaced dead `author_name` parameter with `entity_label` in merge validation errors (`admin_authors.py`);
  ruff-formatted 5 files; fixed mypy errors in 4 test files
- **Playwright test resilience**: Playwright tests now skip gracefully when the VM library is empty (pristine VM), instead of erroring on missing test data

## [8.0.1.1] - 2026-03-30

### Fixed

- **Cover art loading failures**: Covers now served as static files directly from the proxy, bypassing the Flask proxy hop that caused ~5.5% of cover images to fail loading through the external stack (Cloudflare tunnel → Caddy → proxy → Flask urllib.request). Content-addressed filenames get immutable cache headers.
- **Cover onerror retry**: Cover image `onerror` handler now retries twice with staggered delays (500ms, 1s) before falling back to placeholder, instead of immediately destroying the `<img>` element

## [8.0.1] - 2026-03-29

### Changed

- **Backend consolidation**: Extracted shared `run_async_operation()` and `handle_result()` into `utilities_ops/_helpers.py`, eliminating ~490 lines of duplicated async endpoint boilerplate across library.py, hashing.py, audible.py, and maintenance.py
- **Scanner consolidation**: Created `scanner/utils/` package with shared `SUPPORTED_FORMATS`, `is_cover_art_file()`, and `get_or_create_lookup_id()` — previously duplicated across 4 scanner modules
- **Frontend consolidation**: Extracted shared API client (`js/api.js`) and utility functions (`js/utils.js`) for date formatting, operation polling, and auth checking — removed ~305 lines of duplicated frontend code
- **CSS variable consolidation**: Extracted repeated transition, shadow, and other values into CSS custom properties in theme-art-deco.css; replaced hardcoded values across 13 CSS files
- **Test helper consolidation**: Moved shared `wait_for_thread_completion()` into `tests/helpers/` package for reuse across utilities_ops test files

### Fixed

- **Python 3.14 compatibility**: Added `encodings.idna` import in conftest.py to fix werkzeug hostname resolution under Python 3.14

## [8.0.0] - 2026-03-28

### Added

- **Per-user preferences**: Key-value preference system with `user_preferences` table, full CRUD API (`/api/preferences`), and 19 unit tests — supports theme, layout, and display customization per user
- **Dynamic collections**: Auto-generated collections from enrichment data (genres, narrators, decades, ratings, etc.) with `/api/collections` endpoint and 36 unit tests — browsable curated groupings without manual curation
- **Accessibility quick panel**: Slide-out accessibility settings panel with font size, contrast, reduced motion, and dyslexia-friendly font controls — persists via user preferences API
- **Account preferences UI**: Account settings page for managing display preferences, notification settings, and accessibility options
- **Series metadata on library cards**: Series name and book order number displayed on library card overlays

### Fixed

- **Scanner-importer field mismatch**: `enrich_metadata()` now outputs `eras` (list) in addition to `literary_era` (string) — bulk importer (`import_to_db.py`) expected `eras` but scanner only produced `literary_era`, silently dropping era data for all bulk-scanned audiobooks
- **None genre crash**: `categorize_genre(None)` no longer crashes with `AttributeError` — now returns uncategorized
- **Narrator counts SQL**: `/api/narrator-counts` now returns per-narrator counts instead of a single aggregated row (missing `GROUP BY`)

## [7.6.1] - 2026-03-29

### Fixed

- **Credential reset claim flow**: Existing users with reset credentials can now complete the claim process (passkey/TOTP registration). Previously, the claim flow only checked `access_requests` (new users) and rejected existing users from `pending_registrations`
- **Claim URL generation**: Admin credential-reset endpoints now return `/claim.html?username=...&token=...` (browser page) instead of `/auth/register/claim?token=...` (POST-only API endpoint) — fixes 405 Method Not Allowed when navigating to claim URL
- **Token hash mismatch**: All 5 passkey/FIDO2 claim flows stored `hash(full_32_char_token)` but displayed truncated 16-char tokens — claim endpoint hash lookups always failed. Consolidated into `_create_claim_token()` helper that updates the DB hash after truncation
- **CI fix**: Renamed ambiguous single-letter variable `l` to `line` in `test_gunicorn_migration.py` — resolves ruff E741 lint error that caused CI failure in python-security workflow

## [7.6.0] - 2026-03-28

### Added

- **Art Deco UI polish**: Knife switches for marquee/maintenance dismissal, Back Office brass button, Help section door knocker, warm copper/brass/gold link colors replacing browser-default blue
- **Toast deduplication**: 10-second cooldown per unique message+type prevents toast spam during long-running operations

### Changed

- **Scan timeout architecture**: Overall timeout check moved inside the stdout read loop (old `process.wait` was unreachable after blocking read loop); increased to 2 hours for large libraries (437GB+)
- **Operation polling robustness**: `pollOperationStatus` checks `response.ok` before parsing JSON; after 10 consecutive errors, gracefully stops with informational message instead of showing "ID: undefined"

### Fixed

- **Case-insensitive sorting**: Added `COLLATE NOCASE` to all remaining text-column `ORDER BY` clauses — author, narrator, genre, publisher, and series sorts now ignore case
- **GeventWebSocket crash**: Replaced removed `GeventWebSocketWorker` with standard gevent worker — fixes gunicorn startup failure
- **Letter-group filter wrapping**: Fixed A-Z filter buttons wrapping at tablet widths (1024px) — `flex-wrap: nowrap` on `.letter-groups`, vertical stacking on `.filters-container` at 769-1024px
- **Card header truncation**: Added overflow protection (`text-overflow: ellipsis`, `min-width: 0`) to `.card-header` and `.catalog-card` — prevents "SCAN & IMPOR..." truncation
- **Cabinet tabs overflow**: Fixed Back Office tab bar overflow with `flex: 1 1 0; min-width: 0` and ellipsis on tab text

## [7.5.3] - 2026-03-28

### Added

- **Test coverage expansion**: 3305 tests (up from ~2282), 95.66% coverage — comprehensive unit tests for all backend modules
- **v8 version-gated test markers**: `@pytest.mark.v8` auto-skips tests for v8 features when VERSION major < 8, enabling parallel v7/v8 development
- **Phase ST project validation**: `/test --phase=ST` now validates any project's test suite (discoverability, redundancy, fixture conflicts, isolation) in addition to framework self-test

### Fixed

- **Sort options**: Populate name columns (`author_last_name`, etc.) and rebuild junction tables — fixes all author/narrator sort options
- **Artifact cleanup**: Systemic cleanup of orphaned covers, backups, sessions, supplements, and staging files
- **Unused import**: Removed `is_group_name` from `scripts/populate_names.py` (flagged by ruff)

## [7.5.2.1] - 2026-03-27

### Fixed

- **Author deduplication**: Unicode apostrophe/dash variants (curly quotes, en-dashes) now normalize before dedup — eliminates duplicate author entries like "Patrick O'Brian" vs "Patrick O\u2019Brian"
- **Publication date sort**: Grouped author/narrator views now sort books chronologically using COALESCE chain (published_date → release_date → published_year), with title as tiebreaker
- **Narrator enrichment gap**: Audible enrichment now persists narrator metadata to both flat columns and normalized tables — fixes "Unknown Narrator" for newly acquired books like "52 Pickup"

## [7.5.1.3] - 2026-03-27

### Changed

- **Roadmap → Forthcoming**: Renamed all user-facing "Roadmap" labels to "Forthcoming" across help and utilities pages
- **Help header**: Added "Coming soon to your local branch library" link below suggestions button

### Fixed

- **Instance badge** (`shell.html`): Empty red pill visible in production header — CSS `display:inline-block` overrode HTML `hidden` attribute; switched to explicit `style="display:none"`
- **safeFetch misuse** (`utilities.js`): Fixed `deleteRoadmapItem()` and save handler — `safeFetch()` returns parsed JSON, not a Response object

## [7.5.1.2] - 2026-03-27

### Fixed

- **Roadmap admin panel** (`utilities.js`): `loadRoadmapAdmin()` always showed "Failed to load roadmap" because `safeFetch()` returns parsed JSON, not a Response object — removed redundant `.ok` check and double `.json()` parse

## [7.5.1.1] - 2026-03-27

### Fixed

- **Security: CodeQL #372** (`utilities_system.py`): Replace exception text leak with generic error message in Cloudflare API handler
- **Security: CodeQL #373 / ReDoS** (`suggestions.py`): Lazy quantifier in HTML-stripping regex to prevent polynomial backtracking
- **Security: XSS** (`utilities.js`): Escape `file_path` via `escapeHtml()` in duplicate list rendering
- **Legacy cleanup** (`upgrade.sh`): Added `deploy.sh` and `deploy-vm.sh` to legacy file removal list

## [7.5.1] - 2026-03-27

### Added

- **User suggestion comment pad** (`help.html`): Users can submit feature suggestions and comments with an admin notification drawer for reviewing submissions
- **Admin-editable roadmap** (`roadmap.html`): Back Office roadmap page with admin editing capability, `content_type` filter on library, and login redirect fix
- **v8 design spec** (`docs/superpowers/specs/`): Collections overhaul and user preferences design specification for future v8 release

### Changed

- **Isolated audible-cli venv**: `audible-cli` now runs in a separate virtualenv (`/var/lib/audiobooks/audible-venv`) to resolve httpx version conflicts. `install.sh` and `upgrade.sh` manage this venv independently.
- **Art Deco button styling**: Account page buttons (`.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-inline`) redesigned with gradient backgrounds, 3D box-shadows, hover lift effects, and active press effects

### Fixed

- **Iframe logout loop**: Shell iframe now redirects `window.top` on auth failure instead of creating nested login pages. Login page includes iframe breakout guard.
- **Suggestion drawer visibility**: `.suggestion-drawer[hidden]` now uses `display: none !important` to prevent flash-of-content
- **Production safety gate** (`upgrade.sh`): Added validation that `--from-project` directory is a real project, not production install path
- **Security: Bandit B310** (`utilities_system.py`): URL scheme validation before `urlopen` for Cloudflare cache purge
- **Security: Bandit B608** (`import_to_db.py`): Column name whitelist for dynamic SQL in enrichment data restore

## [7.5.0] - 2026-03-26

### Added

- **Metadata enrichment suite** (`library/scripts/`): Five new scripts for enriching and verifying audiobook metadata:
  - `enrich_from_audible.py` — queries all Audible API response groups (ratings, categories, reviews, series, language, subtitles, publisher summary, cover URLs, 15+ fields)
  - `enrich_from_isbn.py` — Google Books and Open Library fallback enrichment for books without ASINs
  - `enrich_single.py` — inline enrichment called automatically after each new book is imported
  - `populate_series_from_audible.py` — populate series data from Audible API in bulk
  - `verify_metadata.py` — cross-references embedded file tags vs Audible vs ISBN data, detects conflicts, auto-corrects high-confidence issues
- **Schema expansion** (migration 012): 18 new columns on `audiobooks` table plus `audible_categories` and `editorial_reviews` tables for richer metadata storage
- **Auto-enrichment at import time**: `import_single.py` and `add_new_audiobooks.py` now automatically enrich and verify each new book at import time
- **Cache purge endpoint** (`POST /api/system/purge-cache`): Purges Cloudflare CDN cache and browser Cache API on library Refresh. Authenticates via CF token file.
- **Toast notifications on Refresh**: Library Refresh now shows auto-dismissing toast notifications instead of blocking `alert()` dialogs

### Fixed

- **FTS5 trigger corruption** (migration 013): External content FTS5 tables require `DELETE + INSERT`, not `UPDATE SET`. Was silently corrupting the search index on every book update.
- **Opus metadata date fields**: `metadata_utils.py` now parses `published_year`, `published_date`, and `acquired_date` from `streams[0].tags` so date-based sorting works for Opus files.
- **Upgrade preflight for GitHub check**: `upgrade.sh` GitHub `--check` path now writes `upgrade-preflight.json` so the web UI "Start Upgrade" button is no longer blocked after a successful "Check for Updates". Fixed filename mismatch in `upgrade-helper-process`.
- **audiobook-redirect service at boot**: `install.sh` now explicitly enables `audiobook-redirect` in the `systemctl enable` loop, so HTTP→HTTPS redirect auto-starts after reboot.

## [7.4.2] - 2026-03-25

### Added

- **FIDO2 test flag** (`--fido2`): New pytest flag in `conftest.py` controlling hardware vs. software FIDO2 authenticator. Without `--fido2`, FIDO2 tests run automatically with a software authenticator (sets `FIDO2_SOFTWARE=1`). With `--fido2`, tests require a physical hardware key (e.g., YubiKey). The `--hardware` flag now explicitly excludes FIDO2 tests. Added `hardware_touch_attempt()` helper for hardware key touch retries (up to 3 attempts within 90 seconds).
- **Account button on index.html**: Added My Account button and modal directly to `index.html` so users can manage their account regardless of whether they access the page through the shell wrapper or directly. Account modal CSS extracted from `shell.css` into shared `account.css`.

### Changed

- **TOTP authenticator recommendations**: Updated from Authy to 2FAS (free, open source, multi-platform) across README.md, help.html, register.html, SECURE_REMOTE_ACCESS_SPEC.md, and utilities.js. Authy has ended free multi-device sync; 2FAS is the recommended replacement.

### Fixed

- **Upgrade filepicker "No projects found"**: The project browser in Back Office → System → Upgrade was showing "No projects found" because the API only searched hardcoded paths (`~/projects`, `/opt/projects`) and ignored the directory typed in the input field. Backend now accepts `base_path` query parameter; frontend passes the input value. Also broadened matching to include any directory with a `VERSION` file.
- **upgrade.sh author migration PYTHONPATH**: Fixed `PYTHONPATH` passed to `migrate_to_normalized_authors` — changed from `$target` to `$target/library` and module path from `library.backend.migrations.migrate_to_normalized_authors` to `backend.migrations.migrate_to_normalized_authors`, matching the actual package layout inside the install directory.
- **CI: `test_websocket.py` import error**: Fixed broken import `from library.backend.api_modular.websocket import ConnectionManager` → `from backend.api_modular.websocket import ConnectionManager`, resolving 95-commit CI failure.
- **Security dependency updates** (`requirements.txt`): Added `qrcode[pil]>=8.2` (required for TOTP QR code generation), pinned `requests>=2.32.3` (CVE-2024-47081 path traversal fix). Added corresponding path traversal guard in `utilities_system.py`.
- **Security: path injection in project browser** (`utilities_system.py`): Refactored `list_projects()` to validate user-provided `base_path` against an allowlist of configured safe directories, preventing directory enumeration outside permitted paths. Resolved 6 CodeQL `py/path-injection` HIGH alerts.
- **CI: `test_get_db_returns_connection` failure**: Fixed test to monkeypatch `DB_PATH` to the Flask app's test database, resolving `sqlite3.OperationalError` on GitHub Actions where the default system path doesn't exist.
- **Test reliability: Playwright VM tests**: Added VM reachability check to `test_player_navigation_persistence.py` so tests skip gracefully when the test VM is stopped instead of producing connection errors.
- **Test warnings: custom pytest marks**: Registered `integration`, `fido2`, `hardware`, and `docker` custom marks in `conftest.py` to suppress `PytestUnknownMarkWarning`.

## [7.4.1.2] - 2026-03-25

### Fixed

- **Disappearing account button (root cause: Cloudflare Rocket Loader)**: Rocket Loader was rewriting `<script>` tags in shell.html, deferring `account.js` execution and causing the account button to fail initialization. Disabled Rocket Loader at the Cloudflare zone level. Added `data-cfasync="false"` to all script tags across 7 HTML files as defense in depth.
  Rewrote `account.js` so the button is NEVER hidden -- shows username when authenticated, "Sign In" when not. Added MutationObserver guard against external DOM manipulation.
- **Persistent sessions expiring after 30 days**: "Stay logged in" sessions were silently killed after 30 days of server-side inactivity timeout despite having a 1-year cookie. Persistent sessions now never expire from inactivity — they last until the user explicitly signs out. Session cookie extended to ~10 years.
- **Test assertion for table count**: Updated `test_auth.py` table count from 18 to 19 to account for `user_hidden_books` table added in v7.4.1.1.
- **Test path resolution**: Fixed `test_gunicorn_migration.py` and `test_maintenance_banner.py` to use `Path(__file__).resolve()` instead of hardcoded relative paths that broke when pytest ran from `library/` subdirectory.

## [7.4.1.1] - 2026-03-24

### Added

- **My Library hide/unhide**: Users can hide finished or unwanted books from the My Library view using card checkboxes + Hide/Unhide button. Hidden books are preserved (positions, history, downloads intact) and can be restored from the Hidden view.

### Fixed

- **Upgrade preflight gate lost on page refresh**: `preflightData` was stored only in JS memory — a page refresh forced re-running "Check for Updates" even though the backend still had a valid preflight file. Now hydrates from `/api/system/upgrade/preflight` on page load. Also aligned frontend staleness timeout to match backend (10 min → 30 min).
- **Upgrade maintenance page showed no progress**: Static `maintenance.html` (served by Caddy when API is down) only polled `/api/system/health` — no stage updates during upgrade. Now the upgrade-helper mirrors status to `/etc/caddy/upgrade-status.json` and `maintenance.html` renders live stage progress with checkmarks.
- **Account button permanently hidden after upgrade restart**: `account.js` hid the user profile button on any single `/auth/account` failure with no retry. After API restart, the brief unavailability caused the button to vanish. Added retry logic with backoff (superseded by full rewrite in v7.4.1.2).

## [7.4.1] - 2026-03-24

### Added

- **Web-based admin user management** (USERS tab in Back Office): create users with TOTP/Magic Link/Passkey auth, change username/email, switch auth method, reset credentials, toggle admin/download roles, delete accounts
- **Last-admin guard**: prevents deletion or demotion of the last remaining admin account
- **Audit logging** for all user management actions: actor, target user, action type, details (JSON), timestamp — stored in `audit_log` table (schema version 7)
- **Paginated, filterable audit log** in Back Office USERS tab with notification badge for new entries
- **Real-time WebSocket push** for audit events to connected admin sessions
- **Self-service My Account modal** in shell header: authenticated users change username, email, auth method, or credentials without admin involvement
- **Admin notification helpers**: in-app badge increment and email notification on critical user actions (role changes, deletions)
- **Granular admin user endpoints** (v7.4+): `PUT /auth/admin/users/<id>/username`, `PUT .../email`, `PUT .../roles`, `PUT .../auth-method`, `POST .../reset-credentials`, `DELETE .../delete`, `GET .../audit-log`
- **Self-service endpoints**: `PUT /auth/user/me/username`, `PUT .../email`, `PUT .../auth-method`, `POST .../reset-credentials` with ownership enforcement
- **Database schema v7**: `audit_log` table (actor_id, target_user_id ON DELETE SET NULL, action, details JSON, created_at) and `last_audit_seen_id` column on users
- **Legacy `DELETE /auth/admin/users/<id>`**: now records audit entry and enforces last-admin guard (parity with new endpoint)
- **Auth fixture helpers** (`conftest.py`): `make_admin_client()`, `make_user_client()`, `create_test_user()` for user management test isolation

### Changed

- **`PATCH /auth/admin/users/<id>`** (legacy edit-profile): now records `update_profile` audit log entry and validates username max-length 24 chars
- **USERS tab JS**: edit modal now calls granular endpoints (`/username`, `/email`) instead of the legacy combined PATCH, producing per-field audit entries
- **Username validation**: max length enforced as 24 characters (ASCII printable, 3–24 chars) consistently across backend schema, API validation, and client-side guards in `admin.js` and `account.js`
- **`SCHEMA_VERSION`** bumped to 7 for `audit_log` table and `last_audit_seen_id` column

### Fixed

- **WebSocket double handshake**: Removed `geventwebsocket.handler.WebSocketHandler` from direct `api_server.py` execution — `flask_sock` handles WebSocket upgrades natively; both handlers together caused duplicate 101 responses that corrupted WebSocket framing
- **Proxy WebSocket blocking**: Changed `proxy_server.py` from single-threaded `HTTPServer` to `ThreadingHTTPServer` — active WebSocket tunnels no longer block all other HTTP requests
- **SSL buffer starvation in proxy WebSocket tunnel**: Added `ssl.SSLSocket.pending()` check before `select.select()` — heartbeats arriving through TLS were stuck in the SSL decryption buffer, invisible to `select()`, causing server-side receive timeouts
- **Live Connections always showing 0**: Fixed race condition where `/api/admin/connections` was fetched before WebSocket had time to register; now uses delayed initial fetch, event-driven refresh on WebSocket open, 30-second polling, and refresh on Activity tab click
- **upgrade.sh backup cleanup**: Root-owned `.pyc` files in old backups caused `rm -rf` to fail under `set -e`, silently aborting the upgrade before file sync; added sudo fallback
- **Admin delete endpoint**: JS `usersTab.js` was calling the wrong path (`/delete` missing); now targets the audited `/delete` route correctly
- **Toggle admin/download endpoints**: `toggle_user_admin` and `toggle_user_download` now record audit entries for each role change
- **Legacy `AccessRequestRepository` methods**: relocated from `UserRepository` to correct class; fixed test isolation for `access_requests` table
- **Self-deletion guard**: `admin_delete_user_v2` now checks if actor is deleting their own account and rejects with 400 before last-admin check
- **Access request cleanup**: deleting a user now also removes their pending access requests to prevent orphaned entries
- **Stale docstrings**: `3-32 chars` corrected to `3-24 chars, ASCII printable` in username constraint documentation
- **TOTP QR code display**: Embedded base64 PNG in 6 API endpoints that return TOTP setup data; frontend now uses data: URI for display and download instead of a non-existent image endpoint
- **Unified account button**: Consolidated two separate user buttons (shell.html header + index.html iframe) into a single account button in the shell header with full modal (profile, auth management, contact admin, sign out, delete account)
- **upgrade.sh service restart**: Added root UID detection so `systemctl` runs properly when upgrade.sh is invoked via `sudo` (services were silently not restarting)
- **Stack-trace exposure**: Auth health check endpoint no longer returns raw exception strings in error responses
- **Cache busting**: All HTML files now use consistent `?v=` timestamps across CSS/JS references
- **Tutorial outdated references**: Updated tutorial step targeting removed `#user-menu` element to reference the account button in the header bar
- **Help page**: Updated "Your Profile" section to reflect the new My Account modal
- **CodeQL false positive suppression**: Added `lgtm[]` inline comments to prevent recurring alerts for test code (verify=False, chmod 777) and known-safe production patterns (flask-debug, path-injection)
- **Account panel QR code rendering**: `showSetupResult()` in account.js now displays the QR code image when changing auth method to TOTP (was previously text-only)
- **Dead code cleanup**: Removed ~135 lines of orphaned `.user-menu` CSS from auth.css and dead `getElementById("user-menu")` calls from library.js after v7.4.0 UI migration
- **Exception information exposure**: Duplicate checksum endpoints in duplicates.py no longer return raw `str(e)` in error responses

## [7.3.0.1] - 2026-03-23

### Added

- **Art Deco themed error pages** for Caddy reverse proxy: maintenance (upgrade) page with librarian-on-ladder bookshelf scene and sliding progress bar; unavailable page with librarian-pushing-cart scene and blinking status indicator
- **Generic unavailable page** (`caddy/unavailable.html`) — shown by Caddy on 502/503 when backend is unreachable, with JS health polling (5s) and meta-refresh (30s) fallback for auto-recovery

### Changed

- **Maintenance page** (`caddy/maintenance.html`) restyled from plain dark theme to full Art Deco design matching the app (sunburst panel, gold chevron border, diamond lattice background, Optima font, gold/brass/cream palette)

## [7.3.0] - 2026-03-22

### Added

- **Mandatory preflight check system** (LEAPP-inspired): Upgrades require preflight validation before execution
- **Always-on backup with rolling retention** (keep last 5): Backup is no longer optional
- **`--skip-service-lifecycle`** internal flag for helper-owned lifecycle
- **Full upgrade feature parity in web UI**: Force, major version, and specific version fields
- **GET `/api/system/upgrade/preflight`** endpoint with staleness computation
- **Preflight gate on POST `/api/system/upgrade`**: Blocks upgrades without valid preflight (unless force)
- **Caddy maintenance page** for external visitors during upgrade (auto-reloads via health polling)
- **Resilient browser upgrade overlay** with 9-step progress tracking (tolerates API downtime)
- **Upgrade consistency enforcement rule** (`.claude/rules/upgrade-consistency.md`)

### Changed

- **Helper lifecycle** rewritten to 9-step orchestration with status file durability
- **`--backup` flag** is now a no-op (backup always runs)
- **Upgrade overlay** uses textContent/DOM APIs exclusively (no innerHTML)

### Fixed

- **Service name bug in upgrade-helper-process**: `audiobooks-*` (plural) → `audiobook-*` (singular) — ALL web-triggered service operations were silently failing
- **CSP headers**: Added `wss:` and `ws:` WebSocket schemes to `connect-src` in proxy_server.py (blocked WebSocket connections in strict CSP environments)
- **SQL injection**: Parameterized raw string interpolation in maintenance.py `_get_history()` and `_get_windows()` queries
- **Flask debug bind**: Fixed `app.run(host='localhost')` to `app.run(host='127.0.0.1')` in maintenance.py — `localhost` may resolve to `::1` on IPv6 systems
- **python-security.yml**: Fixed pip-audit invocation path to use venv pip directly
- **ASIN GLOB test**: Corrected `test_audiobooks_extended.py` ASIN pattern from `B0*` to `[AB][0-9A-Z]*` to match real Audible ASIN format
- **Dev DB orphaned refs**: Cleaned stale foreign-key references in audiobooks-dev.db (orphaned edition, position, and hash records)
- **`library/launch-v3.sh`**: Deleted deprecated launch script (replaced by `audiobook.target` systemd service)

## [7.2.1.1] - 2026-03-21

### Added

- **`upgrade.sh --major-version`**: New flag for major version upgrades — forces venv rebuild (removes old deps like waitress, installs new ones), runs config migrations, enables new services
- **`upgrade.sh` audit and cleanup**: Every upgrade now scans for and fixes broken symlinks, orphaned systemd units, stale legacy files, and deprecated config variables
- **`install.sh --fresh-install`**: Reinstall from scratch while preserving audiobook library and user settings (ports, auth, data dirs)
- **Config migration system**: `config-migrations/` directory with numbered idempotent scripts that add new config variables to existing installations
- **`show_usage()`**: Both `upgrade.sh` and `install.sh` now show comprehensive formatted help with `--help`, `-h`, or no arguments

### Changed

- **Documentation**: All Waitress references updated to Gunicorn+geventwebsocket across README, ARCHITECTURE, INSTALL.md, install-services.sh, proxy_server.py
- **`launch-v3.sh`**: Marked as deprecated with notice pointing to systemd services
- **`audiobooks.conf.example`**: Added `AUDIOBOOKS_RUN_DIR` setting

## [7.2.1] - 2026-03-21

### Fixed

- **Maint Sched tab never initialized**: MutationObserver watched `style` attribute changes but the tab system uses `classList.toggle("active")` — `initMaintSched()` was dead code, so task dropdown was always empty and schedule toggle never worked
- **Task Type dropdown empty**: Added `credentials: "same-origin"` to all fetch calls, placeholder option, and error state instead of silent `.catch()`
- **Dates labeled "(UTC)"**: Removed misleading label — `datetime-local` input works in the user's local timezone; added helper text clarifying automatic conversion
- **Datetime picker not discoverable**: Added explicit "Pick" button calling `showPicker()` and styled `::-webkit-calendar-picker-indicator` with `invert` filter for dark theme visibility
- **Cron scheduling hidden**: Promoted from buried radio button under Recurring to top-level "Cron (advanced)" dropdown option with inline format examples
- **Announcement banner not activating**: `sendMessage()` now dispatches `CustomEvent` directly after POST; added `visibilitychange` re-fetch for tab-switch catch-up

### Changed

- **Cache-Control headers**: Proxy now sends `no-cache` for HTML (revalidate each request), `immutable, max-age=1yr` for versioned JS/CSS (`?v=`), `max-age=5min` for unversioned JS/CSS, `max-age=1day` for images/fonts
- **Cache-busting**: Added `?v=` parameters to all new JS/CSS assets (maint-sched.js, websocket.js, maintenance-banner.js, maintenance-banner.css)

## [7.2.0] - 2026-03-21

### Added

- **Maintenance scheduling system**: Cron-based automated task execution with 5 built-in tasks (db_vacuum, db_integrity, db_backup, library_scan, hash_verify)
- **WebSocket infrastructure**: Migrated from Waitress to Gunicorn+geventwebsocket for real-time bidirectional communication
- **Maintenance announcement banner**: Pulsing indicator with expandable panel, SVG knife switch dismiss control, Web Audio API synthesized sounds
- **Maintenance scheduler daemon**: `audiobook-scheduler.service` with file lock, notification queue, and graceful shutdown
- **Admin Maint Sched tab**: Full CRUD for maintenance windows, manual announcements, execution history
- **Notification bridge**: Gevent greenlet polls DB every 5s, broadcasts to WebSocket clients
- **Proxy WebSocket tunneling**: Raw TCP socket relay in proxy_server.py for WebSocket upgrade requests

### Changed

- **API server**: Migrated from Waitress to Gunicorn with `GeventWebSocketWorker` (`-w 1` hard constraint for in-memory connection manager)
- **Docker entrypoint**: Updated from Waitress to Gunicorn startup
- **Requirements**: Replaced `waitress` with `gunicorn`, `gevent`, `gevent-websocket`, `flask-sock`, `croniter`

## [7.1.3.4] - 2026-03-20

### Fixed

- **Systemd restart resilience**: Increased `StartLimitIntervalSec` from 60s to 300s and `RestartSec` to 30s across all long-running services (api, proxy, redirect, converter, mover) — services that depend on slow RAID array mounts were hitting the start limit and locking out permanently at boot

## [7.1.3.3] - 2026-03-19

### Changed

- **Play always resumes**: Play button now always resumes from the user's last saved position — removed the separate Resume button from all views (grid cards, book detail modal, edition view, grouped view) as unnecessary UX clutter
- **Position threshold lowered**: Save/resume position threshold reduced from 30 seconds to 5 seconds across the entire stack (frontend save guards, frontend position reader, backend API validation)

### Fixed

- **Position lost on scrub/skip**: Scrubbing the progress bar, pressing +30s/-30s skip buttons, or using media session seek controls now immediately saves position to localStorage and queues API sync — previously these operations only modified `audio.currentTime` without persisting, so closing the browser after scrubbing while paused would lose the position entirely
- **Position save interval**: Reduced localStorage save interval from 30s to 5s and API save delay from 15s to 5s for more frequent persistence during playback
- **CRLF injection sanitization**: Strip `\r` and `\n` from query string in `proxy_server.py` redirect to prevent HTTP response splitting (CodeQL #315)
- **Docker CVE pins**: Add `pyopenssl>=26.0.0` (CVE-2026-27448, CVE-2026-27459) and bump `pyasn1>=0.6.3` in `requirements-docker.txt`
- **Unused import**: Remove unused `auth_if_enabled` import from `utilities_system.py`

## [7.1.3.2] - 2026-03-18

### Fixed

- **Resume button missing on mobile**: Scoped `.btn-resume` and `.btn-download` CSS hide rules to `.book-card` only — the book detail modal Resume button was hidden on all mobile browsers (Android Chrome/Brave/Firefox, iOS Safari/iPadOS) due to unscoped `display: none !important` in responsive breakpoints
- **Book detail modal obscured by browser chrome**: Changed modal from bottom-sheet (`align-items: flex-end`) to centered layout with safe area inset padding on all edges — the Play/Resume buttons were hidden behind mobile browser navigation bars
- **Comprehensive safe area audit**: Added `env(safe-area-inset-*)` and `--browser-chrome-bottom` protection to all fixed/edge-touching elements across 8 CSS files: modals, sidebar, auth pages, user dropdown menu, backoffice toasts/modals, help tooltips, tutorial tooltips, and help page

## [7.1.3.1] - 2026-03-18

### Fixed

- **Version endpoint auth**: Removed `@auth_if_enabled` from `/api/system/version` — the About page fetches version without authentication, so the endpoint must be public. Previously displayed "Unknown" for unauthenticated visitors
- **Path info leak**: Removed `project_root` from version API response — internal filesystem paths should not be exposed to unauthenticated users

## [7.1.3] - 2026-03-18

### Fixed

- **Mobile viewport**: Shell layout uses `visualViewport` API with dynamic `--app-height` and `postMessage` to communicate browser chrome offset to iframe, preventing mobile browser bottom bars from obscuring UI (Resume button, scrubber, player controls)
- **Clean URL (complete fix)**: All auth pages (`login.html`, `verify.html`, `claim.html`) and autoplay redirect now navigate to `/` instead of `shell.html`, ensuring browser address bar always shows the clean canonical URL
- **Proxy query string handling**: `proxy_server.py` now uses `urlparse` to separate path from query string, correctly serving `/?autoplay=...` and preserving query strings across `/shell.html` → `/` redirects
- **Continue badge removed**: Removed "Continue" text badge overlay from book cover art images — the Resume button is sufficient indication of saved position

## [7.1.2.1] - 2026-03-17

### Added

- **Cover Art Resolver**: New tiered external cover art resolver (`scanner/utils/cover_resolver.py`) fetches missing covers from Audible CDN (by ASIN), Open Library API, and Google Books API as a fallback when embedded and sidecar covers are absent

### Changed

- **Clean URL**: Web proxy now serves shell.html content directly at `/` instead of 302 redirect; `/shell.html` returns 301 → `/` for clean browser URLs

### Fixed

- **Mobile player clipping**: Added `env(safe-area-inset-bottom)` CSS padding to shell player to prevent bottom browser chrome from covering the scrub bar on mobile devices

## [7.1.2] - 2026-03-16

### Fixed

- **Critical: Position save reset bug** — Playback position could reset to 0:00 when auto-save fired at the start of playback (e.g., pausing then resuming "A Dirty Job" lost 6h+ of progress). Root cause: `onTimeUpdate()` auto-save guard used `> 0` instead of `> 30`, allowing near-zero positions (168ms) to overwrite real saved positions when audio restarts from the beginning
- **Frontend defense-in-depth** — All position save paths (auto-save, pause, book-switch, page-close, `savePosition()`, `flushToAPI()`) now guard against positions < 30s, consistent with the read-side filter
- **Backend defense-in-depth** — API endpoint `PUT /api/position/<id>` now rejects positions 1–29999ms with HTTP 422, preventing near-zero positions from reaching the database regardless of frontend behavior (position 0 is still allowed for intentional clear on book completion)
- **Reset auto-save timer on book switch** — `_lastSaveTime` is now reset in `playBook()` so auto-save doesn't fire immediately with a stale timer when restarting a book

## [7.1.1.1] - 2026-03-16

### Changed

- **Cloudflare credentials**: Cache purge script and upgrade.sh now source credentials from `~/.config/api-keys.env` (shared with cloudflare-manager) instead of requiring a dedicated token file at `/etc/audiobooks/cloudflare-api-token`
- **Zone ID hardcoded**: thebosco.club zone ID is now hardcoded, eliminating an unnecessary API lookup on every purge
- **Config cleanup**: Removed `CF_TOKEN_FILE` from `audiobook-config.sh` (no longer needed)

## [7.1.1] - 2026-03-16

### Added

- **3D Cuboid Buttons**: All buttons throughout the entire UI now have a pronounced 3D cuboid appearance with visible colored wall shadows (dark brown walls, gold highlights) — main library, back office, admin panel, player, sidebar, modals, help, tutorial, auth, and shell pages
- **Tab Color Identity**: BROWSE ALL tab has distinct gold identity; MY LIBRARY tab has distinct emerald identity for visual differentiation
- **Cloudflare Cache Purge Script**: New `audiobook-purge-cache` standalone script for manual CDN cache purging with auto-detected zone ID, selective URL purging, and quiet mode for scripting
- **Upgrade Cache Purge**: `upgrade.sh` now automatically purges Cloudflare CDN cache after both local and remote deployments (non-fatal — skips if no API token configured)
- **Config Variables**: Added `CF_TOKEN_FILE` and `CF_ZONE_ID` to `audiobook-config.sh` for Cloudflare CDN integration

### Changed

- **3D Shadow System**: Replaced invisible `rgba(0,0,0,...)` box-shadows with colored wall shadows (`#4a3520`, `#6b5030`, `#3a2810`) that are visible on the dark Art Deco theme
- **Cache Busters**: Switched from semantic version strings (`?v=7.1.3`) to timestamp-based cache busters (`?v=1773686866`) across all 13 HTML files and all CSS `@import` chains to reliably bypass Cloudflare CDN caching

### Fixed

- **CSS Cache Chain**: Fixed `@import` cache-buster mismatch where HTML `<link>` tags had updated versions but inner CSS `@import` statements still referenced old versions, causing Cloudflare to serve stale theme CSS
- **Scrub Bar & Position Saving**: Fixed player scrub bar, position saving, and Resume button functionality (committed in prior session as d548ed2)

## [7.1.0] - 2026-03-14

### Added

- **Help Page**: Added FAQ section with 10 common questions about the library (multi-device sync, downloads, grouped vs sorted view, collections, media keys, etc.)
- **Help Page**: Added Grouped View documentation to the sorting section explaining collapsible author/narrator headers
- **Tutorial**: Updated Sort Options step to mention grouped view feature

### Changed

- **Documentation**: Comprehensive audit of all documentation for v7.0.0-v7.0.2 changes — updated README, ARCHITECTURE, CONTRIBUTING, INSTALL, QUICKSTART, UPGRADE_GUIDE, and API docs with multi-author normalization, grouped views, admin endpoints, and normalized database tables
- **Documentation**: Fixed stale port references (5000 → 5001) and removed obsolete `api.py` references in library docs
- **Description**: Updated project description to reflect removal of Audible sync (removed in v6.3.0); now reads "Self-hosted audiobook library browser with conversion, web player, and optional Audible downloading"
- **Help Page**: Updated cache busters from v7.0.0 to v7.0.2

## [7.0.2] - 2026-03-14

### Fixed

- Fixed `upgrade.sh --from-github` failing with "Invalid target directory" due to 0-indexed array bug in `find_installed_dir()` (`${found[1]}` → `${found[0]}`)

## [7.0.1] - 2026-03-14

### Fixed

- Updated `install-manifest.json` with BTRFS subvolume entries for `/var/lib/audiobooks` and `/etc/audiobooks`
- Updated ARCHITECTURE.md recommended subvolume layout to reflect NVMe-backed state and config directories
- Resolved all 343 E501 line-too-long violations across 70+ files (88 char limit)
- Added `defusedxml` for safe XML parsing in librivox_downloader.py (S314 security fix)
- Fixed `from library.` import paths that failed under pytest (ModuleNotFoundError)
- Updated hardcoded-path test scanner to handle multi-line `environ.get()` calls

## [7.0.0] - 2026-03-13

### Added

- **Multi-author/narrator normalization**: New `authors`, `narrators`, `book_authors`, and `book_narrators` tables provide proper many-to-many relationships. Books with multiple authors (e.g., "Stephen King, Peter Straub") now have each author as a separate, sortable entity.
- **Name parser module** (`name_parser.py`): Three-tier metadata extraction — structured tags, delimiter splitting (semicolons, "and", "&"), and comma disambiguation ("Last, First" vs "Author1, Author2"). Handles group names (Full Cast, BBC Radio), compound last names (de, van, von, le), and role suffixes.
- **Grouped sort view**: New "Author (Grouped A-Z)" and "Narrator (Grouped A-Z)" sort options in the frontend. Books appear under collapsible author/narrator headers with Art Deco styling. Multi-author books appear under each author group.
- **Grouped API endpoint**: `GET /api/audiobooks/grouped?by=author|narrator` returns books grouped by normalized author/narrator with sort_name ordering and orphan "Unknown" group.
- **Enriched flat API**: `/api/audiobooks` response now includes `authors` and `narrators` arrays alongside existing flat string fields for backward compatibility.
- **Admin correction endpoints**: `PUT /api/admin/authors/<id>` (rename), `POST /api/admin/authors/merge` (merge duplicates), `PUT /api/admin/books/<id>/authors` (reassign). Symmetric endpoints for narrators. All operations regenerate flat text columns.
- **Schema migration** (`011_multi_author_narrator.sql`): DDL for normalized tables with foreign keys and indices. Applied automatically during `upgrade.sh`.
- **Data migration** (`migrate_to_normalized_authors.py`): Idempotent script parses flat author/narrator columns and populates junction tables. Group name redirection ensures entities like "Full Cast" are always classified as narrators.

### Changed

- **Author/narrator sidebar filter**: Now displays individual names from normalized `authors`/`narrators` tables instead of raw composite strings from flat columns. 572 individual authors (was 138 composite strings). Sorted by last name.
- **Author/narrator book filtering**: Selecting an author in the sidebar now uses junction table JOINs for exact matching, finding all books linked to that author — including multi-author books.
- **Narrator counts**: `/api/narrator-counts` now uses normalized narrator table for accurate per-narrator book counts.
- **upgrade.sh**: Now detects and applies database schema migrations automatically. Checks for `authors` table existence before applying DDL, runs data migration if tables are empty. `--force` flag now forwarded to remote SSH deploys.
- **Cache busting**: All CSS/JS version strings updated to `v=7.0.0` across all HTML files and CSS `@import` chain.

### Fixed

- **Role suffix exclusion**: Names with role suffixes (translator, editor, foreword, illustrator, etc.) are excluded from the authors table during migration. 43 entries like "Frances Riddle - translator" properly filtered.
- **Schema migration reliability**: `apply_schema_migrations()` extracted as standalone function, runs even when versions are identical (handles cases where code was deployed but migration didn't run). Fixed quote stripping from `AUDIOBOOKS_DATABASE` config value.

## [6.7.2.4] - 2026-03-05

### Fixed

- **UTC timezone label**: Invitation timestamps in admin UI now display "UTC" suffix so admins don't mistake UTC times for local time
- **Cache-buster sync**: Updated `?v=` query strings to 6.7.2.4 across all 12 HTML files and CSS `@import` chain (stale at 6.7.2.2, caused browser to serve cached JS without invitation timestamp feature)

## [6.7.2.3] - 2026-03-05

### Added

- **Back office invitation timestamps**: Admin user list now shows "Invited: [date]" and "Expires/Expired: [date]" for users who haven't logged in yet, replacing the uninformative "Never" label. Expired invitations display in red, pending ones in yellow.
- **API invitation data**: `/auth/admin/users` endpoint now includes `invite_expires_at` and `invite_expired` fields for unclaimed invitations, sourcing expiry from `pending_recovery` (magic link) or `access_requests` (TOTP/passkey)

## [6.7.2.2] - 2026-03-03

### Fixed

- **Marquee ticker mode**: When few new books don't fill the viewport, marquee now uses a single-pass news-ticker scroll (right-to-left, no visible duplication) instead of the 2-copy infinite loop. Classic seamless scroll still activates when content fills or overflows the viewport.
- **Cache-buster sync**: Updated `?v=` query strings to 6.7.2.2 across all 12 HTML files and CSS `@import` chain (were stale at 6.6.3 or 6.7.2)
- **Backup code recovery**: Fixed login.html backup code form calling non-existent `/auth/login/backup` — now correctly calls `/auth/recover/backup-code` and displays new TOTP credentials
- **JS formatting**: Auto-formatted all 7 JavaScript files with Prettier for consistent style

## [6.7.2.1] - 2026-03-03

### Fixed

- **Marquee viewport fill**: New books marquee now measures one cycle's width and repeats content enough times to always overflow the viewport, eliminating visible duplication when only 1-2 books are new

## [6.7.2] - 2026-03-02

### Added

- **Mobile book detail bottom-sheet**: Tapping compact book cards on mobile (≤480px or landscape) opens a slide-up modal showing full details — cover art, title, author, narrator, format, duration, progress, and Play/Resume/Download buttons
- **Compact card tap handler**: Event delegation on book grid detects compact layout via `matchMedia` and routes taps to the detail modal instead of default card behavior

### Fixed

- **Double-click Play bug**: Fixed the root cause of needing to click Play twice — `shellPlay()` now preserves play intent via `sessionStorage` when redirecting from bare `index.html` to `shell.html`, and `shell.js` picks up the autoplay parameter on load
- **Player bar layout shift**: Shell player bar now overlays content (position: fixed, z-index: 9999) instead of shrinking the iframe from 100% to `calc(100% - 80px)`, eliminating the visual "refresh" that users perceived as the first click failing
- **Dev Caddyfile**: Changed `try_files` fallback from `/index.html` to `/shell.html` (correct entry point) and `X-Frame-Options` from `DENY` to `SAMEORIGIN` (allows same-origin iframe embedding)
- **Landscape media query desktop leak**: Added `max-width` constraints to landscape-orientation media queries (`960px` and `1024px`) so they no longer match desktop browser windows that happen to have a landscape aspect ratio

## [6.7.1.5] - 2026-03-01

### Fixed

- **Audit fixes**: Version sync across Dockerfile, install-manifest, SECURITY.md; Docker env var bridging for `HTTP_REDIRECT_PORT`/`HTTP_REDIRECT_ENABLED`; shellcheck fixes (SC2076, SC2064, SC2120); auth.db backup retention (keep 5); `LimitNOFILE=65536` for API service; marshmallow minimum bumped to >=4.0.0
- **Test coverage**: Fixed 2 skipped supplement scan tests — replaced stale mock targets with real filesystem operations, bringing test count from 1415 to 1417
- **Code formatting**: 16 Python files reformatted with ruff

## [6.7.1.4] - 2026-03-01

### Fixed

- **Mobile responsive overhaul**: Complete rework of phone viewport layouts — portrait shows 4-column dense grid with 40px icon covers, wrapping non-bold titles, and only title/author/play visible. Landscape (≤700px height) shows 10-column dense grid with same compact treatment. Small phones (≤360px) inherit compact rules cleanly. Desktop unchanged.
- **CSS cascade priority**: Fixed responsive.css overrides being ignored on mobile — `@import` cascade order caused library.css base rules to win at equal specificity. All mobile overrides now use `!important` to compensate (documented in CSS comments).
- **Dead CSS cleanup**: Removed conflicting card/cover/title rules in Section F (≤360px) that were overridden by Section E2's `!important` rules at ≤480px.

## [6.7.1.3] - 2026-02-28

### Fixed

- **Play/resume plumbing**: Fixed `shellPlay()` ignoring the `resume` parameter — both Play and Resume buttons previously did the same thing. Resume now correctly restores saved position; Play starts fresh. Same-book unpause short-circuits without reloading.
- **upgrade.sh service restart safety**: Added EXIT trap with dirty-flag pattern so services always restart if the script dies mid-upgrade (previously `set -e` could kill the script between `stop_services` and `start_services`, leaving services dead — caused production 502)
- **Genre scanner→importer disconnect**: Connected scanner genre output (`genre` field) to importer genre input (`genres` field) so scanned genre data actually populates the database
- **NAMESPACE crash**: Prevented API service crash when `library/data` directory is missing on fresh installs

## [6.7.1.2] - 2026-02-27

### Fixed

- **Shell script permissions**: `upgrade.sh` now ensures all `.sh` files are world-readable (755) after upgrade — fixes `/etc/profile.d` scripts failing to `source` shared libraries like `audiobook-config.sh` when permissions were 711

### Changed

- **ARMv7 homage**: Added a tribute to 32-bit ARM users in README

## [6.7.1.1] - 2026-02-27

### Changed

- **Drop ARM/v7 platform support**: Removed `linux/arm/v7` from Docker multi-arch builds — `sqlcipher3` package's Conan build system does not support the armv7l architecture, causing CI build failures on v6.7.0.2 and v6.7.1
- **Docker platforms**: Now builds for `linux/amd64` and `linux/arm64` only (x86-64 and 64-bit ARM including Apple Silicon, Raspberry Pi 3/4/5)

### Fixed

- **Docker CI build failure**: Resolved `sqlcipher3` arm/v7 build error that blocked Docker image publishing for v6.7.0.2 and v6.7.1

## [6.7.1] - 2026-02-27

### Fixed

- **Cloudflare 524 timeout**: Resolved origin server timeout when navigating from audio player back to library — Waitress thread starvation caused by audio streaming holding worker threads during playback
- **SQLite WAL mode**: Enabled Write-Ahead Logging on all API database connections so position sync writes no longer block library page reads
- **SQLite performance**: Added `synchronous=NORMAL`, `cache_size=8MB`, and `busy_timeout=5s` pragmas across all API connection factories
- **N+1 query elimination**: Replaced per-book query loop in `GET /api/audiobooks` (300 queries per page) with 6 batch queries using `WHERE IN` for genres, eras, topics, supplements, and edition detection
- **Thread capacity**: Increased Waitress worker threads from 4 to 16 to handle concurrent audio streams alongside API requests

## [6.7.0.3] - 2026-02-27

### Fixed

- **Play button regression**: Resolved play button failures in shell+iframe architecture — buttons (play, pause, resume, download) stopped working between v6.6.6.1 and v6.7.0.2
- **postMessage bridge**: Replaced cross-frame postMessage with direct `window.parent.shellPlayer` access (Cloudflare proxy chain blocked postMessage)
- **Shell player scope**: Changed `let shellPlayer` to `var shellPlayer` so it's accessible as a `window` property from the iframe (root cause of two-click play bug)
- **Book property normalization**: Normalized API property names (`id`/`cover_path` vs `bookId`/`coverUrl`) in shell player
- **User gesture window**: Reordered `playBook()` to call `audio.play()` before async `getBestPosition()` to preserve browser autoplay gesture activation
- **Button text scaling**: Added CSS container queries with `clamp()` font-size to prevent button text truncation on narrow cards
- **Audio CORS**: Removed unnecessary `crossOrigin='anonymous'` attribute on audio element (same-origin streaming)
- **Cache-busting**: Bumped query params to `?v=6.7.0.5` across shell.html and index.html to bypass Cloudflare edge cache

## [6.7.0.2] - 2026-02-26

### Added

- **Streaming-only note**: Help page Audio Player section now explains that The Library streams from server storage and cannot access user-side files, with link to Downloads section
- **Offline player recommendations**: Help page Downloads section recommends audiobook players per platform (VLC, foobar2000, IINA, Celluloid, BookMobile, Smart AudioBook Player) for Windows, Mac, Linux, iOS, and Android
- **Download tutorial step**: Interactive tutorial now includes an optional step for the Download button explaining the streaming-only design

### Changed

- **Download button tooltip**: Updated to explain streaming-only design and recommend local players for offline listening

## [6.7.0.1] - 2026-02-25

### Added

- **Docker ARM/v7 support**: Docker images now build for linux/amd64, linux/arm64, and linux/arm/v7 (covers Raspberry Pi and other 32-bit ARM devices)

### Changed

- **Production data isolation rules**: Clarified test/QA isolation boundary — no live mounts to production storage (copies are fine); added release leak prevention safeguards for licensed content

## [6.7.0] - 2026-02-25

### Added

- **Persistent Player**: New shell+iframe architecture — audio playback persists across page navigation. `shell.html` wraps content in an iframe while keeping `<audio>` element and player controls in the parent frame
- **Shell Player**: Full-featured player bar with play/pause, rewind/forward 30s, speed cycling (0.5-2.5x), volume, progress scrubbing, close button
- **Media Session API**: Lockscreen/notification controls for audio playback
- **postMessage Bridge**: Bidirectional communication between shell and iframe content pages for play/pause/seek/playerState
- **iframe Link Safety**: Auth page links from content pages use `target="_top"` to navigate out of iframe; JS redirects use `window.top.location.href`

### Changed

- **Security Headers**: X-Frame-Options changed from `DENY` to `SAMEORIGIN`; CSP updated with `frame-ancestors 'self'` and `frame-src 'self'` to allow same-origin iframe embedding
- **Login Flow**: Auth pages (login, claim, verify) now redirect to `shell.html` instead of `index.html`

### Fixed

- **My Library**: Added `credentials: 'include'` to `savePositionToAPI()` and `getPositionFromAPI()` — session cookies were not sent, server saw unauthenticated requests, and My Library appeared empty for all users
- **Button Spinners**: Fixed `.button-loading` spinners visible before user interaction on login/claim pages — `display: inline-flex` was overriding HTML `hidden` attribute

### Removed

- **Audible Sync**: Removed dead frontend Audible sync code (~80 lines) — `syncWithAudible()`, timer management, and all call sites. Backend was already cleaned up
- **AudioPlayer/PlaybackManager**: Removed from `library.js` (moved to `shell.js` as `ShellPlayer`); removed `<audio>` element and player overlay from `index.html`

## [6.6.7] - 2026-02-25

### Fixed

- **Admin UI**: Resolved audiobook titles in admin activity log — denormalized title into auth DB at event time instead of relying on cross-DB ID lookups that break after library reimport

## [6.6.6.1] - 2026-02-25

### Fixed

- **Upgrade**: Service detection grep pattern `audiobooks` → `audiobook-` to match actual unit names — stop/start were silently skipping all service management during upgrades
- **Upgrade**: Added `*.target` to systemd unit file deployment glob — `audiobook.target` was not being copied to `/etc/systemd/system/`
- **Scripts**: Converted 28 remaining zsh `read -r "var?prompt"` instances to bash `read -r -p "prompt" var` across install.sh (13), uninstall.sh (3), upgrade.sh (2), install-user.sh (4), migrate-api.sh (1), install-services.sh (4) — interactive prompts were silently broken since bash conversion
- **CI**: Removed stale "Install zsh" step from release.yml (all scripts use bash since v6.6.5)

## [6.6.6] - 2026-02-24

### Changed

- **Auth**: Username limits changed from 5-16 to 3-24 characters — updated SQL schema, migrations, Python backend, CLI, JavaScript frontend, HTML forms, and tests

### Fixed

- **Scripts**: Replaced all zsh `${0:A:h}` syntax with bash `$(dirname "$(readlink -f "$0")")` in 9 wrapper scripts — broke all CLI commands when called via symlinks
- **Scripts**: Removed `local` keyword outside function scope (SC2168) in `move-staged-audiobooks` and `monitor-audiobook-conversion` — `local` only works inside functions in bash
- **Scripts**: Fixed `find-duplicate-sources` crash on empty Sources directory — empty associative array triggered `set -u` unbound variable error
- **Scripts**: `audiobook-help` now sources config to display resolved paths instead of literal `$VARIABLE` names
- **Install**: Fixed `((issues_found++))` → `((issues_found++)) || true` in `install.sh` (8 locations) and `migrate-api.sh` (3 locations) — bash arithmetic `((0++))` returns exit code 1, killing scripts under `set -e`
- **Install**: `chown /var/log/audiobooks` for audiobooks user, source files get 644 and shell scripts get 755 permissions on deploy
- **Install**: Fixed `lib/audiobook-config.sh` permissions from 711 to 755; fixed 21 source files from 600 to 644
- **Install**: Added `chown audiobooks:audiobooks` for data subdirectories (`Library/`, `Sources/`, `Supplements/`) — were created as root-owned, blocking service writes
- **Install**: `embed-cover-art.py` wrapper now uses venv Python for mutagen dependency instead of system Python
- **Install**: Added `DATA_DIR="/var/lib/audiobooks/data"` to generated `audiobooks.conf` and create `.index` directory with proper ownership
- **Install**: Wrapper script templates in `install.sh` and `install-user.sh` now generate `#!/bin/bash` shebangs
- **Manifest**: Fixed `install-manifest.json` DB path to `/var/lib/audiobooks/audiobooks.db`
- **Converter**: Fixed critical `$AAXC_FILE` → `$SOURCE_FILE` variable in 3 locations — was causing stale queue entries and re-processing of already-converted files
- **Converter**: Changed shebang to `#!/bin/bash` (required for `export -f` function exports)
- **Services**: Added `RequiresMountsFor=/srv/audiobooks` to mover service to prevent race conditions at boot
- **Systemd**: Moved `StartLimitIntervalSec`/`StartLimitBurst` from `[Service]` to `[Unit]` section in `audiobook-api.service` — systemd was ignoring these directives with warnings when placed in `[Service]`
- **Systemd**: `audiobook-proxy.service` uses venv Python (`/opt/audiobooks/library/venv/bin/python`) instead of system Python
- **Proxy**: Removed duplicate CORS headers from proxy error responses
- **Mover**: `move-staged-audiobooks` now uses venv Python for `import_single.py`
- **Shell**: Reverted all 35 scripts from `#!/usr/bin/env zsh` back to `#!/bin/bash` — bash is the universal Linux standard, maximizes portability across distros
- **Shell**: Removed all zsh-specific workarounds (reserved variable comments, echo JSON corruption notes, `${0:A:h}` syntax)
- **Shell**: Simplified `audiobook-config.sh` source guard from dual bash/zsh to bash-only
- **Shell**: Converted all zsh syntax to bash equivalents — `${(L)var}` → `${var,,}`, `typeset -A` → `declare -A`, `${(@kv)}` → `${!array[@]}`, `(N)` → `shopt -s nullglob`
- **Upgrade**: Added `audiobook-downloader.timer` and `audiobook-shutdown-saver` to upgrade.sh service stop/start lists
- **Uninstall**: Replaced `arr=($(cmd))` with `mapfile -t arr < <(cmd)` to fix ShellCheck SC2207 word splitting
- **CI**: Added `libsqlcipher-dev` system dependency for Python tests

### Added

- **CI**: ShellCheck linting in GitHub Actions — catches shell script errors at PR time

## [6.6.5.1] - 2026-02-24

### Changed

- **Auth**: Unified all invitation expiry to 48 hours — TOTP/passkey claim tokens now store `claim_expires_at` in `access_requests` table; magic link invitations changed from 24h to 48h
- **Auth**: All invitation email templates updated with correct 48-hour expiry notice
- **Auth**: Added `claim_expires_at` column to `access_requests` table with automatic migration

## [6.6.5] - 2026-02-24

### Added

- **Collections**: "Lectures" collection (Feynman Physics x4, Carol Ann Lloyd x1) with `bypasses_filter=True`

### Changed

- **Library**: Lectures and Great Courses content hidden from main library — only visible through dedicated collections
- **Collections**: Great Courses collection updated with `bypasses_filter=True`
- **Collections**: "Podcasts & Shows" query updated to also exclude Lecture content type

### Fixed

- **Cover Art**: Standalone cover recovery — scanner now finds standalone `.jpg`/`.png` files (extracted by converter) and copies them to `.covers/` cache when no embedded cover art exists in Opus files. Recovers 645 of 646 previously missing covers
- **Converter**: Added `VENV_PYTHON` variable for cover art embedding — `embed_ogg_cover()` now uses the venv Python (which has mutagen) instead of bare `python3`

## [6.6.4] - 2026-02-24

### Added

- **Collections**: "Podcasts & Shows" collection with `bypasses_filter=True` for non-audiobook content types (Podcast, Show, Episode, Radio/TV)
- **Scripts**: `populate_content_types.py` — queries Audible API library + catalog to tag content types

### Changed

- **Converter**: Only processes DRM-encrypted formats (AAXC/AAX/AA); playable formats (MP3, M4A, etc.) are skipped with a message instead of silently ignored

### Fixed

- **Config**: Bash/zsh compatibility in `audiobook-config.sh` — detects `BASH_SOURCE[0]` before falling back to zsh `${0:A:h}`, preventing unbound variable errors in bash scripts with `set -u`
- **Service**: Removed vestigial `/opt/audiobooks/library/data` from `ReadWritePaths` that caused 230+ namespace failures when directory didn't exist under `ProtectSystem=strict`
- **Service**: Added `StartLimitBurst=5`/`StartLimitIntervalSec=60` to prevent rapid restart loops
- **Service**: Changed proxy `Requires` to `PartOf` so proxy restarts with API service
- **Converter**: Fixed queue builder prefix title matching — added word-boundary enforcement so "trial" no longer false-matches "trials of koli"
- **Install**: `ReadWritePaths` patching in `install.sh` and `upgrade.sh` when `AUDIOBOOKS_DATA` differs from `/srv/audiobooks`
- **Cover Art**: Warning when ffmpeg succeeds but cover file not created (ProtectSystem=strict silently blocks writes)

## [6.6.3] - 2026-02-23

### Added

- **Collections**: Restructured from flat list to hierarchical tree with 18 top-level genres and 35 subgenre children (53 navigable items). Categories: special, main, nonfiction, subgenre
- **Collections**: Collapsible sidebar navigation — parent genres have toggle arrows for expand/collapse of subgenre branches
- **Collections**: New genres: Romance, Politics & Social Sciences, Religion & Spirituality, Young Adult, plus subgenres (Police Procedurals, Espionage, Hard-Boiled, Noir, Space Opera, Dystopian, Post-Apocalyptic, etc.)

### Changed

- **UI**: Replaced fixed font-size/padding/gap values with CSS `clamp()` across all 9 CSS files for smooth fluid scaling at any viewport size
- **UI**: Removed redundant breakpoint overrides — breakpoints now only handle structural layout changes (flex-direction, grid columns, element hiding)
- **UI**: Wrapped grid `minmax()` with `min()` to prevent horizontal overflow on narrow screens

### Fixed

- **UI**: Eliminated jarring layout jumps between breakpoints on mobile and tablet viewports
- **Deploy**: Consolidated `deploy.sh` and `deploy-vm.sh` into `upgrade.sh` with `--remote`, `--user`, and `--yes` flags

## [6.6.2.6] - 2026-02-23

### Fixed

- **Deploy**: Fixed all deployment scripts (`deploy.sh`, `install.sh`, `upgrade.sh`) using pyenv shim for venv creation — symlinks into `/home/` are inaccessible under systemd `ProtectHome=yes`, causing API crash-loops. Now explicitly uses system Python (`/usr/bin/python3.14`) with broken-venv and `/home/`-path detection
- **Deploy**: Added `--exclude='venv'` to `deploy-vm.sh` rsync to prevent overwriting production venvs with dev pyenv-based venvs
- **Upgrade**: Added post-upgrade venv health check to `upgrade.sh` — detects broken symlinks and pyenv paths, recreates with system Python and reinstalls dependencies

## [6.6.2.5] - 2026-02-23

### Fixed

- **Collections**: Fixed historical-fiction collection returning 0 results — was using `genre_query('Fiction')` (non-existent genre) instead of `genre_query("Historical Fiction")` (now returns 102 books)
- **Collections**: Fixed action-adventure collection only catching 13 books via text search — switched to `multi_genre_query(["Action & Adventure", "Adventure", "Sea Adventures"])` (now returns 190 books)
- **Collections**: Removed dead `text_search_query()` helper function (no longer used by any collection)
- **Tests**: Updated schema version assertions from 5 to 6 and table count from 16 to 17 in test_auth.py, test_auth_api.py, and test_upgrade_safety.py (reflect migration 006 adding webauthn_credentials table)

## [6.6.2.4] - 2026-02-23

### Fixed

- **Auth**: Added `safeJsonParse()` to all 8 auth HTML pages — gracefully handles HTML error responses (500s, WAF blocks) instead of crashing with `Unexpected token '<'`
- **Auth**: Added missing `webauthn_credentials` table to schema.sql and migration 006 — passkey/security key registration was failing with `no such table` error
- **Auth**: Reordered WebAuthn response handling to check `response.ok` before parsing JSON, preventing parse errors on error responses

## [6.6.2.3] - 2026-02-23

### Fixed

- **Web UI**: Added cache-busting version params (`?v=X.Y.Z`) to all `<script>`, `<link>`, and CSS `@import` references across all 12 HTML files — prevents browsers from serving stale JS/CSS after deploys
- **Web UI**: Fixed user dropdown menu extending beyond left browser edge — changed `right: 0` to `left: 0` in `.user-dropdown` CSS
- **Web UI**: Added null guards to `escapeHtml()`, `selectAuthor()`, and `selectNarrator()` in library.js to prevent "null" text in filter/search inputs

## [6.6.2.2] - 2026-02-22

### Added

- **Uninstall**: Comprehensive `uninstall.sh` with dynamic discovery — finds and removes all traces (27 symlinks, 12 systemd units, configs, certs, runtime files, user/group) with `--keep-data`/`--delete-data`/`--dry-run`/`--force` options
- **Uninstall**: Group membership cleanup before `groupdel` to prevent PAM/SSH failures for other users

### Fixed

- **Install**: zsh reserved variable bugs — `local path=` corrupts `$PATH` (tied variable), `local status=` fails (read-only); renamed to `target_path`/`svc_state` across install.sh, upgrade.sh, migrate-api.sh
- **Install**: `show_detected_storage()` silent abort when directories don't exist yet — added fallback defaults
- **Docs**: Corrected VM snapshot revert procedure (discard overlay, don't commit into base)

## [6.6.2.1] - 2026-02-22

### Added

- **Upgrade**: `--force` flag for `upgrade.sh` to allow same-version reinstall

### Fixed

- **Docs**: Updated `paths-and-separation.md` to reflect actual production layout (`/opt/audiobooks`)

## [6.6.2] - 2026-02-22

### Added

- **Auth**: Magic link UX overhaul for non-technical users — admin invite defaults to magic link, auto-fill claim page from email URL params with auto-submit, inline "Send me a new link" form on expired verify page, improved login magic link sent state
- **UI**: Mobile responsive utilities — horizontal scroll tabs, iOS auto-zoom prevention, small phone (≤480px) and landscape orientation breakpoints

### Changed

- **UI**: Removed Audible Sync tab, section, and all related JS/CSS (replaced by per-user position tracking)
- **UI**: Utilities tabs reduced from 7 to 6 (Database, Conversion, Duplicates, Bulk Ops, Activity, System)
- **Dependencies**: Removed `audible` and `audible-cli` packages from requirements (Audible Sync removed)

### Fixed

- **Auth**: Edit Profile passkey switching — added `novalidate` on form, explicit button types to prevent browser validation errors
- **Auth**: Missing `import json` in auth.py WebAuthn registration handler (F821)
- **Auth**: Claim email URLs now include username and token params for auto-fill
- **UI**: Marquee "NEW" badge showing with no titles — fixed guard to check `data.books.length` instead of `data.count`
- **UI**: Marquee click-anywhere-to-dismiss removed (only dismiss button works now)
- **UI**: Edit Profile modal off-screen on small viewports — added `modal-small` class
- **CI**: Fixed `audible` vs `httpx` version conflict that broke CI tests and pip-audit

## [6.6.1.1] - 2026-02-22

### Added

- **Auth**: Magic link authentication as selectable auth method in claim flow — users choose TOTP, passkey, or magic link during account setup
- **Auth**: Profile auth method switching — users can switch between TOTP, passkey, and magic link from their profile settings
- **UI**: Auth method selector added to utilities.html invite modal (TOTP, Magic Link, Passkey) with contextual hints, defaulting to magic link

### Fixed

- **Build**: `test-report.md` and `audit-*.log` added to `.gitignore` (were being tracked)

## [6.6.1] - 2026-02-22

### Added

- **Security**: HTTP security headers on all API responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy` (default-src 'self', media-src 'self' blob:), `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- **Security**: `Strict-Transport-Security` header (HSTS, 1-year, includeSubDomains) when HTTPS is enabled
- **Config**: `AUDIOBOOKS_HTTP_REDIRECT_ENABLED` variable added to `lib/audiobook-config.sh` defaults (default: true)
- **Tests**: `.coveragerc` added with 85% minimum coverage threshold

### Changed

- **CI**: Upgraded Python version in `ci.yml` from 3.11 to 3.14 to match project requirements
- **Security**: Session cookies hardened with `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`

### Fixed

- **Security**: Patched CVE-2025-43859 (h11 HTTP request smuggling) — upgraded h11 to 0.16.0, httpcore to 1.0.9, httpx to 0.28.1
- **Install**: `tmpfiles.conf` source filename corrected in `install.sh` and `upgrade.sh` (was using wrong path pattern, causing `/tmp/audiobook-staging` and `/tmp/audiobook-triggers` to not be recreated on reboot)
- **Security**: `NoNewPrivileges=yes` added to `audiobook-upgrade-helper.service` (was incorrectly set to `no`)
- **Manifest**: `install-manifest.json` updated to version 6.6.1, corrected port 8081 → 8080 for HTTP redirect, corrected `audiobook-mover` expected state from `inactive` to `active`
- **Docker**: `.dockerignore` glob patterns fixed (`__pycache__` → `**/__pycache__`, `*.py[cod]` → `**/*.py[cod]`) to exclude Python bytecode in all subdirectories
- **Tests**: `test_player_features_documented` decoupled from `test_audiobook` fixture (fixture was required but never used by the test body)

## [6.6.0] - 2026-02-22

### Changed

- **Scripts**: Eliminated script drift between repo and production — replaced 6 stale full copies in `/usr/local/bin/` with symlinks to canonical `/opt/audiobooks/scripts/` location
- **Scripts**: Added versioned wrapper scripts to `scripts/` directory (audiobook-api, audiobook-web, audiobook-scan, audiobook-import, audiobook-config, audiobook-user, audiobook-upgrade, audiobook-migrate) replacing inline generation
- **Deploy**: Added `refresh_bin_symlinks()` function and SCRIPT_ALIASES map to deploy.sh, upgrade.sh, install.sh, install-system.sh, and deploy-vm.sh for consistent symlink maintenance
- **Install**: Replaced inline wrapper script generation with shared symlink refresh pattern across all installation entry points

## [6.5.0.1] - 2026-02-22

### Changed

- **CLI Naming**: Standardized all CLI commands from plural `audiobooks-*` to singular `audiobook-*` across install scripts, systemd services, docs, and install-manifest
- **YAML**: Fixed 76 yamllint issues (document-start markers, truthy quoting, indentation, line-length wrapping) across all workflow and config YAML files
- **Markdown**: Fixed ~2,255 markdownlint issues (heading spacing, code block language specifiers, list formatting) across 40+ documentation files
- **Shell**: Applied shfmt formatting to scripts/purge-users.sh

## [6.5.0] - 2026-02-22

### Added

- **Release Workflow**: Two-phase release support (`--local` stage and `--promote` publish) for testing releases before publishing to GitHub

### Changed

- **Systemd**: Added restart limits to proxy service for boot race recovery
- **Systemd**: Added `RequiresMountsFor` data directory mount dependency to prevent boot race 502s
- **CSS**: Improved header flex-wrap and refined marquee neon styling
- **CSS**: Corrected viewport handling for layout consistency

### Fixed

- **Security**: Fixed log injection vulnerability in utilities_crud.py (integer cast sanitization)
- **Dependencies**: Added missing `audible-cli` to requirements.txt
- **Tests**: Backoffice integration tests gracefully skip when Audible is unconfigured
- **Systemd**: Corrected venv path in audiobook-api service file
- **Scripts**: Separation check no longer falsely flags legitimate production symlinks

## [6.4.0.1] - 2026-02-22

### Fixed

- **Scripts**: Separation check in `upgrade.sh` and `install.sh` falsely flagged legitimate production symlinks as dev contamination — `grep "$SCRIPT_DIR"` matched `/opt/audiobooks` paths when run from production; changed to check for `ClaudeCodeProjects` specifically
- **Scripts**: Fixed `install.sh` glob pattern from `audiobooks-*` to `audiobook-*` to match actual symlink names

## [6.4.0] - 2026-02-22

### Added

- **Guest Access**: Unauthenticated visitors can browse the library, search, and view book details without an account
- **Guest Gate**: Play/download buttons show a styled tooltip directing guests to sign in or request access
- **Magic Link Auth**: Email-based authentication as an alternative to TOTP — admin can invite users with magic link auth type
- **Magic Link Login**: Users with magic_link auth type receive sign-in links via email instead of entering TOTP codes
- **Auth Method Preference**: Users can switch between TOTP, passkey, and magic link authentication in their profile
- **Persistent Login**: Multi-layer session persistence (cookie + localStorage + IndexedDB) with "Stay logged in" option
- **Session Restore**: `POST /auth/session/restore` endpoint recovers sessions from client-side storage
- **Auth Status**: `GET /auth/status` public endpoint returns auth state for frontend guest/user detection
- **Upgrade Safety**: Pre-upgrade auth database backup and post-upgrade validation in `upgrade.sh`
- **Schema Migration v4→v5**: Adds `magic_link` auth type, `is_persistent` session flag, `preferred_auth_method` on access requests
- **Purge Script**: `scripts/purge-users.sh` — reusable script to delete users not in a keep list
- **Docker Tests**: 19 comprehensive Docker container tests (build, lifecycle, API, volumes, env, security)
- **Upgrade Safety Tests**: Migration integrity tests verifying tokens, sessions, and credentials survive schema upgrades

### Changed

- **Docker**: Upgraded base image from `python:3.11-slim` to `python:3.14-slim` (Debian Trixie, Python 3.14.3)
- **Docker**: Added `apt-get upgrade -y` and `pip install --upgrade pip` for security patching
- **Docker**: Created `requirements-docker.txt` excluding `audible` package (not needed in standalone container)
- **Auth Endpoints**: Read-only API endpoints (`/api/audiobooks`, `/api/collections`, etc.) now use `@guest_allowed` instead of `@auth_if_enabled`
- **Login UI**: Magic link users see email-based login flow instead of TOTP/passkey forms
- **Admin Invite**: Invite modal includes auth method selector (TOTP, Magic Link, Passkey)

### Fixed

- **Test**: Fixed `test_generate_backup_code_format` — `isupper()` returns `False` for all-digit strings, changed to `part == part.upper()`
- **Docker**: Increased health check timeout for slower build environments
- **Docker**: Fixed entrypoint bind address for container networking

## [6.3.0] - 2026-02-21

### Added

- **Per-User State**: New auth database tables for listening history, download tracking, and user preferences (migration `004_per_user_state.sql`)
- **API**: New `/api/user/history` endpoint — per-user listening history with pagination and date filters
- **API**: New `/api/user/downloads` endpoint — per-user download history with pagination
- **API**: New `/api/user/downloads/<id>/complete` endpoint — record download completion
- **API**: New `/api/user/library` endpoint — personalized library view with progress bars and recently listened
- **API**: New `/api/user/new-books` endpoint — books added since user's last visit
- **API**: New `/api/user/new-books/dismiss` endpoint — mark new books as seen
- **API**: New `/api/admin/activity` endpoint — admin audit log with filtering by user, type, and date range
- **API**: New `/api/admin/activity/stats` endpoint — aggregate activity statistics (listens, downloads, active users, top content)
- **API**: New `/api/genres` endpoint — list all genres with book counts
- **API**: New `PUT /api/audiobooks/<id>/genres` endpoint — set genres for a single audiobook
- **API**: New `POST /api/audiobooks/bulk-genres` endpoint — add/remove genres across multiple audiobooks
- **UI**: My Library tab with progress bars, listening history, and recently-listened section
- **UI**: Art Deco neon new-books marquee highlighting recently added audiobooks
- **UI**: About The Library page with credits, third-party attributions, and dynamic version display
- **UI**: Activity audit section in Back Office with stats cards, top-listened/downloaded lists, filterable activity log, and pagination
- **UI**: Genre management in Back Office Bulk Ops — genre picker with add/remove modes and new genre creation
- **UI**: JavaScript fetch/blob download with completion tracking (replaces raw anchor downloads)
- **Docs**: Help page updated with sections for My Library, progress tracking, downloads, and new books
- **Docs**: Tutorial updated with steps for new per-user features
- **Tests**: Multi-user integration tests and auth-disabled fallback tests
- **Tests**: Per-user state schema and model tests
- **Tests**: About page, activity audit UI, genre management, help update tests

### Changed

- **Position Sync**: Removed Audible cloud sync dependency — position tracking is now fully local and per-user
- **Position Sync**: Positions stored in encrypted auth database (SQLCipher) instead of main library database
- **Docs**: Rewrote `docs/POSITION_SYNC.md` for per-user local-only system
- **Docs**: Updated `docs/ARCHITECTURE.md` with new tables, blueprints, and endpoint documentation

### Fixed

- **UI**: About page version display parsed raw JSON text instead of extracting version field (`r.text()` → `r.json().version`)

## [6.2.0.1] - 2026-02-20

### Fixed

- **UI**: Header title now visually centered using 3-column flex layout (replaced absolute positioning that caused off-center title with asymmetric nav content)

## [6.2.0] - 2026-02-20

### Added

- **Health**: New unauthenticated `/api/system/health` endpoint for monitoring (returns status, version, database connectivity)
- **UI**: Help system with 11-section user guide and interactive 11-step spotlight tutorial
- **Tests**: 50 new tests for health endpoint, proxy headers, help page, tutorial, header layout

### Changed

- **Security**: FLASK_DEBUG default changed from `true` to `false`
- **Security**: USE_WAITRESS default changed from `false` to `true` (production-safe)
- **Security**: Added `Access-Control-Allow-Credentials` header when CORS origin is specific
- **Security**: Added `@admin_or_localhost` decorator to `/api/system/upgrade/check`
- **Security**: Added hop-by-hop header filtering in proxy responses
- **Infrastructure**: systemd service ExecStart wrapper names aligned with installed scripts
- **Infrastructure**: Dockerfile HEALTHCHECK uses `/api/system/health` instead of data endpoint
- **Infrastructure**: HTTP redirect port corrected (8081 → 8080 to match audiobook-config.sh)
- **Quality**: Shell formatting (shfmt) applied to 45 scripts
- **Quality**: Python formatting (ruff format) applied to all backend code
- **Quality**: YAML lint fixes in CI workflows

### Fixed

- **UI**: Back Office button no longer visible to non-admin users (CSS `display:flex` was overriding `hidden` attribute)
- **UI**: Header restructured with balanced left/right navigation
- **Database**: Added `try/finally` to `get_hash_stats` and `get_duplicates` for connection cleanup
- **Paths**: Eliminated remaining hardcoded data-storage paths in duplicates.py, hashing.py, and scripts — all now use `AUDIOBOOKS_DATA`
- **Docker**: docker-compose.yml image name corrected (`audiobook-toolkit` → `audiobook-manager`)
- **Docker**: Added comprehensive `.dockerignore` entries for dev artifacts
- **Docs**: Added `/api/system/health` to README API table and ARCHITECTURE health checks
- **Docs**: Updated AUTH_RUNBOOK health check script to use `/api/system/health`
- **Branding**: Corrected `greogory` → `TheBoscoClub` in Dockerfile and systemd targets

## [6.1.3] - 2026-02-19

### Fixed

- **Auth**: Rewrite invite flow — invitations no longer pre-create users, eliminating "credentials already claimed" and method selection loop bugs during claim
- **Auth**: TOTP and WebAuthn claim endpoints now read invite metadata for admin-set download permissions
- **Auth**: Delete user now cascade-deletes associated access requests, preventing orphaned records
- **Auth**: Invite endpoint replaces stale access requests instead of blocking with "already exists" error
- **Admin**: Download toggle button now calls correct API endpoint (`/toggle-download` POST instead of non-existent `/permissions` PUT)
- **Scan**: Library rescan progress meter now shows real-time updates in web UI (was stuck at 5% due to ANSI escape codes in scanner output breaking regex parser)

## [6.1.2.1] - 2026-02-18

### Added

- **Admin**: Invite User button in user administration page for pre-registering and approving new users with claim token workflow

## [6.1.2] - 2026-02-18

### Fixed

- **Auth**: First-user registration returned backup codes as formatted string instead of JSON array, causing JavaScript TypeError displayed as "Connection error"
- **Auth**: Added clipboard copy button for TOTP backup codes on registration page
- **Proxy**: HTTP error handler now forwards Flask's original response body instead of generic error message
- **Upgrade**: Removed data directories (e.g. `/srv/audiobooks`) from installed app detection candidates — only actual app installation paths are checked
- **System**: Removed development-specific paths from project discovery endpoint, keeping only `AUDIOBOOKS_PROJECT_DIR` env var and generic fallbacks

## [6.1.1] - 2026-02-18

### Fixed

- **Scripts**: Comprehensive bash-to-zsh compatibility fixes across all shell scripts
  - Convert `read -p` bash-isms to zsh `read "?prompt"` syntax
  - Convert `${var,,}` bash lowercase to zsh `${(L)var}` syntax
  - Fix associative array iteration, string manipulation, and other bash-specific patterns
- **CI**: Track `library/auth/schema.sql` in git (was excluded by `*.sql` gitignore rule, breaking CI auth tests)
- **CI**: Add `# noqa: E402` to test files with `sys.path.insert()` before imports (fixes ruff linting in CI)

## [6.1.0] - 2026-02-18

### Added

- **UI**: Comprehensive responsive design for mobile, desktop, portrait, landscape, and zoom/pinch scenarios
  - New `responsive.css` (425 lines, 6 media queries) with safe area insets, touch-aware interactions, landscape compaction, tablet/small phone layouts, fluid scaling, and reduced motion support
  - `viewport-fit=cover` on all HTML pages for notched device support
  - Touch targets minimum 44px (Apple HIG), `touch-action: manipulation` to eliminate 300ms tap delay
  - `@media (prefers-reduced-motion: reduce)` accessibility support
  - `clamp()` fluid typography and spacing for smooth desktop resize

### Changed

- **UI**: Header navigation converts to flex column layout at 768px breakpoint (fixes overlap with title)
- **UI**: Audio player compacts in landscape mobile orientation (max-height: 500px)
- **CI**: GitHub Actions release workflow installs zsh on runner for script compatibility
- **CI**: Fixed GHCR package permissions for Docker image push

### Fixed

- **Install**: `install.sh` separation check uses dynamic `$SCRIPT_DIR` instead of hardcoded path pattern
- **Install**: `upgrade.sh` separation check uses dynamic `$SCRIPT_DIR` instead of hardcoded path pattern
- **Code**: Removed unused `PilImage` import from `library/auth/totp.py`

## [6.0.0] - 2026-02-18

### Added

- **Security**: Dual-mode security architecture — `admin_or_localhost` decorator adapts endpoint protection based on deployment mode
  - `AUTH_ENABLED=true` (remote): Admin endpoints require authenticated admin user
  - `AUTH_ENABLED=false` (standalone): Admin endpoints restricted to localhost only
  - Admin endpoints are **never** wide-open regardless of mode
- **Install**: System installer (`install-system.sh`) now creates dedicated `audiobooks` service account (group + user with nologin shell)
- **Install**: Auth encryption key auto-generated during system install (64 hex chars, mode 0600, owned by audiobooks user)
- **Install**: Database auto-initialized from `schema.sql` during all install modes (system, user, unified)
- **Install**: Python virtual environment validated functionally (`python --version`) — detects broken symlinks from rsync copies
- **Install**: Python dependencies installed from `requirements.txt` during install (not just Flask)
- **Install**: systemd services configured with `User=audiobooks`, `Group=audiobooks`, `WorkingDirectory`
- **Config**: Remote access configuration variables added to `audiobooks.conf.example`: `AUDIOBOOKS_HOSTNAME`, `BASE_URL`, `CORS_ORIGIN`, `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGIN`
- **Config**: Email/SMTP configuration section added: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `ADMIN_EMAIL`
- **Testing**: `vm-test-manifest.json` added for `/test` Phase V integration
- **Rules**: Project-specific `.claude/rules/` files: `audio-metadata.md`, `paths-and-separation.md`, `testing.md`

### Changed

- **BREAKING**: All 27 shell scripts converted from `#!/bin/bash` to `#!/usr/bin/env zsh` (reverted back to bash in 6.6.5.1)
- **Security**: 9 admin endpoints in `utilities_system.py` now use `@admin_or_localhost` instead of `@localhost_only`
- **API**: CORS origin defaults to `*` (permissive, safe for standalone) — configurable via `CORS_ORIGIN` env var for remote deployments
- **API**: `BASE_URL` auto-detected from request headers — no hardcoded domain defaults
- **API**: Email configuration (`_get_email_config()`) uses agnostic defaults — no hardcoded domain references
- **Proxy**: `proxy_server.py` forwards `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Real-IP`, and `Host` headers from upstream reverse proxies
- **Proxy**: CORS locked to configurable `CORS_ORIGIN` value in proxy responses
- **Config**: `audiobooks.conf.example` reorganized with Remote Access and Email/SMTP sections
- **CI**: Removed `.github/workflows/ci.yml` (was using Python 3.11, incompatible with current Python 3.14 stack)

### Fixed

- **Install**: Wrapper scripts reference `api_server.py` (not stale `api.py`)
- **Install**: Auth key generated as 64 hex chars (`xxd -p | tr -d '\n'`), matching code validation — was base64 (~44 chars)
- **Install**: Auth key permissions set to `audiobooks:audiobooks 0600` — was `root:audiobooks 0640`
- **Install**: Correct pip package name `webauthn` (not `py-webauthn`)
- **Testing**: Integration tests now read the VM target from env vars (`VM_HOST`, `VM_NAME`); stale hardcoded VM name removed from `pytest.ini` and docstrings
- **Deps**: `pillow` 12.1.0 → 12.1.1 (GHSA-cfh3-3jmp-rvhc, OOB write on PSD)
- **Deps**: `cryptography` floor raised to ≥46.0.5 (GHSA-r6ph-v2qm-q3c2, subgroup attack)

## [5.0.2] - 2026-02-06

### Added

- **Testing**: VM_TESTS environment variable for proper WebAuthn origin selection in integration tests
- **JS**: Optional onCancel callback for showConfirmModal to support async confirm dialogs

### Changed

- **Testing**: Updated the reference test VM used during integration runs (details live in the tester's local env, not the repo)
- **Deploy**: Add library/scripts/ and library/common.py to VM deployment sync

### Fixed

- **API**: Use sys.executable instead of hardcoded "python3" in subprocess calls for venv compatibility
- **API**: Prevent duplicate access request errors with has_any_request() check
- **Scripts**: Initialize bash array to avoid unbound variable error with set -u
- **Scripts**: Fix shellcheck warnings in download-new-audiobooks (SC2188, SC2038, SC2086)
- **Deploy**: Correct venv path from /opt/audiobooks/library/venv to /opt/audiobooks/venv
- **Deploy**: Add /opt/audiobooks/library/data to systemd ReadWritePaths
- **Tests**: Fix WebAuthn origin mismatch for VM tests (port 8443 vs 9090)
- **Tests**: Fix SSH cleanup command venv path in auth integration tests

### Security

- **CI**: Add explicit permissions blocks to all GitHub Actions workflow jobs

## [5.0.1.1] - 2026-02-01

### Removed

- **Periodicals**: Remove all remaining periodicals code, systemd services, sync scripts, and install manifest entries (feature was removed in v4.0.3 but artifacts remained)
- **Periodicals**: Clean up "periodicals" and "Reading Room" references in code comments across audiobooks.py, schema.sql, metadata_utils.py, populate_asins.py

### Fixed

- **Systemd**: Fix API service boot failures caused by `ProtectSystem=strict` resolving symlinked data paths to an unmounted target — use the real mount path and explicit `After=` mount ordering so the unit waits for the data filesystem
- **Systemd**: Fix HTTPS proxy permanently failing on boot due to cascade dependency failure from API service
- **Systemd**: Fix stale symlinks with wrong "audiobooks-" prefix (should be "audiobook-") for shutdown-saver and upgrade-helper units
- **Systemd**: Update ExecStartPre port checks from lsof to ss (iproute2, always available)

## [5.0.1] - 2026-01-30

### Fixed

- **Proxy**: HTTPS reverse proxy now routes `/auth/*` endpoints to Flask backend (was only proxying `/api/*` and `/covers/*`, causing auth endpoints to return 405)
- **Proxy**: Forward `Cookie` header through reverse proxy for session-based authentication
- **Docs**: Updated all project documentation for v5.0.0 authentication release

## [5.0.0] - 2026-01-29

### Added

- **Authentication**: Multi-user authentication system with three auth methods:
  - **TOTP** (authenticator app) - time-based one-time passwords via Authy, Google Authenticator, etc.
  - **Passkey** (platform authenticator) - biometrics, phone, password manager (Bitwarden, 1Password)
  - **FIDO2** (hardware security key) - YubiKey, Titan Security Key, etc.
- **Authentication**: Encrypted auth database using SQLCipher (AES-256 at rest)
- **Authentication**: Admin approval flow for new user registrations with claim token system
- **Authentication**: Backup code recovery (8 single-use codes per user)
- **Authentication**: Session management with secure HTTP-only cookies
- **Authentication**: Per-user playback position tracking
- **Authentication**: WebAuthn/FIDO2 with dynamic origin detection from deployment config
- **Web UI**: Login page with auth-method-aware form (TOTP code input vs WebAuthn tap prompt)
- **Web UI**: Claim page for new users to set up credentials after admin approval
- **Web UI**: Admin panel for user management (approve/deny requests, edit users, view sessions)
- **Web UI**: Contact page and notification system
- **API**: Auth-gated endpoints with conditional decorators (bypass when AUTH_ENABLED=false)
- **API**: Download endpoint for offline audiobook listening
- **Server**: HTTPS reverse proxy with TLS 1.2+ and HTTP-to-HTTPS redirect
- **Infrastructure**: VM deployment script for remote testing
- **Infrastructure**: Caddy-based development server configuration

### Changed

- **BREAKING**: All API endpoints now require authentication when AUTH_ENABLED=true
- **BREAKING**: Web UI redirects to login page for unauthenticated users
- Passkey registration no longer restricts to platform authenticators (allows phone, password manager, hardware key)
- WebAuthn origin and RP ID auto-derived from AUDIOBOOKS_HOSTNAME, WEB_PORT, and HTTPS settings
- Token generation uses alphanumeric-only alphabet to avoid dash ambiguity in formatted tokens

### Fixed

- WebAuthn registration parsing uses py-webauthn 2.7.0 helper functions (not Pydantic model methods)
- NoneType.strip() crash on nullable recovery_email/recovery_phone fields
- WebAuthn JS API paths corrected from /api/auth/ to /auth/
- Backup codes returned as array (not formatted ASCII string) for frontend .forEach() compatibility
- WebAuthn claim flow creates session for auto-login (matching TOTP behavior)
- Hostname detection treats .localdomain and single-label hostnames as localhost for RP ID

## [4.1.2] - 2026-01-22

### Added

- **Web UI**: "Check for Updates" button in Utilities page for dry-run upgrade preview
  - Shows verbose output of what would happen without making changes
  - Displays current vs available version comparison
  - Reports result of multi-installation detection

### Fixed

- **Upgrade**: Fixed `--from-github` and `--from-project` options not upgrading the correct installation
  - `find_installed_dir()` now prioritizes system paths (`/opt/audiobooks`) over custom data locations
  - Adds warning when multiple installations are found, showing versions of each
  - Tells user to use `--target` if auto-selected location isn't correct

## [4.1.1] - 2026-01-20

### Fixed

- **Security**: Fixed insecure temporary file creation in ASIN population subprocess (CodeQL alert #187)
  - Changed `tempfile.mktemp()` to `tempfile.mkstemp()` in `maintenance.py`
  - Prevents TOCTOU (time-of-check-time-of-use) race condition vulnerability
  - The atomically-created file descriptor is immediately closed so the subprocess can write to it

## [4.1.0] - 2026-01-20

### Added

- **Player**: Media Session API integration for OS-level media controls:
  - Lock screen playback controls (play/pause, seek forward/back, skip)
  - Notification center media controls
  - Track metadata display (title, author, narrator, cover art)
  - Progress bar with seek support
- **Player**: Live Audible position sync during local playback:
  - Automatically syncs position with Audible every 5 minutes while playing
  - Uses "furthest ahead wins" logic to preserve furthest progress
  - Graceful handling when Audible service is unavailable
  - Only syncs books with ASIN (Audible-sourced audiobooks)

## [4.0.5] - 2026-01-20

### Fixed

- **Security**: Addressed 26 CodeQL alerts with TLS hardening and documentation:
  - Enforce TLS 1.2 minimum version in HTTPS server (was allowing older versions)
  - Replace stack trace exposure with generic error message in bulk delete API
  - Added CodeQL suppression comments for validated false positives (SQL injection with allowlists, path injection with validation, SSRF with localhost-only access, XSS with escapeHtml sanitization)

## [4.0.4] - 2026-01-20

### Fixed

- **Systemd**: Fixed API service failing at boot with NAMESPACE error on HDD/NAS storage. Added `AUDIOBOOKS_DATA` to `RequiresMountsFor` so systemd waits for the data mount before setting up the security namespace. Previously only waited for `/opt/audiobooks`.
- **Auth**: Fixed timestamp format mismatch in session cleanup causing incorrect stale session deletion. SQLite uses space separator (`YYYY-MM-DD HH:MM:SS`) while Python's `isoformat()` uses `T` separator, causing string comparison failures.

### Added

- **Documentation**: Added "HDD and Network Storage Considerations" section to README explaining how to configure `RequiresMountsFor` for slow mounts (HDDs, NAS, NFS, CIFS)

## [4.0.3] - 2026-01-18

### Fixed

- **API**: All async operations (Audible download, library import, rescan) now stream real-time progress with detailed item counts, percentages, and status updates
- **Docker**: Synced Dockerfile `ARG APP_VERSION` default to match VERSION file (4.0.2 → 4.0.3)
- **Code Quality**: Removed unused imports and marked unused regex patterns in test and library code

## [4.0.2] - 2026-01-18

### Fixed

- **API**: Fixed library rescan progress reporting to properly capture scanner output. Scanner uses carriage returns (`\r`) for in-place progress updates, but the API was only reading newline-terminated lines. Now reads character-by-character to capture both `\r` and `\n` delimited output.
- **Scripts**: Fixed duplicate entries in `source_checksums.idx`. The `generate_source_checksum()` function now checks if a filepath already exists before appending, preventing the same file from being indexed multiple times.
- **Systemd**: Fixed "Read-only file system" error when rebuilding conversion queue. Added `AUDIOBOOKS_DATA` path to `ReadWritePaths` in `audiobook-api.service` since `ProtectSystem=strict` was blocking write access to the index directory.

## [4.0.1] - 2026-01-17

### Fixed

- **API**: Library rescan now streams real-time progress updates to the web UI. Previously showed "Starting scanner..." for the entire scan duration; now shows actual progress with file counts and percentages.
- **Security**: Patched CVE-2025-43859 (h11 HTTP request smuggling) by upgrading to h11 0.16.0
- **Security**: Patched CVE-2026-23490 (pyasn1 parsing issue) by upgrading to pyasn1 0.6.2
- **Security**: Added CodeQL suppression comments for validated false positives in path handling and log sanitization code

## [4.0.0.2] - 2026-01-17

### Fixed

- **CI**: Fixed Docker workflow to support 4-digit tweak versions (X.Y.Z.W). The `docker/metadata-action` semver pattern doesn't handle 4-segment versions, so switched to raw tag extraction.

## [4.0.0.1] - 2026-01-17

### Fixed

- **Documentation**: Corrected migration path in CHANGELOG.md - was `migrations/010_drop_periodicals.sql`, now correctly shows `library/backend/migrations/010_drop_periodicals.sql`

## [4.0.0] - 2026-01-17

### Removed

- **BREAKING: Periodicals Feature Extracted**: The entire "Reading Room" periodicals subsystem has been removed from the main codebase
  - Removed `library/backend/api_modular/periodicals.py` - Flask Blueprint (~1,345 lines)
  - Removed `library/tests/test_periodicals.py` - Test suite (~1,231 lines)
  - Removed `library/web-v2/periodicals.html` - Reading Room UI (~1,079 lines)
  - Removed `library/web-v2/css/periodicals.css` - CSS module (~1,405 lines)
  - Removed `systemd/audiobook-periodicals-sync.service` - Systemd service
  - Removed `systemd/audiobook-periodicals-sync.timer` - Systemd timer
  - Removed `scripts/sync-periodicals-index` - Sync script (~391 lines)
  - Removed `docs/PERIODICALS.md` - Feature documentation
  - Total: ~5,700 lines removed

### Changed

- **Database Migration**: Added `010_drop_periodicals.sql` to clean up periodicals tables
  - Drops `periodicals`, `periodicals_sync_status`, `periodicals_playback_history` tables
  - Drops related views and triggers
  - Note: `content_type` column in `audiobooks` table is retained
- **Download Script**: Removed podcast episode detection logic from `download-new-audiobooks`
- **Status Script**: Removed periodicals timer from `audiobook-status` service checks
- **Web UI**: Removed "Reading Room" navigation link from main library header
- **Documentation**: Updated README.md and ARCHITECTURE.md to remove periodicals references

### Migration Notes

- **Before upgrading**: Disable periodicals services

  ```bash
  sudo systemctl stop audiobook-periodicals-sync.timer
  sudo systemctl disable audiobook-periodicals-sync.timer
  ```

- **After upgrading**: Run the cleanup migration

  ```bash
  sqlite3 /path/to/audiobooks.db < /opt/audiobooks/library/backend/migrations/010_drop_periodicals.sql
  ```

- **To restore periodicals**: Use tag `v3.11.2-with-periodicals` or branch `feature/periodicals-rnd`

## [3.11.2] - 2026-01-17

### Added

- **Podcast Episode Download & Conversion**: Full support for downloading and converting podcast episodes from Audible
  - `download-new-audiobooks`: Detects podcast episodes via database, uses `--resolve-podcasts` flag for proper MP3 download
  - `convert-audiobooks-opus-parallel`: Handles MP3-to-Opus conversion for podcasts (no DRM, simple ffmpeg transcode)
  - `build-conversion-queue`: Now includes `.mp3` files in source/converted indexing
- **Periodicals Orphan Detection**: Find and delete episodes whose parent series no longer exists
  - `GET /api/v1/periodicals/orphans`: List orphaned episodes
  - `DELETE /api/v1/periodicals/orphans`: Expunge all orphaned episodes (files + database)
  - UI button "🔍 Find Orphans" in periodicals header with modal display

### Fixed

- **Periodicals SSE**: Fixed Flask request context issue in SSE generator by capturing `g.db_path` before generator starts
- **Security - SQL Injection**: Added table name whitelist (`ALLOWED_LOOKUP_TABLES`) in scanner modules to prevent SQL injection via genre/era/topic lookups
- **Security - Log Injection**: Converted 4 files to use `%s` formatting instead of f-strings in log calls (`periodicals.py`, `add_new_audiobooks.py`, `position_sync.py`, `import_single.py`)
- **Security - XSS**: Changed `innerHTML` to `textContent` for user-controlled content in `library.js`
- **Build Queue**: Fixed `build-conversion-queue` to only process AAX/AAXC files, not MP3 podcasts (which don't need DRM removal)
- **Lint**: Added missing `# noqa: E402` comment for module-level import in `test_metadata_consistency.py`

## [3.11.1] - 2026-01-14

### Fixed

- **Deploy Script**: Fixed `deploy.sh` to include root-level management scripts (`upgrade.sh`, `migrate-api.sh`) that were being silently skipped during deployment. These scripts live in the project root but need to be copied to `$target/scripts/` for the `audiobook-upgrade` wrapper to function.

## [3.11.0] - 2026-01-14

### Added

- **Periodicals Sorting**: Reading Room now supports multiple sort options:
  - By title (A-Z, Z-A)
  - By release date (newest/oldest first)
  - By subscription status (subscribed first)
  - By download status (downloaded first)
- **Whispersync Position Sync**: Periodicals now support Audible position synchronization
  - Individual episode sync via `/api/periodicals/<asin>/sync-position`
  - Batch sync for all episodes via `/api/periodicals/sync-all-positions`
  - Real-time progress via SSE endpoint
- **Auto-Download for Subscribed Podcasts**: Automatically queue downloads for new episodes of subscribed series
- **Podcast Expungement**: Complete removal of unsubscribed podcast content including:
  - Audio files, covers, chapter data
  - Database entries with cascade to episodes
  - Index file cleanup
- **ASIN Sync**: Periodicals table now syncs `is_downloaded` status when audiobooks are imported

### Changed

- **Database Path Handling**: Clarified and fixed database path configuration across the codebase
- **Index Rebuilds**: Prevented destructive index rebuilds, added database sync protection

### Fixed

- **Test Schema**: Made periodicals sync conditional to prevent test failures
- **Duplicates Test**: Fixed path validation assertion for out-of-bounds paths
- **SSE Headers**: Removed hop-by-hop `Connection` header for PEP 3333 compliance
- **API Test Expectations**: Added 503 status for unavailable Audible, 400 for missing VERSION
- **Unused Code**: Removed unused `EXPUNGEABLE_TYPES` variable
- **CodeQL Alerts**: Resolved security and lint issues from static analysis

## [3.10.1] - 2026-01-14

### Added

- **Architecture Documentation**: Comprehensive update to ARCHITECTURE.md with 4 new sections:
  - Scanner Module Architecture (data pipeline flow diagram)
  - API Module Architecture (utilities_ops submodules documentation)
  - Systemd Services Reference (complete service inventory)
  - Scripts Reference (21 scripts organized by category)

### Changed

- **Periodicals Sync**: Enhanced parent/child hierarchy support for podcast episodes
  - Sync script now properly tracks episode parent ASINs
  - Improved episode metadata extraction from Audible API

### Fixed

- **Hardcoded Paths**: Fixed 2 hardcoded paths in shell scripts:
  - `move-staged-audiobooks`: Changed `/opt/audiobooks/library/scanner/import_single.py` to `${AUDIOBOOKS_HOME}/...`
  - `sync-periodicals-index`: Changed `/opt/audiobooks/library/backend/migrations/006_periodicals.sql` to `${AUDIOBOOKS_HOME}/...`
- **Systemd Inline Comments**: Removed invalid inline comments from 6 systemd service files (systemd doesn't support inline comments)
- **Test Config**: Updated hardcoded path tests to properly handle systemd files and shell variable defaults

## [3.10.0] - 2026-01-14

### Changed

- **BREAKING: Naming Convention Standardization**: All service names, CLI commands, and config files
  now use singular "audiobook-" prefix instead of plural "audiobooks-" to align with project name
  "audiobook-manager"
  - Renamed `lib/audiobooks-config.sh` → `lib/audiobook-config.sh`
  - Renamed all systemd units: `audiobooks-*` → `audiobook-*`
  - Updated all script references to new config file name
- **Status Script Enhancement**: `audiobook-status` now displays services and timers in separate sections

### Fixed

- **Unused Imports**: Removed 45 unused imports across codebase via ruff auto-fix
- **Test Schema Handling**: Marked schema-dependent tests as xfail pending migration 007
  (source_asin column, content_type column, indexes, FTS triggers)
- **Documentation Dates**: Updated last-modified dates in ARCHITECTURE.md and POSITION_SYNC.md

### Migration Notes

After upgrading, run these commands to migrate systemd services:

```bash
# Stop old services
sudo systemctl stop audiobooks-api audiobooks-converter audiobooks-mover audiobooks-proxy audiobooks-redirect

# Disable old services
sudo systemctl disable audiobooks-api audiobooks-converter audiobooks-mover audiobooks-proxy audiobooks-redirect

# Remove old service files
sudo rm /etc/systemd/system/audiobooks-*.service /etc/systemd/system/audiobooks-*.timer /etc/systemd/system/audiobooks.target

# Run upgrade script
sudo /opt/audiobooks/upgrade.sh
```

## [3.9.8] - 2026-01-14

### Changed

- **Major Refactoring**: Split monolithic `utilities_ops.py` (994 lines) into modular package
  - `utilities_ops/audible.py` - Audible API operations (download, metadata sync)
  - `utilities_ops/hashing.py` - Hash generation operations
  - `utilities_ops/library.py` - Library content management
  - `utilities_ops/maintenance.py` - Database and index maintenance
  - `utilities_ops/status.py` - Status endpoint operations
- **Shared Utilities**: Extract common code to `library/common.py` (replacing `library/utils.py`)
- **Test Coverage**: Added 27 new test files, coverage increased from 77% to 85%
  - New test files for all API modules (audiobooks, duplicates, supplements, position_sync)
  - New test files for utilities_ops submodules
  - Extended test coverage for edge cases and error handling

### Fixed

- **Unused Imports**: Removed `TextIO` from utilities_conversion.py, `Path` from utilities_ops/library.py
- **Incorrect Default**: Fixed AUDIOBOOKS_DATA default in audible.py from `/var/lib/audiobooks` to `/srv/audiobooks`
- **Example Config**: Added missing PARALLEL_JOBS, DATA_DIR, and INDEX variables to audiobooks.conf.example
- **Documentation**: Updated api_modular/README.md to remove obsolete utilities_ops.py references

### Security

- **CVE-2025-43859 Documentation**: Documented h11 vulnerability as blocked by audible 0.8.2 dependency chain
  (audible pins httpx<0.24.0 which requires h11<0.15). Monitor for audible updates.

## [3.9.7.1] - 2026-01-13

### Fixed (Audit Fixes)

- **PIL Rebuild for Python 3.14**: Rebuilt Pillow wheel in virtual environment to fix compatibility
  with Python 3.14 (CachyOS rolling release). PIL was compiled against older Python, causing
  import failures during audiobook cover processing.
- **flask-cors Removal**: Removed deprecated flask-cors from `install.sh` and `install-user.sh`.
  CORS has been handled natively since v3.2.0; the pip install was a no-op that could fail on
  systems without the package available.
- **systemd ConditionPathExists**: Fixed incorrect `ConditionPathExists` paths in multiple
  systemd service files that referenced non-existent queue/trigger files, causing services
  to skip activation silently.

## [3.9.7] - 2026-01-13

### Fixed

- **Upgrade Script Path Bug**: Fixed `upgrade-helper-process` referencing wrong path
  - Was: `/opt/audiobooks/upgrade.sh` (root level, doesn't exist)
  - Now: `/opt/audiobooks/scripts/upgrade.sh` (correct location)
  - This broke the web UI upgrade button and `audiobook-upgrade` command
- **Duplicate Finder Endpoint**: Fixed JavaScript calling non-existent API endpoint
  - Was: `/api/duplicates/by-hash` (doesn't exist)
  - Now: `/api/duplicates` (correct endpoint)
  - This silently broke "Find Duplicates" for hash-based detection in Back Office
- **Upgrade Script Sync**: Added root-level management scripts to `do_upgrade()` sync
  - `upgrade.sh` and `migrate-api.sh` now properly sync from project root to `target/scripts/`
  - Previously these were only installed by `install.sh`, not synced during upgrades

## [3.9.6] - 2026-01-13

### Security

- **CVE-2025-43859**: Fix HTTP request smuggling vulnerability by upgrading h11 to >=0.16.0
- **TLS 1.2 Minimum**: Enforce TLS 1.2 as minimum protocol version in proxy_server.py
  - Prevents downgrade attacks to SSLv3, TLS 1.0, or TLS 1.1
- **SSRF Protection**: Add path validation in proxy_server.py to prevent SSRF attacks
  - Only allows `/api/` and `/covers/` paths to be proxied
  - Blocks attempts to access internal services via crafted URLs
- **Stack Trace Exposure**: Replace 12 instances of raw exception messages in API responses
  with generic error messages; full tracebacks now logged server-side only

### Fixed

- **CodeQL Remediation**: Fix 30 code scanning alerts across the codebase
  - Add missing `from typing import Any` import in duplicates.py
  - Fix import order in utilities_ops.py (E402)
  - Document 7 intentional empty exception handlers
  - Fix mixed return statements in generate_hashes.py
  - Remove unused variable in audiobooks.py
  - Add `__all__` exports in scan_audiobooks.py for re-exported symbols
- **Index Corruption Bug**: Fixed `generate_library_checksum()` in `move-staged-audiobooks`
  that caused phantom duplicates in the library checksum index
  - Bug: Script appended entries without checking if filepath already existed
  - Result: Same file could appear 8+ times in index after reprocessing
  - Fix: Now removes existing entry before appending (idempotent operation)

### Changed

- Upgrade httpx to 0.28.1 and httpcore to 1.0.9 (required for h11 CVE fix)

## [3.9.5.1] - 2026-01-13

### Added

- Multi-segment version badges in README with hierarchical color scheme
- Version history table showing release progression

## [3.9.5] - (Previous)

### Fixed (rolled back from 3.9.7)

- **CRITICAL: Parallelism Restored**: Fixed 7 variable expansion bugs in `build-conversion-queue`
  that completely broke parallel conversions (was running 1 at a time instead of 12)
  - Bug: `: > "queue_file"` (literal string) instead of `: > "$queue_file"` (variable)
  - Introduced by incomplete shellcheck SC2188 fix in fd686b9
  - Affected functions: `build_converted_asin_index`, `build_source_asin_index`,
    `build_converted_index`, `load_checksum_duplicates`, `build_queue`
- **Progress Tracking**: Fixed conversion progress showing 0% for all jobs
  - Changed from `read_bytes` to `rchar` in `/proc/PID/io` parsing
  - `read_bytes` only counts actual disk I/O; `rchar` includes cached reads
  - FFmpeg typically reads from kernel cache, so `read_bytes` was always 0
- **UI Safety**: Removed `audiobook-api` and `audiobook-proxy` from web UI service controls
  - These are core infrastructure services that should not be stoppable via UI
  - Prevents accidental self-destruction of the running application

## [3.9.7] - 2026-01-11 *(rolled back)*

> **Note**: This release was rolled back due to critical bugs in the queue builder
> that broke parallel conversions. The fixes below are valid but were released
> alongside unfixed bugs from 3.9.6. See [Unreleased] for the complete fixes.

### Fixed

- **Database Connection Leaks**: Fixed 6 connection leaks in `position_sync.py`
  - All API endpoints now properly close database connections via try/finally blocks
  - Affected routes: `get_position`, `update_position`, `sync_position`, `sync_all_positions`, `list_syncable`, `get_position_history`
- **Version Sync**: Synchronized version across all files (Dockerfile, install-manifest.json, documentation)
- **Database Path**: Corrected database path in install-manifest.json and documentation
  - Changed from `/var/lib/audiobooks/audiobooks.db` to `/var/lib/audiobooks/db/audiobooks.db`

### Changed

- **Code Cleanup**: Removed unused `Any` import from `duplicates.py`

## [3.9.6] - 2026-01-10 *(never released)*

> **Note**: This version was committed but never tagged/released. The queue script
> fix below was incomplete (claimed 3 instances, actually 7). See [Unreleased] for
> the complete fix.

### Added

- **Storage Tier Detection**: Installer now automatically detects NVMe, SSD, and HDD storage
  - Displays detected storage tier for each installation path
  - Warns if database would be placed on slow storage (HDD)
  - Explains performance impact: "SQLite query times: NVMe ~0.002s vs HDD ~0.2s (100x difference)"
  - Option to cancel installation and adjust paths
- **Installed App Documentation**: New documentation at `/opt/audiobooks/`
  - `README.md` - Quick start guide and service overview
  - `CHANGELOG.md` - Version history for installed application
  - `USAGE.md` - Comprehensive usage guide with troubleshooting

### Fixed

- **Proxy hop-by-hop headers**: Fixed `AssertionError: Connection is a "hop-by-hop" header` from Waitress
  - Added `HOP_BY_HOP_HEADERS` filter to `proxy_server.py` (PEP 3333 / RFC 2616 compliance)
  - Prevents silently dropped API responses through reverse proxy
- **Service permissions**: Fixed silent download failures due to directory ownership mismatch
  - Documented in ARCHITECTURE.md with detection script
- **Rebuild queue script** *(incomplete)*: Attempted fix for variable expansion in `build-conversion-queue`
  - Fixed 3 of 7 instances; remaining 4 caused parallelism to fail

### Changed

- **ARCHITECTURE.md**: Added reverse proxy architecture and service permissions sections
- **INSTALL.md**: Added storage tier detection documentation with example output

## [3.9.5] - 2026-01-10

### Added

- **Schema Tracking**: `schema.sql` now tracked in git repository
  - Contains authoritative database schema with all columns, indices, and views
  - Includes `content_type` and `source_asin` columns for periodical classification
  - Added `library_audiobooks` view and `idx_audiobooks_content_type` index
- **Utility Script**: `rnd/update_content_types.py` for syncing content_type from Audible API
  - Fetches content_type for all library items with ASINs
  - Handles Audible's pagination and inconsistent tagging

### Changed

- **Content Filter**: Expanded `AUDIOBOOK_FILTER` to include more content types
  - Now includes: Product, Lecture, Performance, Speech (main library)
  - Excludes: Podcast, Radio/TV Program (Reading Room)
  - Handles NULL content_type for legacy entries

### Fixed

- **Reliability**: Prevent concurrent `build-conversion-queue` processes with flock
  - Multiple simultaneous rebuilds caused race conditions and duplicate conversions
- **Scripts**: Fixed shellcheck warnings in `build-conversion-queue` and `move-staged-audiobooks`
  - SC2188: Use `: >` instead of `>` for file truncation
  - SC2086: Quote numeric variables properly

## [3.9.4] - 2026-01-09

### Added

- **Developer Safeguards**: Pre-commit hook blocks hardcoded paths in scripts and services
  - Rejects commits containing literal paths like `/run/audiobooks`, `/var/lib/audiobooks`, `/srv/audiobooks`
  - Enforces use of configuration variables (`$AUDIOBOOKS_RUN_DIR`, `$AUDIOBOOKS_VAR_DIR`, etc.)
  - Shareable hooks in `scripts/hooks/` with installer script (`scripts/install-hooks.sh`)
- **Database Schema**: Added `content_type` column to audiobooks table
  - Stores Audible content classification (Product, Podcast, Lecture, Performance, Speech, Radio/TV Program)
  - Added `library_audiobooks` view to separate main library from periodicals
  - New index `idx_audiobooks_content_type` for efficient filtering
  - Used by `AUDIOBOOK_FILTER` to exclude periodical content from main library queries

### Changed

- **Runtime Directory**: Changed `AUDIOBOOKS_RUN_DIR` from `/run/audiobooks` to `/var/lib/audiobooks/.run`
  - Fixes namespace isolation issues with systemd's `ProtectSystem=strict` security hardening
  - Using `/run/` directories doesn't work reliably with sandboxed services

### Fixed

- **Security**: Replace insecure `mktemp()` with `mkstemp()` in `google_play_processor.py`
  - Eliminates TOCTOU (time-of-check-time-of-use) race condition vulnerability
- **Reliability**: Add signal trap to converter script for clean FFmpeg shutdown
  - Prevents orphan FFmpeg processes on service stop/restart
- **Code Quality**: Fix missing `import os` in `librivox_downloader.py`
- **Code Quality**: Remove unused `LOG_DIR` variable from `librivox_downloader.py`
- **Code Quality**: Remove unused `PROJECT_DIR` import from `scan_supplements.py`
- **Code Quality**: Add logging for silent exceptions in `duplicates.py` index updates
- **Systemd Services**: Removed `RuntimeDirectory=audiobooks` from all services
  - API, converter, downloader, mover, and periodicals-sync services updated
  - tmpfiles.d now creates `/var/lib/audiobooks/.run` at boot
- **Periodicals Sync**: Fixed SSE FIFO path to use `$AUDIOBOOKS_RUN_DIR` variable
- **Scripts**: Fixed `set -e` failure in log function (changed `$VERBOSE && echo` to `if $VERBOSE; then echo`)

## [3.9.3] - 2026-01-08

### Changed

- **Periodicals (Reading Room)**: Simplified to flat data schema with skip list support
  - Each periodical is now a standalone item (matching Audible's content_type classification)
  - API endpoints use single `asin` instead of parent/child model
  - UI rewritten with details card view for better browsing
  - Added skip list support via `/etc/audiobooks/periodicals-skip.txt`
  - Content types: Podcast, Newspaper/Magazine, Show, Radio/TV Program

### Fixed

- **Mover Service**: Prevented `build-conversion-queue` process stampede
  - Added `flock -n` wrapper to prevent multiple concurrent rebuilds
  - Previously, 167+ zombie processes could accumulate consuming 200% CPU

## [3.9.2] - 2026-01-08

### Fixed

- **Reading Room API**: Fixed 500 Internal Server Error - all `get_db()` calls were missing required `db_path` argument
- **Periodicals Sync Service**: Fixed startup failure - removed non-existent `/var/log/audiobooks` from ReadWritePaths (service logs to systemd journal)

## [3.9.1] - 2026-01-08

### Fixed

- **Systemd Target**: All services now properly bind to `audiobook.target` for correct stop/start behavior during upgrades
  - Added `audiobook.target` to WantedBy for: api, proxy, redirect, periodicals-sync services and timer
  - Added explicit `Wants=` in audiobook.target for all core services and timers
  - Previously only converter/mover responded to `systemctl stop/start audiobook.target`

## [3.9.0] - 2026-01-08

### Added

- **Periodicals "Reading Room"**: New subsystem for episodic Audible content
  - Dedicated page for browsing podcasts, newspapers, meditation series
  - Category filtering (All, Podcasts, News, Meditation, Other)
  - Episode selection with bulk download capability
  - Real-time sync status via Server-Sent Events (SSE)
  - **On-demand refresh button** to sync periodicals index from Audible
  - Twice-daily automatic sync via systemd timer (06:00, 18:00)
  - Skip list integration - periodicals excluded from main library by default
- **Periodicals API Endpoints**:
  - `GET /api/v1/periodicals` - List all periodical parents with counts
  - `GET /api/v1/periodicals/<asin>` - List episodes for a parent
  - `GET /api/v1/periodicals/<asin>/<ep>` - Episode details
  - `POST /api/v1/periodicals/download` - Queue episodes for download
  - `DELETE /api/v1/periodicals/download/<asin>` - Cancel queued download
  - `GET /api/v1/periodicals/sync/status` - SSE stream for sync status
  - `POST /api/v1/periodicals/sync/trigger` - Manually trigger sync
  - `GET /api/v1/periodicals/categories` - List categories with counts
- **New Database Tables**: `periodicals` (content index), `periodicals_sync_status` (sync tracking)
- **New Systemd Units**: `audiobook-periodicals-sync.service`, `audiobook-periodicals-sync.timer`
- **Security**: XSS-safe DOM rendering using textContent and createElement (no innerHTML)
- **Technology**: HTMX for declarative interactions, SSE for real-time updates

### Changed

- **Library Header**: Added "Reading Room" navigation link next to "Back Office"
- **CSS Layout**: Header navigation now uses flex container for multiple links

### Fixed

- **Security**: Pinned minimum versions for transitive dependencies with CVEs
  - urllib3>=2.6.3 (CVE-2026-21441)
  - h11>=0.16.0 (CVE-2025-43859)
- **Security**: Fixed exception info exposure in position_sync.py (now returns generic error messages)
- **Code Cleanup**: Removed dead CSS code (banker-lamp classes) from utilities.css

## [3.8.0] - 2026-01-07

### Added

- **Position Sync with Audible**: Bidirectional playback position synchronization with Audible cloud
  - "Furthest ahead wins" conflict resolution - you never lose progress
  - Seamlessly switch between Audible apps and self-hosted library
  - Sync single books or batch sync all audiobooks with ASINs
  - Position history tracking for debugging and progress review
- **Position Sync API Endpoints**:
  - `GET /api/position/<id>` - Get position for a single audiobook
  - `PUT /api/position/<id>` - Update local playback position (from web player)
  - `POST /api/position/sync/<id>` - Sync single book with Audible
  - `POST /api/position/sync-all` - Batch sync all books with ASINs
  - `GET /api/position/syncable` - List all syncable audiobooks
  - `GET /api/position/history/<id>` - Get position history for a book
  - `GET /api/position/status` - Check if position sync is available
- **Web Player Integration**: Dual-layer position storage (localStorage + API)
  - Automatic position save every 15 seconds during playback
  - Resume from best position (furthest ahead from cache or API)
  - Immediate flush on player close
- **Credential Management**: Encrypted Audible auth password storage using Fernet (PBKDF2)
- **ASIN Population Tool**: `rnd/populate_asins.py` matches local books to Audible library
- **Documentation**: New comprehensive `docs/POSITION_SYNC.md` guide with:
  - Setup prerequisites and configuration steps
  - First sync instructions with batch-sync command
  - Ongoing sync maintenance patterns
  - API reference with examples
  - Troubleshooting guide

### Changed

- **Architecture Docs**: Added Position Sync Architecture section with data flow diagrams
- **README**: Added Position Sync section with quick setup guide

## [3.7.2] - 2026-01-07

### Added

- **Position Sync with Audible**: Bidirectional playback position synchronization with Audible cloud
  - "Furthest ahead wins" conflict resolution - you never lose progress
  - Seamlessly switch between Audible apps and self-hosted library
  - Sync single books or batch sync all audiobooks with ASINs
  - Position history tracking for debugging and progress review
- **Position Sync API Endpoints**:
  - `GET /api/position/<id>` - Get position for a single audiobook
  - `PUT /api/position/<id>` - Update local playback position (from web player)
  - `POST /api/position/sync/<id>` - Sync single book with Audible
  - `POST /api/position/sync-all` - Batch sync all books with ASINs
  - `GET /api/position/syncable` - List all syncable audiobooks
  - `GET /api/position/history/<id>` - Get position history for a book
  - `GET /api/position/status` - Check if position sync is available
- **Web Player Integration**: Dual-layer position storage (localStorage + API)
  - Automatic position save every 15 seconds during playback
  - Resume from best position (furthest ahead from cache or API)
  - Immediate flush on player close
- **Credential Management**: Encrypted Audible auth password storage using Fernet (PBKDF2)
- **ASIN Population Tool**: `rnd/populate_asins.py` matches local books to Audible library
- **Documentation**: New comprehensive `docs/POSITION_SYNC.md` guide with:
  - Setup prerequisites and configuration steps
  - First sync instructions with batch-sync command
  - Ongoing sync maintenance patterns
  - API reference with examples
  - Troubleshooting guide

### Changed

- **Architecture Docs**: Added Position Sync Architecture section with data flow diagrams
- **README**: Added Position Sync section with quick setup guide
- **Service Management**: Renamed `audiobooks-scanner.timer` to `audiobook-downloader.timer` in API
  and helper script to match actual systemd unit name

### Fixed

- **Download Feature**: Fixed "Read-only file system" error when downloading audiobooks
  - Added `/run/audiobooks` to `ReadWritePaths` in API service for lock files and temp storage
- **Vacuum Database**: Fixed "disk I/O error" when vacuuming database
  - Added `PRAGMA temp_store = MEMORY` to avoid temp file creation in sandboxed environment
- **Service Timer Control**: Fixed "Unit not found" error when starting/stopping timer
  - Updated service name from `audiobooks-scanner.timer` to `audiobook-downloader.timer`

## [3.7.1] - 2026-01-05

### Added

- **Duplicate Deletion**: Added delete capability for checksum-based duplicates in Back Office
  - New API endpoint `POST /api/duplicates/delete-by-path` for path-based deletion
  - Library checksum duplicates now show checkboxes for selection
  - Source checksum duplicates also support deletion (file-only, not in database)
  - Removed "manual deletion required" notice - duplicates can now be deleted from the UI

### Changed

- **Service Management**: Renamed `audiobooks-scanner.timer` to `audiobook-downloader.timer` in API
  and helper script to match actual systemd unit name
- **API Service**: Updated systemd service `ReadWritePaths` to include Library and Sources directories
  - Required for API to delete duplicate files (previously had read-only access)

### Fixed

- **Download Feature**: Fixed "Read-only file system" error when downloading audiobooks
  - Added runtime directory to `ReadWritePaths` in API service for lock files and temp storage
- **Vacuum Database**: Fixed "disk I/O error" when vacuuming database
  - Added `PRAGMA temp_store = MEMORY` to avoid temp file creation in sandboxed environment
- **Service Timer Control**: Fixed "Unit not found" error when starting/stopping timer
  - Updated service name from `audiobooks-scanner.timer` to `audiobook-downloader.timer`

## [3.7.0.1] - 2026-01-04

### Changed

- **Documentation**: Mark v3.5.x as end-of-life (no security patches or updates)

## [3.7.0] - 2026-01-04

### Changed

- **UI Styling**: Changed dark green text on dark backgrounds to cream-light for better contrast
  - Progress output text, success stats, active file indicators now use `--cream-light`

### Fixed

- **upgrade.sh**: Fixed non-interactive upgrade failures in systemd service
  - Fixed arithmetic increment `((issues_found++))` causing exit code 1 with `set -e`
  - Changed to `issues_found=$((issues_found + 1))` which always succeeds
- **upgrade-helper-process**: Auto-confirm upgrade prompts
  - Pipe "y" to upgrade script since user already confirmed via web UI
  - Fixes `read` command failing with no TTY in systemd context

## [3.6.4.1] - 2026-01-04

### Added

- **CSS Customization Guide**: New `docs/CSS-CUSTOMIZATION.md` documenting how to customize
  colors, fonts, shadows, and create custom themes for the web UI

### Changed

- **UI Styling**: Enhanced visual depth and contrast across web interface
  - Darkened header sunburst background for better separation from content
  - Brightened all cream-colored text (85% opacity → 100% with cream-light color)
  - Added shadow elevation system to theme for consistent depth cues
  - Matched Back Office header/background styling to main Library page
- **Back Office**: Removed hardcoded version from header (available in System tab)

### Fixed

- **Upgrade Button**: Fixed confirm dialog always resolving as "Cancel"
  - `confirmAction()` was resolving with `false` before `resolve(true)` could run
  - Clicking "Confirm" on upgrade dialog now properly triggers the upgrade
- **Duplicate Detection**: Improved detection of already-converted audiobooks
  - Added word-set matching for titles with same words in different order
    (e.g., "Bill Bryson's... Ep. 1: Title" vs "Ep. 1: Title (Bill Bryson's...)")
  - Added title fallback matching for ASIN files (catches same-book-different-ASIN scenarios)
  - Added 2-word prefix matching for title variations
    (e.g., "Blue Belle Burke Book 3" matches "Blue Belle: A Burke Novel 3")

## [3.6.4] - 2026-01-04

### Fixed

- **upgrade.sh**: Self-healing tarball extraction with flexible pattern matching
  - Now tries multiple directory patterns (`audiobook-manager-*`, `audiobooks-*`, `Audiobook-Manager-*`)
  - Fallback pattern for any versioned directory (`*-[0-9]*`)
  - Added debug output showing temp dir contents on extraction failure
  - Prevents bootstrap problems where old upgrade scripts can't upgrade themselves

## [3.6.3] - 2026-01-03

### Fixed

- **upgrade.sh**: Fixed GitHub release extraction failing with "Could not find extracted directory"
  - Changed glob pattern from `audiobooks-*` to `audiobook-manager-*` to match actual tarball structure
- **upgrade.sh**: Fixed project upgrade (`--from-project`) failing with exit code 1 when no upgrade needed
  - Now exits cleanly with code 0 when versions are identical (matches GitHub mode behavior)
  - Fixes web UI upgrade from project showing "Upgrade failed" when already up to date

## [3.6.2] - 2026-01-03

### Changed

- **utilities_system.py**: Project discovery now searches multiple paths instead of a hardcoded
  maintainer directory — checks `AUDIOBOOKS_PROJECT_DIR` env, `~/ClaudeCodeProjects`,
  `~/projects`, and `/opt/projects`

### Fixed

- Version sync: Updated `install-manifest.json`, `Dockerfile`, `CLAUDE.md`, and
  `docs/ARCHITECTURE.md` to match VERSION file (3.6.1 → now 3.6.2)
- Removed unused imports in `scan_audiobooks.py` (re-exported from `metadata_utils` for
  backwards compatibility with tests)
- Added `.claudeignore` to exclude `.snapshots/` from Claude Code settings scanning

## [3.6.1] - 2026-01-03

### Added

- **Privilege-separated helper service**: System operations (service control, upgrades) now work
  with the API's `NoNewPrivileges=yes` security hardening via a helper service pattern
  - `audiobook-upgrade-helper.service`: Runs privileged operations as root
  - `audiobook-upgrade-helper.path`: Watches for request files to trigger helper
  - Control files stored in `/var/lib/audiobooks/.control/` (avoids systemd namespace issues)

### Changed

- **API utilities_system.py**: Refactored from direct sudo calls to file-based IPC with helper
- **install.sh/upgrade.sh**: Now deploy the helper service units

### Fixed

- Service control (start/stop/restart) from web UI now works with sandboxed API
- Upgrade from web UI now works with `NoNewPrivileges=yes` security hardening
- Race condition in status polling that caused false failure responses

## [3.6.0] - 2026-01-03

### Added

- **Audible Sync tab**: New Back Office section for syncing metadata from Audible library exports
  - Sync Genres: Match audiobooks to Audible entries and populate genre fields
  - Update Narrators: Fill in missing narrator information from Audible data
  - Populate Sort Fields: Generate author_sort and title_sort for proper alphabetization
  - Prerequisites check: Verifies library_metadata.json exists before operations
- **Pipeline Operations**: Download Audiobooks, Rebuild Queue, Cleanup Indexes accessible from UI
- **Tooltips**: Comprehensive tooltips on all buttons and action items for discoverability
- **CSS modular architecture**: Separated styles into focused modules:
  - `theme-art-deco.css`: Art Deco color palette, typography, decorative elements
  - `layout.css`: Grid systems, card layouts, responsive breakpoints
  - `components.css`: Buttons, badges, status indicators, forms
  - `sidebar.css`: Collections panel with pigeon-hole design
  - `player.css`: Audio player styling
  - `modals.css`: Dialog and modal styling
- **Check Audible Prerequisites endpoint**: `/api/utilities/check-audible-prereqs`

### Changed

- **Art Deco theme applied globally**: Complete visual redesign across entire application:
  - Dark geometric diamond background pattern
  - Gold, cream, and charcoal color palette
  - Sunburst headers with chevron borders
  - Stepped corners on book cards
  - High-contrast dark inputs and dropdowns
  - Enhanced banker's lamp SVG with glow effect
  - Filing cabinet tab navigation with pigeon-hole metaphor
- Updated Python script API endpoints to use `--execute` flag (dry-run is default)
- Improved column balance with `align-items: stretch` for equal card heights
- Database tab reorganized into balanced 2x2 card layout

### Fixed

- Removed duplicate API endpoint definitions causing Flask startup failures
- Fixed bash `log()` functions to work with `set -e` (use if/then instead of &&)
- Fixed genre sync, narrator sync, and sort field population API argument handling
- Fixed cream-on-cream contrast issues in Back Office intro cards
- Fixed light background on form inputs and dropdowns throughout application

## [3.5.0] - 2026-01-03

> ⚠️ **END OF LIFE - NO LONGER SUPPORTED**
>
> The 3.5.x branch reached end-of-life with the release of v3.7.0.
>
> - **No further updates** will be released for 3.5.x
> - **No security patches** - upgrade to 3.7.0+ immediately
> - **Migration required**: v3.5.0 was the last version supporting the legacy monolithic API (`api.py`)
>
> Users still on 3.5.x must upgrade to v3.7.0 or later. See [upgrade documentation](docs/ARCHITECTURE.md).

### Added

- **Checksum tracking**: MD5 checksums (first 1MB) generated automatically during download and move operations
- **Generate Checksums button**: New Utilities maintenance feature for Sources AND Library with hover tooltips
- **Index cleanup script**: `cleanup-stale-indexes` removes entries for deleted files from all indexes
- Automatic index cleanup: Deleted files are removed from checksum indexes via delete operations
- Real-time index updates after each conversion completes
- Prominent remaining summary box in Conversion Monitor
- Inline database import in Back Office UI

### Changed

- **Bulk Operations redesign**: Clear step-by-step workflow with explanatory intro, descriptive filter options, and use-case examples
- **Conversion queue**: Hybrid ASIN + title matching for accurate queue building
- Removed redundant "Audiobooks" tab from Back Office (audiobook search available on main library page)
- Updated "Generate Hashes" button tooltip to clarify it regenerates ALL hashes
- Download and mover services now append checksums to index files in real-time
- Mover timing optimization: reduced file age check from 5min to 1min, polling from 5min to 30sec

### Fixed

- Fixed chapters.json ASIN extraction in cleanup script (ASINs are in JSON content, not filename)
- Queue builder robustness: title normalization, subshell issues, edition handling
- Version display fixes in Back Office header

## [3.4.2] - 2026-01-02

### Changed

- Refactored utilities.py (1067 lines) into 4 focused sub-modules:
  - `utilities_crud.py`: CRUD operations (259 lines)
  - `utilities_db.py`: Database maintenance (291 lines)
  - `utilities_ops.py`: Async operations with progress tracking (322 lines)
  - `utilities_conversion.py`: Conversion monitoring with extracted helpers (294 lines)
- Refactored scanner modules with new shared `metadata_utils.py`:
  - Extracted genre taxonomy, topic keywords, and metadata extraction helpers
  - `scan_audiobooks.py`: D(24) → A(3) complexity on main function
  - `add_new_audiobooks.py`: D(21) → C(13) max complexity
  - Average scanner complexity now B(5.2)
- Reduced average cyclomatic complexity from D (high) to A (3.7)
- Extracted helper functions (`get_ffmpeg_processes`, `parse_job_io`, `get_system_stats`) for testability

### Fixed

- Fixed conversion progress showing "100% Complete" while active FFmpeg processes still running
- Fixed REMAINING and QUEUE SIZE showing 0 when conversions are in-progress (now shows active count)
- Removed unused imports and variables (code cleanup)
- Removed orphaned test fixtures from conftest.py
- Updated Dockerfile version default to match current VERSION

## [3.4.1] - 2026-01-02

### Added

- Comprehensive ARCHITECTURE.md guide with:
  - System component diagrams and symlink architecture
  - Install, upgrade, and migrate workflow diagrams
  - Storage tier recommendations by component type
  - Filesystem recommendations (ext4, XFS, Btrfs, ZFS, F2FS)
  - Kernel compatibility matrix (LTS through rolling release)
  - I/O scheduler recommendations
- Installed directory structure documentation in README.md

### Changed

- `install.sh` now uses `/opt/audiobooks` as canonical install location instead of `/usr/local/lib/audiobooks`
- Wrapper scripts now source from `/opt/audiobooks/lib/audiobook-config.sh` (canonical path)
- Added backward-compatibility symlink `/usr/local/lib/audiobooks` → `/opt/audiobooks/lib/`
- `install.sh` now automatically enables and starts services after installation (no manual step needed)
- `migrate-api.sh` now stops services before migration and starts them after (proper lifecycle management)
- `/etc/profile.d/audiobooks.sh` now sources from canonical `/opt/audiobooks/lib/` path

### Fixed

- Fixed `install.sh` to create symlinks in `/usr/local/bin/` instead of copying scripts
- Fixed proxy server to forward `/covers/` requests to API backend

## [3.4.0] - 2026-01-02

### Added

- Per-job conversion stats with progress percentage and throughput (MiB/s)
- Sortable Active Conversions list (by percent, throughput, or name)
- Expandable conversion details panel in Back Office UI
- Text-search based collection subgenres: Short Stories & Anthologies, Action & Adventure, Historical Fiction
- Short Stories collection detects: editor in author field, ": Stories" suffix, "Complete/Collected" patterns

### Changed

- Active conversions now use light background with dark text for better readability
- Cover art now stored in data directory (`${AUDIOBOOKS_DATA}/.covers`) instead of application directory
- Config template uses `${AUDIOBOOKS_DATA}` references for portability across installations
- Scripts now installed to `/opt/audiobooks/scripts/` (canonical) with symlinks in `/usr/local/bin/`
- Clear separation: `/opt/audiobooks/` (application), `${AUDIOBOOKS_DATA}/` (user data), `/var/lib/` (database)

### Fixed

- **CRITICAL**: Fixed `DATA_DIR` config not reading from `/etc/audiobooks/audiobooks.conf`, which caused "Reimport Database" to read from test fixtures instead of production data
- Fixed collection genre queries to match actual database genre names (Fiction, Sci-Fi & Fantasy, etc.)
- Fixed queue count sync - now shows actual remaining files instead of stale queue.txt count
- Fixed cover serving to use `COVER_DIR` from config instead of hardcoded path
- Fixed proxy server to forward `/covers/` requests to API backend (was returning 404)
- Fixed `install.sh` to create symlinks in `/usr/local/bin/` instead of copying scripts (upgrades now automatically update commands)
- Removed false-positive Romance collection (was matching "Romantics" literary movement and "Neuromancer")
- Added test data validation in `import_to_db.py` to prevent importing test fixtures
- Fixed Docker entrypoint paths: `api.py` → `api_server.py`, `web-v2` → `web`
- Fixed UI contrast and added ionice for faster conversions
- Improved conversion details panel legibility and data display
- Cleaned up obsolete scripts and symlinks from user data directory

## [3.3.1] - 2026-01-01

### Changed

- Upgrade script now automatically stops services before upgrade and restarts them after
- Removed manual "Remember to restart services" reminder (now handled automatically)
- Service status summary displayed after upgrade completes

## [3.3.0] - 2026-01-01

### Added

- Conversion Monitor in Back Office web UI with real-time progress bar, rate calculation, and ETA
- `/api/conversion/status` endpoint returning file counts, active ffmpeg processes, and system stats
- ProgressTracker class in scanner with visual progress bar (█░), rate, and ETA display
- `build-conversion-queue` script for index-based queue building with ASIN + unique non-ASIN support
- `find-duplicate-sources` script for identifying duplicate .aaxc files
- Incremental audiobook scanner with progress tracking UI
- Ananicy rules for ffmpeg priority tuning during conversions

### Changed

- Scanner now shows visual progress bar instead of simple percentage output
- Conversion queue includes unique non-ASIN files that have no ASIN equivalent

### Fixed

- Type safety improvements across codebase
- Version sync between project files
- Duplicate file handling in source directory

## [3.2.1] - 2025-12-30

### Added

- Docker build job to release workflow for automated container builds

### Changed

- Increased default parallel conversion jobs from 8 to 12
- Removed redundant config fallbacks from scripts (single source of truth in audiobook-config.sh)

### Fixed

- Updated documentation to v3.2.0 and fixed obsolete paths

## [3.2.0] - 2025-12-29

### Added

- Standalone installation via GitHub releases (`bootstrap-install.sh`)
- GitHub-based upgrade system (`audiobook-upgrade --from-github`)
- Release automation workflow (`.github/workflows/release.yml`)
- Release tarball builder (`create-release.sh`)

### Changed

- Renamed repository from `audiobook-toolkit` to `Audiobook-Manager`
- Removed Flask-CORS dependency (CORS now handled natively)
- Updated all documentation to reflect new repository name

### Removed

- Deleted monolithic `api.py` (2,244 lines) - superseded by `api_modular/`
- Deleted legacy `web.legacy/` directory - superseded by `web-v2/`

### Fixed

- Flask blueprint double-registration error in `api_modular`
- SQL injection vulnerability in `generate_hashes.py`
- Configuration path mismatch after repository rename

## [3.1.1] - 2025-12-29

### Fixed

- RuntimeDirectoryMode changed from 0755 to 0775 to allow group write access, fixing permission errors when running downloader from desktop shortcuts

## [3.1.0] - 2025-12-29

### Added

- Install manifest (`install-manifest.json`) for production validation
- API architecture selection and migration tools (`migrate-api.sh`)
- Modular Flask Blueprint architecture (`api_modular/`)
- Deployment infrastructure with dev configuration
- Post-install permission verification with umask 022

### Changed

- Refactored codebase with linting fixes and test migration to api_modular

### Fixed

- Resolved 7 hanging tests by correcting mock paths in test suite
- Fixed 13 shellcheck warnings across shell scripts
- Resolved 18 mypy type errors across Python modules
- Addressed security vulnerabilities and code quality issues

## [3.0.5] - 2025-12-27

### Security

- Fixed SQL injection vulnerability in genre query functions
- Docker container now runs as non-root user
- Added input escaping for LIKE patterns

### Changed

- Pinned Docker base image to python:3.11.11-slim
- Standardized port configuration (8443 for HTTPS, 8080 for HTTP redirect)
- Updated Flask version constraint to >=3.0.0

### Added

- LICENSE file (MIT)
- CONTRIBUTING.md with contribution guidelines
- .env.example template for easier setup
- This CHANGELOG.md

## [3.0.0] - 2025-12-25

### Added

- Modular API architecture (api_modular/ blueprints)
- PDF supplements support with viewer
- Multi-source audiobook support (experimental)
- HTTPS support with self-signed certificates
- Docker multi-platform builds (amd64, arm64)

### Changed

- Migrated from monolithic api.py to Flask Blueprints
- Improved test coverage (234 tests)
- Enhanced deployment scripts with dry-run support

### Fixed

- Cover art extraction for various formats
- Database import performance improvements
- CORS configuration for cross-origin requests

## [2.0.0] - 2024-11-28

### Added

- Web-based audiobook browser
- Search and filtering capabilities
- Cover art display and caching
- Audiobook streaming support
- SQLite database backend
- Docker containerization
- Systemd service integration

### Changed

- Complete rewrite from shell scripts to Python/Flask

## [1.0.0] - 2024-09-15

### Added

- Initial release
- AAXtoMP3 converter integration
- Basic audiobook scanning
- JSON metadata export

[Unreleased]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.14...HEAD
[8.3.8.14]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.13...v8.3.8.14
[8.3.8.13]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.12...v8.3.8.13
[8.3.8.12]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.11...v8.3.8.12
[8.3.8.11]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.10...v8.3.8.11
[8.3.8.10]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.9...v8.3.8.10
[8.3.8.9]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.8...v8.3.8.9
[8.3.8.8]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.7...v8.3.8.8
[8.3.8.7]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.6...v8.3.8.7
[8.3.8.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.5...v8.3.8.6
[8.3.8.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.4...v8.3.8.5
[8.3.8.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.3...v8.3.8.4
[8.3.8.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.2...v8.3.8.3
[8.3.8.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8.1...v8.3.8.2
[8.3.8.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.8...v8.3.8.1
[8.3.8]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.7.1...v8.3.8
[8.3.7.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.7...v8.3.7.1
[8.3.7]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.6...v8.3.7
[8.3.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.2...v8.3.6
[8.3.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.1...v8.3.2
[8.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.0.1...v8.3.1
[8.3.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.3.0...v8.3.0.1
[8.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3.6...v8.3.0
[8.2.3.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3.5...v8.2.3.6
[8.2.3.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3.4...v8.2.3.5
[8.2.3.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3.3...v8.2.3.4
[8.2.3.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3.2...v8.2.3.3
[8.2.3.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3...v8.2.3.2
[8.2.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.3...v8.2.3.1
[8.2.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.2.1...v8.2.3
[8.2.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.2...v8.2.2.1
[8.2.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.1.1...v8.2.2
[8.2.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.1...v8.2.1.1
[8.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.0.2...v8.2.1
[8.2.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.0.1...v8.2.0.2
[8.2.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.2.0...v8.2.0.1
[8.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.1.2...v8.2.0
[8.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.1.1...v8.1.2
[8.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.1.0...v8.1.1
[8.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.4.1...v8.1.0
[8.0.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.4...v8.0.4.1
[8.0.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.3.2...v8.0.4
[8.0.3.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.3.1...v8.0.3.2
[8.0.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.3...v8.0.3.1
[8.0.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.2.2...v8.0.3
[8.0.2.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.2.1...v8.0.2.2
[8.0.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.2...v8.0.2.1
[8.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.1.5...v8.0.2
[8.0.1.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.1.4...v8.0.1.5
[8.0.1.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.1.3...v8.0.1.4
[8.0.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.1.2...v8.0.1.3
[8.0.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.1.1...v8.0.1.2
[8.0.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.1...v8.0.1.1
[8.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v8.0.0...v8.0.1
[8.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.6.1...v8.0.0
[7.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.6.0...v7.6.1
[7.6.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.3...v7.6.0
[7.5.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.2.1...v7.5.3
[7.5.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.2...v7.5.2.1
[7.5.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.1.2...v7.5.1.3
[7.5.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.1.1...v7.5.1.2
[7.5.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.1...v7.5.1.1
[7.5.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.5.0...v7.5.1
[7.5.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.4.2...v7.5.0
[7.4.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.4.1.2...v7.4.2
[7.4.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.4.1.1...v7.4.1.2
[7.4.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.4.1...v7.4.1.1
[7.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.3.0.1...v7.4.1
[7.3.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.3.0...v7.3.0.1
[7.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.2.1.1...v7.3.0
[7.2.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.2.1...v7.2.1.1
[7.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.2.0...v7.2.1
[7.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.4...v7.2.0
[7.1.3.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.3...v7.1.3.4
[7.1.3.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.2...v7.1.3.3
[7.1.3.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.1...v7.1.3.2
[7.1.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3...v7.1.3.1
[7.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.2.1...v7.1.3
[7.1.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.2...v7.1.2.1
[7.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.1.1...v7.1.2
[7.1.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.1...v7.1.1.1
[7.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.0...v7.1.1
[7.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.0.2...v7.1.0
[7.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.0.1...v7.0.2
[7.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.0.0...v7.0.1
[7.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.4...v7.0.0
[6.7.2.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.3...v6.7.2.4
[6.7.2.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.2...v6.7.2.3
[6.7.2.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.1...v6.7.2.2
[6.7.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2...v6.7.2.1
[6.7.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.5...v6.7.2
[6.7.1.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.4...v6.7.1.5
[6.7.1.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.3...v6.7.1.4
[6.7.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.2...v6.7.1.3
[6.7.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.1...v6.7.1.2
[6.7.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1...v6.7.1.1
[6.7.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0.3...v6.7.1
[6.7.0.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0.2...v6.7.0.3
[6.7.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0.1...v6.7.0.2
[6.7.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0...v6.7.0.1
[6.7.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.7...v6.7.0
[6.6.7]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.6.1...v6.6.7
[6.6.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.6...v6.6.6.1
[6.6.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.5.1...v6.6.6
[6.6.5.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.5...v6.6.5.1
[6.6.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.4...v6.6.5
[6.6.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.3...v6.6.4
[6.6.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.6...v6.6.3
[6.6.2.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.5...v6.6.2.6
[6.6.2.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.4...v6.6.2.5
[6.6.2.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.3...v6.6.2.4
[6.6.2.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.2...v6.6.2.3
[6.6.2.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.1...v6.6.2.2
[6.6.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2...v6.6.2.1
[6.6.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.1.1...v6.6.2
[6.6.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.1...v6.6.1.1
[6.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.0...v6.6.1
[6.6.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.5.0.1...v6.6.0
[6.5.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.5.0...v6.5.0.1
[6.5.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.4.0.1...v6.5.0
[6.4.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.4.0...v6.4.0.1
[6.4.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.3.0...v6.4.0
[6.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.2.0.1...v6.3.0
[6.2.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.2.0...v6.2.0.1
[6.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.3...v6.2.0
[6.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.2.1...v6.1.3
[6.1.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.2...v6.1.2.1
[6.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.1...v6.1.2
[6.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.0...v6.1.1
[6.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.0.0...v6.1.0
[6.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.2...v6.0.0
[5.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.1.1...v5.0.2
[5.0.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.1...v5.0.1.1
[5.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.0...v5.0.1
[5.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.1.2...v5.0.0
[4.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.1.1...v4.1.2
[4.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.1.0...v4.1.1
[4.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.5...v4.1.0
[4.0.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.4...v4.0.5
[4.0.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.3...v4.0.4
[4.0.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.2...v4.0.3
[4.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.1...v4.0.2
[4.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.0.2...v4.0.1
[4.0.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.0.1...v4.0.0.2
[4.0.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.0...v4.0.0.1
[4.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.11.2...v4.0.0
[3.11.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.11.1...v3.11.2
[3.11.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.11.0...v3.11.1
[3.11.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.8...v3.10.0
[3.9.8]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.7...v3.9.8
[3.9.7]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.6...v3.9.7
[3.9.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.5...v3.9.6
[3.9.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.4...v3.9.5
[3.9.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.3...v3.9.4
[3.9.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.2...v3.9.3
[3.9.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.1...v3.9.2
[3.9.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.0...v3.9.1
[3.9.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.2...v3.8.0
[3.7.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.1...v3.7.2
[3.7.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.0.1...v3.7.1
[3.7.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.0...v3.7.0.1
[3.7.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.4.1...v3.7.0
[3.6.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.4...v3.6.4.1
[3.6.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.3...v3.6.4
[3.6.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.2...v3.6.3
[3.6.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.1...v3.6.2
[3.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.0...v3.6.1
[3.6.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.4.2...v3.5.0
[3.4.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.4.1...v3.4.2
[3.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.4.0...v3.4.1
[3.4.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.3.1...v3.4.0
[3.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.3.0...v3.3.1
[3.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.2.1...v3.3.0
[3.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.0.5...v3.1.0
[3.0.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.0.0...v3.0.5
[3.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v1.0.0
