/**
 * i18n — Frontend internationalization loader.
 *
 * Loads JSON translation catalogs from /api/i18n/<locale> and provides
 * a global t(key) function for string lookup. Works in both the shell
 * (parent frame) and content pages (iframes) via localStorage.
 *
 * Usage:
 *   <script src="js/i18n.js"></script>
 *   ...
 *   t("shell.account")  // → "Account" or "账户"
 *
 * Elements with data-i18n="key" are auto-translated on load and locale change.
 * Elements with data-i18n-placeholder="key" get their placeholder translated.
 * Elements with data-i18n-title="key" get their title attribute translated.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "audiobooks_locale";
  var CATALOG_KEY = "audiobooks_i18n_catalog";
  var DEFAULT_LOCALE = "en";
  var catalog = {};
  var currentLocale = DEFAULT_LOCALE;

  // ── Core translation function ──

  function t(key, params) {
    var val = catalog[key] || key;
    if (params) {
      Object.keys(params).forEach(function (k) {
        var placeholder = "{" + k + "}";
        while (val.indexOf(placeholder) !== -1) {
          val = val.replace(placeholder, params[k]);
        }
      });
    }
    return val;
  }

  // ── Locale management ──

  function getLocale() {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_LOCALE;
  }

  function setLocale(locale) {
    currentLocale = locale;
    localStorage.setItem(STORAGE_KEY, locale);
    return loadCatalog(locale).then(function () {
      applyTranslations();
      updateHtmlLang();
      document.dispatchEvent(new CustomEvent("localeChanged", { detail: { locale: locale } }));
    });
  }

  function updateHtmlLang() {
    document.documentElement.lang = currentLocale;
    if (currentLocale.startsWith("zh") || currentLocale.startsWith("ja") || currentLocale.startsWith("ko")) {
      document.documentElement.classList.add("cjk");
    } else {
      document.documentElement.classList.remove("cjk");
    }
  }

  // ── Catalog loading ──

  function loadCatalog(locale) {
    // Cache-bust by the day: catalogs change with each release but don't
    // need per-request freshness. Without this, Cloudflare aggressively
    // caches /api/i18n/<locale> and serves stale keys for up to an hour
    // after a deploy.
    var cb = Math.floor(Date.now() / 3600000);
    return fetch("/api/i18n/" + encodeURIComponent(locale) + "?cb=" + cb, {
      cache: "no-store",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load catalog for " + locale);
        return res.json();
      })
      .then(function (data) {
        catalog = data;
        try {
          localStorage.setItem(CATALOG_KEY, JSON.stringify(data));
        } catch (e) {
          // localStorage full — non-fatal
        }
      })
      .catch(function () {
        var cached = localStorage.getItem(CATALOG_KEY);
        if (cached) {
          try { catalog = JSON.parse(cached); } catch (e) { catalog = {}; }
        }
      });
  }

  // ── DOM translation ──

  function applyTranslations(root) {
    root = root || document;

    // data-i18n → textContent
    root.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      var translated = t(key);
      if (translated !== key) el.textContent = translated;
    });

    // data-i18n-placeholder → placeholder attribute
    root.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-placeholder");
      var translated = t(key);
      if (translated !== key) el.placeholder = translated;
    });

    // data-i18n-title → title attribute
    root.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-title");
      var translated = t(key);
      if (translated !== key) el.title = translated;
    });

    // data-i18n-label → label attribute (for optgroup elements)
    root.querySelectorAll("[data-i18n-label]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-label");
      var translated = t(key);
      if (translated !== key) el.label = translated;
    });
  }

  // ── Initialize ──

  currentLocale = getLocale();

  var cached = localStorage.getItem(CATALOG_KEY);
  if (cached) {
    try { catalog = JSON.parse(cached); } catch (e) { /* ignore */ }
  }

  loadCatalog(currentLocale).then(function () {
    applyTranslations();
    updateHtmlLang();
  });

  function syncLocaleToServer(locale) {
    if (typeof api !== "undefined" && api.patch) {
      api.patch("/api/user/preferences", { locale: locale }, { toast: false, keepalive: true }).catch(function () {});
    }
  }

  // ── Cross-frame locale sync ──
  // When this page is inside an iframe, the parent shell sends a
  // postMessage on locale change so we can re-render without a refresh.
  window.addEventListener("message", function (event) {
    if (event.origin !== window.location.origin) return;
    var msg = event.data;
    if (msg && msg.type === "localeChanged" && msg.locale) {
      setLocale(msg.locale);
    }
  });

  // ── Generic string translation via source-hash cache ──
  // Short SHA-256 prefix (hex, 16 chars) — must match backend _hash_source.
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
    // Last-resort fallback: use the string itself (cache miss, but functional).
    return text;
  }

  function translateStrings(strings) {
    var locale = getLocale();
    if (locale === "en" || !strings || !strings.length) {
      return Promise.resolve({});
    }
    return fetch("/api/translations/strings", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locale: locale, strings: strings }),
    })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; });
  }

  /**
   * Translate the visible text of a NodeList / array of elements in place.
   * Preserves child element nodes (e.g. <span class="section-icon">) by
   * only replacing the last significant text node's value. Skips elements
   * that are empty or already tagged with data-i18n (those use the catalog).
   */
  async function translateElements(elements) {
    var locale = getLocale();
    if (locale === "en" || !elements || !elements.length) return;

    var list = Array.prototype.slice.call(elements);
    var items = [];
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (!el || el.hasAttribute("data-i18n")) continue;
      // Extract only direct text-node content, ignoring child elements
      // like <span class="section-icon">. This keeps decorative icons out
      // of the string sent for translation AND out of the replacement.
      var hasChildElement = false;
      var textPieces = [];
      for (var k = 0; k < el.childNodes.length; k++) {
        var cn = el.childNodes[k];
        if (cn.nodeType === 3) {
          textPieces.push(cn.nodeValue || "");
        } else if (cn.nodeType === 1) {
          hasChildElement = true;
        }
      }
      var text = textPieces.join(" ").replace(/\s+/g, " ").trim();
      // If the element has no child elements and is a pure-text element
      // (paragraph, link, li), fall back to full textContent so we capture
      // inline formatting correctly.
      if (!text && !hasChildElement) {
        text = (el.textContent || "").replace(/\s+/g, " ").trim();
      }
      if (!text) continue;
      var h = await hashSource(text);
      items.push({ el: el, text: text, hash: h, hasChildElement: hasChildElement });
    }
    if (!items.length) return;

    var unique = [];
    var seenText = {};
    items.forEach(function (it) {
      if (!seenText[it.hash]) {
        seenText[it.hash] = true;
        unique.push(it.text);
      }
    });

    var map = await translateStrings(unique);
    if (!map) return;

    items.forEach(function (it) {
      var translated = map[it.hash];
      if (!translated) return;
      // Find the last non-empty text node and replace it. Keeps icon spans intact.
      var lastText = null;
      for (var j = it.el.childNodes.length - 1; j >= 0; j--) {
        var n = it.el.childNodes[j];
        if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim()) {
          lastText = n;
          break;
        }
      }
      if (lastText) {
        // Preserve a leading space if the original had one (e.g. after an icon span).
        var leading = /^\s/.test(lastText.nodeValue) ? " " : "";
        lastText.nodeValue = leading + translated;
      } else {
        it.el.textContent = translated;
      }
    });
  }

  // ── Public API ──

  window.t = t;
  window.i18n = {
    t: t,
    getLocale: getLocale,
    setLocale: function (locale) {
      return setLocale(locale).then(function () {
        syncLocaleToServer(locale);
      });
    },
    applyTranslations: applyTranslations,
    translateStrings: translateStrings,
    translateElements: translateElements,
    catalog: function () { return catalog; }
  };
})();
