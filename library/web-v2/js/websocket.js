/**
 * WebSocket client for real-time maintenance announcements and heartbeat.
 *
 * - Sends heartbeat every 10 seconds with player activity state
 * - Auto-reconnects with exponential backoff (1s -> 30s max)
 * - Falls back to REST polling after 3 failed WebSocket attempts
 * - Dispatches custom DOM events for downstream consumers
 */
(function () {
  "use strict";

  var HEARTBEAT_INTERVAL = 10000; // 10 seconds
  var POLL_INTERVAL = 30000; // 30 seconds (fallback)
  var MAX_RETRIES = 3;
  var MAX_BACKOFF = 30000;

  var ws = null;
  var heartbeatTimer = null;
  var pollTimer = null;
  var retryCount = 0;
  var retryTimer = null;
  var usingPolling = false;

  function getPlayerState() {
    var audio = document.getElementById("audio-player");
    if (!audio) return "idle";
    if (audio.paused) return "paused";
    return "streaming";
  }

  function dispatch(eventName, detail) {
    document.dispatchEvent(new CustomEvent(eventName, { detail: detail }));
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
      return;
    }

    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/api/ws";

    try {
      ws = new WebSocket(url);
    } catch (e) {
      onFail();
      return;
    }

    ws.onopen = function () {
      retryCount = 0;
      usingPolling = false;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      startHeartbeat();
      dispatch("ws-connected", {});
    };

    ws.onmessage = function (event) {
      try {
        var msg = JSON.parse(event.data);
        if (msg.type === "maintenance_announce") {
          dispatch("maintenance-announce", msg);
        } else if (msg.type === "maintenance_dismiss") {
          dispatch("maintenance-dismiss", msg);
        } else if (msg.type === "maintenance_update") {
          dispatch("maintenance-update", msg);
        } else if (msg.type === "audit_notify") {
          dispatch("audit-notify", msg);
        } else if (msg.type === "suggestion_new") {
          dispatch("suggestion-new", msg);
        }
      } catch (e) {
        // ignore malformed messages
      }
    };

    ws.onclose = function () {
      stopHeartbeat();
      onFail();
    };

    ws.onerror = function () {
      // onclose will fire after onerror
    };
  }

  function onFail() {
    retryCount++;
    if (retryCount > MAX_RETRIES) {
      startPolling();
      return;
    }
    var delay = Math.min(1000 * Math.pow(2, retryCount - 1), MAX_BACKOFF);
    retryTimer = setTimeout(connect, delay);
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(function () {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "heartbeat", state: getPlayerState() }));
      }
    }, HEARTBEAT_INTERVAL);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function startPolling() {
    if (usingPolling) return;
    usingPolling = true;
    pollForAnnouncements();
    pollTimer = setInterval(pollForAnnouncements, POLL_INTERVAL);
  }

  function pollForAnnouncements() {
    fetch("/api/maintenance/announcements")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        dispatch("maintenance-announce", {
          type: "maintenance_announce",
          messages: data.messages || [],
          windows: data.windows || [],
        });
      })
      .catch(function () { /* ignore fetch errors */ });
  }

  // Public API
  window.audioWs = {
    isConnected: function () {
      return ws && ws.readyState === WebSocket.OPEN;
    },
    isPolling: function () {
      return usingPolling;
    },
    reconnect: function () {
      retryCount = 0;
      connect();
    },
  };

  // Connect on load
  connect();
})();
