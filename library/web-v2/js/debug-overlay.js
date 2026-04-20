/**
 * debug-overlay.js — QA-only on-device diagnostic panel.
 *
 * Chrome iOS has no real DevTools; Safari Web Inspector needs a Mac. This
 * gives Qing a screenshot-ready panel showing the failure state.
 *
 * Activation:
 *   ?debug=1   — turn on (persists in localStorage)
 *   ?debug=0   — turn off and clear
 *
 * When active: fixed panel at the top of the page with live snapshots of
 * MSE codec support, streaming-translate state, <audio> element state,
 * and the tail of a rolling event log. Does NOT translate (translate="no")
 * — Google Translate on Chrome iOS mangles it otherwise.
 *
 * Other modules can push context by calling window.__debugLog(kind, payload).
 * If this module is not loaded (prod), __debugLog is a no-op.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "debugOverlay";
  var MAX_EVENTS = 24;
  var SNAPSHOT_MS = 500;

  function qsParam(name) {
    try {
      var u = new URL(window.location.href);
      return u.searchParams.get(name);
    } catch (e) { return null; }
  }

  var qsDebug = qsParam("debug");
  if (qsDebug === "0") {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    window.__debugLog = function () {};
    return;
  }
  if (qsDebug === "1") {
    try { localStorage.setItem(STORAGE_KEY, "1"); } catch (e) {}
  }
  var enabled = false;
  try { enabled = localStorage.getItem(STORAGE_KEY) === "1"; } catch (e) {}
  if (!enabled) {
    window.__debugLog = function () {};
    return;
  }

  var events = [];
  function logEvent(kind, payload) {
    var entry = {
      t: new Date().toISOString().slice(11, 23),
      kind: String(kind || "?"),
      data: payload === undefined ? "" : safeJson(payload),
    };
    events.push(entry);
    if (events.length > MAX_EVENTS) events.shift();
  }
  window.__debugLog = logEvent;

  function safeJson(v) {
    try {
      if (v instanceof Error) return v.name + ": " + v.message;
      if (typeof v === "string") return v;
      return JSON.stringify(v, function (k, val) {
        if (val instanceof Set) return Array.from(val);
        if (val instanceof Map) return Array.from(val.entries());
        return val;
      });
    } catch (e) { return String(v); }
  }

  function mseProbes() {
    if (typeof MediaSource === "undefined") return { supported: false };
    var codecs = [
      'audio/webm; codecs="opus"',
      'audio/ogg; codecs="opus"',
      'audio/mp4; codecs="opus"',
      'audio/mp4; codecs="mp4a.40.2"',
    ];
    var out = { supported: true };
    codecs.forEach(function (c) {
      try { out[c] = MediaSource.isTypeSupported(c); }
      catch (e) { out[c] = "ERR:" + e.message; }
    });
    return out;
  }

  function audioSnap() {
    var a = document.getElementById("audio-element");
    if (!a) return { found: false };
    var errCode = a.error ? a.error.code : null;
    var errMsg = a.error ? a.error.message : null;
    return {
      found: true,
      currentSrc: a.currentSrc || "",
      srcPrefix: (a.src || "").slice(0, 60),
      readyState: a.readyState,
      networkState: a.networkState,
      paused: a.paused,
      muted: a.muted,
      volume: a.volume,
      currentTime: Number(a.currentTime || 0).toFixed(2),
      duration: Number(a.duration || 0).toFixed(2),
      buffered: a.buffered && a.buffered.length
        ? "[" + Number(a.buffered.start(0)).toFixed(1) + "-" +
          Number(a.buffered.end(a.buffered.length - 1)).toFixed(1) + "]"
        : "[]",
      errorCode: errCode,
      errorMessage: errMsg,
    };
  }

  function streamingSnap() {
    var st = window.streamingTranslate;
    if (!st) return { loaded: false };
    var out = { loaded: true };
    try { out.state = st.getState && st.getState(); } catch (e) {}
    try { out.isIdle = st.isIdle && st.isIdle(); } catch (e) {}
    try { out.isBuffering = st.isBuffering && st.isBuffering(); } catch (e) {}
    try { out.isStreaming = st.isStreaming && st.isStreaming(); } catch (e) {}
    return out;
  }

  function shellSnap() {
    var s = window.shell || {};
    var book = s.currentBook || s.book || null;
    return {
      currentBookId: book && (book.id || book.asin) || null,
      currentLocale: (window.i18n && window.i18n.getLocale && window.i18n.getLocale()) || null,
      htmlLang: document.documentElement && document.documentElement.lang,
      pageUrl: location.pathname + location.search,
    };
  }

  function render() {
    var out = [];
    out.push("=== Audiobook-Manager debug overlay ===");
    out.push("UA: " + (navigator.userAgent || "?"));
    out.push("TS: " + new Date().toISOString());
    out.push("");
    out.push("— shell —");
    out.push(safeJson(shellSnap()));
    out.push("");
    out.push("— streamingTranslate —");
    out.push(safeJson(streamingSnap()));
    out.push("");
    out.push("— <audio> —");
    out.push(safeJson(audioSnap()));
    out.push("");
    out.push("— MSE codec support —");
    out.push(safeJson(mseProbes()));
    out.push("");
    out.push("— events (last " + events.length + "/" + MAX_EVENTS + ") —");
    for (var i = 0; i < events.length; i++) {
      var e = events[i];
      out.push(e.t + " " + e.kind + " " + e.data);
    }
    return out.join("\n");
  }

  // Capture global JS errors and unhandled rejections so they don't go
  // silent on iOS — hugely important since there's no console.
  window.addEventListener("error", function (e) {
    logEvent("js-error", {
      msg: e.message,
      src: e.filename + ":" + e.lineno + ":" + e.colno,
      err: e.error && e.error.message,
    });
  });
  window.addEventListener("unhandledrejection", function (e) {
    logEvent("promise-reject", {
      reason: (e.reason && (e.reason.message || e.reason.toString())) || "?",
    });
  });

  // Hook the streaming DOM events fired by websocket.js → streaming-translate.js
  ["segment-ready", "buffer-progress", "chapter-ready", "ws-connected"].forEach(function (ev) {
    document.addEventListener(ev, function (e) {
      logEvent(ev, e.detail || {});
    });
  });

  // Audio-element error/stall/abort surfacing once DOM is ready.
  function hookAudio() {
    var a = document.getElementById("audio-element");
    if (!a) return;
    ["error", "stalled", "abort", "emptied", "suspend", "waiting", "play", "playing", "pause", "ended"].forEach(function (ev) {
      a.addEventListener(ev, function () {
        var info = {};
        if (ev === "error" && a.error) {
          info.code = a.error.code;
          info.message = a.error.message;
        }
        info.readyState = a.readyState;
        info.networkState = a.networkState;
        info.currentTime = Number(a.currentTime || 0).toFixed(2);
        logEvent("audio:" + ev, info);
      });
    });
  }

  // DOM build.
  function build() {
    var panel = document.createElement("div");
    panel.id = "debug-overlay";
    panel.setAttribute("translate", "no");
    panel.className = "notranslate";
    panel.style.cssText = [
      "position:fixed",
      "top:0",
      "left:0",
      "right:0",
      "max-height:55vh",
      "overflow-y:auto",
      "background:#000",
      "color:#0f0",
      "font:11px/1.35 ui-monospace,Menlo,Consolas,monospace",
      "padding:6px 8px",
      "z-index:2147483647",
      "border-bottom:1px solid #0f0",
      "white-space:pre-wrap",
      "word-break:break-all",
      "-webkit-user-select:text",
      "user-select:text",
    ].join(";");

    var bar = document.createElement("div");
    bar.style.cssText = "display:flex;gap:6px;margin-bottom:4px;position:sticky;top:0;background:#000;padding-bottom:4px;border-bottom:1px dashed #040";
    var btnCopy = document.createElement("button");
    btnCopy.textContent = "Copy";
    btnCopy.style.cssText = "background:#030;color:#0f0;border:1px solid #0f0;padding:3px 8px;font:inherit;cursor:pointer;min-height:32px";
    var btnClose = document.createElement("button");
    btnClose.textContent = "×";
    btnClose.title = "Close overlay (clears ?debug=1)";
    btnClose.style.cssText = "background:#030;color:#0f0;border:1px solid #0f0;padding:3px 10px;font:inherit;cursor:pointer;min-height:32px;margin-left:auto";
    var btnRefresh = document.createElement("button");
    btnRefresh.textContent = "Force refresh";
    btnRefresh.style.cssText = "background:#030;color:#0f0;border:1px solid #0f0;padding:3px 8px;font:inherit;cursor:pointer;min-height:32px";
    bar.appendChild(btnCopy);
    bar.appendChild(btnRefresh);
    bar.appendChild(btnClose);

    var pre = document.createElement("pre");
    pre.id = "debug-overlay-body";
    pre.style.cssText = "margin:0;white-space:pre-wrap;word-break:break-all;color:#0f0";

    panel.appendChild(bar);
    panel.appendChild(pre);
    document.body.appendChild(panel);

    btnCopy.addEventListener("click", function () {
      var text = pre.textContent || "";
      function fallbackCopy() {
        try {
          var ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.top = "-9999px";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          btnCopy.textContent = "Copied!";
        } catch (e) { btnCopy.textContent = "Copy failed"; }
        setTimeout(function () { btnCopy.textContent = "Copy"; }, 1500);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          btnCopy.textContent = "Copied!";
          setTimeout(function () { btnCopy.textContent = "Copy"; }, 1500);
        }).catch(fallbackCopy);
      } else {
        fallbackCopy();
      }
    });

    btnClose.addEventListener("click", function () {
      try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
      panel.remove();
      var url = new URL(location.href);
      url.searchParams.delete("debug");
      history.replaceState(null, "", url.toString());
    });

    btnRefresh.addEventListener("click", function () {
      pre.textContent = render();
    });

    return pre;
  }

  function start() {
    hookAudio();
    var pre = build();
    pre.textContent = render();
    setInterval(function () { pre.textContent = render(); }, SNAPSHOT_MS);
    logEvent("overlay-ready", { ua: navigator.userAgent });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
