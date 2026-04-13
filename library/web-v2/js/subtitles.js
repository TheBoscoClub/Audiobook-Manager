/**
 * Subtitle and transcript display module.
 *
 * Loads VTT subtitle files, displays inline subtitles synced to audio playback,
 * and manages the side panel transcript view. Works with the shell player's
 * audio element for timing synchronization.
 *
 * Dependencies: i18n.js (for t() and locale), shell.js (for audio element)
 */
(function () {
  "use strict";

  var API_BASE = "/api";
  var subtitleDisplay = null;
  var subtitleSource = null;
  var subtitleTranslated = null;
  var transcriptPanel = null;
  var transcriptContent = null;

  // State
  var sourceCues = [];
  var translatedCues = [];
  var currentCueIndex = -1;
  var subtitlesVisible = false;
  var transcriptVisible = false;
  var currentBookId = null;
  var currentChapterIndex = 0;
  var playingTranslated = false;
  var translatedAudioEntries = [];
  var _loadedChapters = {};
  var _chapterPollTimer = null;
  var _waitResolve = null;

  // ── VTT Parsing ──

  function parseVTT(text) {
    var cues = [];
    var blocks = text.split(/\n\n+/);
    for (var i = 0; i < blocks.length; i++) {
      var lines = blocks[i].trim().split("\n");
      for (var j = 0; j < lines.length; j++) {
        var match = lines[j].match(
          /(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})/
        );
        if (match) {
          var startMs =
            parseInt(match[1]) * 3600000 +
            parseInt(match[2]) * 60000 +
            parseInt(match[3]) * 1000 +
            parseInt(match[4]);
          var endMs =
            parseInt(match[5]) * 3600000 +
            parseInt(match[6]) * 60000 +
            parseInt(match[7]) * 1000 +
            parseInt(match[8]);
          var textLines = lines.slice(j + 1).join("\n").trim();
          if (textLines) {
            cues.push({ startMs: startMs, endMs: endMs, text: textLines });
          }
          break;
        }
      }
    }
    return cues;
  }

  // ── Subtitle Loading ──

  function loadSubtitles(bookId, chapterIndex) {
    currentBookId = bookId;
    currentChapterIndex = chapterIndex || 0;
    sourceCues = [];
    translatedCues = [];
    currentCueIndex = -1;
    _loadedChapters = {};
    stopChapterPoll();
    hideTtsBanner();

    var locale =
      typeof i18n !== "undefined" ? i18n.getLocale() : "en";

    fetch(API_BASE + "/audiobooks/" + bookId + "/subtitles")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (entries) {
        var enChapters = [];
        var trChapters = [];
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].locale === "en") enChapters.push(entries[i].chapter_index);
          else if (entries[i].locale === locale) trChapters.push(entries[i].chapter_index);
        }

        var fetches = [];
        for (var j = 0; j < enChapters.length; j++) {
          fetches.push(fetchChapterVTT(bookId, enChapters[j], "en"));
        }
        if (locale !== "en") {
          for (var k = 0; k < trChapters.length; k++) {
            fetches.push(fetchChapterVTT(bookId, trChapters[k], locale));
          }
        }

        return Promise.all(fetches).then(function () {
          mergeCues();
          onSubtitlesUpdated(bookId, chapterIndex, locale);

          if (sourceCues.length === 0 && translatedCues.length === 0 && locale !== "en") {
            showGenBanner(bookId, locale);
          }

          startChapterPoll(bookId, locale);
        });
      })
      .catch(function () {
        onSubtitlesUpdated(bookId, chapterIndex, locale);
      });
  }

  function fetchChapterVTT(bookId, chIdx, locale) {
    var key = locale + ":" + chIdx;
    if (_loadedChapters[key]) return Promise.resolve();

    var url = API_BASE + "/audiobooks/" + bookId + "/subtitles/" + chIdx + "/" + encodeURIComponent(locale);
    return fetch(url)
      .then(function (r) { return r.ok ? r.text() : ""; })
      .then(function (text) {
        if (text) {
          _loadedChapters[key] = parseVTT(text);
        }
      })
      .catch(function () {});
  }

  function mergeCues() {
    var src = [];
    var tr = [];
    var keys = Object.keys(_loadedChapters).sort();
    for (var i = 0; i < keys.length; i++) {
      var cues = _loadedChapters[keys[i]];
      if (keys[i].indexOf("en:") === 0) {
        src = src.concat(cues);
      } else {
        tr = tr.concat(cues);
      }
    }
    src.sort(function (a, b) { return a.startMs - b.startMs; });
    tr.sort(function (a, b) { return a.startMs - b.startMs; });
    sourceCues = src;
    translatedCues = tr;
  }

  function startChapterPoll(bookId, locale) {
    stopChapterPoll();
    var noNewCount = 0;
    _chapterPollTimer = setInterval(function () {
      if (currentBookId !== bookId) { stopChapterPoll(); return; }
      fetch(API_BASE + "/audiobooks/" + bookId + "/subtitles")
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (entries) {
          var newFetches = [];
          for (var i = 0; i < entries.length; i++) {
            var e = entries[i];
            var key = e.locale + ":" + e.chapter_index;
            if (!_loadedChapters[key] && (e.locale === "en" || e.locale === locale)) {
              newFetches.push(fetchChapterVTT(bookId, e.chapter_index, e.locale));
            }
          }
          if (newFetches.length === 0) {
            noNewCount++;
            if (noNewCount >= 6) stopChapterPoll();
            return;
          }
          noNewCount = 0;
          Promise.all(newFetches).then(function () {
            mergeCues();
            onSubtitlesUpdated(bookId, 0, locale);
            if (_waitResolve && hasSubtitlesAtPosition(_waitPositionMs)) {
              _waitResolve();
              _waitResolve = null;
            }
          });
        })
        .catch(function () {});
    }, 5000);
  }

  function stopChapterPoll() {
    if (_chapterPollTimer) {
      clearInterval(_chapterPollTimer);
      _chapterPollTimer = null;
    }
  }

  function onSubtitlesUpdated(bookId, chapterIndex, locale) {
    var hasSubtitles = sourceCues.length > 0 || translatedCues.length > 0;

    var ccBtn = document.getElementById("sp-subtitle-toggle");
    var trBtn = document.getElementById("sp-transcript-toggle");
    if (ccBtn) ccBtn.style.display = hasSubtitles ? "" : "none";
    if (trBtn) trBtn.style.display = hasSubtitles ? "" : "none";

    checkTranslatedAudio(bookId, chapterIndex, locale);

    if (hasSubtitles) {
      buildTranscriptPanel();
      hideGenBanner();
    } else if (locale !== "en") {
      showGenBanner(bookId, locale);
    } else {
      hideGenBanner();
    }
  }

  var _waitPositionMs = 0;

  function hasSubtitlesAtPosition(posMs) {
    var cues = sourceCues.length > 0 ? sourceCues : translatedCues;
    if (cues.length === 0) return false;
    var last = cues[cues.length - 1];
    return posMs <= last.endMs;
  }

  function isGenerationActive() {
    return _genPollTimer !== null || _chapterPollTimer !== null;
  }

  function waitForSubtitlesAt(positionMs) {
    if (hasSubtitlesAtPosition(positionMs)) return Promise.resolve();
    if (!isGenerationActive()) return Promise.resolve();
    _waitPositionMs = positionMs;
    return new Promise(function (resolve) {
      _waitResolve = resolve;
      setTimeout(resolve, 30000);
    });
  }

  // ── On-demand subtitle generation banner ──
  // When a translated-locale user opens a book without subtitles, offer to
  // generate them. Because STT (Whisper on Vast.ai) + translation is slow and
  // involves a GPU cold start, we poll /api/subtitles/status and surface a
  // human-readable phase so the user isn't staring at a silent spinner.

  var _genPollTimer = null;
  var _genBannerBookId = null;

  function genBanner() { return document.getElementById("subtitle-gen-banner"); }

  function setGenUi(mode) {
    var banner = genBanner();
    if (!banner) return;
    var idle = banner.querySelector(".sgb-idle");
    var prog = banner.querySelector(".sgb-progress");
    if (mode === "idle") {
      if (idle) idle.style.display = "";
      if (prog) prog.style.display = "none";
    } else if (mode === "progress") {
      if (idle) idle.style.display = "none";
      if (prog) prog.style.display = "";
    }
    banner.style.display = "";
  }

  function hideGenBanner() {
    stopGenPoll();
    var banner = genBanner();
    if (banner) banner.style.display = "none";
  }

  function showGenBanner(bookId, locale) {
    _genBannerBookId = bookId;
    var banner = genBanner();
    if (!banner) return;
    // Skip idle state — translation is automatic, just show progress
    setGenUi("progress");
    renderGenStatus({ phase: "queued", message: (typeof t === "function" ? t("subtitleGen.queued") : "Preparing translation…") });
    // Check if a job is already running or queued
    fetch(API_BASE + "/translation/status/" + bookId + "/" + encodeURIComponent(locale))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (s && (s.state === "processing" || s.state === "pending")) {
          renderGenStatus(s);
          startGenPoll(bookId, locale);
        } else if (!s || s.state === "not_queued") {
          // Auto-queue this book
          fetch(API_BASE + "/translation/bump", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ audiobook_id: bookId, locale: locale }),
          }).then(function () {
            startGenPoll(bookId, locale);
          }).catch(function () {});
        } else if (s.state === "completed") {
          hideGenBanner();
        } else if (s.state === "failed") {
          renderGenStatus({ phase: "error", message: s.error || "Translation failed" });
        }
      })
      .catch(function () {});
  }

  function startGeneration(bookId, locale) {
    setGenUi("progress");
    renderGenStatus({ phase: "queued", message: (typeof t === "function" ? t("subtitleGen.queued") : "Queued…") });
    fetch(API_BASE + "/user/subtitles/request", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audiobook_id: bookId, locale: locale }),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          // 401: sign-in required. 429: cooldown. Show the server message.
          renderGenStatus({
            phase: "error",
            message: (res.body && res.body.error) || (res.body && res.body.message) || (typeof t === "function" ? t("subtitleGen.startFailed") : "Could not start subtitle generation."),
          });
          return;
        }
        startGenPoll(bookId, locale);
      })
      .catch(function () {
        renderGenStatus({ phase: "error", message: (typeof t === "function" ? t("subtitleGen.networkError") : "Network error. Please try again.") });
      });
  }

  function startGenPoll(bookId, locale) {
    stopGenPoll();
    var _genIdleCount = 0;
    _genPollTimer = setInterval(function () {
      if (_genBannerBookId !== bookId) { stopGenPoll(); return; }
      fetch(API_BASE + "/translation/status/" + bookId + "/" + encodeURIComponent(locale))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (s) {
          if (!s) return;
          if (!s.state || s.state === "not_queued") {
            _genIdleCount++;
            if (_genIdleCount >= 6) {
              stopGenPoll();
              hideGenBanner();
            }
            return;
          }
          _genIdleCount = 0;
          renderGenStatus(s);
          if (s.state === "completed") {
            stopGenPoll();
            hideGenBanner();
            setTimeout(function () { loadSubtitles(bookId, currentChapterIndex); }, 500);
          } else if (s.state === "failed") {
            stopGenPoll();
          }
        })
        .catch(function () {});
    }, 3000);
  }

  function stopGenPoll() {
    if (_genPollTimer) {
      clearInterval(_genPollTimer);
      _genPollTimer = null;
    }
  }

  function renderGenStatus(status) {
    var phaseEl = document.getElementById("sgb-phase");
    var detailEl = document.getElementById("sgb-detail");
    var progressEl = document.getElementById("sgb-progress-bar");
    if (!phaseEl || !detailEl) return;

    var phaseKey = "subtitleGen.phase." + (status.phase || "queued");
    var phaseLabel = typeof t === "function" ? t(phaseKey) : phaseKey;
    var phaseTranslated = phaseLabel !== phaseKey;
    if (!phaseTranslated) {
      phaseLabel = status.message || phaseKey;
    }

    // Chapter progress: "Transcribing chapter 3 of 42: The Departure"
    if (status.chapter_total && status.chapter_total > 1 && status.phase === "transcribing") {
      var chNum = (status.chapter_index || 0) + 1;
      var chTotal = status.chapter_total;
      var chTitle = status.chapter_title || "";
      if (typeof t === "function") {
        phaseLabel = t("subtitleGen.phase.transcribingChapter", {
          current: chNum, total: chTotal, title: chTitle
        });
        if (phaseLabel.indexOf("{") !== -1) {
          phaseLabel = "Chapter " + chNum + " of " + chTotal + (chTitle ? ": " + chTitle : "");
        }
      } else {
        phaseLabel = "Chapter " + chNum + " of " + chTotal + (chTitle ? ": " + chTitle : "");
      }
    }
    phaseEl.textContent = phaseLabel;

    // Progress bar for chapter-by-chapter work
    if (progressEl) {
      if (status.chapter_total && status.chapter_total > 1) {
        var pct = Math.round(((status.chapter_index || 0) / status.chapter_total) * 100);
        progressEl.style.width = pct + "%";
        progressEl.parentElement.style.display = "";
      } else {
        progressEl.parentElement.style.display = "none";
      }
    }

    var showDetail = false;
    if (!phaseTranslated && status.message && !status.chapter_total) {
      showDetail = true;
    }
    if (status.state === "failed" && status.error) {
      detailEl.textContent = status.error;
      showDetail = true;
    } else if (showDetail) {
      detailEl.textContent = status.message || "";
    } else {
      detailEl.textContent = "";
    }
    detailEl.style.display = showDetail ? "" : "none";

    var banner = genBanner();
    if (!banner) return;
    banner.classList.remove("sgb-error", "sgb-done");
    if (status.state === "failed" || status.phase === "error") {
      banner.classList.add("sgb-error");
    } else if (status.state === "completed") {
      banner.classList.add("sgb-done");
    }
  }

  function checkTranslatedAudio(bookId, chapterIndex, locale) {
    if (locale === "en") return;
    var url =
      API_BASE +
      "/audiobooks/" +
      bookId +
      "/translated-audio?locale=" +
      encodeURIComponent(locale);
    fetch(url)
      .then(function (r) {
        return r.ok ? r.json() : [];
      })
      .then(function (entries) {
        translatedAudioEntries = entries;
        var langBtn = document.getElementById("sp-lang-toggle");
        if (langBtn) {
          langBtn.style.display = entries.length > 0 ? "" : "none";
          playingTranslated = false;
          langBtn.classList.remove("active");
        }
        // If subtitles exist but no translated audio, offer TTS generation
        var hasSubtitles = sourceCues.length > 0 || translatedCues.length > 0;
        if (hasSubtitles && entries.length === 0) {
          showTtsBanner(bookId, locale);
        } else {
          hideTtsBanner();
        }
      })
      .catch(function () {});
  }

  function toggleAudioLanguage() {
    if (!currentBookId || translatedAudioEntries.length === 0) return;

    var audio = document.getElementById("audio-element");
    if (!audio) return;

    var currentTime = audio.currentTime;
    var wasPlaying = !audio.paused;
    var locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";

    playingTranslated = !playingTranslated;

    if (playingTranslated) {
      // Switch to translated audio
      var entry = translatedAudioEntries[currentChapterIndex] || translatedAudioEntries[0];
      if (entry) {
        audio.src =
          API_BASE +
          "/audiobooks/" +
          currentBookId +
          "/translated-audio/" +
          (entry.chapter_index || 0) +
          "/" +
          encodeURIComponent(locale);
      }
    } else {
      // Switch back to original audio
      var needsWebm = !audio.canPlayType("audio/ogg; codecs=opus");
      audio.src =
        API_BASE + "/stream/" + currentBookId + (needsWebm ? "?format=webm" : "");
    }

    // Restore position and play state after source change
    audio.addEventListener(
      "loadedmetadata",
      function onLoaded() {
        audio.removeEventListener("loadedmetadata", onLoaded);
        // Seek to equivalent position (translated audio may have different duration)
        if (currentTime > 0 && currentTime < audio.duration) {
          audio.currentTime = currentTime;
        }
        if (wasPlaying) {
          audio.play().catch(function () {});
        }
      }
    );
    audio.load();

    // Update button state
    var langBtn = document.getElementById("sp-lang-toggle");
    if (langBtn) {
      langBtn.classList.toggle("active", playingTranslated);
      langBtn.title = playingTranslated
        ? (typeof t === "function" ? t("player.switchToOriginal") : "Switch to original audio")
        : (typeof t === "function" ? t("player.switchToTranslated") : "Switch to translated audio");
    }
  }

  // ── On-demand TTS generation banner ──
  // When a user has subtitles but no translated audio, offer to generate it.
  // Mirrors the subtitle generation banner pattern but for TTS.

  var _ttsPollTimer = null;
  var _ttsBannerBookId = null;

  function ttsBanner() { return document.getElementById("tts-gen-banner"); }

  function setTtsUi(mode) {
    var banner = ttsBanner();
    if (!banner) return;
    var idle = banner.querySelector(".tgb-idle");
    var prog = banner.querySelector(".tgb-progress");
    if (mode === "idle") {
      if (idle) idle.style.display = "";
      if (prog) prog.style.display = "none";
    } else if (mode === "progress") {
      if (idle) idle.style.display = "none";
      if (prog) prog.style.display = "";
    }
    banner.style.display = "";
  }

  function hideTtsBanner() {
    stopTtsPoll();
    var banner = ttsBanner();
    if (banner) banner.style.display = "none";
  }

  function showTtsBanner(bookId, locale) {
    _ttsBannerBookId = bookId;
    var banner = ttsBanner();
    if (!banner) return;
    setTtsUi("progress");
    renderTtsStatus({ phase: "queued", message: (typeof t === "function" ? t("ttsGen.queued") : "Preparing narration…") });
    fetch(API_BASE + "/translation/status/" + bookId + "/" + encodeURIComponent(locale))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (s && (s.state === "processing" || s.state === "pending")) {
          if (s.step === "tts") {
            renderTtsStatus(s);
          }
          startTtsPoll(bookId, locale);
        } else if (s && s.state === "completed") {
          hideTtsBanner();
          checkTranslatedAudio(bookId, currentChapterIndex, locale);
        }
      })
      .catch(function () {});
  }

  function startTtsGeneration(bookId, locale) {
    setTtsUi("progress");
    renderTtsStatus({ phase: "queued", message: (typeof t === "function" ? t("ttsGen.queued") : "Queued…") });
    fetch(API_BASE + "/user/translated-audio/request", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audiobook_id: bookId, locale: locale }),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); })
      .then(function (res) {
        if (!res.ok) {
          renderTtsStatus({
            phase: "error",
            message: (res.body && res.body.error) || (res.body && res.body.message) || (typeof t === "function" ? t("ttsGen.startFailed") : "Could not start audio generation."),
          });
          return;
        }
        startTtsPoll(bookId, locale);
      })
      .catch(function () {
        renderTtsStatus({ phase: "error", message: (typeof t === "function" ? t("ttsGen.networkError") : "Network error. Please try again.") });
      });
  }

  function startTtsPoll(bookId, locale) {
    stopTtsPoll();
    var _ttsIdleCount = 0;
    _ttsPollTimer = setInterval(function () {
      if (_ttsBannerBookId !== bookId) { stopTtsPoll(); return; }
      fetch(API_BASE + "/translation/status/" + bookId + "/" + encodeURIComponent(locale))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (s) {
          if (!s) return;
          if (!s.state || s.state === "not_queued") {
            _ttsIdleCount++;
            if (_ttsIdleCount >= 6) {
              stopTtsPoll();
              hideTtsBanner();
            }
            return;
          }
          _ttsIdleCount = 0;
          if (s.step === "tts") renderTtsStatus(s);
          if (s.state === "completed") {
            stopTtsPoll();
            hideTtsBanner();
            checkTranslatedAudio(bookId, currentChapterIndex, locale);
          } else if (s.state === "failed") {
            stopTtsPoll();
            renderTtsStatus({ phase: "error", message: s.error || "Translation failed" });
          }
        })
        .catch(function () {});
    }, 4000);
  }

  function stopTtsPoll() {
    if (_ttsPollTimer) {
      clearInterval(_ttsPollTimer);
      _ttsPollTimer = null;
    }
  }

  function renderTtsStatus(status) {
    var phaseEl = document.getElementById("tgb-phase");
    var detailEl = document.getElementById("tgb-detail");
    if (!phaseEl || !detailEl) return;

    var phaseKey = "ttsGen.phase." + (status.phase || "queued");
    var phaseLabel = typeof t === "function" ? t(phaseKey) : phaseKey;
    var phaseTranslated = phaseLabel !== phaseKey;
    if (!phaseTranslated) {
      phaseLabel = status.message || phaseKey;
    }
    phaseEl.textContent = phaseLabel;

    var showDetail = false;
    if (!phaseTranslated && status.message) {
      showDetail = true;
    }
    if (status.state === "failed" && status.error) {
      detailEl.textContent = status.error;
      showDetail = true;
    } else if (showDetail) {
      detailEl.textContent = status.message || "";
    } else {
      detailEl.textContent = "";
    }
    detailEl.style.display = showDetail ? "" : "none";

    var banner = ttsBanner();
    if (!banner) return;
    banner.classList.remove("tgb-error", "tgb-done");
    if (status.state === "failed" || status.phase === "error") {
      banner.classList.add("tgb-error");
    } else if (status.state === "completed") {
      banner.classList.add("tgb-done");
    }
  }

  // ── Inline Subtitle Display ──
  // Inline shows translated (Chinese) text only.
  // Falls back to source (English) when no translation exists.
  // The side panel transcript shows both languages.

  function findCueIndex(cues, timeMs) {
    if (cues.length === 0) return -1;
    var lo = 0, hi = cues.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >>> 1;
      if (timeMs < cues[mid].startMs) { hi = mid - 1; }
      else if (timeMs > cues[mid].endMs) { lo = mid + 1; }
      else { return mid; }
    }
    return -1;
  }

  function updateSubtitleDisplay(currentTimeMs) {
    if (!subtitlesVisible) return;
    if (sourceCues.length === 0 && translatedCues.length === 0) return;

    // Use translated cues for timing when available, fall back to source
    var cues = translatedCues.length > 0 ? translatedCues : sourceCues;
    var newIndex = findCueIndex(cues, currentTimeMs);

    if (newIndex === currentCueIndex) return;
    currentCueIndex = newIndex;

    // Hide the source line in inline mode — only show translated text
    if (subtitleSource) subtitleSource.style.display = "none";

    // Crossfade: fade out, swap text, fade in
    var newText = "";
    if (newIndex !== -1) {
      newText = translatedCues[newIndex]
        ? translatedCues[newIndex].text
        : sourceCues[newIndex]
          ? sourceCues[newIndex].text
          : "";
    }

    if (subtitleTranslated) {
      subtitleTranslated.classList.add("crossfade");
      setTimeout(function () {
        subtitleTranslated.textContent = newText;
        subtitleTranslated.classList.remove("crossfade");
      }, 300);
    }

    // Highlight active cue in transcript
    highlightTranscriptCue(newIndex);
  }

  // ── Transcript Panel ──

  // Gap threshold (ms) — insert a break divider when consecutive cues
  // are separated by more than this. 2s catches speaker changes, paragraph
  // breaks, and scene transitions in typical audiobook narration.
  var GAP_THRESHOLD_MS = 2000;

  function buildTranscriptPanel() {
    if (!transcriptContent) return;
    transcriptContent.textContent = "";

    var cues = sourceCues.length >= translatedCues.length ? sourceCues : translatedCues;

    for (var i = 0; i < cues.length; i++) {
      // Insert a break divider when there's a significant time gap
      if (i > 0) {
        var gap = cues[i].startMs - cues[i - 1].endMs;
        if (gap >= GAP_THRESHOLD_MS) {
          var divider = document.createElement("div");
          divider.className = "transcript-break";
          divider.setAttribute("aria-hidden", "true");
          divider.textContent = "\u25C6"; // ◆ diamond
          transcriptContent.appendChild(divider);
        }
      }

      var cueEl = document.createElement("div");
      cueEl.className = "transcript-cue";
      cueEl.setAttribute("data-cue-index", i);

      var timeEl = document.createElement("div");
      timeEl.className = "transcript-cue-time";
      timeEl.textContent = formatTime(cues[i].startMs);
      cueEl.appendChild(timeEl);

      if (sourceCues[i]) {
        var srcEl = document.createElement("div");
        srcEl.className = "transcript-cue-source";
        srcEl.textContent = sourceCues[i].text;
        cueEl.appendChild(srcEl);
      }

      if (translatedCues[i]) {
        var trEl = document.createElement("div");
        trEl.className = "transcript-cue-translated";
        trEl.textContent = translatedCues[i].text;
        cueEl.appendChild(trEl);
      }

      // Click to seek
      (function (startMs) {
        cueEl.addEventListener("click", function () {
          var audio = document.getElementById("audio-element");
          if (audio) {
            audio.currentTime = startMs / 1000;
          }
        });
      })(cues[i].startMs);

      transcriptContent.appendChild(cueEl);
    }
  }

  function highlightTranscriptCue(index) {
    if (!transcriptContent) return;
    var prev = transcriptContent.querySelector(".transcript-cue.active");
    if (prev) prev.classList.remove("active");

    if (index >= 0) {
      var el = transcriptContent.querySelector(
        '[data-cue-index="' + index + '"]'
      );
      if (el) {
        el.classList.add("active");
        if (transcriptVisible) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }
    }
  }

  function formatTime(ms) {
    var totalSec = Math.floor(ms / 1000);
    var min = Math.floor(totalSec / 60);
    var sec = totalSec % 60;
    return min + ":" + (sec < 10 ? "0" : "") + sec;
  }

  // ── Toggle Controls ──

  function setSubtitlesVisible(visible) {
    subtitlesVisible = visible;
    if (subtitleDisplay) {
      subtitleDisplay.style.display = subtitlesVisible ? "" : "none";
    }
    var btn = document.getElementById("sp-subtitle-toggle");
    if (btn) {
      btn.classList.toggle("active", subtitlesVisible);
    }
  }

  function toggleSubtitles() {
    setSubtitlesVisible(!subtitlesVisible);
  }

  function toggleTranscript() {
    transcriptVisible = !transcriptVisible;
    if (transcriptPanel) {
      transcriptPanel.style.display = transcriptVisible ? "" : "none";
    }
    var btn = document.getElementById("sp-transcript-toggle");
    if (btn) {
      btn.classList.toggle("active", transcriptVisible);
    }
    // When opening, immediately highlight + scroll to the current cue
    if (transcriptVisible && currentCueIndex >= 0) {
      highlightTranscriptCue(currentCueIndex);
    }
  }

  // ── Initialize ──

  document.addEventListener("DOMContentLoaded", function () {
    subtitleDisplay = document.getElementById("subtitle-display");
    subtitleSource = document.getElementById("subtitle-source");
    subtitleTranslated = document.getElementById("subtitle-translated");
    transcriptPanel = document.getElementById("transcript-panel");
    transcriptContent = document.getElementById("transcript-content");

    var ccBtn = document.getElementById("sp-subtitle-toggle");
    if (ccBtn) ccBtn.addEventListener("click", toggleSubtitles);

    var trBtn = document.getElementById("sp-transcript-toggle");
    if (trBtn) trBtn.addEventListener("click", toggleTranscript);

    var langBtn = document.getElementById("sp-lang-toggle");
    if (langBtn) langBtn.addEventListener("click", toggleAudioLanguage);

    var closeBtn = document.getElementById("transcript-close");
    if (closeBtn)
      closeBtn.addEventListener("click", function () {
        transcriptVisible = false;
        if (transcriptPanel) transcriptPanel.style.display = "none";
        var btn = document.getElementById("sp-transcript-toggle");
        if (btn) btn.classList.remove("active");
      });

    // Hook into audio timeupdate
    var audio = document.getElementById("audio-element");
    if (audio) {
      audio.addEventListener("timeupdate", function () {
        updateSubtitleDisplay(Math.floor(audio.currentTime * 1000));
      });
    }
  });

  // ── Public API ──

  window.subtitles = {
    load: loadSubtitles,
    show: function () { setSubtitlesVisible(true); },
    hide: function () { setSubtitlesVisible(false); },
    toggle: toggleSubtitles,
    isVisible: function () { return subtitlesVisible; },
    toggleTranscript: toggleTranscript,
    toggleLanguage: toggleAudioLanguage,
    waitForSubtitlesAt: waitForSubtitlesAt,
    hasSubtitlesAtPosition: hasSubtitlesAtPosition,
    isGenerationActive: isGenerationActive,
  };
})();
