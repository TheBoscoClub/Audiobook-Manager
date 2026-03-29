/**
 * Shared Knife Switch — reusable dismiss control with electrical arc + sound.
 *
 * Creates a compact SVG knife switch with Web Audio synthesized bzzzt + clunk.
 * Two sizes: "full" (38px tall, for standalone panels) and "compact" (22px, inline).
 *
 * Usage:
 *   var sw = createKnifeSwitch({ size: "compact", onDismiss: function() { ... } });
 *   container.appendChild(sw);
 */
(function () {
  "use strict";

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

  function playBzzzt() {
    var ctx = getAudioCtx();
    if (!ctx) return;
    var dur = 0.07;
    var buf = ctx.createBuffer(1, ctx.sampleRate * dur, ctx.sampleRate);
    var d = buf.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * 0.25;
    var src = ctx.createBufferSource();
    src.buffer = buf;
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 800;
    bp.Q.value = 2;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.35, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + dur);
    src.connect(bp);
    bp.connect(g);
    g.connect(ctx.destination);
    src.start();
  }

  function playClunk() {
    var ctx = getAudioCtx();
    if (!ctx) return;
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = 100;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.4, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.12);
    osc.connect(g);
    g.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.12);
  }

  /**
   * Create a knife switch button element.
   * @param {Object} opts
   * @param {string}   [opts.size="compact"]  "compact" (22px) or "full" (38px)
   * @param {string}   [opts.title]           Tooltip text
   * @param {string}   [opts.label]           Optional text label beside the switch
   * @param {Function} opts.onDismiss         Callback fired after the throw animation
   * @param {number}   [opts.delay=350]       ms before onDismiss fires (animation time)
   * @returns {HTMLElement}
   */
  function createKnifeSwitch(opts) {
    opts = opts || {};
    var size = opts.size || "compact";
    var delay = opts.delay != null ? opts.delay : 350;
    var isCompact = size === "compact";

    var wrap = document.createElement("button");
    wrap.className = "knife-switch-universal" + (isCompact ? " ks-compact" : " ks-full");
    wrap.setAttribute("role", "switch");
    wrap.setAttribute("aria-checked", "true");
    wrap.setAttribute("title", opts.title || "Dismiss");

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 28 44");
    svg.setAttribute("aria-hidden", "true");

    // Jaw contacts
    var jawL = document.createElementNS(svgNS, "rect");
    jawL.setAttribute("x", "9"); jawL.setAttribute("y", "2");
    jawL.setAttribute("width", "3"); jawL.setAttribute("height", "12");
    jawL.setAttribute("rx", "1"); jawL.setAttribute("fill", "#B8860B");
    svg.appendChild(jawL);

    var jawR = document.createElementNS(svgNS, "rect");
    jawR.setAttribute("x", "16"); jawR.setAttribute("y", "2");
    jawR.setAttribute("width", "3"); jawR.setAttribute("height", "12");
    jawR.setAttribute("rx", "1"); jawR.setAttribute("fill", "#B8860B");
    svg.appendChild(jawR);

    // Blade
    var blade = document.createElementNS(svgNS, "rect");
    blade.setAttribute("x", "12"); blade.setAttribute("y", "8");
    blade.setAttribute("width", "4"); blade.setAttribute("height", "22");
    blade.setAttribute("rx", "1"); blade.setAttribute("class", "ks-blade");
    svg.appendChild(blade);

    // Handle
    var handleEl = document.createElementNS(svgNS, "rect");
    handleEl.setAttribute("x", "10"); handleEl.setAttribute("y", "28");
    handleEl.setAttribute("width", "8"); handleEl.setAttribute("height", "10");
    handleEl.setAttribute("rx", "2"); handleEl.setAttribute("fill", "#8B1A1A");
    svg.appendChild(handleEl);

    // Pivot
    var pivot = document.createElementNS(svgNS, "circle");
    pivot.setAttribute("cx", "14"); pivot.setAttribute("cy", "30");
    pivot.setAttribute("r", "2"); pivot.setAttribute("fill", "#666");
    svg.appendChild(pivot);

    // Arc flash (hidden by default, shown on throw)
    var arc = document.createElementNS(svgNS, "circle");
    arc.setAttribute("cx", "14"); arc.setAttribute("cy", "14");
    arc.setAttribute("r", "5"); arc.setAttribute("class", "ks-arc");
    svg.appendChild(arc);

    wrap.appendChild(svg);

    if (opts.label) {
      var lbl = document.createElement("span");
      lbl.className = "ks-label";
      lbl.textContent = opts.label;
      wrap.appendChild(lbl);
    }

    var thrown = false;
    wrap.addEventListener("click", function (e) {
      e.stopPropagation();
      if (thrown) return;
      thrown = true;
      wrap.setAttribute("aria-checked", "false");
      wrap.classList.add("ks-thrown");

      playBzzzt();
      setTimeout(playClunk, 70);

      if (typeof opts.onDismiss === "function") {
        setTimeout(opts.onDismiss, delay);
      }
    });

    return wrap;
  }

  // Expose globally
  window.createKnifeSwitch = createKnifeSwitch;
})();
