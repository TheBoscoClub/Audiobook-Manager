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
    return fetch("/api/i18n/" + encodeURIComponent(locale))
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
    catalog: function () { return catalog; }
  };
})();
