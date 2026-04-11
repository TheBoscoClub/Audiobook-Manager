/**
 * Maintenance announcement banner with Frankenstein knife switch.
 *
 * Listens for custom events from websocket.js and renders:
 * - Pulsing red indicator when announcements active
 * - Expandable panel with message text in neon red
 * - SVG knife switch with Web Audio API sounds
 *
 * Uses safe DOM methods exclusively (createElement, textContent, setAttribute).
 */
(function () {
  "use strict";

  var STATE_KEY = "maint-banner-dismissed";
  var indicator = null;
  var panel = null;
  var messagesContainer = null;
  var currentMessages = [];
  var currentWindows = [];
  var audioCtx = null;

  function getAudioCtx() {
    if (!audioCtx) {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) {
        return null;
      }
    }
    return audioCtx;
  }

  // -- Web Audio synthesized sounds --

  function synthesizeBzzzt(longer) {
    var ctx = getAudioCtx();
    if (!ctx) return;
    var duration = longer ? 0.1 : 0.06;
    var bufferSize = ctx.sampleRate * duration;
    var buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < bufferSize; i++) {
      data[i] = (Math.random() * 2 - 1) * 0.3;
    }
    var source = ctx.createBufferSource();
    source.buffer = buffer;

    var bandpass = ctx.createBiquadFilter();
    bandpass.type = "bandpass";
    bandpass.frequency.value = 800;
    bandpass.Q.value = 2;

    var gain = ctx.createGain();
    gain.gain.setValueAtTime(0.4, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + duration);

    source.connect(bandpass);
    bandpass.connect(gain);
    gain.connect(ctx.destination);
    source.start();
  }

  function synthesizeClunk(heavy) {
    var ctx = getAudioCtx();
    if (!ctx) return;
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = heavy ? 80 : 120;

    var gain = ctx.createGain();
    gain.gain.setValueAtTime(0.5, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
  }

  // -- DOM construction --

  function buildIndicator() {
    indicator = document.createElement("button");
    indicator.className = "maintenance-indicator";
    indicator.setAttribute("aria-label", typeof t === "function" ? t("maintenance.ariaLabel") : "Maintenance announcements");
    indicator.title = typeof t === "function" ? t("maintenance.indicatorTitle") : "Click to view maintenance announcements";
    indicator.textContent = "!";
    indicator.addEventListener("click", function (e) {
      e.stopPropagation();
      togglePanel();
    });
    document.body.appendChild(indicator);
  }

  function buildPanel() {
    panel = document.createElement("div");
    panel.className = "maintenance-panel";
    panel.addEventListener("click", function (e) {
      e.stopPropagation();
    });

    messagesContainer = document.createElement("div");
    messagesContainer.className = "maintenance-panel-messages";
    panel.appendChild(messagesContainer);

    // Knife switch
    var switchBtn = document.createElement("button");
    switchBtn.className = "knife-switch";
    switchBtn.setAttribute("role", "switch");
    switchBtn.setAttribute("aria-checked", "true");
    switchBtn.title = typeof t === "function" ? t("maintenance.dismissTitle") : "Dismiss maintenance announcements for this session";

    // SVG knife switch
    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 48 80");
    svg.setAttribute("aria-hidden", "true");

    // Jaw contacts (top)
    var jawLeft = document.createElementNS(svgNS, "rect");
    jawLeft.setAttribute("x", "16");
    jawLeft.setAttribute("y", "8");
    jawLeft.setAttribute("width", "6");
    jawLeft.setAttribute("height", "20");
    jawLeft.setAttribute("rx", "1");
    jawLeft.setAttribute("class", "knife-switch-jaw");
    svg.appendChild(jawLeft);

    var jawRight = document.createElementNS(svgNS, "rect");
    jawRight.setAttribute("x", "26");
    jawRight.setAttribute("y", "8");
    jawRight.setAttribute("width", "6");
    jawRight.setAttribute("height", "20");
    jawRight.setAttribute("rx", "1");
    jawRight.setAttribute("class", "knife-switch-jaw");
    svg.appendChild(jawRight);

    // Blade (pivots)
    var blade = document.createElementNS(svgNS, "rect");
    blade.setAttribute("x", "21");
    blade.setAttribute("y", "15");
    blade.setAttribute("width", "6");
    blade.setAttribute("height", "40");
    blade.setAttribute("rx", "2");
    blade.setAttribute("class", "knife-switch-blade");
    svg.appendChild(blade);

    // Handle (bottom)
    var handle = document.createElementNS(svgNS, "rect");
    handle.setAttribute("x", "18");
    handle.setAttribute("y", "52");
    handle.setAttribute("width", "12");
    handle.setAttribute("height", "18");
    handle.setAttribute("rx", "3");
    handle.setAttribute("class", "knife-switch-handle");
    svg.appendChild(handle);

    // Pivot point
    var pivot = document.createElementNS(svgNS, "circle");
    pivot.setAttribute("cx", "24");
    pivot.setAttribute("cy", "55");
    pivot.setAttribute("r", "3");
    pivot.setAttribute("fill", "#666");
    svg.appendChild(pivot);

    switchBtn.appendChild(svg);

    var label = document.createElement("span");
    label.className = "knife-switch-label";
    label.textContent = typeof t === "function" ? t("notification.dismissLabel") : "Dismiss";
    switchBtn.appendChild(label);

    switchBtn.addEventListener("click", function () {
      var isOn = switchBtn.getAttribute("aria-checked") === "true";
      var newState = !isOn;
      switchBtn.setAttribute("aria-checked", String(newState));

      // Sound: bzzzt during arc, clunk at contact
      if (newState) {
        synthesizeBzzzt(true);
        setTimeout(function () { synthesizeClunk(true); }, 100);
      } else {
        synthesizeBzzzt(false);
        setTimeout(function () { synthesizeClunk(false); }, 80);
      }

      if (!newState) {
        sessionStorage.setItem(STATE_KEY, "1");
        hideIndicator();
        closePanel();
      } else {
        sessionStorage.removeItem(STATE_KEY);
        updateDisplay();
      }
    });

    switchBtn.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        switchBtn.click();
      }
    });

    panel.appendChild(switchBtn);
    document.body.appendChild(panel);
  }

  // -- Display logic --

  // SHA-256 short digest matching backend _hash_source / i18n.js hashSource.
  async function hashSource(text) {
    if (window.crypto && window.crypto.subtle) {
      var bytes = new TextEncoder().encode(text);
      var buf = await window.crypto.subtle.digest("SHA-256", bytes);
      var hex = Array.prototype.map
        .call(new Uint8Array(buf), function (b) {
          return ("00" + b.toString(16)).slice(-2);
        })
        .join("");
      return hex.slice(0, 16);
    }
    return text;
  }

  // Translate admin-authored strings (message bodies, window names/descriptions)
  // then overlay the translations onto the already-rendered <p> elements.
  // Each <p> stores its source strings in data-src-* attributes so we can
  // re-render on locale change without losing the originals.
  async function applyMessageTranslations() {
    if (!messagesContainer || !window.i18n) return;
    var locale = window.i18n.getLocale();
    var paragraphs = messagesContainer.querySelectorAll(".maintenance-panel-message");
    if (!paragraphs.length) return;

    // English: restore originals (handles locale switch back to en).
    if (locale === "en") {
      paragraphs.forEach(function (p) {
        var src = p.getAttribute("data-src-text");
        if (src) p.textContent = src;
      });
      return;
    }

    // Collect unique source fragments (name + description + message).
    var fragments = [];
    var seen = {};
    paragraphs.forEach(function (p) {
      ["data-src-name", "data-src-desc", "data-src-message"].forEach(function (attr) {
        var v = p.getAttribute(attr);
        if (v && !seen[v]) {
          seen[v] = true;
          fragments.push(v);
        }
      });
    });
    if (!fragments.length) return;

    var map;
    try {
      map = await window.i18n.translateStrings(fragments);
    } catch (e) {
      return;
    }
    if (!map) return;

    // Hash each fragment → pick translation, then rebuild each <p>.
    var fragMap = {};
    for (var i = 0; i < fragments.length; i++) {
      var h = await hashSource(fragments[i]);
      if (map[h]) fragMap[fragments[i]] = map[h];
    }

    paragraphs.forEach(function (p) {
      var name = p.getAttribute("data-src-name");
      var desc = p.getAttribute("data-src-desc");
      var message = p.getAttribute("data-src-message");
      var when = p.getAttribute("data-src-when");
      var text;
      if (message) {
        text = fragMap[message] || message;
      } else {
        text = fragMap[name] || name || "";
        if (when) text += " -- " + when;
        if (desc) text += ": " + (fragMap[desc] || desc);
      }
      p.textContent = text;
    });
  }

  function updateMessages() {
    if (!messagesContainer) return;
    while (messagesContainer.firstChild) {
      messagesContainer.removeChild(messagesContainer.firstChild);
    }

    // Manual messages first
    currentMessages.forEach(function (m) {
      var p = document.createElement("p");
      p.className = "maintenance-panel-message";
      var msg = m.message || m;
      p.textContent = msg;
      p.setAttribute("data-src-message", msg);
      p.setAttribute("data-src-text", msg);
      messagesContainer.appendChild(p);
    });

    // Scheduled window announcements
    currentWindows.forEach(function (w) {
      var p = document.createElement("p");
      p.className = "maintenance-panel-message";
      var when = w.next_run_at ? new Date(w.next_run_at).toLocaleString() : "";
      var text = w.name || "";
      if (when) text += " -- " + when;
      if (w.description) text += ": " + w.description;
      p.textContent = text;
      if (w.name) p.setAttribute("data-src-name", w.name);
      if (w.description) p.setAttribute("data-src-desc", w.description);
      if (when) p.setAttribute("data-src-when", when);
      p.setAttribute("data-src-text", text);
      messagesContainer.appendChild(p);
    });

    // Fire-and-forget overlay translation pass for non-en locales.
    applyMessageTranslations().catch(function () {});
  }

  function hasContent() {
    return currentMessages.length > 0 || currentWindows.length > 0;
  }

  function isDismissed() {
    return sessionStorage.getItem(STATE_KEY) === "1";
  }

  function showIndicator() {
    if (indicator) indicator.classList.add("active");
  }

  function hideIndicator() {
    if (indicator) indicator.classList.remove("active");
  }

  function togglePanel() {
    if (panel) panel.classList.toggle("open");
    updateMessages();
  }

  function closePanel() {
    if (panel) panel.classList.remove("open");
  }

  function updateDisplay() {
    if (!hasContent()) {
      hideIndicator();
      closePanel();
      return;
    }
    if (isDismissed()) {
      hideIndicator();
      return;
    }
    showIndicator();
  }

  // -- Event handlers --

  function onAnnounce(e) {
    var detail = e.detail || {};
    if (detail.messages) currentMessages = detail.messages;
    if (detail.windows) currentWindows = detail.windows;
    updateDisplay();
    if (panel && panel.classList.contains("open")) {
      updateMessages();
    }
  }

  function onDismiss(e) {
    var detail = e.detail || {};
    if (detail.message_id) {
      currentMessages = currentMessages.filter(function (m) {
        return m.id !== detail.message_id;
      });
    }
    updateDisplay();
    if (panel && panel.classList.contains("open")) {
      updateMessages();
    }
  }

  function onUpdate(e) {
    var detail = e.detail || {};
    if (detail.window_id && detail.status === "completed") {
      currentWindows = currentWindows.filter(function (w) {
        return w.id !== detail.window_id;
      });
      updateDisplay();
    }
  }

  // -- Data fetching --

  function fetchAnnouncements() {
    fetch("/api/maintenance/announcements", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        currentMessages = data.messages || [];
        currentWindows = data.windows || [];
        updateDisplay();
      })
      .catch(function () {});
  }

  // -- Init --

  function init() {
    buildIndicator();
    buildPanel();

    // Click outside to close panel
    document.addEventListener("click", function () {
      closePanel();
    });

    // Listen for WebSocket events
    document.addEventListener("maintenance-announce", onAnnounce);
    document.addEventListener("maintenance-dismiss", onDismiss);
    document.addEventListener("maintenance-update", onUpdate);

    // Re-translate dynamic text on locale change
    document.addEventListener("localeChanged", function () {
      if (indicator) {
        indicator.setAttribute("aria-label", typeof t === "function" ? t("maintenance.ariaLabel") : "Maintenance announcements");
        indicator.title = typeof t === "function" ? t("maintenance.indicatorTitle") : "Click to view maintenance announcements";
      }
      if (panel) {
        var dismissLabel = panel.querySelector(".knife-switch-label");
        if (dismissLabel) dismissLabel.textContent = typeof t === "function" ? t("notification.dismissLabel") : "Dismiss";
        var switchBtn = panel.querySelector(".knife-switch");
        if (switchBtn) switchBtn.title = typeof t === "function" ? t("maintenance.dismissTitle") : "Dismiss maintenance announcements for this session";
      }
      // Re-render admin-authored dynamic content in the new locale.
      applyMessageTranslations().catch(function () {});
    });

    // Initial fetch for page load (before WebSocket connects)
    fetchAnnouncements();

    // Re-fetch when page regains focus (catches announcements sent while
    // user was on another tab or when WebSocket is not connected)
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) fetchAnnouncements();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
