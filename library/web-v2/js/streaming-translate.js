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

  var state = State.IDLE;
  var currentBookId = null;
  var currentLocale = null;
  var currentChapter = 0;
  var sessionId = null;
  var segmentBitmap = {}; // chapter -> Set of completed segment indices
  var notificationAudio = null;
  var notificationPlayed = false;

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

    // Determine which notification clip to play
    var audioFile = "/audio/translation-buffering-" + locale + ".mp3";
    // Fallback to English if locale-specific file doesn't exist
    notificationAudio = new Audio(audioFile);
    notificationAudio.volume = 0.7;
    notificationAudio.play().catch(function () {
      // Try English fallback
      notificationAudio = new Audio("/audio/translation-buffering-en.mp3");
      notificationAudio.volume = 0.7;
      notificationAudio.play().catch(function () {
        // Audio autoplay blocked — that's OK, visual overlay is enough
      });
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
      if (bitmap.all_cached) {
        // Already cached — skip buffering entirely
        enterStreaming();
        return;
      }
      completed = Array.isArray(bitmap.completed) ? bitmap.completed.length : 0;
      total = bitmap.total || 0;

      // Update local bitmap
      if (!segmentBitmap[chapterIndex]) segmentBitmap[chapterIndex] = new Set();
      if (Array.isArray(bitmap.completed)) {
        bitmap.completed.forEach(function (idx) {
          segmentBitmap[chapterIndex].add(idx);
        });
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
      ? t("streaming.preparing")
      : "Preparing translation...";
    setMessage(msg);
    showOverlay(completed, total);
    playNotification(locale);

    // Pause the main audio while buffering
    var audio = document.getElementById("audio-element");
    if (audio && !audio.paused) {
      audio.pause();
    }
  }

  function enterStreaming() {
    state = State.STREAMING;
    hideOverlay();
    stopNotification();

    // Resume playback with translated audio
    var audio = document.getElementById("audio-element");
    if (audio && audio.paused && currentBookId) {
      audio.play().catch(function () {});
    }
  }

  function enterIdle() {
    state = State.IDLE;
    hideOverlay();
    stopNotification();
    currentBookId = null;
    currentLocale = null;
    sessionId = null;
    segmentBitmap = {};
    notificationPlayed = false;
  }

  // ── Segment tracking ──

  function onSegmentReady(data) {
    if (data.audiobook_id !== currentBookId || data.locale !== currentLocale) return;

    var ch = data.chapter_index;
    var seg = data.segment_index;

    if (!segmentBitmap[ch]) segmentBitmap[ch] = new Set();
    segmentBitmap[ch].add(seg);
  }

  function onBufferProgress(data) {
    if (data.audiobook_id !== currentBookId || data.locale !== currentLocale) return;
    if (data.chapter_index !== currentChapter) return;

    var completed = data.completed || 0;
    var total = data.total || 0;
    var threshold = data.threshold || BUFFER_THRESHOLD;

    updateProgress(completed, total);

    if (state === State.BUFFERING && completed >= threshold) {
      enterStreaming();
    }
  }

  function onChapterReady(data) {
    if (data.audiobook_id !== currentBookId || data.locale !== currentLocale) return;

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

    // Fire GPU warm-up on load (for non-English locales)
    var locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
    if (locale !== "en") {
      warmupGPU();
    }
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
  };
})();
