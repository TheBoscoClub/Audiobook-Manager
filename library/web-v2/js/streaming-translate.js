/**
 * Streaming translation state machine.
 *
 * Manages the on-demand translation pipeline from the player's perspective:
 * - IDLE: no translation needed or book fully cached
 * - BUFFERING: waiting for GPU to process initial segments
 * - STREAMING: playing translated audio while pipeline stays ahead
 *
 * Integrates with:
 * - shell.js (ShellPlayer) — intercepts playBook() and seek events
 * - subtitles.js — loads completed VTT segments
 * - websocket.js — receives segment_ready/chapter_ready/buffer_progress events
 *
 * Dependencies: shell.js, subtitles.js, websocket.js, i18n.js
 */
(function () {
  "use strict";

  var API_BASE = "/api";
  var BUFFER_THRESHOLD = 6; // 3 minutes = 6 × 30-sec segments
  var SEGMENT_DURATION_SEC = 30;

  // State machine
  var State = {
    IDLE: "idle",
    BUFFERING: "buffering",
    STREAMING: "streaming",
  };

  /**
   * MseAudioChain — MediaSource Extensions segment appender.
   *
   * Chains per-segment opus fetches into a single HTMLAudioElement, giving
   * gapless playback of the translated stream while segments continue to
   * arrive over the WebSocket. Each 30-second opus seg is fetched via the
   * /streaming-audio/<book>/<ch>/<seg>/<locale> route and fed into a
   * SourceBuffer in sequence mode — the browser stitches them.
   *
   * Lifecycle:
   *   new MseAudioChain(audioEl) — creates MediaSource, attaches to audio
   *   enqueueSegment(url)       — fetch + appendBuffer (queued if updating)
   *   endOfStream()             — signals no more segments will arrive
   *   teardown()                — revokes ObjectURL, drops the MediaSource
   */
  function MseAudioChain(audioEl) {
    var self = this;
    self.audio = audioEl;
    self.queue = [];
    self.sourceBuffer = null;
    self.objectUrl = null;
    self.closed = false;
    // URLs already handed to enqueueSegment. Guards against a duplicate
    // append when the same segment is seen by both the enterStreaming
    // replay loop and a racing segment_ready WS event (e.g. reconnect
    // replay, or a late-arriving duplicate from the server).
    self.enqueuedUrls = {};

    if (typeof MediaSource === "undefined") {
      // Browser lacks MSE (rare on Chromium/Firefox). Chain is inert; the
      // player falls back to whatever the <audio> element already does.
      self.ready = Promise.reject(new Error("MediaSource unavailable"));
      return;
    }

    self.mediaSource = new MediaSource();
    self.objectUrl = URL.createObjectURL(self.mediaSource);
    self.audio.src = self.objectUrl;

    self.ready = new Promise(function (resolve, reject) {
      function onOpen() {
        try {
          // WebM-Opus container — Chromium-based browsers (including Brave)
          // do NOT support Ogg-Opus in MSE, only WebM-Opus and MP4-Opus.
          // Backend serves Opus inside a WebM container (no transcoding,
          // just a container repackage of the same codec).
          self.sourceBuffer = self.mediaSource.addSourceBuffer(
            'audio/webm; codecs="opus"'
          );
          self.sourceBuffer.mode = "sequence";
          self.sourceBuffer.addEventListener("updateend", function () {
            self._drain();
          });
          resolve();
        } catch (e) {
          reject(e);
        }
      }
      self.mediaSource.addEventListener("sourceopen", onOpen, { once: true });
    });
  }

  MseAudioChain.prototype.enqueueSegment = function (url) {
    var self = this;
    if (self.closed) return;
    // Dedup: if we've already kicked off a fetch for this URL, skip.
    // Prevents double-append when replay loop + WS event race.
    if (self.enqueuedUrls[url]) return;
    self.enqueuedUrls[url] = true;
    // Even if .ready rejected (no MSE), we skip silently.
    return self.ready
      .then(function () {
        return fetch(url);
      })
      .then(function (r) {
        if (!r.ok) throw new Error("segment fetch " + r.status);
        return r.arrayBuffer();
      })
      .then(function (buf) {
        if (self.closed) return;
        self.queue.push(buf);
        self._drain();
      })
      .catch(function () {
        // Swallow — one failed seg should not break the chain. The
        // buffering overlay and WS retry handle re-dispatch server-side.
      });
  };

  MseAudioChain.prototype._drain = function () {
    if (!this.sourceBuffer || this.sourceBuffer.updating) return;
    var next = this.queue.shift();
    if (next) {
      try {
        this.sourceBuffer.appendBuffer(next);
      } catch (e) {
        // QuotaExceededError or buffer full — drop; updateend will retry.
      }
    }
  };

  MseAudioChain.prototype.endOfStream = function () {
    if (this.closed) return;
    if (this.mediaSource && this.mediaSource.readyState === "open") {
      try {
        this.mediaSource.endOfStream();
      } catch (e) {
        // endOfStream can throw if buffer is still updating; ignore.
      }
    }
  };

  MseAudioChain.prototype.teardown = function () {
    this.closed = true;
    this.queue = [];
    this.enqueuedUrls = {};
    // Transition the MediaSource to 'ended' before dropping references.
    // Revoking the ObjectURL alone leaves the MSE in 'open' state —
    // the audio element still holds it via the blob URL, leaking it
    // across book-switches over long sessions.
    if (this.mediaSource && this.mediaSource.readyState === "open") {
      try { this.mediaSource.endOfStream(); } catch (e) {}
    }
    if (this.objectUrl) {
      try { URL.revokeObjectURL(this.objectUrl); } catch (e) {}
      this.objectUrl = null;
    }
    this.sourceBuffer = null;
    this.mediaSource = null;
  };

  var state = State.IDLE;
  var currentBookId = null;
  var currentLocale = null;
  var currentChapter = 0;
  var sessionId = null;
  var segmentBitmap = {}; // chapter -> Set of completed segment indices
  var notificationAudio = null;
  var notificationPlayed = false;
  var mseChain = null; // MseAudioChain for translated audio playback

  // ── Polling fallback when WS is silent (Task 15, v8.3.2) ──
  //
  // If the WebSocket disconnects (mobile network change, laptop sleep,
  // Caddy restart) or stops delivering events mid-chapter, the overlay
  // freezes because nothing else feeds progress data. We arm a 5 s stall
  // timer whenever we're in BUFFERING/STREAMING; if no WS event resets
  // it, we start polling GET /api/translate/session/<id>/<locale> every
  // 3 s and synthesize a buffer_progress event from each response so the
  // existing onBufferProgress handler runs — DRY.
  //
  // TODO(task-22): add Playwright e2e coverage for polling fallback.

  var STALL_TIMEOUT_MS = 5000;   // no WS event for this long → start polling
  var POLL_INTERVAL_MS = 3000;   // poll cadence while in fallback

  var stallTimer = null;         // fires when no WS event seen for STALL_TIMEOUT_MS
  var pollTimer = null;          // recurring poll while in fallback
  var pollingActive = false;

  function armStallTimer() {
    if (stallTimer) {
      clearTimeout(stallTimer);
      stallTimer = null;
    }
    if (state === State.BUFFERING || state === State.STREAMING) {
      stallTimer = setTimeout(startStreamingPolling, STALL_TIMEOUT_MS);
    }
  }

  function onAnyStreamingEvent() {
    // Called when any streaming-related signal (WS event, state
    // transition, ws-connected) arrives. Resets the stall clock and
    // exits polling if we were in it.
    if (pollingActive) stopStreamingPolling();
    armStallTimer();
  }

  function startStreamingPolling() {
    if (pollingActive) return;
    if (!currentBookId || !currentLocale) return;
    pollingActive = true;
    pollOnce();
    pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS);
  }

  function stopStreamingPolling() {
    pollingActive = false;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function pollOnce() {
    if (!currentBookId || !currentLocale) return;
    var url = API_BASE + "/translate/session/" + currentBookId +
              "/" + encodeURIComponent(currentLocale);
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || data.state === "none") return;
        // Synthesize a buffer_progress event from the polled payload so the
        // existing onBufferProgress handler handles the UI update — DRY.
        var synthesized = {
          type: "buffer_progress",
          audiobook_id: currentBookId,
          chapter_index: data.active_chapter,
          locale: currentLocale,
          completed: data.completed || 0,
          total: data.total || 0,
          threshold: data.buffer_threshold || BUFFER_THRESHOLD,
          phase: data.phase || "idle",
          // Marks this event as poll-originated so onBufferProgress
          // doesn't stop the polling loop that just produced it.
          _synthesized: true,
        };
        onBufferProgress(synthesized);
      })
      .catch(function () { /* ignore; next poll will retry */ });
  }

  // DOM references
  var overlay = null;
  var overlayMessage = null;
  var progressFill = null;
  var progressText = null;

  // ── Overlay UI ──

  function showOverlay(completedCount, total) {
    if (!overlay) return;
    overlay.style.display = "";
    updateProgress(completedCount, total);
  }

  function hideOverlay() {
    if (!overlay) return;
    overlay.style.display = "none";
  }

  function updateProgress(completed, total) {
    if (!progressFill || !progressText) return;
    var threshold = total > 0 ? Math.min(BUFFER_THRESHOLD, total) : BUFFER_THRESHOLD;
    var pct = threshold > 0 ? Math.min(100, Math.round((completed / threshold) * 100)) : 0;
    progressFill.style.width = pct + "%";
    progressText.textContent = completed + " / " + threshold;
  }

  function setMessage(msg) {
    if (overlayMessage) overlayMessage.textContent = msg;
  }

  // ── Notification audio ──

  function playNotification(locale) {
    if (notificationPlayed) return;
    notificationPlayed = true;

    // Play only the locale-specific clip. No English fallback — overlapping
    // zh-Hans + EN playback was confusing users on QA (2026-04-19). If the
    // locale clip fails to decode or autoplay is blocked, the visual overlay
    // already communicates "preparing translation…" — silent audio is fine.
    var audioFile = "/audio/translation-buffering-" + locale + ".mp3";
    notificationAudio = new Audio(audioFile);
    notificationAudio.volume = 0.7;
    notificationAudio.play().catch(function () {
      // Autoplay blocked or file unavailable — visual overlay is enough.
    });
  }

  function stopNotification() {
    if (notificationAudio) {
      notificationAudio.pause();
      notificationAudio = null;
    }
  }

  // ── State transitions ──

  function enterBuffering(bookId, locale, chapterIndex, bitmap) {
    state = State.BUFFERING;
    currentBookId = bookId;
    currentLocale = locale;
    currentChapter = chapterIndex;
    notificationPlayed = false;

    // Parse bitmap
    var completed = 0;
    var total = 0;
    if (bitmap) {
      completed = Array.isArray(bitmap.completed) ? bitmap.completed.length : 0;
      total = bitmap.total || 0;

      // Update local bitmap — MUST happen before the `all_cached` fast-path
      // below, otherwise `enterStreaming` finds an empty
      // segmentBitmap[chapterIndex] and its enqueueSegment loop is a no-op.
      // Symptom of getting this wrong: books with short ch=0 (e.g. a 1-seg
      // "This is Audible" intro) load the player, decide the chapter is
      // already cached, skip buffering, then sit at audio.currentTime=0
      // with readyState=0 forever because MSE was never fed. Caught during
      // v8.3.8.6 orphan-repair browser proof on books 115401 (1 seg ch=0),
      // 115852 (3 segs), 116062 (1 seg).
      if (!segmentBitmap[chapterIndex]) segmentBitmap[chapterIndex] = new Set();
      if (Array.isArray(bitmap.completed)) {
        bitmap.completed.forEach(function (idx) {
          segmentBitmap[chapterIndex].add(idx);
        });
      }
      if (bitmap.all_cached) {
        // Already cached — skip buffering entirely. segmentBitmap is now
        // populated, so enterStreaming's replay loop will enqueue each
        // cached segment into the MSE chain.
        enterStreaming();
        return;
      }
    }

    // Check if we already have enough segments
    var threshold = total > 0 ? Math.min(BUFFER_THRESHOLD, total) : BUFFER_THRESHOLD;
    if (completed >= threshold) {
      enterStreaming();
      return;
    }

    // Show overlay and play notification
    var msg = typeof t === "function"
      ? t("streaming.phase.warmup")
      : "Preparing…";
    setMessage(msg);
    showOverlay(completed, total);
    playNotification(locale);

    // Pause the main audio while buffering
    var audio = document.getElementById("audio-element");
    if (audio && !audio.paused) {
      audio.pause();
    }

    // Arm the stall timer so we kick over to polling if the WS stays
    // silent for STALL_TIMEOUT_MS.
    armStallTimer();
  }

  function enterStreaming() {
    state = State.STREAMING;
    hideOverlay();
    stopNotification();

    // Create the MSE chain that drives translated-audio playback. The
    // English <audio> element is paused by enterBuffering; once we're in
    // STREAMING, the chain takes over segment-by-segment.
    var audio = document.getElementById("audio-element");
    if (audio && !mseChain) {
      mseChain = new MseAudioChain(audio);

      // Replay any segments that completed while we were buffering into
      // the new chain, so the MSE buffer starts populated rather than
      // waiting for the next segment_ready WS event.
      var chMap = segmentBitmap[currentChapter];
      if (chMap && chMap !== "all" && chMap.forEach) {
        var sorted = Array.from(chMap).sort(function (a, b) { return a - b; });
        sorted.forEach(function (segIdx) {
          var url = "/streaming-audio/" + currentBookId + "/" + currentChapter +
                    "/" + segIdx + "/" + currentLocale;
          mseChain.enqueueSegment(url);
        });
      }
    }

    // Resume playback with translated audio
    if (audio && audio.paused && currentBookId) {
      audio.play().catch(function () {});
    }

    // Arm the stall timer — entering STREAMING means we should start
    // expecting a steady cadence of WS events; if they stop, kick over
    // to polling after STALL_TIMEOUT_MS.
    armStallTimer();
  }

  function enterIdle() {
    // Tear down the polling fallback before we null out book/locale —
    // pollOnce() guards against missing currentBookId but it's cleaner
    // to stop the timers up front.
    stopStreamingPolling();
    if (stallTimer) {
      clearTimeout(stallTimer);
      stallTimer = null;
    }

    state = State.IDLE;
    hideOverlay();
    stopNotification();
    if (mseChain) {
      mseChain.teardown();
      mseChain = null;
    }
    currentBookId = null;
    currentLocale = null;
    sessionId = null;
    segmentBitmap = {};
    notificationPlayed = false;
  }

  // ── Drain on player-close / tab-close (v8.3.2 Bug D) ──
  //
  // Graceful drain signals:
  //   1. Player frame closed
  //   2. Click X on player (sp-close) — shell.js::close()
  //   3. MediaSession Stop — shell.js wires to close()
  //   4. Tab/browser close — pagehide listener below
  //
  // "Drain" means: stop queueing new segments for this session (backend
  // DELETEs any pending rows and flips state='stopped'; worker's LEFT
  // JOIN filter blocks further claims even if a row slips the race).
  // In-flight processing rows complete naturally — we don't kill them.
  //
  // Book-switch (event 5 in the spec) is abort + pivot: playBook() in
  // shell.js calls drain() before streamingTranslate.check() starts the
  // new session, so the old book's pipeline stops before the new one
  // begins.
  function drainStreaming(useBeacon) {
    if (state === State.IDLE) return;
    var bookId = currentBookId;
    var locale = currentLocale;
    if (!bookId || !locale) {
      enterIdle();
      return;
    }
    var body = JSON.stringify({
      audiobook_id: bookId,
      locale: locale,
    });
    // navigator.sendBeacon is the only reliable transport once the page
    // is unloading — fetch() gets cancelled by most browsers mid-unload.
    // keepalive on fetch helps for close() but not pagehide/beforeunload.
    if (useBeacon && typeof navigator !== "undefined" &&
        typeof navigator.sendBeacon === "function") {
      try {
        var blob = new Blob([body], { type: "application/json" });
        navigator.sendBeacon(API_BASE + "/translate/stop", blob);
      } catch (e) { /* swallow — beacon is best-effort by design */ }
    } else {
      fetch(API_BASE + "/translate/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        credentials: "same-origin",
        keepalive: true,
      }).catch(function () { /* best-effort */ });
    }
    enterIdle();
  }

  // ── Segment tracking ──

  function onSegmentReady(data) {
    if (data.audiobook_id !== currentBookId || data.locale !== currentLocale) return;

    // Every genuine WS event resets the stall timer + exits polling.
    onAnyStreamingEvent();

    var ch = data.chapter_index;
    var seg = data.segment_index;

    // segmentBitmap[ch] can be:
    //   - undefined (first segment for this chapter)
    //   - a Set (normal in-progress case)
    //   - the string "all" (chapter was previously marked fully cached via
    //     onChapterReady). This happens when a prior session's false-cached
    //     chapter was detected or when the backend reports chapter_ready
    //     before a new p=0 segment arrives (e.g. after the cursor advanced
    //     past the sampler-only chapter into a freshly-activated chapter).
    //
    // In all three cases, a new segment_ready for this chapter is valid
    // input. Replace a "all" sentinel with a fresh Set and record the
    // segment — the chapter is manifestly NOT fully cached if we just got
    // a new segment for it.
    if (!segmentBitmap[ch] || segmentBitmap[ch] === "all") {
      segmentBitmap[ch] = new Set();
    }
    segmentBitmap[ch].add(seg);

    // Feed the MSE chain with the opus for this segment so translated
    // audio playback stays ahead of the cursor. Only enqueue for the
    // currently-playing chapter — future chapters will get their own
    // chain when the player advances.
    if (state === State.STREAMING && mseChain && ch === currentChapter) {
      var url = "/streaming-audio/" + data.audiobook_id + "/" + ch +
                "/" + seg + "/" + data.locale;
      mseChain.enqueueSegment(url);
    }
  }

  function onBufferProgress(data) {
    if (data.audiobook_id !== currentBookId || data.locale !== currentLocale) return;
    if (data.chapter_index !== currentChapter) return;

    // Genuine WS-delivered buffer_progress events reset the stall timer +
    // exit polling. Synthesized events that originate from the polling
    // loop carry ``_synthesized=true`` so we don't stop our own poller.
    if (!data._synthesized) onAnyStreamingEvent();

    // v8.3.2 Bug C: when the backend phase resolves to "error" (any
    // streaming_segments row for this session reached state='failed'
    // after retry_count>=3), the spinner must not keep turning. Surface
    // the error on the overlay and collapse to IDLE so the user isn't
    // trapped behind a perpetual "Preparing…". Fresh playback attempt is
    // the path to retry — the pipeline does not self-heal.
    if (data.phase === "error") {
      var errMsg = typeof t === "function"
        ? t("streaming.phase.error")
        : "Translation error — please try again";
      setMessage(errMsg);
      updateProgress(data.completed || 0, data.total || 0);
      setTimeout(function () { enterIdle(); }, 3000);
      return;
    }

    var completed = data.completed || 0;
    var total = data.total || 0;
    // Cap threshold to total: a chapter with fewer segments than
    // BUFFER_THRESHOLD must still be able to complete warmup — otherwise
    // the BUFFERING → STREAMING transition can never fire via segment
    // progress, leaving the user stuck on the spinner until the chapter
    // fully consolidates (onChapterReady). Matches the same cap in
    // updateProgress() and the phase/message logic below.
    var rawThreshold = data.threshold || BUFFER_THRESHOLD;
    var threshold = total > 0 ? Math.min(rawThreshold, total) : rawThreshold;

    updateProgress(completed, total);

    if (state === State.BUFFERING && completed >= threshold) {
      enterStreaming();
    }
  }

  function onChapterReady(data) {
    if (data.audiobook_id !== currentBookId || data.locale !== currentLocale) return;

    // Every genuine WS event resets the stall timer + exits polling.
    onAnyStreamingEvent();

    var ch = data.chapter_index;
    segmentBitmap[ch] = "all"; // Mark entire chapter as cached

    // If this is the active chapter and we were buffering, start streaming
    if (ch === currentChapter && state === State.BUFFERING) {
      enterStreaming();
    }

    // Reload subtitles for this chapter if it's the one being played
    if (typeof window.subtitles !== "undefined" && window.subtitles.load) {
      // Don't reload everything — just trigger a chapter poll refresh
    }
  }

  // ── Seek / skip handling ──

  function isSegmentCached(chapterIndex, segmentIndex) {
    var chMap = segmentBitmap[chapterIndex];
    if (!chMap) return false;
    if (chMap === "all") return true;
    return chMap.has(segmentIndex);
  }

  function positionToSegment(positionSec) {
    return Math.floor(positionSec / SEGMENT_DURATION_SEC);
  }

  function handleSeek(positionSec, chapterIndex) {
    if (state === State.IDLE) return; // Not in streaming mode

    var segIdx = positionToSegment(positionSec);
    var ch = chapterIndex !== undefined ? chapterIndex : currentChapter;

    if (isSegmentCached(ch, segIdx)) {
      // Within cached range — allow seek, no buffering
      return;
    }

    // Seek into uncached territory — re-enter buffering
    currentChapter = ch;

    fetch(API_BASE + "/translate/seek", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audiobook_id: currentBookId,
        locale: currentLocale,
        chapter_index: ch,
        segment_index: segIdx,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.state === "cached") return; // Already cached
        enterBuffering(currentBookId, currentLocale, ch, data.segment_bitmap);
      })
      .catch(function () {});
  }

  // ── Integration with ShellPlayer.playBook() ──

  function checkAndInitStreaming(bookId, locale) {
    if (locale === "en") {
      enterIdle();
      return;
    }

    // Show overlay + pause audio SYNCHRONOUSLY so the user sees feedback
    // before /translate/stream resolves. Without this the overlay appears
    // ~200-500ms late and any English audio that started can play audibly
    // through the gap. enterBuffering / enterIdle below will reconcile.
    currentBookId = bookId;
    currentLocale = locale;
    state = State.BUFFERING;
    setMessage(typeof t === "function" ? t("streaming.phase.warmup") : "Preparing…");
    showOverlay(0, 0);
    var audio = document.getElementById("audio-element");
    if (audio && !audio.paused) {
      audio.pause();
    }

    // Ask the coordinator if this book needs streaming
    fetch(API_BASE + "/translate/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audiobook_id: bookId,
        locale: locale,
        chapter_index: 0,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.state === "cached") {
          // All cached — no streaming needed
          enterIdle();
        } else if (data.state === "buffering") {
          sessionId = data.session_id;
          enterBuffering(bookId, locale, data.chapter_index, data.segment_bitmap);
        }
      })
      .catch(function () {
        // On network error, don't block — fall through to normal playback
        enterIdle();
      });
  }

  // ── GPU warm-up on app open ──

  function warmupGPU() {
    fetch(API_BASE + "/translate/warmup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }).catch(function () {}); // Fire and forget
  }

  // ── Initialize ──

  document.addEventListener("DOMContentLoaded", function () {
    overlay = document.getElementById("streaming-overlay");
    overlayMessage = document.getElementById("streaming-message");
    progressFill = document.getElementById("streaming-progress-fill");
    progressText = document.getElementById("streaming-progress-text");

    // Listen for WebSocket events
    document.addEventListener("segment-ready", function (e) {
      onSegmentReady(e.detail);
    });
    document.addEventListener("buffer-progress", function (e) {
      onBufferProgress(e.detail);
    });
    document.addEventListener("chapter-ready", function (e) {
      onChapterReady(e.detail);
    });

    // When the WebSocket reconnects (see websocket.js dispatch on open),
    // tear down any active polling fallback and re-arm the stall timer.
    // The companion ws-disconnected event does not exist today — the 5 s
    // stall timer is the only disconnect-detection signal the player
    // needs, so don't listen for it.
    document.addEventListener("ws-connected", function () {
      if (pollingActive) stopStreamingPolling();
      armStallTimer();
    });

    // Fire GPU warm-up on load (for non-English locales)
    var locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
    if (locale !== "en") {
      warmupGPU();
    }

    // Tab/browser close drain. pagehide fires on all modern browsers for
    // back-forward cache and genuine unload alike; beforeunload is the
    // desktop-Chrome safety net. Either triggers a sendBeacon drain so
    // the backend sees state='stopped' and deletes pending rows even if
    // the user never clicked the X or Stop.
    window.addEventListener("pagehide", function () {
      if (state !== State.IDLE) drainStreaming(true);
    });
    window.addEventListener("beforeunload", function () {
      if (state !== State.IDLE) drainStreaming(true);
    });
  });

  // ── Public API ──

  window.streamingTranslate = {
    check: checkAndInitStreaming,
    handleSeek: handleSeek,
    getState: function () { return state; },
    isBuffering: function () { return state === State.BUFFERING; },
    isStreaming: function () { return state === State.STREAMING; },
    isIdle: function () { return state === State.IDLE; },
    isSegmentCached: isSegmentCached,
    enterIdle: enterIdle,
    drain: drainStreaming,
  };
})();
