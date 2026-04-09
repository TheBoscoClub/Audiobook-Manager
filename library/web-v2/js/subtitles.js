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

    var locale =
      typeof i18n !== "undefined" ? i18n.getLocale() : "en";

    // Load source (English) subtitles
    var sourceUrl =
      API_BASE +
      "/audiobooks/" +
      bookId +
      "/subtitles/" +
      currentChapterIndex +
      "/en";
    var translatedUrl =
      API_BASE +
      "/audiobooks/" +
      bookId +
      "/subtitles/" +
      currentChapterIndex +
      "/" +
      encodeURIComponent(locale);

    var sourcePromise = fetch(sourceUrl)
      .then(function (r) {
        if (!r.ok) return "";
        return r.text();
      })
      .catch(function () {
        return "";
      });

    var translatedPromise =
      locale === "en"
        ? Promise.resolve("")
        : fetch(translatedUrl)
            .then(function (r) {
              if (!r.ok) return "";
              return r.text();
            })
            .catch(function () {
              return "";
            });

    Promise.all([sourcePromise, translatedPromise]).then(function (results) {
      sourceCues = results[0] ? parseVTT(results[0]) : [];
      translatedCues = results[1] ? parseVTT(results[1]) : [];

      var hasSubtitles = sourceCues.length > 0 || translatedCues.length > 0;

      // Show/hide subtitle control buttons
      var ccBtn = document.getElementById("sp-subtitle-toggle");
      var trBtn = document.getElementById("sp-transcript-toggle");
      if (ccBtn) ccBtn.style.display = hasSubtitles ? "" : "none";
      if (trBtn) trBtn.style.display = hasSubtitles ? "" : "none";

      // Show language toggle if translated audio exists
      checkTranslatedAudio(bookId, chapterIndex, locale);

      if (hasSubtitles) {
        buildTranscriptPanel();
      }
    });
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
        var langBtn = document.getElementById("sp-lang-toggle");
        if (langBtn) {
          langBtn.style.display = entries.length > 0 ? "" : "none";
        }
      })
      .catch(function () {});
  }

  // ── Inline Subtitle Display ──

  function updateSubtitleDisplay(currentTimeMs) {
    if (!subtitlesVisible) return;
    if (sourceCues.length === 0 && translatedCues.length === 0) return;

    var cues = sourceCues.length > 0 ? sourceCues : translatedCues;
    var newIndex = -1;

    for (var i = 0; i < cues.length; i++) {
      if (currentTimeMs >= cues[i].startMs && currentTimeMs <= cues[i].endMs) {
        newIndex = i;
        break;
      }
    }

    if (newIndex === currentCueIndex) return;
    currentCueIndex = newIndex;

    if (newIndex === -1) {
      if (subtitleSource) subtitleSource.textContent = "";
      if (subtitleTranslated) subtitleTranslated.textContent = "";
    } else {
      if (subtitleSource && sourceCues[newIndex]) {
        subtitleSource.textContent = sourceCues[newIndex].text;
      }
      if (subtitleTranslated && translatedCues[newIndex]) {
        subtitleTranslated.textContent = translatedCues[newIndex].text;
      }
    }

    // Highlight active cue in transcript
    highlightTranscriptCue(newIndex);
  }

  // ── Transcript Panel ──

  function buildTranscriptPanel() {
    if (!transcriptContent) return;
    transcriptContent.textContent = "";

    var cues = sourceCues.length >= translatedCues.length ? sourceCues : translatedCues;

    for (var i = 0; i < cues.length; i++) {
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

  function toggleSubtitles() {
    subtitlesVisible = !subtitlesVisible;
    if (subtitleDisplay) {
      subtitleDisplay.style.display = subtitlesVisible ? "" : "none";
    }
    var btn = document.getElementById("sp-subtitle-toggle");
    if (btn) {
      btn.classList.toggle("active", subtitlesVisible);
    }
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
    toggle: toggleSubtitles,
    toggleTranscript: toggleTranscript,
  };
})();
