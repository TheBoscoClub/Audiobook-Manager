/**
 * Accessibility quick-panel (v8).
 *
 * Loads user preferences from the API, applies them as CSS custom properties
 * and body classes, and saves changes on interaction.  Falls back to defaults
 * for unauthenticated users (stored in localStorage only).
 */
(function () {
    'use strict';

    var DEFAULTS = {
        font_size: '16',
        line_spacing: '1.5',
        contrast: 'normal',
        bg_opacity: '100',
        color_temperature: 'neutral',
        reduce_animations: 'false',
        high_contrast: 'false'
    };

    var prefs = Object.assign({}, DEFAULTS);
    var isAuthenticated = false;
    var panel = document.getElementById('a11y-panel');
    var btn = document.getElementById('accessibility-btn');

    // ── Panel toggle ────────────────────────────────────────────────────────

    function togglePanel() {
        var showing = panel.hidden;
        panel.hidden = !showing;
        btn.classList.toggle('active', showing);
    }

    btn.addEventListener('click', function (e) {
        e.stopPropagation();
        togglePanel();
    });

    document.getElementById('a11y-panel-close').addEventListener('click', function () {
        panel.hidden = true;
        btn.classList.remove('active');
    });

    // Close on outside click
    document.addEventListener('click', function (e) {
        if (!panel.hidden && !panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
            panel.hidden = true;
            btn.classList.remove('active');
        }
    });

    // ── Apply preferences to DOM ────────────────────────────────────────────

    function applyPrefs() {
        var root = document.documentElement;
        var body = document.body;

        // Font size
        root.style.setProperty('--user-font-size', prefs.font_size + 'px');

        // Line spacing
        root.style.setProperty('--user-line-spacing', prefs.line_spacing);

        // Background opacity
        var opacity = parseInt(prefs.bg_opacity, 10) / 100;
        root.style.setProperty('--user-bg-opacity', opacity);

        // Reduce animations
        body.classList.toggle('a11y-reduce-motion', prefs.reduce_animations === 'true');

        // High contrast
        body.classList.toggle('a11y-high-contrast', prefs.high_contrast === 'true');

        // Color temperature
        body.classList.remove('a11y-temp-warm', 'a11y-temp-cool');
        if (prefs.color_temperature === 'warm') body.classList.add('a11y-temp-warm');
        if (prefs.color_temperature === 'cool') body.classList.add('a11y-temp-cool');

        // Contrast level
        body.classList.remove('a11y-contrast-medium', 'a11y-contrast-high');
        if (prefs.contrast === 'medium') body.classList.add('a11y-contrast-medium');
        if (prefs.contrast === 'high') body.classList.add('a11y-contrast-high');

        // Propagate to iframe
        var iframe = document.getElementById('content-frame');
        if (iframe && iframe.contentDocument) {
            try {
                var iRoot = iframe.contentDocument.documentElement;
                var iBody = iframe.contentDocument.body;
                iRoot.style.setProperty('--user-font-size', prefs.font_size + 'px');
                iRoot.style.setProperty('--user-line-spacing', prefs.line_spacing);
                iRoot.style.setProperty('--user-bg-opacity', opacity);
                if (iBody) {
                    iBody.classList.toggle('a11y-reduce-motion', prefs.reduce_animations === 'true');
                    iBody.classList.toggle('a11y-high-contrast', prefs.high_contrast === 'true');
                    iBody.classList.remove('a11y-temp-warm', 'a11y-temp-cool');
                    if (prefs.color_temperature === 'warm') iBody.classList.add('a11y-temp-warm');
                    if (prefs.color_temperature === 'cool') iBody.classList.add('a11y-temp-cool');
                    iBody.classList.remove('a11y-contrast-medium', 'a11y-contrast-high');
                    if (prefs.contrast === 'medium') iBody.classList.add('a11y-contrast-medium');
                    if (prefs.contrast === 'high') iBody.classList.add('a11y-contrast-high');
                }
            } catch (e) { /* cross-origin iframe — ignore */ }
        }
    }

    // ── Sync UI controls to current prefs ───────────────────────────────────

    function syncUI() {
        // Segmented buttons
        document.querySelectorAll('.a11y-segmented').forEach(function (group) {
            var key = group.dataset.key;
            var val = prefs[key] || DEFAULTS[key];
            group.querySelectorAll('button').forEach(function (b) {
                b.classList.toggle('active', b.dataset.value === val);
            });
        });

        // Range slider
        var slider = document.getElementById('a11y-bg-opacity');
        slider.value = prefs.bg_opacity || DEFAULTS.bg_opacity;
        document.getElementById('a11y-bg-opacity-val').textContent = slider.value + '%';

        // Checkboxes
        document.getElementById('a11y-reduce-motion').checked = prefs.reduce_animations === 'true';
        document.getElementById('a11y-high-contrast').checked = prefs.high_contrast === 'true';
    }

    // ── Save a single preference ────────────────────────────────────────────

    function savePref(key, value) {
        prefs[key] = value;
        applyPrefs();

        if (isAuthenticated) {
            var body = {};
            body[key] = value;
            api.patch('/api/user/preferences', body, { toast: false }).catch(function () {});
        }

        // Also save to localStorage as fallback
        try { localStorage.setItem('a11y_' + key, value); } catch (e) {}
    }

    // ── Wire up controls ────────────────────────────────────────────────────

    // Segmented buttons
    document.querySelectorAll('.a11y-segmented').forEach(function (group) {
        group.addEventListener('click', function (e) {
            var target = e.target.closest('button');
            if (!target) return;
            savePref(group.dataset.key, target.dataset.value);
            group.querySelectorAll('button').forEach(function (b) {
                b.classList.toggle('active', b === target);
            });
        });
    });

    // Range slider
    var opacitySlider = document.getElementById('a11y-bg-opacity');
    opacitySlider.addEventListener('input', function () {
        document.getElementById('a11y-bg-opacity-val').textContent = this.value + '%';
        prefs.bg_opacity = this.value;
        applyPrefs();
    });
    opacitySlider.addEventListener('change', function () {
        savePref('bg_opacity', this.value);
    });

    // Checkboxes
    document.getElementById('a11y-reduce-motion').addEventListener('change', function () {
        savePref('reduce_animations', this.checked ? 'true' : 'false');
    });
    document.getElementById('a11y-high-contrast').addEventListener('change', function () {
        savePref('high_contrast', this.checked ? 'true' : 'false');
    });

    // Reset button
    document.getElementById('a11y-reset-btn').addEventListener('click', function () {
        Object.assign(prefs, DEFAULTS);
        syncUI();
        applyPrefs();

        if (isAuthenticated) {
            // Reset accessibility keys on server
            var resetBody = {};
            Object.keys(DEFAULTS).forEach(function (k) { resetBody[k] = DEFAULTS[k]; });
            api.patch('/api/user/preferences', resetBody, { toast: false }).catch(function () {});
        }

        // Clear localStorage fallbacks
        Object.keys(DEFAULTS).forEach(function (k) {
            try { localStorage.removeItem('a11y_' + k); } catch (e) {}
        });
    });

    // ── Load preferences ────────────────────────────────────────────────────

    function loadFromLocalStorage() {
        Object.keys(DEFAULTS).forEach(function (k) {
            try {
                var val = localStorage.getItem('a11y_' + k);
                if (val !== null) prefs[k] = val;
            } catch (e) {}
        });
    }

    function init() {
        // Try loading from API first
        api.get('/api/user/preferences', { toast: false })
            .then(function (data) {
                isAuthenticated = true;
                // Merge API response into prefs (only accessibility keys)
                Object.keys(DEFAULTS).forEach(function (k) {
                    if (data[k] !== undefined) prefs[k] = data[k];
                });
                syncUI();
                applyPrefs();
            })
            .catch(function () {
                // Not authenticated — use localStorage
                isAuthenticated = false;
                loadFromLocalStorage();
                syncUI();
                applyPrefs();
            });
    }

    // Re-apply when iframe loads new content
    var iframe = document.getElementById('content-frame');
    if (iframe) {
        iframe.addEventListener('load', function () {
            applyPrefs();
        });
    }

    init();
})();
