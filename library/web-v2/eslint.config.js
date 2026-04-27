// ESLint flat config for library/web-v2/js/ — v8.3.9 baseline.
// Keeps existing rules permissive: warn on unused vars, no-console allowed
// (web-v2 logs through console intentionally for in-page debugging).
//
// web-v2 loads JS via classic <script> tags — every module shares the global
// scope. The globals list below covers the standard browser APIs PLUS the
// project-internal helpers that one file declares and another consumes.
// Update this list when a new cross-file global is introduced.
import js from "@eslint/js";

export default [
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: "latest",
      // sourceType=script matches reality (classic <script> tags). Setting
      // this to "module" caused 378 false-positive no-undef errors because
      // ESLint expects every cross-file reference to be imported.
      sourceType: "script",
      globals: {
        // ── Browser DOM/event globals ───────────────────────────────
        window: "readonly",
        document: "readonly",
        navigator: "readonly",
        location: "readonly",
        history: "readonly",
        screen: "readonly",
        getComputedStyle: "readonly",
        Event: "readonly",
        CustomEvent: "readonly",
        EventTarget: "readonly",
        MutationObserver: "readonly",
        IntersectionObserver: "readonly",
        ResizeObserver: "readonly",
        // ── Network/IO ─────────────────────────────────────────────
        fetch: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        FormData: "readonly",
        Headers: "readonly",
        Request: "readonly",
        Response: "readonly",
        WebSocket: "readonly",
        AbortController: "readonly",
        AbortSignal: "readonly",
        // ── Storage/IO ─────────────────────────────────────────────
        localStorage: "readonly",
        sessionStorage: "readonly",
        indexedDB: "readonly",
        caches: "readonly",
        // ── Console/timers ─────────────────────────────────────────
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        // ── Media/asset constructors ───────────────────────────────
        Audio: "readonly",
        Image: "readonly",
        MediaSource: "readonly",
        MediaMetadata: "readonly",
        SourceBuffer: "readonly",
        // ── Files/blobs ────────────────────────────────────────────
        FileReader: "readonly",
        File: "readonly",
        Blob: "readonly",
        // ── Encoding/crypto ────────────────────────────────────────
        atob: "readonly",
        btoa: "readonly",
        crypto: "readonly",
        TextEncoder: "readonly",
        TextDecoder: "readonly",
        PublicKeyCredential: "readonly",
        // ── Internationalization ───────────────────────────────────
        Intl: "readonly",
        // ── Dialog/CSS ─────────────────────────────────────────────
        alert: "readonly",
        confirm: "readonly",
        prompt: "readonly",
        CSS: "readonly",
        // ── Node-style export check (webauthn.js gates on `typeof module
        //     !== "undefined"` so the same file can be loaded as a <script>
        //     in the browser AND import-ed in a Node test environment).
        module: "readonly",
        // ── Project-internal cross-file globals (declared in one
        //     <script>, consumed by another). Keep alphabetical for
        //     easy diffing. If a new cross-file helper is added,
        //     extend this list. ─────────────────────────────────────
        api: "readonly",                   // js/api.js — central fetch wrapper
        checkAuthStatus: "readonly",       // js/auth.js
        createKnifeSwitch: "readonly",     // js/knife-switch.js
        formatDate: "readonly",            // js/utils.js
        formatLocal: "readonly",           // js/utils.js
        formatRelativeTime: "readonly",    // js/utils.js
        i18n: "readonly",                  // js/i18n.js — translation API
        initMarquee: "readonly",           // js/marquee.js
        // `library` is `let library` declared in js/library.js and assigned
        // on DOMContentLoaded — it MUST be writable so eslint doesn't flag
        // the assignment as no-global-assign / no-redeclare.
        library: "writable",               // js/library.js — page-scoped library namespace
        SessionPersistence: "readonly",    // js/session-persistence.js
        shellPlay: "readonly",             // js/shell.js
        showToast: "readonly",             // js/utils.js
        t: "readonly",                     // i18n shorthand
      },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-console": "off",
      // The codebase has many empty catch blocks that intentionally swallow
      // non-fatal errors (telemetry / DOM-edge-cases). Downgrade to warn so
      // CI surfaces them without blocking, and the team can sweep them
      // incrementally.
      "no-empty": "warn",
      // Many catch (e) sites rethrow without a `cause` for legacy reasons.
      // Tracked for sweep; not a CI gate today.
      "preserve-caught-error": "warn",
      // The cross-file globals declared above (api, library, etc.) are also
      // declared by `function`/`let` in the file that owns them. With the
      // default `builtinGlobals: true`, eslint flags those local declarations
      // as redeclaring the global. `builtinGlobals: false` lets the owning
      // file declare locally while consumers pick up the global — the actual
      // pattern this codebase uses (classic <script> tags, no ES module
      // imports). Genuine within-file `let foo; let foo;` typos are still
      // caught.
      "no-redeclare": ["error", { builtinGlobals: false }],
    },
  },
];
