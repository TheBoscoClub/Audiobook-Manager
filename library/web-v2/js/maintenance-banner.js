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
    indicator.setAttribute("aria-label", "Maintenance announcements");
    indicator.title = "Click to view maintenance announcements";
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
    switchBtn.title = "Dismiss maintenance announcements for this session";

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
    label.textContent = "Dismiss";
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

  function updateMessages() {
    if (!messagesContainer) return;
    while (messagesContainer.firstChild) {
      messagesContainer.removeChild(messagesContainer.firstChild);
    }

    // Manual messages first
    currentMessages.forEach(function (m) {
      var p = document.createElement("p");
      p.className = "maintenance-panel-message";
      p.textContent = m.message || m;
      messagesContainer.appendChild(p);
    });

    // Scheduled window announcements
    currentWindows.forEach(function (w) {
      var p = document.createElement("p");
      p.className = "maintenance-panel-message";
      var text = w.name;
      if (w.next_run_at) {
        text += " -- " + new Date(w.next_run_at).toLocaleString();
      }
      if (w.description) {
        text += ": " + w.description;
      }
      p.textContent = text;
      messagesContainer.appendChild(p);
    });
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
