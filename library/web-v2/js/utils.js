/**
 * Shared utility functions — date formatting, polling, and auth state.
 *
 * Loaded before page-specific JS files.  All functions are on window scope
 * so they're available everywhere without imports.
 */

// ============================================
// Date Formatting
// ============================================

/**
 * Format a date/timestamp string into a human-readable form.
 *
 * @param {string|number|Date} value - ISO string, unix ms, or Date object
 * @param {string} [style="short"] - Formatting style:
 *   "short"    — "Mar 29, 2026"
 *   "long"     — "Mar 29, 2026 2:15 PM"
 *   "time"     — "2:15 PM"
 *   "relative" — "5 minutes ago" / "2 hours ago" / "Mar 29"
 *   "iso"      — "2026-03-29T14:15:00.000Z"
 * @returns {string}
 */
function formatDate(value, style) {
  if (!value) return "-";
  style = style || "short";
  var _T = function (k, fb) {
    if (typeof t === "function") {
      var v = t(k);
      if (v && v !== k) return v;
    }
    return fb;
  };

  var d;
  if (value instanceof Date) {
    d = value;
  } else {
    d = new Date(value);
  }
  if (isNaN(d.getTime())) return String(value);

  switch (style) {
    case "short":
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });

    case "long":
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });

    case "time":
      return d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      });

    case "relative": {
      var now = new Date();
      var diffMs = now - d;
      var diffSec = Math.floor(diffMs / 1000);
      var diffMin = Math.floor(diffSec / 60);
      var diffHour = Math.floor(diffMin / 60);

      if (diffSec < 60) return _T("utils.justNow", "just now");
      if (diffMin < 60) {
        var mKey = diffMin === 1 ? "utils.minuteAgo" : "utils.minutesAgo";
        var mVal = typeof t === "function" ? t(mKey, { n: diffMin }) : mKey;
        if (mVal !== mKey) return mVal;
        return diffMin + (diffMin === 1 ? " minute ago" : " minutes ago");
      }
      if (diffHour < 24) {
        var hKey = diffHour === 1 ? "utils.hourAgo" : "utils.hoursAgo";
        var hVal = typeof t === "function" ? t(hKey, { n: diffHour }) : hKey;
        if (hVal !== hKey) return hVal;
        return diffHour + (diffHour === 1 ? " hour ago" : " hours ago");
      }

      // Older than 24h — show date, omit year if same year
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: d.getFullYear() !== now.getFullYear() ? "numeric" : undefined,
      });
    }

    case "iso":
      return d.toISOString();

    default:
      return d.toLocaleString();
  }
}

/**
 * Format a date with both date and time parts (convenience for the common
 * "short date + time" pattern used in suggestions-admin, maint-sched, etc.).
 *
 * @param {string|number|Date} value
 * @returns {string} e.g. "Mar 29, 2026 2:15 PM"
 */
function formatDateTime(value) {
  return formatDate(value, "long");
}

/**
 * Format an ISO date string to user's local timezone (used by maint-sched).
 * Equivalent to formatDate(value, "long") but returns "N/A" for missing values.
 *
 * @param {string} isoStr
 * @returns {string}
 */
function formatLocal(isoStr) {
  if (!isoStr) {
    if (typeof t === "function") {
      var v = t("common.na");
      if (v && v !== "common.na") return v;
    }
    return "N/A";
  }
  return formatDate(isoStr, "long");
}

// ============================================
// Operation Polling
// ============================================

/**
 * Poll an operation endpoint until it completes or fails.
 *
 * @param {string} statusUrl - URL to poll for status (GET)
 * @param {object} callbacks
 * @param {Function} [callbacks.onProgress]  - called with status object on each poll
 * @param {Function} [callbacks.onComplete]  - called with final status when done
 * @param {Function} [callbacks.onError]     - called with error message on failure
 * @param {object} [options]
 * @param {number} [options.interval=1000]     - ms between polls
 * @param {number} [options.maxErrors=30]      - consecutive errors before giving up
 * @param {number} [options.timeout=0]         - max total ms (0 = no timeout)
 * @returns {{ stop: Function }} - call stop() to cancel polling
 */
function pollOperation(statusUrl, callbacks, options) {
  callbacks = callbacks || {};
  options = options || {};
  var interval = options.interval || 1000;
  var maxErrors = options.maxErrors || 30;
  var timeout = options.timeout || 0;
  var errorCount = 0;
  var stopped = false;
  var startTime = Date.now();
  var timer = null;

  function poll() {
    if (stopped) return;

    if (timeout > 0 && Date.now() - startTime > timeout) {
      stopped = true;
      if (callbacks.onError) {
        callbacks.onError("Operation timed out after " + Math.round(timeout / 1000) + "s");
      }
      return;
    }

    fetch(statusUrl, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) {
          errorCount++;
          if (errorCount >= maxErrors) {
            stopped = true;
            if (callbacks.onError) {
              callbacks.onError("Lost connection after " + maxErrors + " consecutive errors");
            }
            return;
          }
          timer = setTimeout(poll, interval);
          return res; // don't process
        }
        errorCount = 0;
        return res.json();
      })
      .then(function (status) {
        if (stopped || !status) return;

        if (callbacks.onProgress) {
          callbacks.onProgress(status);
        }

        // Check for completion — "running" and "pending" are active states
        var state = status.state || status.status || "";
        var isRunning = status.running === true ||
          state === "running" || state === "pending";

        if (!isRunning) {
          stopped = true;
          if (callbacks.onComplete) {
            callbacks.onComplete(status);
          }
          return;
        }

        timer = setTimeout(poll, interval);
      })
      .catch(function (err) {
        errorCount++;
        if (errorCount >= maxErrors) {
          stopped = true;
          if (callbacks.onError) {
            callbacks.onError("Polling failed: " + err.message);
          }
          return;
        }
        timer = setTimeout(poll, interval);
      });
  }

  poll();

  return {
    stop: function () {
      stopped = true;
      if (timer) clearTimeout(timer);
    },
  };
}

// ============================================
// Auth State Check
// ============================================

/**
 * Check the current authentication state.
 *
 * @returns {Promise<{ auth_enabled: boolean, user: object|null, guest: boolean }>}
 */
async function checkAuthStatus() {
  try {
    var response = await fetch("/auth/status", { credentials: "same-origin" });
    if (response.ok) {
      return await response.json();
    }
  } catch (_e) {
    // Auth endpoint not available
  }
  return { auth_enabled: false, user: null, guest: false };
}
