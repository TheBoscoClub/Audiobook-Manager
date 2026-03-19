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

    // Position persistence
    this.storagePrefix = "audiobook_";
    this.apiSaveTimeout = null;
    this.apiSaveDelay = 15000; // API save every 15s
    this.positionSaveInterval = 30000; // localStorage save every 30s

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
    });
    document.getElementById("sp-forward").addEventListener("click", () => {
      this.audio.currentTime = Math.min(
        this.audio.duration || 0,
        this.audio.currentTime + 30,
      );
    });
    document
      .getElementById("sp-speed")
      .addEventListener("click", () => this.cycleSpeed());
    document.getElementById("sp-volume").addEventListener("input", (e) => {
      this.audio.volume = e.target.value / 100;
    });
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
      let message = "Unknown error";
      if (error) {
        switch (error.code) {
          case 1:
            message = "MEDIA_ERR_ABORTED";
            break;
          case 2:
            message = "MEDIA_ERR_NETWORK";
            break;
          case 3:
            message = "MEDIA_ERR_DECODE";
            break;
          case 4:
            message = "MEDIA_ERR_SRC_NOT_SUPPORTED";
            break;
        }
      }
      console.error("Audio error:", message, error);
    });

    this.audio.addEventListener("timeupdate", () => this.onTimeUpdate());
    this.audio.addEventListener("loadedmetadata", () =>
      this.onMetadataLoaded(),
    );

    this.audio.addEventListener("ended", () => {
      this.setPlayPauseIcon(false);
      if (this.currentBook) this.clearPosition(this.currentBook.id);
    });

    this.audio.addEventListener("play", () => {
      this.setPlayPauseIcon(true);
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

    this.currentBook = { ...book, bookId, coverUrl };

    // Reset save timer so auto-save doesn't fire immediately with a stale
    // _lastSaveTime when restarting a book (prevents saving near-zero position)
    this._lastSaveTime = Date.now();

    // Update player bar UI
    document.getElementById("sp-title").textContent =
      book.title || "Unknown Title";
    document.getElementById("sp-author").textContent =
      book.author || "Unknown Author";

    const cover = document.getElementById("sp-cover");
    if (coverUrl) {
      cover.src = coverUrl;
      cover.alt = book.title;
    } else {
      cover.src = "";
      cover.alt = "";
    }

    // Load audio — WebM fallback for Safari
    const needsWebm = !this.audio.canPlayType("audio/ogg; codecs=opus");
    this.audio.src = `${API_BASE}/stream/${bookId}${needsWebm ? "?format=webm" : ""}`;

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

    // Start playback — must happen within user gesture window.
    // Cross-frame calls (iframe → parent) lose gesture activation if async
    // operations (like API fetch) run first.
    try {
      await this.audio.play();
    } catch (error) {
      console.error("Failed to play audio:", error);
    }

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
  }

  togglePlayPause() {
    if (this.audio.paused) {
      this.audio.play();
    } else {
      this.audio.pause();
    }
  }

  cycleSpeed() {
    this.currentRateIndex =
      (this.currentRateIndex + 1) % this.playbackRates.length;
    const rate = this.playbackRates[this.currentRateIndex];
    this.audio.playbackRate = rate;
    document.getElementById("sp-speed-display").textContent = rate + "x";
    this.saveSpeed(rate);
  }

  close() {
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
      if (!response.ok)
        console.warn(`Failed to save position: ${response.status}`);
    } catch (error) {
      console.warn("Error saving position to API:", error);
    }
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
      console.log(
        `Using API position (${apiPos.position}s) over local (${localPos.position}s)`,
      );
      return apiPos;
    }
    console.log(
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
    } catch (e) {
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
    });
    navigator.mediaSession.setActionHandler("seekforward", (d) => {
      this.audio.currentTime = Math.min(
        this.audio.duration || 0,
        this.audio.currentTime + (d.seekOffset || 30),
      );
      this.updateMediaPositionState();
    });
    navigator.mediaSession.setActionHandler("seekto", (d) => {
      if (d.seekTime !== undefined && this.audio.duration) {
        this.audio.currentTime = Math.min(d.seekTime, this.audio.duration);
        this.updateMediaPositionState();
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
    navigator.mediaSession.metadata = new MediaMetadata({
      title: book.title || "Unknown Title",
      artist: book.author || "Unknown Author",
      album: book.narrator ? `Narrated by ${book.narrator}` : book.series || "",
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

// Mobile viewport fix: measure the ACTUAL visible viewport height via
// visualViewport API and set --app-height on the shell. Also calculate
// how much of the layout viewport is hidden behind browser chrome and
// post that offset to the iframe so it can add bottom padding.
function setupViewportFix() {
  const iframe = document.getElementById("content-frame");
  function update() {
    const vv = window.visualViewport;
    const visibleH = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty("--app-height", visibleH + "px");

    // Calculate bottom chrome offset: difference between layout viewport
    // (window.innerHeight) and visual viewport. This is the space eaten
    // by browser bottom bars (Chrome nav, Safari toolbar, etc.)
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

// Initialize when DOM is ready.
// MUST use var (not let/const) so shellPlayer is a window property,
// accessible from the iframe via window.parent.shellPlayer.
var shellPlayer;
document.addEventListener("DOMContentLoaded", () => {
  setupViewportFix();
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
});
