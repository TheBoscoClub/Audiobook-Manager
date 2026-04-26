/**
 * version-poller — detect a deploy and reload the page on user
 * acknowledgement.
 *
 * Polls /api/system/version every POLL_INTERVAL_MS. When the response's
 * `version` field differs from the version captured at page load, render
 * an Art Deco "New version available" banner pinned above the player.
 * **Either** clicking Reload **or** clicking the close (×) button
 * triggers `location.reload()`. There is intentionally no "stay on the
 * old version" escape hatch — the user explicitly chose to remove that
 * choice (2026-04-25): once a deploy is detected, any acknowledgement
 * means refresh.
 *
 * Why this exists
 * ----------------
 * The HTML is `Cache-Control: no-cache` and `scripts/bump-cachebust.sh`
 * rotates a `?v=<epoch>` stamp on every CSS/JS reference at deploy time,
 * so any *fresh* page load picks up new assets automatically. But a tab
 * that's already open keeps running the old code until the user reloads
 * — which is how Qing's iPhone Chrome ran the broken pre-v8.3.8.9 CSS
 * for hours after the prod hot-patch was live.
 *
 * Behavior
 * --------
 * - Inert until the first version request resolves (~immediate).
 * - Polls only while the document is visible (Page Visibility API), so
 *   backgrounded tabs don't burn battery or hammer the API.
 * - Fail-soft: network errors during a poll are swallowed; the next
 *   tick tries again.
 * - Banner appears at most once per page-load (we capture the initial
 *   version once and any later mismatch shows the banner; once shown,
 *   we don't stack a second one).
 * - Reload uses `location.reload()` — no `true` argument (deprecated;
 *   no-op in modern browsers anyway). The cachebust stamps in the new
 *   HTML do the cache-busting work.
 * - Banner re-renders on `localeChanged` so a mid-session EN↔中文 flip
 *   updates its text live.
 */
(function () {
  "use strict";

  var POLL_INTERVAL_MS = 60_000;
  var initialVersion = null;
  var bannerEl = null;
  var bannerVersion = null; // version the banner is currently advertising
  var pollTimer = null;

  function fetchVersion() {
    return fetch("/api/system/version", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (j) {
        return (j && j.version) || null;
      });
  }

  function tt(key, fallback) {
    return typeof window.t === "function" ? window.t(key) || fallback : fallback;
  }

  function reloadNow() {
    // Stop polling so a slow reload doesn't refire the banner against
    // a tab that's already navigating away.
    if (pollTimer) clearInterval(pollTimer);

    // INTENTIONALLY a plain `location.reload()` — NO cookie wipe, NO
    // storage wipe, NO `Clear-Site-Data: "*"` header from the server.
    //
    // The pieces that need to be fresh after a deploy are:
    //   - The HTML (so the user sees new `?v=...` cachebust stamps).
    //     Already handled: HTML is served `Cache-Control: no-cache`,
    //     so the browser revalidates on every load.
    //   - The CSS/JS assets. Already handled: each deploy rotates the
    //     `?v=` query string in shell.html (`scripts/bump-cachebust.sh`),
    //     and the browser treats a different URL as a cache miss.
    //
    // What MUST stay untouched: the user's auth cookie, their accessibility
    // preferences in localStorage, their playback position cached client-
    // side. Wiping those (via `Clear-Site-Data: "cookies", "storage"` or
    // `"*"`) would log them out and lose state — not what an "update
    // available, please reload" prompt should ever do.
    //
    // `location.reload()` with no argument is the right tool: it triggers
    // a normal navigation that respects the cache headers we've already
    // set up. The deprecated `location.reload(true)` would force-bypass
    // the cache, which is unnecessary given the cachebust strategy and
    // a no-op in modern browsers anyway.
    location.reload();
  }

  function buildBanner(_newVersion) {
    var b = document.createElement("div");
    b.id = "version-update-banner";
    b.className = "version-update-banner";
    b.setAttribute("role", "status");
    b.setAttribute("aria-live", "polite");

    var msg = document.createElement("span");
    msg.className = "vub-message";
    msg.textContent = tt("update.available", "New version available — reload to apply");

    var reloadBtn = document.createElement("button");
    reloadBtn.type = "button";
    reloadBtn.className = "vub-reload";
    reloadBtn.textContent = tt("update.reload", "Reload");
    reloadBtn.addEventListener("click", reloadNow);

    // The close (×) button intentionally also reloads. Once a deploy is
    // detected the user has no "stay on old version" option — every
    // acknowledgement (action button OR close button) refreshes. Per
    // explicit decision 2026-04-25 to remove the escape hatch.
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "vub-dismiss";
    closeBtn.setAttribute(
      "aria-label",
      tt("update.dismiss", "Close — reloads to apply update"),
    );
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", reloadNow);

    b.appendChild(msg);
    b.appendChild(reloadBtn);
    b.appendChild(closeBtn);
    return b;
  }

  function showBanner(newVersion) {
    // Already showing a banner? Update its text in case version changed
    // again, but don't stack banners.
    if (bannerEl && document.body.contains(bannerEl)) return;
    bannerEl = buildBanner(newVersion);
    bannerVersion = newVersion;
    document.body.appendChild(bannerEl);
  }

  // Re-localize the banner if it's visible and the user changes the
  // locale via the shell's locale switcher. Without this the banner
  // text stays in whatever language was active when it first rendered,
  // which would be wrong for a user who flipped EN→中文 mid-session.
  function relocalizeBanner() {
    if (!bannerEl || !document.body.contains(bannerEl) || !bannerVersion) return;
    var fresh = buildBanner(bannerVersion);
    bannerEl.replaceWith(fresh);
    bannerEl = fresh;
  }
  document.addEventListener("localeChanged", relocalizeBanner);

  function shouldPromptFor(newVersion) {
    if (!newVersion || !initialVersion || newVersion === initialVersion) {
      return false;
    }
    // Same version the user already dismissed? Skip until backoff expires.
    if (
      dismissedFor === newVersion &&
      Date.now() - dismissedAt < BACKOFF_AFTER_DISMISS_MS
    ) {
      return false;
    }
    return true;
  }

  function poll() {
    if (document.hidden) return; // Page Visibility API: defer when backgrounded
    fetchVersion()
      .then(function (v) {
        if (shouldPromptFor(v)) showBanner(v);
      })
      .catch(function () {
        /* swallow network blips; next tick retries */
      });
  }

  function start() {
    fetchVersion()
      .then(function (v) {
        initialVersion = v;
        // Begin polling. setInterval cadence is fine — there's no
        // rush to detect a new version sub-minute.
        pollTimer = setInterval(poll, POLL_INTERVAL_MS);
        // Also poll immediately on visibility-change so a returning tab
        // gets the up-to-date version without waiting up to 60s.
        document.addEventListener("visibilitychange", function () {
          if (!document.hidden) poll();
        });
      })
      .catch(function () {
        /* If the very first version request fails, give up silently —
           we can't tell the user about updates we can't detect. The
           cachebust on next manual reload still saves us. */
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
