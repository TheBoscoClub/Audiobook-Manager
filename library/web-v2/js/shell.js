// Shell Player — persistent audio player for the shell+iframe architecture.
// Combines AudioPlayer (UI/controls) and PlaybackManager (position persistence).
// Communicates with iframe content pages via postMessage.

const API_BASE = "/api";

class ShellPlayer {
  constructor() {
    this.audio = document.getElementById("audio-element");
    this.playerBar = document.getElementById("shell-player");
    this.iframe = document.getElementById("content-frame");
    this.currentBook = null;
    this.playbackRates = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5];
    this.currentRateIndex = 2; // 1.0x
    this._lastMediaSessionSecond = -1;
    this._isScrubbing = false;
    this._lastSaveTime = 0;
    this._lastVolume = 1;

    // Position persistence
    this.storagePrefix = "audiobook_";
    this.apiSaveTimeout = null;
    this.apiSaveDelay = 5000; // API save every 5s
    this.positionSaveInterval = 5000; // localStorage save every 5s

    // No crossOrigin needed — streaming is same-origin

    this.setupControls();
    this.setupAudioEvents();
    this.setupMediaSession();
    this.setupMessageListener();
  }

  // ═══════════════════════════════════════════
  // CONTROLS
  // ═══════════════════════════════════════════

  setupControls() {
    document
      .getElementById("sp-play-pause")
      .addEventListener("click", () => this.togglePlayPause());
    document.getElementById("sp-rewind").addEventListener("click", () => {
      this.audio.currentTime = Math.max(0, this.audio.currentTime - 30);
      this.saveAfterSeek();
      if (typeof window.streamingTranslate !== "undefined" && window.streamingTranslate.isStreaming()) {
        window.streamingTranslate.handleSeek(this.audio.currentTime);
      }
    });
    document.getElementById("sp-forward").addEventListener("click", () => {
      this.audio.currentTime = Math.min(
        this.audio.duration || 0,
        this.audio.currentTime + 30,
      );
      this.saveAfterSeek();
      if (typeof window.streamingTranslate !== "undefined" && window.streamingTranslate.isStreaming()) {
        window.streamingTranslate.handleSeek(this.audio.currentTime);
      }
    });
    // Chapter-level navigation (Audiobook-Manager-9by). Skip-back is a
    // double-tap-aware "restart current chapter" — within RESTART_THRESHOLD_SEC
    // of chapter start it falls through to the previous chapter, matching the
    // standard audiobook UX (Apple Books, Audible, Pocket Casts). Skip-forward
    // is a single-action jump to the next chapter. Buttons are display:none in
    // the HTML and revealed in playBook() only when chapter boundaries are
    // explicit (streaming MSE OR legacy translatedEntries paths).
    document.getElementById("sp-skip-back-chapter").addEventListener("click", () => {
      this._skipBackChapter();
    });
    document.getElementById("sp-skip-forward-chapter").addEventListener("click", () => {
      this._skipForwardChapter();
    });
    const speedBtn = document.getElementById("sp-speed");
    speedBtn.addEventListener("click", (e) =>
      this.cycleSpeed(e.shiftKey ? -1 : 1),
    );
    speedBtn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      this.cycleSpeed(-1);
    });
    const volumeSlider = document.getElementById("sp-volume");
    volumeSlider.addEventListener("input", (e) => {
      this.audio.volume = e.target.value / 100;
      this._lastVolume = this.audio.volume;
      this.updateVolumeIcon();
    });
    document
      .getElementById("sp-volume-icon")
      .addEventListener("click", () => this.toggleMute());
    const progressBar = document.getElementById("sp-progress");
    progressBar.addEventListener("mousedown", () => {
      this._isScrubbing = true;
    });
    progressBar.addEventListener("touchstart", () => {
      this._isScrubbing = true;
    });
    progressBar.addEventListener("input", (e) => {
      // During drag, only update time display — don't seek yet
      if (this._isScrubbing && this.audio.duration) {
        const seekTime = (e.target.value / 1000) * this.audio.duration;
        document.getElementById("sp-current-time").textContent =
          this.formatTime(seekTime);
      }
    });
    progressBar.addEventListener("change", (e) => {
      // Drag ended — seek once to final position
      if (this.audio.duration) {
        this.audio.currentTime = (e.target.value / 1000) * this.audio.duration;
      }
      this._isScrubbing = false;
      this.saveAfterSeek();
      if (typeof window.streamingTranslate !== "undefined" && !window.streamingTranslate.isIdle()) {
        window.streamingTranslate.handleSeek(this.audio.currentTime);
      }
    });
    progressBar.addEventListener("mouseup", () => {
      this._isScrubbing = false;
    });
    progressBar.addEventListener("touchend", () => {
      this._isScrubbing = false;
    });
    document
      .getElementById("sp-close")
      .addEventListener("click", () => this.close());
  }

  // ═══════════════════════════════════════════
  // AUDIO EVENTS
  // ═══════════════════════════════════════════

  setupAudioEvents() {
    this.audio.addEventListener("error", () => {
      const error = this.audio.error;
      let diag = "Unknown error";
      let i18nKey = "player.error.unknown";
      if (error) {
        switch (error.code) {
          case 1:
            diag = "MEDIA_ERR_ABORTED";
            i18nKey = "player.error.aborted";
            break;
          case 2:
            diag = "MEDIA_ERR_NETWORK";
            i18nKey = "player.error.networkFailed";
            break;
          case 3:
            diag = "MEDIA_ERR_DECODE";
            i18nKey = "player.error.decode";
            break;
          case 4:
            diag = "MEDIA_ERR_SRC_NOT_SUPPORTED";
            i18nKey = "player.error.codecUnsupported";
            break;
        }
      }
      console.error("Audio error:", diag, error);
      this.showPlayerError(i18nKey);
    });

    this.audio.addEventListener("timeupdate", () => this.onTimeUpdate());
    this.audio.addEventListener("loadedmetadata", () =>
      this.onMetadataLoaded(),
    );

    this.audio.addEventListener("ended", async () => {
      this.setPlayPauseIcon(false);

      // Partial-chapter detection — iOS WKWebView (Safari + Chrome iOS)
      // cannot play WebM via MSE, so iOS clients are served the
      // sampler-consolidated chapter.webm via native <audio src=…>. That
      // file initially contains only the first ~6 minutes of audio
      // (sampler scope), with the rest of the chapter still streaming
      // server-side. When 'ended' fires before the chapter's true
      // duration, do NOT advance — re-fetch the same URL with a cachebust
      // and resume; the server will have grown the consolidated file as
      // more segments completed. See _maybeRetryPartialChapter for the
      // full heuristic. We do this BEFORE the translatedEntries advance
      // branch so partial chapters at indices > 0 also recover.
      if (
        this.translatedEntries &&
        this.currentBook &&
        (await this._maybeRetryPartialChapter())
      ) {
        return;
      }

      // Cached translated audio is delivered one chapter per URL. Advance to
      // the next chapter in the sorted list before declaring the book done —
      // otherwise multi-chapter translated playback halts after chapter 0.
      // The streaming MSE path is unaffected: streamingTranslate owns its
      // own end-of-stream signaling and never sets translatedEntries.
      if (
        this.translatedEntries &&
        this.translatedChapterIdx < this.translatedEntries.length - 1 &&
        this.currentBook
      ) {
        this.translatedChapterIdx += 1;
        this._loadTranslatedEntry(this.translatedChapterIdx);
        return;
      }

      // End of translatedEntries reached. The sampler often produces a
      // short tail of fully-translated leading chapters (intro / dedication /
      // prologue parts) while the bulk of the book remains untranslated and
      // must come from the streaming MSE pipeline. Hand off to streaming
      // for the next chapter rather than declaring the book done — without
      // this, books like "All the Light We Cannot See" (5 sampler chapters
      // of 193) get stuck looping the last sampler chapter after audio.ended
      // (default <audio> tap-play after end-of-media seeks to 0). v8.3.10
      // regression: prod-only because QA had full pre-translation cached.
      if (
        this.translatedEntries &&
        this.translatedEntries.length > 0 &&
        this.currentBook &&
        typeof window.streamingTranslate !== "undefined" &&
        typeof window.streamingTranslate.check === "function"
      ) {
        const lastEntry = this.translatedEntries[this.translatedEntries.length - 1];
        const nextChapter =
          (lastEntry.chapter_index ?? this.translatedEntries.length - 1) + 1;
        const locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
        const bookId = this.currentBook.bookId || this.currentBook.id;
        // Drop the cached-chapter chain so this ended handler doesn't fight
        // with the streaming pipeline's own end-of-stream signaling on the
        // shared <audio> element.
        this.translatedEntries = null;
        this.translatedChapterIdx = 0;
        window.streamingTranslate.check(bookId, locale, nextChapter);
        return;
      }

      if (this.currentBook) this.clearPosition(this.currentBook.id);
    });

    this.audio.addEventListener("play", () => {
      this.setPlayPauseIcon(true);
      this.clearPlayerError();
    });

    this.audio.addEventListener("pause", () => {
      this.setPlayPauseIcon(false);
      // Save position on user-initiated pause (not source changes).
      // When audio.src changes, pause fires with currentTime near 0 — saving
      // that would destroy the real position. Only save meaningful positions.
      if (
        this.currentBook &&
        this.audio.currentTime > 5 &&
        this.audio.duration &&
        this.audio.src
      ) {
        this.savePosition(
          this.currentBook.id,
          this.audio.currentTime,
          this.audio.duration,
        );
        this.flushToAPI(this.currentBook.id, this.audio.currentTime);
      }
    });
  }

  setPlayPauseIcon(isPlaying) {
    // Use textContent with Unicode characters (safe, no innerHTML needed)
    const btn = document.getElementById("sp-play-pause");
    btn.textContent = isPlaying ? "\u23F8" : "\u25B6";
  }

  // Surface a user-visible playback error in the player bar. Without this,
  // audio.play() rejections (NotAllowedError on iOS gesture loss,
  // NotSupportedError on missing Opus/WebM codecs, decode errors) were
  // silently console.error'd — the player bar stayed visible with no
  // audio and no explanation. See bug #65.
  showPlayerError(messageKey, params) {
    const el = document.getElementById("sp-error");
    if (!el) return;
    const msg = (typeof t === "function") ? t(messageKey, params) : messageKey;
    el.textContent = msg;
    el.hidden = false;
  }

  clearPlayerError() {
    const el = document.getElementById("sp-error");
    if (!el) return;
    el.textContent = "";
    el.hidden = true;
  }

  // Map a DOMException from audio.play() to an i18n key. NotAllowedError
  // means the browser blocked autoplay — on iOS Safari this happens when
  // the user gesture was lost across the iframe→parent postMessage boundary
  // or by an intervening await. NotSupportedError means the source cannot
  // be decoded (typically missing codec).
  _errorKeyForPlayRejection(error) {
    if (!error) return "player.error.playbackFailed";
    if (error.name === "NotAllowedError") return "player.error.gestureLost";
    if (error.name === "NotSupportedError") return "player.error.codecUnsupported";
    return "player.error.playbackFailed";
  }

  onTimeUpdate() {
    if (!this.audio.duration) return;

    // Don't fight with the user's drag — skip updates while scrubbing
    if (!this._isScrubbing) {
      const progress = (this.audio.currentTime / this.audio.duration) * 1000;
      document.getElementById("sp-progress").value = progress;
      document.getElementById("sp-current-time").textContent = this.formatTime(
        this.audio.currentTime,
      );
    }

    // Media Session position (throttled to whole seconds)
    if (Math.floor(this.audio.currentTime) !== this._lastMediaSessionSecond) {
      this._lastMediaSessionSecond = Math.floor(this.audio.currentTime);
      this.updateMediaPositionState();
    }

    // Auto-save position periodically during playback.
    // Threshold of 5s prevents overwriting real saved positions with near-zero
    // values when audio restarts from the beginning (the read side already
    // filters out positions < 5s, so this makes save consistent with load).
    if (this.currentBook && this.audio.currentTime > 5) {
      const now = Date.now();
      if (now - this._lastSaveTime >= this.positionSaveInterval) {
        this._lastSaveTime = now;
        this.savePosition(
          this.currentBook.id,
          this.audio.currentTime,
          this.audio.duration,
        );
      }
    }

    // Send state to iframe
    this.sendPlayerState();
  }

  onMetadataLoaded() {
    document.getElementById("sp-total-time").textContent = this.formatTime(
      this.audio.duration,
    );
  }

  // ═══════════════════════════════════════════
  // PLAYBACK
  // ═══════════════════════════════════════════

  async playBook(book, resume = true) {
    // Clear any lingering error from a prior book — a fresh play attempt
    // shouldn't inherit the previous failure's red banner.
    this.clearPlayerError();
    // Normalize property names — API returns id/cover_path, not bookId/coverUrl
    const bookId = book.bookId || book.id;
    const coverUrl =
      book.coverUrl || (book.cover_path ? "/covers/" + book.cover_path : null);

    // If same book is already loaded and paused, just unpause
    if (
      resume &&
      this.currentBook?.bookId === bookId &&
      this.audio.paused &&
      this.audio.currentTime > 0
    ) {
      try {
        await this.audio.play();
      } catch (error) {
        console.error("Failed to resume audio:", error);
        this.showPlayerError(this._errorKeyForPlayRejection(error));
      }
      return;
    }

    // Save current book's position before switching to a new one
    if (
      this.currentBook &&
      this.currentBook.bookId !== bookId &&
      this.audio.currentTime > 5 &&
      this.audio.duration
    ) {
      this.savePosition(
        this.currentBook.id,
        this.audio.currentTime,
        this.audio.duration,
      );
      this.flushToAPI(this.currentBook.id, this.audio.currentTime);
    }

    // v8.3.2 Bug D (abort+pivot): switching to a different book must stop
    // the previous book's streaming translation pipeline before the new
    // one's streamingTranslate.check() kicks off — otherwise two GPU
    // sessions race and the old book's pending rows stay queued.
    if (
      this.currentBook &&
      this.currentBook.bookId !== bookId &&
      typeof window.streamingTranslate !== "undefined" &&
      typeof window.streamingTranslate.drain === "function"
    ) {
      window.streamingTranslate.drain(false);
    }

    this.currentBook = { ...book, bookId, coverUrl };

    // Reset save timer so auto-save doesn't fire immediately with a stale
    // _lastSaveTime when restarting a book (prevents saving near-zero position)
    this._lastSaveTime = Date.now();

    // Update player bar UI
    document.getElementById("sp-title").textContent =
      book.title || (typeof t === "function" ? t("book.unknownTitle") : "Unknown Title");
    document.getElementById("sp-author").textContent =
      book.author || (typeof t === "function" ? t("book.unknownAuthor") : "Unknown Author");

    const cover = document.getElementById("sp-cover");
    if (coverUrl) {
      cover.src = coverUrl;
      cover.alt = book.title;
    } else {
      cover.src = "";
      cover.alt = "";
    }

    // Determine locale and check for translated audio
    const locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
    let useTranslatedAudio = false;
    // Reset chained-chapter state on every playBook — old book's chapter
    // list must not leak into the new book's ended handler.
    this.translatedEntries = null;
    this.translatedChapterIdx = 0;
    // Reset per-book chapter list — populated below via
    // /api/audiobooks/<id>/chapters. Without this reset the previous book's
    // chapters would leak into _skipBackChapter / _skipForwardChapter on the
    // English single-stream path.
    this.chapters = [];

    if (locale !== "en") {
      try {
        const taResp = await fetch(`${API_BASE}/audiobooks/${bookId}/translated-audio?locale=${encodeURIComponent(locale)}`, { credentials: "include" });
        if (taResp.ok) {
          const entries = await taResp.json();
          if (entries.length > 0) {
            useTranslatedAudio = true;
            // Sort by chapter_index so the ended handler walks chapters
            // in playback order regardless of API response ordering.
            this.translatedEntries = [...entries].sort(
              (a, b) => (a.chapter_index ?? 0) - (b.chapter_index ?? 0),
            );
            this.translatedChapterIdx = 0;
            const entry = this.translatedEntries[0];
            this.audio.src = `${API_BASE}/audiobooks/${bookId}/translated-audio/${entry.chapter_index || 0}/${encodeURIComponent(locale)}`;
          }
        }
      } catch { /* fall through to original audio */ }

      if (!useTranslatedAudio) {
        fetch(`${API_BASE}/translation/bump`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ audiobook_id: bookId, locale }),
        }).catch(() => {});
      }
    }

    // When non-English locale has no fully-translated audio yet, on-demand
    // streaming will provide audio via MSE — do NOT load the English stream
    // (would play audibly for ~500ms before streaming check pauses it).
    const streamingNeeded = locale !== "en" && !useTranslatedAudio;

    if (!useTranslatedAudio && !streamingNeeded) {
      const needsWebm = !this.audio.canPlayType("audio/ogg; codecs=opus");
      this.audio.src = `${API_BASE}/stream/${bookId}${needsWebm ? "?format=webm" : ""}`;
    }

    // _streamingNeeded is captured synchronously so _applyChapterButtonVisibility
    // (called after audio.play() resolves below) can evaluate the playBook-time
    // streaming decision even before streamingTranslate flips to active.
    // Synchronous assignment only — DO NOT call _applyChapterButtonVisibility
    // or issue the chapters API call here. (Audiobook-Manager-8mm)
    //
    // Both the chapters fetch and the visibility recompute are deferred to a
    // queueMicrotask block AFTER `await this.audio.play()` (or, on the
    // streaming-needed branch, right after streamingTranslate.check()). Why:
    // the user-gesture activation that authorises audio.play() is consumed by
    // the FIRST async operation that runs on the gesture stack. v8.3.10.2
    // shipped the chapters fetch BEFORE audio.play() — its .then() microtask
    // chain stole the gesture, and audio.play() rejected with NotAllowedError
    // on prod Chromium. Symptom: user clicks Play, nothing happens. See the
    // gesture-activation comment immediately above the audio.play() try block.
    this._streamingNeeded = streamingNeeded;

    // Load saved speed
    const savedSpeed = this.getSpeed();
    const speedIdx = this.playbackRates.indexOf(savedSpeed);
    if (speedIdx !== -1) this.currentRateIndex = speedIdx;
    this.audio.playbackRate = this.playbackRates[this.currentRateIndex];
    document.getElementById("sp-speed-display").textContent =
      this.playbackRates[this.currentRateIndex] + "x";

    // Show player bar
    this.playerBar.hidden = false;
    document.body.classList.add("player-active");

    // Notify iframe to add bottom padding (prevent content hiding behind player)
    this.sendToIframe({ type: "playerVisible", visible: true });

    // Load subtitles and auto-enable for non-English locales
    if (typeof window.subtitles !== "undefined" && window.subtitles.load) {
      // Use the first translated entry's chapter index when chained playback
      // is active so subtitles align with audio from the very first chapter.
      const initialChapter = this.translatedEntries
        ? (this.translatedEntries[0].chapter_index ?? 0)
        : 0;
      window.subtitles.load(bookId, initialChapter);
      if (locale !== "en") {
        window.subtitles.show();
      }
    }

    // Media Session metadata
    this.updateMediaMetadata();

    // Restore saved position BEFORE play when resuming.
    // Read localStorage synchronously (fast) so we can seek before playback
    // starts. Then check the API async and adjust if it has a further position.
    let localPos = null;
    if (resume) {
      localPos = this.getPosition(bookId);
    }

    // If we have a local position, set it before play so audio starts there
    if (localPos && localPos.position > 5) {
      this.audio.addEventListener(
        "loadedmetadata",
        () => {
          this.audio.currentTime = localPos.position;
        },
        { once: true },
      );
    }

    // Kick off the streaming check FIRST so the overlay appears and any
    // English playback is suppressed before audio.play() can fire. This
    // synchronously sets BUFFERING state + shows the overlay; the fetch
    // for /translate/stream resolves later and either keeps us buffering
    // or reverts to IDLE if the book is fully cached.
    if (streamingNeeded && typeof window.streamingTranslate !== "undefined") {
      window.streamingTranslate.check(bookId, locale);
    }

    // Start playback — must happen within user gesture window.
    // Cross-frame calls (iframe → parent) lose gesture activation if async
    // operations (like API fetch) run first. v8.3.10.2 broke this by issuing
    // /api/audiobooks/<id>/chapters on the gesture stack BEFORE audio.play();
    // the chapters fetch's microtask chain stole the gesture and play()
    // rejected with NotAllowedError. Chapters fetch + visibility recompute
    // are now deferred via queueMicrotask AFTER play() (or, on the streaming
    // branch, after streamingTranslate.check()). (Audiobook-Manager-8mm)
    // When streaming is needed we skip play() here; MseAudioChain in
    // streaming-translate.js calls audio.play() once enough segments are
    // buffered. Skipping the English play() avoids audible bleed-through.
    if (!streamingNeeded) {
      try {
        await this.audio.play();
      } catch (error) {
        console.error("Failed to play audio:", error);
        this.showPlayerError(this._errorKeyForPlayRejection(error));
      }
    }

    // Deferred chapter-button wiring (Audiobook-Manager-8mm).
    // Runs as a microtask AFTER audio.play() has already consumed (or failed
    // to consume) the user-gesture activation. Order:
    //   1. Initial visibility pass — reflects streaming/translatedEntries
    //      state. For the EN single-stream path this.chapters is still []
    //      so the buttons stay hidden until the fetch below resolves.
    //   2. Fetch /api/audiobooks/<id>/chapters and recompute visibility
    //      once the chapters array is populated. The 6ub feature
    //      (chapter-skip buttons for the EN single-stream path) is
    //      preserved — it just lights up one event-loop turn later than
    //      it did in v8.3.10.2.
    // For the streaming-needed branch chapter buttons may flash hidden →
    // visible briefly during streaming setup; that's acceptable — the
    // streaming MSE pipeline has its own chapter accounting and does not
    // depend on this fetch.
    queueMicrotask(() => {
      // Discard if user already switched to a different book mid-flight.
      if (!this.currentBook || this.currentBook.bookId !== bookId) return;
      this._applyChapterButtonVisibility();
      fetch(`${API_BASE}/audiobooks/${bookId}/chapters`, { credentials: "include" })
        .then((resp) => (resp.ok ? resp.json() : { chapters: [] }))
        .then((data) => {
          if (!this.currentBook || this.currentBook.bookId !== bookId) return;
          this.chapters = Array.isArray(data.chapters) ? data.chapters : [];
          this._applyChapterButtonVisibility();
        })
        .catch(() => {
          if (!this.currentBook || this.currentBook.bookId !== bookId) return;
          this.chapters = [];
          this._applyChapterButtonVisibility();
        });
    });

    // Check API for a further-ahead position (async, adjusts after play starts)
    if (resume) {
      const apiPos = await this.getPositionFromAPI(bookId);
      if (apiPos && apiPos.position > 5) {
        // Use API position if it's ahead of local, or if no local position
        const currentTarget = localPos ? localPos.position : 0;
        if (apiPos.position > currentTarget) {
          this.audio.currentTime = apiPos.position;
        }
      } else if (!localPos && !apiPos) {
        // No position found anywhere — stays at 0
      }
    }

    // Subtitles appear as they become available — no playback blocking
  }

  togglePlayPause() {
    if (this.audio.paused) {
      this.audio.play();
    } else {
      this.audio.pause();
    }
  }

  cycleSpeed(direction = 1) {
    const len = this.playbackRates.length;
    this.currentRateIndex = (this.currentRateIndex + direction + len) % len;
    const rate = this.playbackRates[this.currentRateIndex];
    this.audio.playbackRate = rate;
    document.getElementById("sp-speed-display").textContent = rate + "x";
    this.saveSpeed(rate);
  }

  toggleMute() {
    if (this.audio.volume > 0) {
      this._lastVolume = this.audio.volume;
      this.audio.volume = 0;
      document.getElementById("sp-volume").value = 0;
    } else {
      this.audio.volume = this._lastVolume || 1;
      document.getElementById("sp-volume").value = this.audio.volume * 100;
    }
    this.updateVolumeIcon();
  }

  updateVolumeIcon() {
    const vol = this.audio.volume;
    const path = document.getElementById("sp-volume-path");
    if (vol === 0) {
      path.setAttribute(
        "d",
        "M16.5 12A4.5 4.5 0 0012 8.5v1.5a3 3 0 010 4V15.5a4.5 4.5 0 004.5-4.5zM19 12a7 7 0 00-7-7v2a5 5 0 010 10v2a7 7 0 007-7zM3 9v6h4l5 5V4L7 9H3zm14.59 3l2.12-2.12-1.41-1.42L16.17 10.6l-2.12-2.12-1.42 1.41L14.76 12l-2.13 2.12 1.42 1.41 2.12-2.12 2.12 2.12 1.41-1.42L17.59 12z",
      );
    } else if (vol < 0.5) {
      path.setAttribute(
        "d",
        "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0012 8.5v7A4.5 4.5 0 0016.5 12z",
      );
    } else {
      path.setAttribute(
        "d",
        "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0012 8.5v1.5a3 3 0 010 4V15.5a4.5 4.5 0 004.5-4.5zM12 3.23v2.06a7 7 0 010 13.42v2.06A9 9 0 0021 12 9 9 0 0012 3.23z",
      );
    }
  }

  close() {
    // v8.3.2 Bug D: drain any in-flight streaming translation session so
    // the backend stops queueing new GPU work for this book. Covers the
    // "click X on player", "MediaSession Stop" (wired to close()) and
    // programmatic close paths — the pagehide listener in
    // streaming-translate.js handles tab/browser close.
    if (typeof window.streamingTranslate !== "undefined" &&
        typeof window.streamingTranslate.drain === "function") {
      window.streamingTranslate.drain(false);
    }

    // Save position before closing (> 5s threshold prevents saving near-zero)
    if (this.currentBook && this.audio.currentTime > 5 && this.audio.duration) {
      this.savePosition(
        this.currentBook.id,
        this.audio.currentTime,
        this.audio.duration,
      );
      this.flushToAPI(this.currentBook.id, this.audio.currentTime);
    }

    this.audio.pause();
    this.audio.src = "";
    this.playerBar.hidden = true;
    document.body.classList.remove("player-active");

    // Notify iframe to remove bottom padding
    this.sendToIframe({ type: "playerVisible", visible: false });

    this.currentBook = null;

    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = null;
    }

    // Notify iframe
    this.sendToIframe({ type: "playerClosed" });
  }

  // ═══════════════════════════════════════════
  // POSITION PERSISTENCE (from PlaybackManager)
  // ═══════════════════════════════════════════

  savePosition(fileId, position, duration) {
    // Defense-in-depth: never save near-zero positions (callers should also guard)
    if (position < 5) return;
    const data = { position, duration, timestamp: Date.now() };
    localStorage.setItem(
      `${this.storagePrefix}position_${fileId}`,
      JSON.stringify(data),
    );
    this.queueAPISave(fileId, position);
  }

  saveAfterSeek() {
    if (this.currentBook && this.audio.currentTime > 5 && this.audio.duration) {
      this.savePosition(
        this.currentBook.id,
        this.audio.currentTime,
        this.audio.duration,
      );
    }
  }

  // Recompute chapter-skip button visibility. Buttons show whenever ANY of
  // these is true: the new chapters array (English single-stream + ffprobe
  // boundaries via /api/audiobooks/<id>/chapters), an active streaming MSE
  // session, or cached translatedEntries. Called from playBook() at load
  // time AND again after the chapters fetch resolves — the fetch is async
  // so first-paint may run before chapters are known. Audiobook-Manager-6ub.
  _applyChapterButtonVisibility() {
    const streamingActive =
      this._streamingNeeded ||
      (typeof window.streamingTranslate !== "undefined" &&
        typeof window.streamingTranslate.isStreaming === "function" &&
        window.streamingTranslate.isStreaming());
    const chapterNavAvailable =
      (this.chapters && this.chapters.length > 0) ||
      streamingActive ||
      (this.translatedEntries && this.translatedEntries.length > 0);
    const skipBackBtn = document.getElementById("sp-skip-back-chapter");
    const skipFwdBtn = document.getElementById("sp-skip-forward-chapter");
    if (skipBackBtn) skipBackBtn.style.display = chapterNavAvailable ? "" : "none";
    if (skipFwdBtn) skipFwdBtn.style.display = chapterNavAvailable ? "" : "none";
  }

  // Chapter-level skip-back. Tap when mid-chapter restarts the current chapter.
  // Tap within RESTART_THRESHOLD_SEC of chapter start jumps to the previous
  // chapter — standard audiobook double-tap pattern. Three pathways, in
  // priority order:
  //   1. this.chapters (Audiobook-Manager-6ub) — covers English single-stream
  //      via /api/audiobooks/<id>/chapters ffprobe boundaries
  //   2. streaming MSE — chapter accounting lives in streamingTranslate
  //   3. legacy translatedEntries — one URL per cached translated chapter
  _skipBackChapter() {
    const RESTART_THRESHOLD_SEC = 3;
    const t = this.audio.currentTime || 0;

    // 1. Generic chapters array (covers EN single-stream + any path with
    //    explicit boundary metadata). audio.currentTime is in seconds;
    //    chapter boundaries are in milliseconds.
    if (this.chapters && this.chapters.length > 0) {
      const nowMs = t * 1000;
      const RESTART_THRESHOLD_MS = RESTART_THRESHOLD_SEC * 1000;
      const currentIdx = this.chapters.findIndex(
        (ch) => ch.start_ms <= nowMs && nowMs < ch.end_ms,
      );
      // Edge case: clicked past the last chapter's end_ms (rare — typically
      // means audio is at duration and 'ended' is about to fire). Treat as
      // "restart last chapter".
      if (currentIdx === -1) {
        const last = this.chapters[this.chapters.length - 1];
        this.audio.currentTime = last.start_ms / 1000;
        this.saveAfterSeek();
        return;
      }
      const current = this.chapters[currentIdx];
      const intoChapterMs = nowMs - current.start_ms;
      if (intoChapterMs > RESTART_THRESHOLD_MS) {
        // Mid-chapter: restart current chapter.
        this.audio.currentTime = current.start_ms / 1000;
      } else if (currentIdx > 0) {
        // Near start: jump to previous chapter's start.
        this.audio.currentTime = this.chapters[currentIdx - 1].start_ms / 1000;
      } else {
        // First chapter, near start: just go to 0.
        this.audio.currentTime = 0;
      }
      this.saveAfterSeek();
      return;
    }

    // 2. Streaming MSE path
    if (
      typeof window.streamingTranslate !== "undefined" &&
      !window.streamingTranslate.isIdle()
    ) {
      if (t > RESTART_THRESHOLD_SEC) {
        this.audio.currentTime = 0;
        window.streamingTranslate.handleSeek(0);
        return;
      }
      const cur = window.streamingTranslate.getCurrentChapter();
      if (cur > 0) {
        window.streamingTranslate.jumpToChapter(cur - 1);
      } else {
        // Already at chapter 0 — just restart it.
        this.audio.currentTime = 0;
      }
      return;
    }

    // 3. Legacy translatedEntries path
    if (this.translatedEntries && this.translatedEntries.length > 0 && this.currentBook) {
      if (t > RESTART_THRESHOLD_SEC) {
        this.audio.currentTime = 0;
        this.saveAfterSeek();
        return;
      }
      if (this.translatedChapterIdx > 0) {
        this.translatedChapterIdx -= 1;
        this._loadTranslatedEntry(this.translatedChapterIdx);
      } else {
        // Already at first translated chapter — just restart it.
        this.audio.currentTime = 0;
      }
      return;
    }

    // Defensive: no chapter info at all — restart the audio source.
    this.audio.currentTime = 0;
    this.saveAfterSeek();
  }

  // Chapter-level skip-forward. Single tap jumps to the next chapter. Routes
  // through whichever pathway is active, in the same priority order as
  // _skipBackChapter: chapters array → streaming MSE → translatedEntries.
  _skipForwardChapter() {
    // 1. Generic chapters array (covers EN single-stream + any path with
    //    explicit boundary metadata). Find first chapter whose start > now.
    if (this.chapters && this.chapters.length > 0) {
      const nowMs = (this.audio.currentTime || 0) * 1000;
      const nextChapter = this.chapters.find((ch) => ch.start_ms > nowMs);
      if (nextChapter) {
        this.audio.currentTime = nextChapter.start_ms / 1000;
      } else {
        // Already in last chapter — seek to its end so 'ended' fires.
        const last = this.chapters[this.chapters.length - 1];
        this.audio.currentTime = last.end_ms / 1000;
      }
      this.saveAfterSeek();
      return;
    }

    // 2. Streaming MSE path
    if (
      typeof window.streamingTranslate !== "undefined" &&
      !window.streamingTranslate.isIdle()
    ) {
      const cur = window.streamingTranslate.getCurrentChapter();
      const total = window.streamingTranslate.getTotalChapters();
      if (total > 0 && cur + 1 < total) {
        window.streamingTranslate.jumpToChapter(cur + 1);
      }
      return;
    }

    // Legacy translatedEntries path — advance within the cached chapter list
    if (
      this.translatedEntries &&
      this.translatedChapterIdx < this.translatedEntries.length - 1 &&
      this.currentBook
    ) {
      this.translatedChapterIdx += 1;
      this._loadTranslatedEntry(this.translatedChapterIdx);
      return;
    }

    // End of translatedEntries — hand off to streaming pipeline for next
    // chapter (same flow as the v8.3.10.1 ended-handler fix).
    if (
      this.translatedEntries &&
      this.translatedEntries.length > 0 &&
      this.currentBook &&
      typeof window.streamingTranslate !== "undefined" &&
      typeof window.streamingTranslate.check === "function"
    ) {
      const lastEntry = this.translatedEntries[this.translatedEntries.length - 1];
      const nextChapter =
        (lastEntry.chapter_index ?? this.translatedEntries.length - 1) + 1;
      const locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
      const bookId = this.currentBook.bookId || this.currentBook.id;
      this.translatedEntries = null;
      this.translatedChapterIdx = 0;
      window.streamingTranslate.check(bookId, locale, nextChapter);
      return;
    }
    // English single-stream — no chapter info, button is hidden in playBook.
  }

  // Load the translatedEntries[idx] entry into the audio element. Shared
  // between the ended-handler chapter advance and the user-initiated skip
  // forward / back. Caller is responsible for updating translatedChapterIdx
  // before invoking.
  _loadTranslatedEntry(idx) {
    const entry = this.translatedEntries[idx];
    const chapterIdx = entry.chapter_index ?? idx;
    const locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
    const bookId = this.currentBook.bookId || this.currentBook.id;
    this.audio.src = `${API_BASE}/audiobooks/${bookId}/translated-audio/${chapterIdx}/${encodeURIComponent(locale)}`;
    if (typeof window.subtitles !== "undefined" && window.subtitles.load) {
      window.subtitles.load(bookId, chapterIdx);
    }
    this._lastSaveTime = Date.now();
    this.audio.play().catch((error) => {
      console.error("Failed to play translated chapter:", error);
      this.showPlayerError(this._errorKeyForPlayRejection(error));
    });
  }

  // Detect a partial chapter (audio.duration shorter than the source
  // chapter's true duration) and re-fetch the translated-audio URL with a
  // cachebust query string so the browser refreshes its HTTP cache. This is
  // the iOS-WebKit safety net: iPhone clients are served a sampler-only
  // chapter.webm initially (~6 minutes), and as more segments stream in
  // server-side, the consolidated file grows. Without this retry, audio
  // 'ended' would fire at the partial duration and advance to the next
  // chapter, skipping the remainder of the current chapter.
  //
  // Returns true if a partial was detected and a re-fetch was scheduled
  // (caller should NOT advance the chapter). Returns false in all other
  // cases — chapter played to its full duration, no chapter metadata
  // available to compare against, or partial-retry budget exhausted.
  //
  // Tunables:
  //   FULL_TOLERANCE = 0.95 — accept up to 5% short for trailing-silence
  //   trim and rounding (real chapters never end exactly on a 30-second
  //   sampler boundary, so the threshold also forgives small mismatches)
  //   STALL_RETRY_DELAY_MS = 30_000 — if the re-fetch returns the same
  //   short audio, wait 30s and try once more before giving up
  //   MAX_RETRIES = 2 — bound the loop so a stuck pipeline doesn't burn
  //   CPU forever on a stale URL
  async _maybeRetryPartialChapter() {
    if (!this.translatedEntries || !this.currentBook) return false;
    const entry = this.translatedEntries[this.translatedChapterIdx];
    if (!entry) return false;
    const chapterIdx = entry.chapter_index ?? this.translatedChapterIdx;

    // Resolve the chapter's true duration from this.chapters (populated by
    // playBook from /api/audiobooks/<id>/chapters). If it's missing, we
    // cannot tell partial from full — fall through to normal advance.
    let trueDurationSec = null;
    if (Array.isArray(this.chapters) && this.chapters.length > 0) {
      const ch = this.chapters[chapterIdx];
      if (ch && typeof ch.start_ms === "number" && typeof ch.end_ms === "number") {
        trueDurationSec = (ch.end_ms - ch.start_ms) / 1000;
      }
    }
    if (!trueDurationSec || !(trueDurationSec > 0)) return false;

    const playedDurationSec = this.audio.duration || 0;
    if (!(playedDurationSec > 0)) return false;

    const FULL_TOLERANCE = 0.95;
    if (playedDurationSec >= trueDurationSec * FULL_TOLERANCE) {
      // Played the full chapter — clear any retry state and let the
      // normal chapter-advance branch run.
      this._partialRetry = null;
      return false;
    }

    // We have a partial. Track per-chapter retry state.
    const retryKey = `${this.currentBook.bookId || this.currentBook.id}:${chapterIdx}`;
    const STALL_RETRY_DELAY_MS = 30_000;
    const MAX_RETRIES = 2;
    if (!this._partialRetry || this._partialRetry.key !== retryKey) {
      this._partialRetry = { key: retryKey, attempts: 0, lastDuration: 0 };
    }
    const retry = this._partialRetry;

    if (retry.attempts >= MAX_RETRIES && playedDurationSec <= retry.lastDuration) {
      // Two retries with no growth — surface a one-time toast and stop.
      const message =
        typeof t === "function"
          ? t("shell.translationCatchingUp", {
              defaultValue:
                "Translation still catching up — refresh in a moment to keep listening.",
            })
          : "Translation still catching up — refresh in a moment to keep listening.";
      this._showShellToast(message, "info");
      this._partialRetry = null;
      return false;
    }

    retry.attempts += 1;
    retry.lastDuration = playedDurationSec;

    const locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
    const bookId = this.currentBook.bookId || this.currentBook.id;
    const cacheBust = Date.now();
    const newSrc = `${API_BASE}/audiobooks/${bookId}/translated-audio/${chapterIdx}/${encodeURIComponent(locale)}?_=${cacheBust}`;

    // Resume at the position we just played to (end of partial). Use
    // loadedmetadata so we seek before play resumes. The audio element
    // resets currentTime to 0 on src change — we restore it once metadata
    // for the new URL has loaded.
    const resumeSec = Math.max(0, playedDurationSec - 0.5);
    const onMeta = () => {
      try {
        this.audio.currentTime = resumeSec;
      } catch (_e) {
        /* seek may fail if new audio is also short — let play try anyway */
      }
    };
    this.audio.addEventListener("loadedmetadata", onMeta, { once: true });

    const triggerLoad = () => {
      this.audio.src = newSrc;
      this._lastSaveTime = Date.now();
      this.audio.play().catch((error) => {
        console.error("Failed to resume partial chapter:", error);
        this.showPlayerError(this._errorKeyForPlayRejection(error));
      });
    };

    if (retry.attempts === 1) {
      // First retry — go now.
      triggerLoad();
    } else {
      // Subsequent retry — wait STALL_RETRY_DELAY_MS so the server has
      // time to grow the consolidated file with newly-streamed segments.
      setTimeout(triggerLoad, STALL_RETRY_DELAY_MS);
    }
    return true;
  }

  queueAPISave(fileId, positionSeconds) {
    if (this.apiSaveTimeout) clearTimeout(this.apiSaveTimeout);
    this.apiSaveTimeout = setTimeout(() => {
      this.savePositionToAPI(fileId, Math.floor(positionSeconds * 1000));
    }, this.apiSaveDelay);
  }

  async savePositionToAPI(fileId, positionMs) {
    try {
      const response = await fetch(`${API_BASE}/position/${fileId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_ms: positionMs }),
        credentials: "include",
      });
      if (!response.ok) {
        console.warn(`Failed to save position: ${response.status}`);
        // 401 = session expired (typically because the user's grace
        // period ran out — audio streams bypass /api/* so they don't
        // refresh last_seen). Surface a one-time toast so the user
        // knows their progress is no longer being saved. Audio playback
        // continues unimpeded — only persistence is affected.
        if (response.status === 401) {
          this._showSessionExpiredToast();
        }
      }
    } catch (error) {
      console.warn("Error saving position to API:", error);
    }
  }

  // Surface a session-expired toast at most once every 5 minutes so we
  // don't spam the user during a long stale-session run.
  _showSessionExpiredToast() {
    const SUPPRESS_MS = 5 * 60 * 1000;
    const now = Date.now();
    if (this._lastSessionExpiredToast && now - this._lastSessionExpiredToast < SUPPRESS_MS) {
      return;
    }
    this._lastSessionExpiredToast = now;
    const message =
      typeof t === "function"
        ? t("shell.sessionExpired", {
            defaultValue:
              "Your session expired — sign in again to keep your progress saved.",
          })
        : "Your session expired — sign in again to keep your progress saved.";
    this._showShellToast(message, "error");
  }

  // Shell-level toast. Uses the #toast-container in shell.html and the
  // .toast/.toast.error styles from notifications.css. Mirrors the
  // pattern in library.js::showToast() so the visual is identical.
  _showShellToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) {
      console.warn("[shell] toast container missing:", message);
      return;
    }
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 6000);
  }

  async getPositionFromAPI(fileId) {
    try {
      const response = await fetch(`${API_BASE}/position/${fileId}`, {
        credentials: "include",
      });
      if (response.ok) {
        const data = await response.json();
        if (data.local_position_ms > 0) {
          return {
            position: data.local_position_ms / 1000,
            duration: data.duration_ms ? data.duration_ms / 1000 : 0,
            timestamp: data.local_position_updated
              ? new Date(data.local_position_updated).getTime()
              : 0,
            source: "api",
          };
        }
      }
    } catch (error) {
      console.warn("Error fetching position from API:", error);
    }
    return null;
  }

  async getBestPosition(fileId) {
    const localPos = this.getPosition(fileId);
    const apiPos = await this.getPositionFromAPI(fileId);

    if (!localPos && !apiPos) return null;
    if (!localPos) return apiPos;
    if (!apiPos) return localPos;

    // Furthest ahead wins
    if (apiPos.position > localPos.position) {
      console.debug(
        `Using API position (${apiPos.position}s) over local (${localPos.position}s)`,
      );
      return apiPos;
    }
    console.debug(
      `Using local position (${localPos.position}s) over API (${apiPos.position}s)`,
    );
    return localPos;
  }

  async flushToAPI(fileId, positionSeconds) {
    if (this.apiSaveTimeout) {
      clearTimeout(this.apiSaveTimeout);
      this.apiSaveTimeout = null;
    }
    // Defense-in-depth: never flush near-zero positions to API
    if (positionSeconds < 5) return;
    await this.savePositionToAPI(fileId, Math.floor(positionSeconds * 1000));
  }

  getPosition(fileId) {
    const data = localStorage.getItem(
      `${this.storagePrefix}position_${fileId}`,
    );
    if (!data) return null;
    try {
      const parsed = JSON.parse(data);
      const pct = (parsed.position / parsed.duration) * 100;
      if (pct > 95 || parsed.position < 5) return null;
      return parsed;
    } catch {
      return null;
    }
  }

  clearPosition(fileId) {
    localStorage.removeItem(`${this.storagePrefix}position_${fileId}`);
    this.savePositionToAPI(fileId, 0);
  }

  saveSpeed(speed) {
    localStorage.setItem(`${this.storagePrefix}speed`, speed.toString());
  }

  getSpeed() {
    const speed = localStorage.getItem(`${this.storagePrefix}speed`);
    return speed ? parseFloat(speed) : 1.0;
  }

  // ═══════════════════════════════════════════
  // MEDIA SESSION API
  // ═══════════════════════════════════════════

  setupMediaSession() {
    if (!("mediaSession" in navigator)) return;

    navigator.mediaSession.setActionHandler("play", () => this.audio.play());
    navigator.mediaSession.setActionHandler("pause", () => this.audio.pause());
    navigator.mediaSession.setActionHandler("seekbackward", (d) => {
      this.audio.currentTime = Math.max(
        0,
        this.audio.currentTime - (d.seekOffset || 30),
      );
      this.updateMediaPositionState();
      this.saveAfterSeek();
    });
    navigator.mediaSession.setActionHandler("seekforward", (d) => {
      this.audio.currentTime = Math.min(
        this.audio.duration || 0,
        this.audio.currentTime + (d.seekOffset || 30),
      );
      this.updateMediaPositionState();
      this.saveAfterSeek();
    });
    navigator.mediaSession.setActionHandler("seekto", (d) => {
      if (d.seekTime !== undefined && this.audio.duration) {
        this.audio.currentTime = Math.min(d.seekTime, this.audio.duration);
        this.updateMediaPositionState();
        this.saveAfterSeek();
      }
    });
    navigator.mediaSession.setActionHandler("stop", () => this.close());
  }

  updateMediaMetadata() {
    if (!("mediaSession" in navigator) || !this.currentBook) return;
    const book = this.currentBook;
    const artwork = [];
    if (book.coverUrl) {
      const sizes = [
        "96x96",
        "128x128",
        "192x192",
        "256x256",
        "384x384",
        "512x512",
      ];
      sizes.forEach((s) =>
        artwork.push({ src: book.coverUrl, sizes: s, type: "image/jpeg" }),
      );
    }
    const _tfn = typeof t === "function" ? t : null;
    let narratedBy = book.series || "";
    if (book.narrator) {
      if (_tfn) {
        const _nb = _tfn("shell.narratedBy", { narrator: book.narrator });
        narratedBy = _nb && _nb !== "shell.narratedBy" ? _nb : `Narrated by ${book.narrator}`;
      } else {
        narratedBy = `Narrated by ${book.narrator}`;
      }
    }
    navigator.mediaSession.metadata = new MediaMetadata({
      title: book.title || (_tfn ? _tfn("book.unknownTitle") : "Unknown Title"),
      artist: book.author || (_tfn ? _tfn("book.unknownAuthor") : "Unknown Author"),
      album: narratedBy,
      artwork,
    });
  }

  updateMediaPositionState() {
    if (!("mediaSession" in navigator) || !this.audio.duration) return;
    try {
      navigator.mediaSession.setPositionState({
        duration: this.audio.duration,
        playbackRate: this.audio.playbackRate,
        position: this.audio.currentTime,
      });
    } catch (e) {
      console.debug("Could not update position state:", e.message);
    }
  }

  // ═══════════════════════════════════════════
  // postMessage BRIDGE
  // ═══════════════════════════════════════════

  setupMessageListener() {
    window.addEventListener("message", (event) => {
      // Validate origin — only accept same-origin messages
      if (event.origin !== window.location.origin) return;

      const msg = event.data;
      if (!msg || !msg.type) return;

      switch (msg.type) {
        case "play":
          this.playBook(msg.book || msg, msg.resume !== false);
          break;
        case "pause":
          this.audio.pause();
          break;
        case "resume":
          this.audio.play();
          break;
        case "seek":
          if (msg.position !== undefined) {
            this.audio.currentTime = msg.position;
            this.saveAfterSeek();
          }
          break;
        case "getPlayerState":
          this.sendPlayerState();
          break;
      }
    });
  }

  sendPlayerState() {
    this.sendToIframe({
      type: "playerState",
      playing: !this.audio.paused,
      bookId: this.currentBook?.bookId || null,
      title: this.currentBook?.title || null,
      author: this.currentBook?.author || null,
      position: this.audio.currentTime,
      duration: this.audio.duration || 0,
    });
  }

  sendToIframe(msg) {
    if (this.iframe && this.iframe.contentWindow) {
      this.iframe.contentWindow.postMessage(msg, window.location.origin);
    }
  }

  // ═══════════════════════════════════════════
  // UTILITIES
  // ═══════════════════════════════════════════

  formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return "0:00";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
      return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
    }
    return `${m}:${s.toString().padStart(2, "0")}`;
  }
}

// Mobile viewport fix: measure the visible viewport height via the
// visualViewport API and publish it as --app-height on <html>. Also
// compute the bottom-chrome offset (layout - visual) and post it to
// the iframe so its content can add bottom padding for keyboards.
//
// CAVEAT (iOS Chrome): visualViewport.height there matches the layout
// viewport — i.e. it INCLUDES the area behind the persistent bottom
// nav bar — so --app-height can be too tall and `bottomChrome` evaluates
// to 0 even when the nav bar visibly occludes content. The shell guards
// against this by capping `<html>` at min(100svh, var(--app-height,
// 100svh)) in shell.css; iframe consumers that depend on `bottomChrome`
// should not rely on it being non-zero on iOS Chrome.
function setupViewportFix() {
  const iframe = document.getElementById("content-frame");
  function update() {
    const vv = window.visualViewport;
    const visibleH = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty("--app-height", visibleH + "px");

    // Calculate bottom chrome offset: difference between layout viewport
    // (window.innerHeight) and visual viewport. This is the space eaten
    // by browser bottom bars (Chrome nav, Safari toolbar, etc.) — except
    // on iOS Chrome where vv.height == innerHeight and this is 0; see
    // CAVEAT in the function header.
    const bottomChrome = vv
      ? Math.max(0, window.innerHeight - vv.height - (vv.offsetTop || 0))
      : 0;
    // Tell the iframe how much bottom padding it needs
    if (iframe && iframe.contentWindow) {
      iframe.contentWindow.postMessage(
        { type: "viewportBottom", offset: bottomChrome },
        window.location.origin,
      );
    }
  }
  update();
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", update);
    window.visualViewport.addEventListener("scroll", update);
  }
  window.addEventListener("resize", update);
  window.addEventListener("orientationchange", () => setTimeout(update, 150));
  // Re-post when iframe loads (it might load after first update)
  if (iframe) iframe.addEventListener("load", update);
}

// Diagnostic overlay for iOS Chrome viewport investigation. Activates only
// when the URL has ?debug=viewport. Shows live values of every viewport
// metric we depend on — visualViewport.*, window.inner*, --app-height,
// resolved 100vh/100svh/100dvh/100lvh, safe-area insets, and UA — so we
// can see what Chrome iOS is actually reporting on a real device (Apple
// blocks remote DevTools inspection of Chrome iOS).
//
// The four sentinel divs measure the resolved pixel value of each viewport
// unit; reading their .clientHeight tells us exactly what each unit equals
// at this moment. If 100svh on Chrome iOS turns out to equal 100lvh
// (i.e. the largest viewport, including chrome), our min() cap in shell.css
// is a no-op there and we have our root cause.
function setupViewportDebugOverlay() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("debug") !== "viewport") return;

  const sentinels = ["100vh", "100svh", "100dvh", "100lvh"].map((unit) => {
    const el = document.createElement("div");
    el.style.cssText = `position:fixed;left:-9999px;top:0;width:1px;height:${unit};pointer-events:none;`;
    el.dataset.unit = unit;
    document.body.appendChild(el);
    return el;
  });

  const overlay = document.createElement("div");
  overlay.id = "viewport-debug-overlay";
  overlay.style.cssText =
    "position:fixed;top:0;left:0;right:0;z-index:2147483647;" +
    "background:rgba(0,0,0,0.85);color:#0f0;font:11px/1.3 monospace;" +
    "padding:6px 8px;pointer-events:none;white-space:pre;" +
    "max-height:50vh;overflow:hidden;";
  document.body.appendChild(overlay);

  function readSafeArea(side) {
    const probe = document.createElement("div");
    probe.style.cssText = `position:fixed;left:-9999px;top:0;height:env(safe-area-inset-${side},0px);width:1px;`;
    document.body.appendChild(probe);
    const v = probe.clientHeight;
    document.body.removeChild(probe);
    return v;
  }

  function elH(id) {
    const el = document.getElementById(id);
    if (!el) return "—";
    const r = el.getBoundingClientRect();
    return `${Math.round(r.height)} (top=${Math.round(r.top)} bot=${Math.round(r.bottom)})`;
  }

  function snapshot() {
    const vv = window.visualViewport;
    const cs = getComputedStyle(document.documentElement);
    const bodyR = document.body.getBoundingClientRect();
    const lines = [
      `UA: ${navigator.userAgent.slice(0, 90)}`,
      `window.inner:        ${window.innerWidth} x ${window.innerHeight}`,
      `visualViewport:      ${vv ? vv.width.toFixed(1) + " x " + vv.height.toFixed(1) : "unsupported"}`,
      `vv.offset / scale:   ${vv ? `top=${vv.offsetTop.toFixed(1)} left=${vv.offsetLeft.toFixed(1)} scale=${vv.scale.toFixed(2)}` : "—"}`,
      `documentElement.cH:  ${document.documentElement.clientHeight}`,
      `--app-height:        ${cs.getPropertyValue("--app-height").trim() || "(unset)"}`,
      `safe-area top/bot:   ${readSafeArea("top")} / ${readSafeArea("bottom")}`,
      `100vh / 100svh:      ${sentinels[0].clientHeight} / ${sentinels[1].clientHeight}`,
      `100dvh / 100lvh:     ${sentinels[2].clientHeight} / ${sentinels[3].clientHeight}`,
      `bottomChrome:        ${vv ? Math.max(0, window.innerHeight - vv.height - (vv.offsetTop || 0)) : 0}`,
      `body rect:           ${Math.round(bodyR.height)} (top=${Math.round(bodyR.top)} bot=${Math.round(bodyR.bottom)})`,
      `#shell-header:       ${elH("shell-header")}`,
      `#content-frame:      ${elH("content-frame")}`,
      `#shell-player:       ${elH("shell-player")}`,
    ];
    overlay.textContent = lines.join("\n");
  }

  snapshot();
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", snapshot);
    window.visualViewport.addEventListener("scroll", snapshot);
  }
  window.addEventListener("resize", snapshot);
  window.addEventListener("orientationchange", () => setTimeout(snapshot, 150));
  window.addEventListener("scroll", snapshot, { passive: true });
}

// Initialize when DOM is ready.
// MUST use var (not let/const) so shellPlayer is a window property,
// accessible from the iframe via window.parent.shellPlayer.
var shellPlayer;
document.addEventListener("DOMContentLoaded", () => {
  setupViewportFix();
  setupViewportDebugOverlay();
  shellPlayer = new ShellPlayer();

  // Check for autoplay intent (from non-iframe redirect)
  const params = new URLSearchParams(window.location.search);
  const autoplayId = params.get("autoplay");
  if (autoplayId) {
    const pending = sessionStorage.getItem("pendingPlay");
    const resume = sessionStorage.getItem("pendingPlayResume") === "1";
    if (pending) {
      sessionStorage.removeItem("pendingPlay");
      sessionStorage.removeItem("pendingPlayResume");
      // Small delay to let iframe load before showing player state
      setTimeout(() => {
        try {
          shellPlayer.playBook(JSON.parse(pending), resume);
        } catch (e) {
          console.warn("Failed to parse pending play data:", e);
        }
      }, 100);
    }
    // Clean URL — remove ?autoplay param
    history.replaceState(null, "", window.location.pathname);
  }

  // ── Locale switcher ──
  var localeSelect = document.getElementById("locale-select");
  if (localeSelect && typeof i18n !== "undefined") {
    localeSelect.value = i18n.getLocale();
    localeSelect.addEventListener("change", function () {
      i18n.setLocale(this.value);
    });
  }

  // When locale changes in the shell, propagate to the iframe so its
  // content re-renders without requiring a manual page refresh.
  document.addEventListener("localeChanged", function (e) {
    var iframe = document.getElementById("content-frame");
    if (iframe && iframe.contentWindow) {
      iframe.contentWindow.postMessage(
        { type: "localeChanged", locale: e.detail.locale },
        window.location.origin
      );
    }
  });
});
