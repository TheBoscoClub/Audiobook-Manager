/**
 * Tutorial Engine — Lightweight click-through guide
 * Highlights UI elements with a spotlight overlay and explanatory tooltips.
 * No external dependencies. Uses safe DOM methods (no innerHTML).
 */
class LibraryTutorial {
  constructor() {
    this.steps = [
      {
        target: ".library-stats",
        title: "Library Stats",
        description:
          "These numbers show the total volumes, hours of audio, authors, and narrators in the collection. They update as you filter.",
      },
      {
        target: "#search-input",
        title: "Search Bar",
        description:
          "Type here to search by title, author, or narrator. Results filter instantly as you type.",
      },
      {
        target: "#author-autocomplete",
        title: "Author Filter",
        description:
          "Filter books by a specific author. Use the letter group buttons (A-E, F-J, etc.) to narrow the list, or type to search.",
      },
      {
        target: "#narrator-autocomplete",
        title: "Narrator Filter",
        description:
          "Same as the author filter, but for narrators. Book counts appear next to each name.",
      },
      {
        target: "#sort-filter",
        title: "Sort Options",
        description:
          "Choose how books are ordered: by title, author, narrator, duration, date acquired, publication year, series, edition, or use grouped view to see books organized under collapsible author/narrator headers.",
      },
      {
        target: "#sidebar-toggle",
        title: "Collections",
        description:
          "Open the sidebar to browse curated collections \u2014 genres, series, and themes. Click a collection to filter the grid.",
      },
      {
        target: '.tab-btn[data-tab="my-library"]',
        title: "My Library",
        description:
          "Switch to My Library to see books you've listened to, downloaded, or have in progress. You can hide finished books using the checkboxes and Hide button, and restore them from the Hidden view. Your personal collection is tracked per-user across devices.",
      },
      {
        target: "#new-books-marquee",
        title: "New Books",
        description:
          "When new audiobooks are added, a scrolling marquee appears here announcing the new titles. Hover to pause, or click the dismiss button to hide it.",
        optional: true,
      },
      {
        target: ".book-card",
        title: "Book Cards",
        description:
          "Each card shows the cover, title, author, narrator, duration, and format. Click a card to start playing.",
      },
      {
        target: ".editions-badge, .book-card .supplement-badge",
        title: "Edition & Supplement Badges",
        description:
          "Some books have multiple editions (different narrators/formats) or supplemental material. Click the badge to expand and see all versions.",
        optional: true,
      },
      {
        target: ".btn-download",
        title: "Download for Offline",
        description:
          "The Library streams from its own server storage and cannot access files on your device. Use the Download button to save a book for offline listening in a local player like VLC or Smart AudioBook Player. See the Help page for app recommendations.",
        optional: true,
      },
      {
        target: "#audio-player",
        title: "Audio Player",
        description:
          "The player appears here when you play a book. Use play/pause, the progress bar, skip buttons, speed control, and volume.",
        fallback:
          "The audio player appears at the bottom of the screen when you play a book.",
      },
      {
        target: "#request-access-link",
        title: "Request Access",
        description:
          "Not a member yet? Click here to request an account. Once the admin approves your request, you'll receive an invitation to set up your login.",
        optional: true,
      },
      {
        target: "#login-link",
        title: "Sign In & Account",
        description:
          "If not signed in, use this link to sign in. Once logged in, your account button appears in the header bar above \u2014 click it to edit your profile, change your authentication method, contact the admin, or sign out.",
        optional: true,
      },
      {
        target: '.header-nav-left .nav-link[href="help.html"]',
        title: "Help Button",
        description:
          "You found it! Click this any time to come back to the User Guide or restart this tutorial.",
      },
    ];
    this.currentStep = 0;
    this.overlay = null;
    this.tooltip = null;
    this.active = false;
  }

  /**
   * Start the tutorial from step 0.
   */
  start() {
    if (this.active) return;
    this.active = true;
    this.currentStep = 0;
    this._createOverlay();
    this._showStep();
  }

  /**
   * End the tutorial and clean up.
   */
  end() {
    this.active = false;
    this._removeHighlight();
    if (this.overlay) {
      this.overlay.remove();
      this.overlay = null;
    }
    if (this.tooltip) {
      this.tooltip.remove();
      this.tooltip = null;
    }
    // Clean up URL param
    const url = new URL(window.location);
    if (url.searchParams.has("tutorial")) {
      url.searchParams.delete("tutorial");
      window.history.replaceState({}, "", url);
    }
  }

  /**
   * Navigate to a specific step.
   */
  goTo(stepIndex) {
    if (stepIndex < 0 || stepIndex >= this.steps.length) {
      this.end();
      return;
    }
    this._removeHighlight();
    this.currentStep = stepIndex;
    this._showStep();
  }

  // --- Private methods ---

  _createOverlay() {
    this.overlay = document.createElement("div");
    this.overlay.className = "tutorial-overlay";
    this.overlay.addEventListener("click", (e) => {
      // Clicking the overlay (outside tooltip) advances
      if (e.target === this.overlay) {
        this.goTo(this.currentStep + 1);
      }
    });
    document.body.appendChild(this.overlay);
  }

  _showStep() {
    const step = this.steps[this.currentStep];
    if (!step) {
      this.end();
      return;
    }

    // Try to find the target element (may be multiple selectors separated by comma)
    const selectors = step.target.split(",").map((s) => s.trim());
    let targetEl = null;
    for (const sel of selectors) {
      targetEl = document.querySelector(sel);
      if (targetEl) break;
    }

    // If target not found and step is optional, skip to next
    if (!targetEl && step.optional) {
      this.goTo(this.currentStep + 1);
      return;
    }

    // If target not found but has fallback text, show tooltip without spotlight
    if (!targetEl && step.fallback) {
      this._positionOverlayNoSpotlight();
      this._showTooltipCentered(step);
      return;
    }

    if (!targetEl) {
      // Skip missing required targets
      this.goTo(this.currentStep + 1);
      return;
    }

    // Scroll into view
    targetEl.scrollIntoView({ behavior: "smooth", block: "center" });

    // Wait for scroll to finish, then position
    setTimeout(() => {
      this._highlightElement(targetEl);
      this._positionOverlay(targetEl);
      this._showTooltip(step, targetEl);
    }, 400);
  }

  _highlightElement(el) {
    this._removeHighlight();
    el.classList.add("tutorial-highlight");
    this._highlightedEl = el;
  }

  _removeHighlight() {
    if (this._highlightedEl) {
      this._highlightedEl.classList.remove("tutorial-highlight");
      this._highlightedEl = null;
    }
  }

  _positionOverlay(targetEl) {
    const rect = targetEl.getBoundingClientRect();
    const pad = 8;
    const x = rect.left - pad;
    const y = rect.top - pad;
    const w = rect.width + pad * 2;
    const h = rect.height + pad * 2;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Create doughnut clip-path: outer rectangle minus inner cutout
    this.overlay.style.clipPath = `polygon(
            0 0, ${vw}px 0, ${vw}px ${vh}px, 0 ${vh}px, 0 0,
            ${x}px ${y}px, ${x}px ${y + h}px, ${x + w}px ${y + h}px, ${x + w}px ${y}px, ${x}px ${y}px
        )`;
  }

  _positionOverlayNoSpotlight() {
    this.overlay.style.clipPath = "";
  }

  _showTooltip(step, targetEl) {
    if (this.tooltip) this.tooltip.remove();

    this.tooltip = this._buildTooltip(step);
    document.body.appendChild(this.tooltip);

    // Position relative to target
    const rect = targetEl.getBoundingClientRect();
    const tooltipRect = this.tooltip.getBoundingClientRect();
    const margin = 16;

    let top, left;

    // Prefer below, then above, then center
    if (rect.bottom + margin + tooltipRect.height < window.innerHeight) {
      top = rect.bottom + margin;
    } else if (rect.top - margin - tooltipRect.height > 0) {
      top = rect.top - margin - tooltipRect.height;
    } else {
      top = Math.max(margin, window.innerHeight / 2 - tooltipRect.height / 2);
    }

    left = rect.left + rect.width / 2 - tooltipRect.width / 2;
    // Clamp to viewport
    left = Math.max(
      margin,
      Math.min(left, window.innerWidth - tooltipRect.width - margin),
    );

    this.tooltip.style.top = `${top}px`;
    this.tooltip.style.left = `${left}px`;
  }

  _showTooltipCentered(step) {
    if (this.tooltip) this.tooltip.remove();

    this.tooltip = this._buildTooltip(step, true);
    document.body.appendChild(this.tooltip);

    const tooltipRect = this.tooltip.getBoundingClientRect();
    this.tooltip.style.top = `${window.innerHeight / 2 - tooltipRect.height / 2}px`;
    this.tooltip.style.left = `${window.innerWidth / 2 - tooltipRect.width / 2}px`;
  }

  /**
   * Build tooltip using safe DOM methods (no innerHTML).
   */
  _buildTooltip(step, useFallback = false) {
    const tooltip = document.createElement("div");
    tooltip.className = "tutorial-tooltip";
    const total = this.steps.length;
    const current = this.currentStep + 1;

    // Step counter
    const counter = document.createElement("div");
    counter.className = "tutorial-step-counter";
    counter.textContent = `Step ${current} of ${total}`;
    tooltip.appendChild(counter);

    // Title
    const title = document.createElement("div");
    title.className = "tutorial-title";
    title.textContent = step.title;
    tooltip.appendChild(title);

    // Description
    const desc = document.createElement("div");
    desc.className = "tutorial-description";
    desc.textContent =
      useFallback && step.fallback ? step.fallback : step.description;
    tooltip.appendChild(desc);

    // Navigation
    const nav = document.createElement("div");
    nav.className = "tutorial-nav";

    if (this.currentStep > 0) {
      const backBtn = document.createElement("button");
      backBtn.className = "tutorial-btn tutorial-btn-back";
      backBtn.title = "Previous step";
      backBtn.textContent = "Back";
      backBtn.addEventListener("click", () => this.goTo(this.currentStep - 1));
      nav.appendChild(backBtn);
    }

    const nextBtn = document.createElement("button");
    nextBtn.className = "tutorial-btn tutorial-btn-next";
    nextBtn.title = current === total ? "Finish the tutorial" : "Next step";
    nextBtn.textContent = current === total ? "Finish" : "Next";
    nextBtn.addEventListener("click", () => this.goTo(this.currentStep + 1));
    nav.appendChild(nextBtn);

    const skipBtn = document.createElement("button");
    skipBtn.className = "tutorial-btn tutorial-btn-skip";
    skipBtn.title = "End the tutorial";
    skipBtn.textContent = "Skip";
    skipBtn.addEventListener("click", () => this.end());
    nav.appendChild(skipBtn);

    tooltip.appendChild(nav);
    return tooltip;
  }
}

// Auto-start if ?tutorial=1 is in the URL
document.addEventListener("DOMContentLoaded", () => {
  window.libraryTutorial = new LibraryTutorial();

  const params = new URLSearchParams(window.location.search);
  if (params.get("tutorial") === "1") {
    // Slight delay to let library finish loading
    setTimeout(() => window.libraryTutorial.start(), 1500);
  }
});
