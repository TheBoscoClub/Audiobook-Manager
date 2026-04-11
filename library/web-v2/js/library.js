// Modern Audiobook Library - API-backed with pagination
// Use relative URL for proxy support (works with both direct API and HTTPS proxy)
const API_BASE = "/api";

// SessionPersistence is loaded from js/session-persistence.js (shared with login/verify pages)

class AudiobookLibraryV2 {
  constructor() {
    this.currentPage = 1;
    this.perPage = 50;
    this.viewMode = "grid"; // "grid" or "list"
    this.totalPages = 1;
    this.totalCount = 0;
    this.currentFilters = {
      search: "",
      author: "",
      narrator: "",
      sort: "title",
      order: "asc",
    };
    this.filters = {
      authors: [],
      narrators: [],
    };
    this.narratorCounts = {}; // narrator -> book count
    this.narratorLetterGroup = "all"; // current letter group filter
    this.narratorSortAsc = true; // A-Z = true, Z-A = false
    this.highlightedNarratorIndex = -1;

    // Author autocomplete state
    this.authorLetterGroup = "all";
    this.authorSortAsc = true;
    this.highlightedAuthorIndex = -1;

    // Collections state
    this.collections = [];
    this.currentCollection = "";

    // Auth state
    this.user = null;
    this.authEnabled = false;
    this.guestMode = false;

    // Tab state
    this.currentTab = "browse";
    this.myLibraryBooks = [];
    this.browseBooks = [];

    // Hide/unhide state
    this.selectedBookIds = new Set();
    this.viewingHidden = false;

    // Compact viewport: card tap opens detail modal
    this.setupCompactCardTap();

    this.init();
  }

  /**
   * Check authentication status and get current user info.
   * Always returns true — guests can browse, no redirect.
   */
  async checkAuth() {
    try {
      let data = await checkAuthStatus();
      this.authEnabled = data.auth_enabled;
      this.user = data.user;
      this.guestMode = data.guest;

      // If guest (no session cookie), try to recover from client storage
      if (this.guestMode && !this.user) {
        const recovered = await this._trySessionRecover();
        if (recovered) {
          // Re-check auth status after session restore
          data = await checkAuthStatus();
          this.user = data.user;
          this.guestMode = data.guest;
        }
      }

      if (this.user) {
        this.updateUserUI();
      } else if (this.guestMode) {
        this.updateGuestUI();
      }
      return true;
    } catch (error) {
      // Network error or auth not configured - allow access
      console.debug("Auth check skipped:", error.message);
      this.authEnabled = false;
      return true;
    }
  }

  async _trySessionRecover() {
    try {
      const token = await SessionPersistence.recover();
      if (!token) return false;

      await api.post("/auth/session/restore", { token }, { toast: false });
      return true;

      // Token invalid — clear stale stored token
      await SessionPersistence.clear();
      return false;
    } catch (e) {
      return false;
    }
  }

  /**
   * Update UI elements based on user auth state.
   */
  updateUserUI() {
    const loginLink = document.getElementById("login-link");
    const backOfficeLink = document.getElementById("admin-backoffice-link");
    const accountBtn = document.getElementById("my-account-btn");

    // Back Office is always visible but only actionable by admins.
    // Non-admins see a locked state and get a guest gate on click.
    if (backOfficeLink) {
      backOfficeLink.hidden = false;
      if (this.user && this.user.is_admin) {
        backOfficeLink.classList.remove("backoffice-locked");
        backOfficeLink.removeAttribute("data-locked");
      } else {
        backOfficeLink.classList.add("backoffice-locked");
        backOfficeLink.setAttribute("data-locked", "true");
      }
    }

    if (this.user) {
      if (loginLink) {
        loginLink.hidden = true;
      }
      // Show account button (account.js populates username/initial)
      if (accountBtn) {
        accountBtn.hidden = false;
      }

      // Show/hide download buttons based on permission
      this.updateDownloadButtons();
    } else if (this.authEnabled) {
      // Auth enabled but no user - show login link, hide account
      if (loginLink) loginLink.hidden = false;
      if (accountBtn) accountBtn.hidden = true;
    } else {
      // Auth not enabled or unknown state - hide login and account
      if (loginLink) loginLink.hidden = true;
      if (accountBtn) accountBtn.hidden = true;
    }
  }

  /**
   * Update UI for guest mode — show sign in / request access, hide user elements.
   */
  updateGuestUI() {
    const loginLink = document.getElementById("login-link");
    const requestAccessLink = document.getElementById("request-access-link");
    const backOfficeLink = document.getElementById("admin-backoffice-link");
    const accountBtn = document.getElementById("my-account-btn");
    const myLibraryTab = document.querySelector(
      '.tab-btn[data-tab="my-library"]',
    );

    if (loginLink) loginLink.hidden = false;
    if (requestAccessLink) requestAccessLink.hidden = false;
    if (backOfficeLink) {
      backOfficeLink.hidden = false;
      backOfficeLink.classList.add("backoffice-locked");
      backOfficeLink.setAttribute("data-locked", "true");
    }
    if (accountBtn) accountBtn.hidden = true;
    if (myLibraryTab) myLibraryTab.style.display = "none";
  }

  /**
   * Show guest gate tooltip near the clicked button.
   * Explains that play/download requires an account.
   */
  showGuestGate(targetElement) {
    // Remove any existing tooltip
    const existing = document.querySelector(".guest-gate-tooltip");
    if (existing) existing.remove();

    const tooltip = document.createElement("div");
    tooltip.className = "guest-gate-tooltip";

    // Build tooltip content with safe DOM methods (no innerHTML)
    const arrow = document.createElement("div");
    arrow.className = "guest-gate-arrow";
    tooltip.appendChild(arrow);

    const heading = document.createElement("strong");
    heading.textContent = t("library.signInToListen");
    tooltip.appendChild(heading);

    const desc = document.createElement("p");
    desc.textContent = t("library.signInDesc");
    tooltip.appendChild(desc);

    const links = document.createElement("div");
    links.className = "guest-gate-links";

    const signInLink = document.createElement("a");
    signInLink.href = "login.html";
    signInLink.textContent = t("library.existingSignIn");
    links.appendChild(signInLink);

    const sep = document.createElement("span");
    sep.className = "guest-gate-separator";
    sep.textContent = "\u00B7";
    links.appendChild(sep);

    const requestLink = document.createElement("a");
    requestLink.href = "register.html";
    requestLink.textContent = t("library.requestAccount");
    links.appendChild(requestLink);

    tooltip.appendChild(links);
    document.body.appendChild(tooltip);

    // Position near the clicked button
    const rect = targetElement.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    let top = rect.bottom + 8 + window.scrollY;
    let left =
      rect.left + rect.width / 2 - tooltipRect.width / 2 + window.scrollX;

    // Keep within viewport
    if (left < 8) left = 8;
    if (left + tooltipRect.width > window.innerWidth - 8) {
      left = window.innerWidth - tooltipRect.width - 8;
    }

    tooltip.style.top = top + "px";
    tooltip.style.left = left + "px";

    // Dismiss on click-away
    const dismiss = (e) => {
      if (!tooltip.contains(e.target)) {
        tooltip.remove();
        document.removeEventListener("click", dismiss);
      }
    };
    // Delay to avoid immediate dismiss from current click
    setTimeout(() => document.addEventListener("click", dismiss), 0);
  }

  /**
   * Show/hide download buttons based on user's download permission.
   */
  updateDownloadButtons() {
    const canDownload = this.user && this.user.can_download;
    document.querySelectorAll(".download-button").forEach((btn) => {
      btn.style.display = canDownload ? "" : "none";
    });
  }

  async applyBookTranslations() {
    const locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";

    // Restore originals when switching back to English
    if (locale === "en") {
      document.querySelectorAll(".book-card[data-id]").forEach((card) => {
        const titleEl = card.querySelector(".book-title");
        const orig = titleEl && titleEl.getAttribute("data-original-title");
        if (orig) { titleEl.textContent = orig; titleEl.removeAttribute("data-original-title"); }
        const authorEl = card.querySelector(".book-author");
        const origA = authorEl && authorEl.getAttribute("data-original-author");
        if (origA) { authorEl.textContent = origA; authorEl.removeAttribute("data-original-author"); }
        const seriesEl = card.querySelector(".book-series");
        const origS = seriesEl && seriesEl.getAttribute("data-original-series");
        if (origS) { seriesEl.textContent = origS; seriesEl.removeAttribute("data-original-series"); }
      });
      return;
    }

    try {
      // Collect visible book IDs to pass as ?ids= for on-demand translation
      const allCards = document.querySelectorAll(".book-card[data-id]");
      const visibleIds = [];
      allCards.forEach((card) => {
        const bookId = card.getAttribute("data-id");
        if (bookId) visibleIds.push(bookId);
      });

      // Single GET request: returns cached + auto-translates missing via DeepL
      const idsParam = visibleIds.length > 0 ? "&ids=" + visibleIds.join(",") : "";
      const translations = await api.get(
        `${API_BASE}/translations/by-locale/${encodeURIComponent(locale)}?t=1${idsParam}`,
        { toast: false }
      );

      // Apply translations to book cards
      this._overlayTranslations(allCards, translations);
    } catch (e) {
      // Translation overlay is non-critical — fail silently
      console.warn("Book translation overlay failed:", e.message || e);
    }
  }

  /**
   * Apply translation data to book card DOM elements.
   */
  _overlayTranslations(cards, translations) {
    cards.forEach((card) => {
      const bookId = card.getAttribute("data-id");
      const tr = translations[bookId];
      if (!tr) return;

      if (tr.title) {
        const titleEl = card.querySelector(".book-title");
        if (titleEl) {
          if (!titleEl.hasAttribute("data-original-title")) {
            titleEl.setAttribute("data-original-title", titleEl.textContent);
          }
          titleEl.textContent = tr.title;
        }
      }
      if (tr.author_display) {
        const authorEl = card.querySelector(".book-author");
        if (authorEl) {
          if (!authorEl.hasAttribute("data-original-author")) {
            authorEl.setAttribute("data-original-author", authorEl.textContent);
          }
          authorEl.textContent = t("book.byAuthor", { author: tr.author_display });
        }
      }
      if (tr.series_display) {
        const seriesEl = card.querySelector(".book-series");
        if (seriesEl) {
          // The rendered series text is "(Series Name)" or "(Series Name, Book N)".
          // Preserve the surrounding parens + sequence suffix, swap only the name.
          const current = seriesEl.textContent || "";
          if (!seriesEl.hasAttribute("data-original-series")) {
            seriesEl.setAttribute("data-original-series", current);
          }
          // Match "(<name>[, <suffix>])" — swap <name> for the translation,
          // and re-render the ", Book N" suffix through i18n so "Book N"
          // becomes "第N册" (zh-Hans) etc.
          const m = current.match(/^\(([^,)]+)(?:,\s*(.*))?\)$/);
          let suffix = "";
          if (m && m[2]) {
            const numMatch = m[2].match(/(\d+)/);
            if (numMatch) {
              suffix = ", " + t("book.seriesBook", { n: numMatch[1] });
            } else {
              suffix = ", " + m[2];
            }
          }
          seriesEl.textContent = "(" + tr.series_display + suffix + ")";
        }
      }
    });
  }

  /**
   * Log out the current user.
   */
  async logout() {
    // Clear client-side session storage before server logout
    await SessionPersistence.clear();
    try {
      await api.post("/auth/logout", null, { toast: false });
    } catch (error) {
      console.error("Logout error:", error);
    }
    window.top.location.href = "/auth/login";
  }

  /**
   * Load and display notifications for the current user.
   */
  async loadNotifications() {
    if (!this.authEnabled || !this.user) {
      return;
    }

    try {
      const data = await api.get("/auth/me", { toast: false });
      const notifications = data.notifications || [];
      this.displayNotifications(notifications);
    } catch (error) {
      console.error("Error loading notifications:", error);
    }
  }

  /**
   * Display notifications in the banner container using safe DOM methods.
   */
  displayNotifications(notifications) {
    const container = document.getElementById("notification-container");
    if (!container) {
      return;
    }

    // Clear existing notifications
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }

    const icons = {
      info: "ℹ️",
      maintenance: "🔧",
      outage: "🔴",
      personal: "📬",
    };

    for (const notif of notifications) {
      const banner = document.createElement("div");
      banner.className = `notification-banner ${notif.type}${notif.dismissable ? " dismissable" : ""}`;
      banner.dataset.id = notif.id;

      // Create content wrapper
      const content = document.createElement("div");
      content.className = "notification-content";

      // Create icon span
      const iconSpan = document.createElement("span");
      iconSpan.className = "notification-icon";
      iconSpan.textContent = icons[notif.type] || "ℹ️";
      content.appendChild(iconSpan);

      // Create message span (textContent is safe)
      const messageSpan = document.createElement("span");
      messageSpan.className = "notification-message";
      messageSpan.textContent = notif.message;
      content.appendChild(messageSpan);

      banner.appendChild(content);

      // Create knife switch dismiss if dismissable
      if (notif.dismissable) {
        const notifId = notif.id;
        const ks = createKnifeSwitch({
          size: "compact",
          title: t("notification.dismiss"),
          label: t("notification.dismissLabel"),
          onDismiss: () => this.dismissNotification(notifId, banner),
        });
        ks.classList.add("notification-dismiss");
        banner.appendChild(ks);
      }

      container.appendChild(banner);
    }
  }

  /**
   * Dismiss a notification.
   */
  async dismissNotification(notificationId, bannerElement) {
    try {
      await api.post(`/auth/notifications/dismiss/${notificationId}`, null, { toast: false });
      // Animate removal
      bannerElement.classList.add("dismissing");
      setTimeout(() => bannerElement.remove(), 300);
    } catch (error) {
      console.error("Error dismissing notification:", error);
    }
  }

  maybeShowPasskeyPrompt() {
    // One-time prompt for magic_link users suggesting passkey for better persistence
    if (!this.user || this.user.auth_type !== "magic_link") return;
    try {
      if (localStorage.getItem("library_passkey_prompt_dismissed")) return;
    } catch (e) {
      return;
    }

    // Check if browser supports WebAuthn
    if (!window.PublicKeyCredential) return;

    const banner = document.createElement("div");
    banner.className = "notification-banner";
    banner.setAttribute("role", "status");
    const content = document.createElement("div");
    content.className = "notification-content";
    const msg = document.createElement("span");
    msg.className = "notification-message";
    msg.textContent = t("notification.passkeyPrompt");
    content.appendChild(msg);
    const ks = createKnifeSwitch({
      size: "compact",
      title: t("notification.dismissSuggestion"),
      onDismiss: function () {
        try {
          localStorage.setItem("library_passkey_prompt_dismissed", "1");
        } catch (e) {}
        banner.classList.add("dismissing");
        setTimeout(function () { banner.remove(); }, 300);
      },
    });
    ks.classList.add("notification-dismiss");
    content.appendChild(ks);
    banner.appendChild(content);

    const container = document.getElementById("notification-container");
    if (container) container.appendChild(banner);
  }

  /**
   * Download an audiobook for offline listening.
   * Fetches the file as a blob, triggers the browser download, then
   * records the completed download via the user-state API so it
   * appears in the user's download history.
   *
   * Failed or cancelled downloads are intentionally NOT recorded.
   */
  async downloadAudiobook(bookId, event) {
    // Guest gate: block download for unauthenticated visitors
    if (this.guestMode) {
      const target = event
        ? event.target
        : document.querySelector(`[onclick*="downloadAudiobook(${bookId})"]`);
      if (target) this.showGuestGate(target);
      return;
    }
    const downloadBtn = document.querySelector(
      `[onclick*="downloadAudiobook(${bookId})"]`,
    );
    if (downloadBtn) {
      downloadBtn.disabled = true;
      downloadBtn.textContent = t("book.downloading");
    }

    try {
      const response = await api.get(`${API_BASE}/download/${bookId}`, { toast: false, raw: true });

      const blob = await response.blob();
      const contentDisposition = response.headers.get("Content-Disposition");
      let filename = `audiobook-${bookId}.opus`;
      if (contentDisposition) {
        const match = contentDisposition.match(
          /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/,
        );
        if (match) filename = match[1].replace(/['"]/g, "");
      }

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      // Record successful download completion
      await api.post(`${API_BASE}/user/downloads/${bookId}/complete`, { file_format: "opus" }, { toast: false });
    } catch (error) {
      console.error("Download error:", error);
      // Failed/cancelled downloads not recorded — by design
    } finally {
      if (downloadBtn) {
        downloadBtn.disabled = false;
        downloadBtn.textContent = t("book.downloadFull");
      }
    }
  }

  /**
   * Extract sort key from a name - returns "LastName, FirstName" format
   * Handles:
   * - Single name: "First Last" → "Last, First"
   * - Multiple names: "First Last, Second Name, ..." → sort by first person's last name
   * - Anthologies: "Gaiman (contributor), Martin (editor)" → sort by editor (Martin)
   * - Named groups: "Full Cast" → treat entire name as surname
   * - Role suffixes: "Name (editor)", "Name - translator" → strip role, sort by name
   */
  getNameSortKey(name) {
    if (!name) return "";

    // Check for comma-separated multiple names
    if (name.includes(",")) {
      const parts = name.split(",").map((p) => p.trim());

      // If there are "(contributor)" entries, this is an anthology - find the editor
      const hasContributors = parts.some((p) => /\(contributor\)/i.test(p));
      if (hasContributors) {
        // Find the editor - they're the "author of record" for anthologies
        const editor = parts.find(
          (p) => /\(editor\)/i.test(p) || /- editor/i.test(p),
        );
        if (editor) {
          return this.getNameSortKey(editor);
        }
      }

      // Otherwise use first person in the list
      return this.getNameSortKey(parts[0]);
    }

    // Strip role suffixes: "(editor)", "(translator)", "- editor", etc.
    let cleanName = name
      .replace(/\s*\([^)]*\)\s*/g, "") // Remove (anything in parentheses)
      .replace(
        /\s*-\s*(editor|translator|contributor|introduction|author|authoreditor|editorauthor)\s*/gi,
        "",
      )
      .trim();

    // If stripping left us with nothing, use original
    if (!cleanName) cleanName = name.trim();

    // Known group names - treat entire name as surname (no first name)
    const groupNames = [
      "full cast",
      "various authors",
      "various narrators",
      "various",
      "unknown narrator",
      "unknown author",
    ];
    if (groupNames.includes(cleanName.toLowerCase())) {
      return cleanName.toLowerCase();
    }

    const parts = cleanName.split(/\s+/);
    if (parts.length === 1) return cleanName.toLowerCase();

    // Last word is the last name, everything else is first/middle
    const lastName = parts[parts.length - 1];
    const firstName = parts.slice(0, -1).join(" ");
    return `${lastName}, ${firstName}`.toLowerCase();
  }

  /**
   * Sort names by last name, first name
   */
  sortByLastName(names, ascending = true) {
    return names.sort((a, b) => {
      const keyA = this.getNameSortKey(a);
      const keyB = this.getNameSortKey(b);
      const cmp = keyA.localeCompare(keyB, undefined, { sensitivity: "base" });
      return ascending ? cmp : -cmp;
    });
  }

  async init() {
    // Check authentication first
    const canAccess = await this.checkAuth();
    if (!canAccess) {
      return; // Redirect in progress
    }

    // Initialize new books marquee (visible to all)
    if (typeof initMarquee === "function") {
      initMarquee();
    }

    // Load notifications only if authenticated (not guest)
    if (this.user) {
      await this.loadNotifications();
      this.maybeShowPasskeyPrompt();
    }

    await this.loadStats();
    await this.loadFilters();
    await this.loadCollections();
    this.setupEventListeners();
    this.initTabs();
    await this.applySavedSortPreference();
    await this.loadAudiobooks();
  }

  /**
   * Apply a sort preference string (e.g. "author_last_asc") to the dropdown and filters.
   * Returns true if successfully applied, false otherwise.
   */
  _applySortString(sortPref) {
    if (!sortPref || sortPref === "title_asc") return false;
    const lastUnderscore = sortPref.lastIndexOf("_");
    if (lastUnderscore <= 0) return false;
    const sort = sortPref.substring(0, lastUnderscore);
    const order = sortPref.substring(lastUnderscore + 1);
    const dropdownValue = sort + ":" + order;
    const sortSelect = document.getElementById("sort-filter");
    const optionExists = Array.from(sortSelect.options).some(o => o.value === dropdownValue);
    if (optionExists) {
      sortSelect.value = dropdownValue;
      this.currentFilters.sort = sort;
      this.currentFilters.order = order;
      return true;
    }
    return false;
  }

  async applySavedSortPreference() {
    // localStorage is the source of truth for the current browser.
    // The API is the cross-device sync mechanism.
    const localSort = localStorage.getItem("audiobook_sort_order");
    this._applySortString(localSort);

    // Apply locally-cached view_mode and items_per_page immediately (no flash)
    const localViewMode = localStorage.getItem("audiobook_view_mode");
    if (localViewMode === "grid" || localViewMode === "list") {
      this._applyViewMode(localViewMode);
    }
    const localPerPage = localStorage.getItem("audiobook_items_per_page");
    if (localPerPage) {
      this._applyItemsPerPage(localPerPage);
    }

    if (!this.user) return;
    try {
      const prefs = await api.get("/api/user/preferences", { toast: false });
      if (!prefs) return;

      // --- sort_order ---
      const serverSort = prefs.sort_order;
      if (serverSort) {
        if (localSort && localSort !== serverSort) {
          api.patch("/api/user/preferences", { sort_order: localSort },
            { toast: false, keepalive: true }).catch(() => {});
        } else if (!localSort) {
          this._applySortString(serverSort);
          localStorage.setItem("audiobook_sort_order", serverSort);
        }
      }

      // --- view_mode ---
      const serverViewMode = prefs.view_mode;
      if (serverViewMode) {
        if (localViewMode && localViewMode !== serverViewMode) {
          api.patch("/api/user/preferences", { view_mode: localViewMode },
            { toast: false, keepalive: true }).catch(() => {});
        } else if (!localViewMode) {
          this._applyViewMode(serverViewMode);
          localStorage.setItem("audiobook_view_mode", serverViewMode);
        }
      }

      // --- items_per_page ---
      const serverPerPage = prefs.items_per_page;
      if (serverPerPage) {
        if (localPerPage && localPerPage !== serverPerPage) {
          api.patch("/api/user/preferences", { items_per_page: localPerPage },
            { toast: false, keepalive: true }).catch(() => {});
        } else if (!localPerPage) {
          this._applyItemsPerPage(serverPerPage);
          localStorage.setItem("audiobook_items_per_page", serverPerPage);
        }
      }
    } catch (_e) {
      // API unavailable — localStorage values already applied above
    }
  }

  _applyViewMode(mode) {
    if (mode !== "grid" && mode !== "list") return;
    this.viewMode = mode;
    const grid = document.getElementById("books-grid");
    if (grid) {
      grid.classList.toggle("list-view", mode === "list");
    }
  }

  _applyItemsPerPage(value) {
    const num = parseInt(value);
    if (![25, 50, 100, 200].includes(num)) return;
    this.perPage = num;
    const perPageSelect = document.getElementById("per-page");
    if (perPageSelect) perPageSelect.value = String(num);
  }

  showLoading(show = true) {
    const overlay = document.getElementById("loading-overlay");
    if (show) {
      overlay.classList.add("active");
    } else {
      overlay.classList.remove("active");
    }
  }

  async loadStats() {
    try {
      const stats = await api.get(`${API_BASE}/stats`, { toast: false });

      document.getElementById("total-books").textContent =
        stats.total_audiobooks.toLocaleString();
      document.getElementById("total-hours").textContent =
        stats.total_hours.toLocaleString();
      document.getElementById("total-authors").textContent =
        stats.unique_authors.toLocaleString();
      document.getElementById("total-narrators").textContent =
        stats.unique_narrators.toLocaleString();
    } catch (error) {
      console.error("Error loading stats:", error);
    }
  }

  async loadFilters() {
    try {
      this.filters = await api.get(`${API_BASE}/filters`, { toast: false });

      // Load narrator counts for autocomplete
      await this.loadNarratorCounts();

      // Setup author autocomplete (similar to narrator)
      this.setupAuthorAutocomplete();

      // Setup narrator autocomplete
      this.setupNarratorAutocomplete();
    } catch (error) {
      console.error("Error loading filters:", error);
    }
  }

  async loadCollections() {
    try {
      this.collections = await api.get(`${API_BASE}/collections`, { toast: false });
      this.renderCollectionButtons();
    } catch (error) {
      console.error("Error loading collections:", error);
    }
  }

  renderCollectionButtons() {
    const container = document.getElementById("collections-buttons");
    if (!container || this.collections.length === 0) {
      return;
    }

    // Group collections by category
    const grouped = {};
    this.collections.forEach((c) => {
      const cat = c.category || "fiction";
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(c);
    });

    // Initialize expand state tracker
    if (!this._expandedCollections) {
      this._expandedCollections = new Set();
    }

    // Build tree-structured sidebar using DOM methods
    container.textContent = "";
    const categoryOrder = ["special", "fiction", "nonfiction", "series", "eras", "topics"];
    const categoryLabels = {
      special: t("collection.special"),
      fiction: t("collection.fiction"),
      nonfiction: t("collection.nonfiction"),
      series: t("collection.series"),
      eras: t("collection.eras"),
      topics: t("collection.topics"),
    };

    categoryOrder.forEach((cat) => {
      if (!grouped[cat] || grouped[cat].length === 0) return;

      const categoryDiv = document.createElement("div");
      categoryDiv.className = "collection-category";

      const label = document.createElement("span");
      label.className = "collection-category-label";
      label.textContent = categoryLabels[cat];
      categoryDiv.appendChild(label);

      const itemsDiv = document.createElement("div");
      itemsDiv.className = "collection-category-items";

      grouped[cat].forEach((collection) => {
        const hasChildren =
          collection.children && collection.children.length > 0;
        const isParentActive = this.currentCollection === collection.id;
        const isChildActive =
          hasChildren &&
          collection.children.some((ch) => ch.id === this.currentCollection);
        const isExpanded =
          isParentActive ||
          isChildActive ||
          this._expandedCollections.has(collection.id);

        const treeNode = document.createElement("div");
        treeNode.className = "collection-tree-node";

        // Parent row: button + optional toggle
        const parentRow = document.createElement("div");
        parentRow.className = "collection-tree-parent";

        const btn = document.createElement("button");
        btn.className = `collection-btn${isParentActive ? " active" : ""}`;
        btn.dataset.collection = collection.id;
        btn.title = collection.description;

        const iconSpan = document.createElement("span");
        iconSpan.className = "icon";
        iconSpan.textContent = collection.icon;
        btn.appendChild(iconSpan);

        const nameSpan = document.createElement("span");
        nameSpan.className = "name";
        nameSpan.textContent = collection.name;
        btn.appendChild(nameSpan);

        const countSpan = document.createElement("span");
        countSpan.className = "count";
        countSpan.textContent = collection.count;
        btn.appendChild(countSpan);

        btn.addEventListener("click", () =>
          this.toggleCollection(collection.id),
        );
        parentRow.appendChild(btn);

        if (hasChildren) {
          const toggleBtn = document.createElement("button");
          toggleBtn.className = `collection-tree-toggle${isExpanded ? " expanded" : ""}`;
          toggleBtn.title = t("collection.showSubgenres");
          const arrow = document.createElement("span");
          arrow.className = "toggle-arrow";
          arrow.textContent = "\u25B6"; // right-pointing triangle
          toggleBtn.appendChild(arrow);
          toggleBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const childrenEl = treeNode.querySelector(
              ".collection-tree-children",
            );
            if (childrenEl) {
              const nowExpanded = childrenEl.classList.toggle("expanded");
              toggleBtn.classList.toggle("expanded", nowExpanded);
              if (nowExpanded) {
                this._expandedCollections.add(collection.id);
              } else {
                this._expandedCollections.delete(collection.id);
              }
            }
          });
          parentRow.appendChild(toggleBtn);
        }

        treeNode.appendChild(parentRow);

        // Children (subgenres)
        if (hasChildren) {
          const childrenDiv = document.createElement("div");
          childrenDiv.className = `collection-tree-children${isExpanded ? " expanded" : ""}`;

          collection.children.forEach((child) => {
            const childBtn = document.createElement("button");
            childBtn.className = `collection-btn collection-child${this.currentCollection === child.id ? " active" : ""}`;
            childBtn.dataset.collection = child.id;
            childBtn.title = child.name;

            const childName = document.createElement("span");
            childName.className = "name";
            childName.textContent = child.name;
            childBtn.appendChild(childName);

            const childCount = document.createElement("span");
            childCount.className = "count";
            childCount.textContent = child.count;
            childBtn.appendChild(childCount);

            childBtn.addEventListener("click", () =>
              this.toggleCollection(child.id),
            );
            childrenDiv.appendChild(childBtn);
          });

          treeNode.appendChild(childrenDiv);
        }

        itemsDiv.appendChild(treeNode);
      });

      categoryDiv.appendChild(itemsDiv);
      container.appendChild(categoryDiv);
    });

    // Update active filter badge
    this.updateFilterBadge();

    // Overlay translated collection names if a non-English locale is active.
    this.applyCollectionTranslations();
  }

  async applyCollectionTranslations() {
    const locale = window.i18n && window.i18n.getLocale ? window.i18n.getLocale() : "en";
    if (!locale || locale === "en") {
      return;
    }
    try {
      const map = await api.get(
        `${API_BASE}/translations/collections/${encodeURIComponent(locale)}`,
        { toast: false },
      );
      if (!map || typeof map !== "object") return;
      this._collectionTranslationMap = map;

      // Overlay button name spans.
      document.querySelectorAll(".collection-btn[data-collection]").forEach((btn) => {
        const cid = btn.dataset.collection;
        const translated = map[cid];
        if (!translated) return;
        const nameSpan = btn.querySelector(".name");
        if (nameSpan) nameSpan.textContent = translated;
      });

      // Overlay category labels. The backend keyed these by the parent collection
      // id, so we use the translated name for the category's first parent entry
      // where available. Category label keys are already localized via t().

      // Update active filter badge with translated name if applicable.
      const badge = document.getElementById("active-filter-badge");
      if (badge && this.currentCollection && map[this.currentCollection]) {
        const collection = this.collections.find((c) => c.id === this.currentCollection)
          || this.collections.flatMap((c) => c.children || []).find(
            (c) => c && c.id === this.currentCollection,
          );
        if (collection) {
          badge.textContent = collection.icon
            ? `${collection.icon} ${map[this.currentCollection]}`
            : map[this.currentCollection];
        }
      }
    } catch (err) {
      console.error("Failed to apply collection translations:", err);
    }
  }

  updateFilterBadge() {
    const badge = document.getElementById("active-filter-badge");
    if (!badge) return;

    if (this.currentCollection) {
      const collection = this.collections.find(
        (c) => c.id === this.currentCollection,
      );
      if (collection) {
        badge.textContent = collection.icon + " " + collection.name;
        badge.classList.add("visible");
      }
    } else {
      badge.textContent = "";
      badge.classList.remove("visible");
    }
  }

  toggleCollection(collectionId) {
    if (this.currentCollection === collectionId) {
      // Deselect - show all books
      this.currentCollection = "";
    } else {
      // Select this collection
      this.currentCollection = collectionId;
    }
    this.currentPage = 1;
    this.renderCollectionButtons();
    this.loadAudiobooks();
    // Close sidebar after selection on mobile
    if (window.innerWidth < 768) {
      this.closeSidebar();
    }
  }

  openSidebar() {
    const sidebar = document.getElementById("collections-sidebar");
    const overlay = document.getElementById("sidebar-overlay");
    if (sidebar) sidebar.classList.add("open");
    if (overlay) overlay.classList.add("active");
    document.body.style.overflow = "hidden";
  }

  closeSidebar() {
    const sidebar = document.getElementById("collections-sidebar");
    const overlay = document.getElementById("sidebar-overlay");
    if (sidebar) sidebar.classList.remove("open");
    if (overlay) overlay.classList.remove("active");
    document.body.style.overflow = "";
  }

  setupSidebarEvents() {
    // Toggle button
    const toggleBtn = document.getElementById("sidebar-toggle");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => this.openSidebar());
    }

    // Close button
    const closeBtn = document.getElementById("sidebar-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => this.closeSidebar());
    }

    // Overlay click to close
    const overlay = document.getElementById("sidebar-overlay");
    if (overlay) {
      overlay.addEventListener("click", () => this.closeSidebar());
    }

    // Clear filter button
    const clearBtn = document.getElementById("sidebar-clear-filter");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        this.currentCollection = "";
        this.currentPage = 1;
        this.renderCollectionButtons();
        this.loadAudiobooks();
      });
    }

    // Escape key to close
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        this.closeSidebar();
      }
    });
  }

  setupAuthorAutocomplete() {
    const container = document.getElementById("author-autocomplete");
    const input = document.getElementById("author-search");
    const dropdown = document.getElementById("author-dropdown");
    const clearBtn = document.getElementById("author-clear");
    const sortBtn = document.getElementById("author-sort");

    if (!container || !input || !dropdown) return;

    // Letter group buttons
    container.querySelectorAll(".letter-group").forEach((btn) => {
      btn.addEventListener("click", () => {
        container
          .querySelectorAll(".letter-group")
          .forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        this.authorLetterGroup = btn.dataset.group;
        const query = input.value.toLowerCase().trim();
        this.highlightedAuthorIndex = -1;
        this.showAuthorDropdown(query);
      });
    });

    // Sort toggle button
    if (sortBtn) {
      sortBtn.addEventListener("click", () => {
        this.authorSortAsc = !this.authorSortAsc;
        sortBtn.textContent = this.authorSortAsc ? "A-Z" : "Z-A";
        const query = input.value.toLowerCase().trim();
        this.highlightedAuthorIndex = -1;
        this.showAuthorDropdown(query);
      });
    }

    // Input event - filter authors as user types
    input.addEventListener("input", (e) => {
      const query = e.target.value.toLowerCase().trim();
      this.highlightedAuthorIndex = -1;
      this.showAuthorDropdown(query);
    });

    // Focus event - show dropdown
    input.addEventListener("focus", () => {
      const query = input.value.toLowerCase().trim();
      this.showAuthorDropdown(query);
    });

    // Keyboard navigation
    input.addEventListener("keydown", (e) => {
      const options = dropdown.querySelectorAll(".author-option");

      if (e.key === "ArrowDown") {
        e.preventDefault();
        this.highlightedAuthorIndex = Math.min(
          this.highlightedAuthorIndex + 1,
          options.length - 1,
        );
        this.updateAuthorHighlight(options);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.highlightedAuthorIndex = Math.max(
          this.highlightedAuthorIndex - 1,
          -1,
        );
        this.updateAuthorHighlight(options);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (
          this.highlightedAuthorIndex >= 0 &&
          options[this.highlightedAuthorIndex]
        ) {
          options[this.highlightedAuthorIndex].click();
        }
      } else if (e.key === "Escape") {
        this.hideAuthorDropdown();
        input.blur();
      }
    });

    // Clear button
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        this.selectAuthor("");
      });
    }

    // Click outside to close
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target)) {
        this.hideAuthorDropdown();
      }
    });
  }

  showAuthorDropdown(query = "") {
    const dropdown = document.getElementById("author-dropdown");
    const authors = this.filters.authors || [];

    // Filter by letter group using sort_name initial
    let filtered = this.filterAuthorsByLetterGroup(
      authors,
      this.authorLetterGroup,
    );

    // Then filter by search query (match against both name and sort_name)
    if (query) {
      filtered = filtered.filter(
        (a) =>
          a.name.toLowerCase().includes(query) ||
          a.sort_name.toLowerCase().includes(query),
      );
    }

    // Sort by sort_name (already sorted from API, but respect ascending/descending)
    filtered = [...filtered].sort((a, b) => {
      const cmp = a.sort_name.localeCompare(b.sort_name, undefined, {
        sensitivity: "base",
      });
      return this.authorSortAsc ? cmp : -cmp;
    });

    // Build dropdown HTML — show all results (scrollable via CSS max-height)
    let html = "";

    // "All Authors" option at top
    const allLabel =
      this.authorLetterGroup === "all"
        ? t("library.allAuthors")
        : t("library.allInGroup", { group: this.authorLetterGroup.toUpperCase() });
    html += `<div class="author-option author-all-option" data-value="">
            <span>${allLabel}</span>
            <span class="count">${t("library.total", { n: filtered.length })}</span>
        </div>`;

    if (filtered.length === 0 && query) {
      html += `<div class="author-no-results">${t("library.noAuthorsMatch", { query: this.escapeHtml(query) })}</div>`;
    } else if (filtered.length === 0) {
      html += `<div class="author-no-results">${t("library.noAuthorsInRange")}</div>`;
    } else {
      filtered.forEach((author) => {
        // Display sort_name ("King, Stephen") but filter by name ("Stephen King")
        html += `<div class="author-option" data-value="${this.escapeHtml(author.name)}" data-sort-name="${this.escapeHtml(author.sort_name)}">
                    <span>${this.highlightMatch(author.sort_name, query)}</span>
                </div>`;
      });
    }

    // XSS safe: All dynamic content passes through escapeHtml() (lines 433, highlightMatch->escapeHtml)
    dropdown.innerHTML = html;

    // Add click handlers to options
    dropdown.querySelectorAll(".author-option").forEach((option) => {
      option.addEventListener("click", () => {
        this.selectAuthor(option.dataset.value, option.dataset.sortName);
      });
    });

    dropdown.classList.add("active");
  }

  hideAuthorDropdown() {
    const dropdown = document.getElementById("author-dropdown");
    if (dropdown) {
      dropdown.classList.remove("active");
    }
  }

  updateAuthorHighlight(options) {
    options.forEach((opt, idx) => {
      opt.classList.toggle("highlighted", idx === this.highlightedAuthorIndex);
    });

    // Scroll into view
    if (
      this.highlightedAuthorIndex >= 0 &&
      options[this.highlightedAuthorIndex]
    ) {
      options[this.highlightedAuthorIndex].scrollIntoView({ block: "nearest" });
    }
  }

  selectAuthor(author, sortName) {
    const input = document.getElementById("author-search");
    const clearBtn = document.getElementById("author-clear");

    this.currentFilters.author = author || "";
    // Display sort_name ("Last, First") in the search box if available
    input.value = sortName || author || "";

    // Show/hide clear button
    if (clearBtn) {
      clearBtn.style.display = author ? "block" : "none";
    }

    this.hideAuthorDropdown();
    this.currentPage = 1;
    this.loadAudiobooks();
  }

  filterAuthorsByLetterGroup(authors, group) {
    if (group === "all") return [...authors];

    const ranges = {
      "a-e": ["A", "B", "C", "D", "E"],
      "f-j": ["F", "G", "H", "I", "J"],
      "k-o": ["K", "L", "M", "N", "O"],
      "p-t": ["P", "Q", "R", "S", "T"],
      "u-z": ["U", "V", "W", "X", "Y", "Z"],
    };

    const letters = ranges[group] || [];
    return authors.filter((a) => {
      const firstLetter = a.sort_name.charAt(0).toUpperCase();
      return letters.includes(firstLetter);
    });
  }

  async loadNarratorCounts() {
    try {
      // Get narrator counts from stats endpoint
      this.narratorCounts = await api.get(`${API_BASE}/narrator-counts`, { toast: false });
    } catch (error) {
      // Fallback
      this.narratorCounts = {};
      this.filters.narrators.forEach((n) => (this.narratorCounts[n] = null));
    }
  }

  setupNarratorAutocomplete() {
    const container = document.getElementById("narrator-autocomplete");
    const input = document.getElementById("narrator-search");
    const dropdown = document.getElementById("narrator-dropdown");
    const clearBtn = document.getElementById("narrator-clear");
    const sortBtn = document.getElementById("narrator-sort");

    // Letter group buttons
    container.querySelectorAll(".letter-group").forEach((btn) => {
      btn.addEventListener("click", () => {
        container
          .querySelectorAll(".letter-group")
          .forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        this.narratorLetterGroup = btn.dataset.group;
        const query = input.value.toLowerCase().trim();
        this.highlightedNarratorIndex = -1;
        this.showNarratorDropdown(query);
      });
    });

    // Sort toggle button
    sortBtn.addEventListener("click", () => {
      this.narratorSortAsc = !this.narratorSortAsc;
      sortBtn.textContent = this.narratorSortAsc ? "A-Z" : "Z-A";
      const query = input.value.toLowerCase().trim();
      this.highlightedNarratorIndex = -1;
      this.showNarratorDropdown(query);
    });

    // Input event - filter narrators as user types
    input.addEventListener("input", (e) => {
      const query = e.target.value.toLowerCase().trim();
      this.highlightedNarratorIndex = -1;
      this.showNarratorDropdown(query);
    });

    // Focus event - show dropdown
    input.addEventListener("focus", () => {
      const query = input.value.toLowerCase().trim();
      this.showNarratorDropdown(query);
    });

    // Keyboard navigation
    input.addEventListener("keydown", (e) => {
      const options = dropdown.querySelectorAll(".narrator-option");

      if (e.key === "ArrowDown") {
        e.preventDefault();
        this.highlightedNarratorIndex = Math.min(
          this.highlightedNarratorIndex + 1,
          options.length - 1,
        );
        this.updateNarratorHighlight(options);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.highlightedNarratorIndex = Math.max(
          this.highlightedNarratorIndex - 1,
          -1,
        );
        this.updateNarratorHighlight(options);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (
          this.highlightedNarratorIndex >= 0 &&
          options[this.highlightedNarratorIndex]
        ) {
          options[this.highlightedNarratorIndex].click();
        }
      } else if (e.key === "Escape") {
        this.hideNarratorDropdown();
        input.blur();
      }
    });

    // Clear button
    clearBtn.addEventListener("click", () => {
      this.selectNarrator("");
    });

    // Click outside to close
    document.addEventListener("click", (e) => {
      if (!container.contains(e.target)) {
        this.hideNarratorDropdown();
      }
    });
  }

  showNarratorDropdown(query = "") {
    const dropdown = document.getElementById("narrator-dropdown");
    const narrators = this.filters.narrators || [];

    // Filter by letter group first
    let filtered = this.filterByLetterGroup(
      narrators,
      this.narratorLetterGroup,
    );

    // Then filter by search query
    if (query) {
      filtered = filtered.filter((n) => n.toLowerCase().includes(query));
    }

    // Sort the results
    filtered = this.sortNarrators(filtered, this.narratorSortAsc);

    // Build dropdown HTML — show all results (scrollable via CSS max-height)
    let html = "";

    // "All Narrators" option at top (shows total count for current group)
    const allLabel =
      this.narratorLetterGroup === "all"
        ? t("library.allNarrators")
        : t("library.allInGroup", { group: this.narratorLetterGroup.toUpperCase() });
    html += `<div class="narrator-option narrator-all-option" data-value="">
            <span>${allLabel}</span>
            <span class="count">${t("library.total", { n: filtered.length })}</span>
        </div>`;

    if (filtered.length === 0 && query) {
      html += `<div class="narrator-no-results">${t("library.noNarratorsMatch", { query: this.escapeHtml(query) })}</div>`;
    } else if (filtered.length === 0) {
      html += `<div class="narrator-no-results">${t("library.noNarratorsInRange")}</div>`;
    } else {
      filtered.forEach((narrator) => {
        const count = this.narratorCounts[narrator];
        const countHtml =
          count != null ? `<span class="count">${count}</span>` : "";
        html += `<div class="narrator-option" data-value="${this.escapeHtml(narrator)}">
                    <span>${this.highlightMatch(narrator, query)}</span>
                    ${countHtml}
                </div>`;
      });
    }

    // XSS safe: All dynamic content passes through escapeHtml() (lines 626, highlightMatch->escapeHtml)
    dropdown.innerHTML = html;

    // Add click handlers to options
    dropdown.querySelectorAll(".narrator-option").forEach((option) => {
      option.addEventListener("click", () => {
        this.selectNarrator(option.dataset.value);
      });
    });

    dropdown.classList.add("active");
  }

  /**
   * Get the last name from a single person's name (no commas)
   */
  extractLastName(singleName) {
    // Strip role suffixes first
    let clean = singleName
      .replace(/\s*\([^)]*\)\s*/g, "")
      .replace(
        /\s*-\s*(editor|translator|contributor|introduction|author|authoreditor|editorauthor)\s*/gi,
        "",
      )
      .trim();
    if (!clean) clean = singleName.trim();

    const parts = clean.split(/\s+/);
    return parts.length > 1 ? parts[parts.length - 1] : clean;
  }

  filterByLetterGroup(names, group) {
    if (group === "all") return [...names];

    const ranges = {
      "a-e": ["A", "B", "C", "D", "E"],
      "f-j": ["F", "G", "H", "I", "J"],
      "k-o": ["K", "L", "M", "N", "O"],
      "p-t": ["P", "Q", "R", "S", "T"],
      "u-z": ["U", "V", "W", "X", "Y", "Z"],
    };

    const letters = ranges[group] || [];
    return names.filter((name) => {
      // For co-authored works, check if ANY author's last name matches
      // This allows "Stephen King, Peter Straub" to appear under both K and S
      if (name.includes(",")) {
        const people = name.split(",").map((p) => p.trim());
        // Skip if it's an anthology (has contributors) - use editor only
        const hasContributors = people.some((p) => /\(contributor\)/i.test(p));
        if (hasContributors) {
          const editor = people.find(
            (p) => /\(editor\)/i.test(p) || /- editor/i.test(p),
          );
          if (editor) {
            const lastName = this.extractLastName(editor);
            return letters.includes(lastName.charAt(0).toUpperCase());
          }
        }
        // Co-authored: match if ANY author's last name is in the letter group
        return people.some((person) => {
          const lastName = this.extractLastName(person);
          return letters.includes(lastName.charAt(0).toUpperCase());
        });
      }

      // Single author - use sort key
      const sortKey = this.getNameSortKey(name);
      const firstLetter = sortKey.charAt(0).toUpperCase();
      return letters.includes(firstLetter);
    });
  }

  sortNarrators(narrators, ascending) {
    return this.sortByLastName(narrators, ascending);
  }

  hideNarratorDropdown() {
    const dropdown = document.getElementById("narrator-dropdown");
    dropdown.classList.remove("active");
    this.highlightedNarratorIndex = -1;
  }

  updateNarratorHighlight(options) {
    options.forEach((opt, i) => {
      opt.classList.toggle("highlighted", i === this.highlightedNarratorIndex);
      if (i === this.highlightedNarratorIndex) {
        opt.scrollIntoView({ block: "nearest" });
      }
    });
  }

  selectNarrator(narrator) {
    const container = document.getElementById("narrator-autocomplete");
    const input = document.getElementById("narrator-search");

    this.currentFilters.narrator = narrator || "";
    input.value = narrator || "";

    if (narrator) {
      container.classList.add("has-value");
      input.classList.add("has-value");
    } else {
      container.classList.remove("has-value");
      input.classList.remove("has-value");
    }

    this.hideNarratorDropdown();
    this.currentPage = 1;
    this.loadAudiobooks();
  }

  highlightMatch(text, query) {
    if (!query) return this.escapeHtml(text);
    const escaped = this.escapeHtml(text);
    const queryEscaped = this.escapeHtml(query);
    // Case-insensitive indexOf loop avoids dynamic RegExp (ReDoS risk)
    var result = "";
    var lower = escaped.toLowerCase();
    var qLower = queryEscaped.toLowerCase();
    var pos = 0;
    var idx = lower.indexOf(qLower, pos);
    while (idx !== -1) {
      result += escaped.substring(pos, idx);
      result += "<strong>" + escaped.substring(idx, idx + queryEscaped.length) + "</strong>";
      pos = idx + queryEscaped.length;
      idx = lower.indexOf(qLower, pos);
    }
    result += escaped.substring(pos);
    return result;
  }

  escapeHtml(text) {
    if (text == null) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  populateSelect(selectId, options) {
    const select = document.getElementById(selectId);
    const currentValue = select.value;

    // Keep the "All" option
    const allOption = select.options[0];

    // Clear existing options except first
    select.innerHTML = "";
    select.appendChild(allOption);

    // Add new options
    options.forEach((option) => {
      const optionEl = document.createElement("option");
      optionEl.value = option;
      optionEl.textContent = option;
      select.appendChild(optionEl);
    });

    // Restore previous selection if it exists
    if (currentValue && options.includes(currentValue)) {
      select.value = currentValue;
    }
  }

  async loadAudiobooks() {
    this.showLoading(true);

    try {
      // Build query parameters
      const params = new URLSearchParams({
        page: this.currentPage,
        per_page: this.perPage,
      });

      if (this.currentFilters.search)
        params.append("search", this.currentFilters.search);
      if (this.currentFilters.author)
        params.append("author", this.currentFilters.author);
      if (this.currentFilters.narrator)
        params.append("narrator", this.currentFilters.narrator);
      if (this.currentCollection)
        params.append("collection", this.currentCollection);
      if (this.currentFilters.sort)
        params.append("sort", this.currentFilters.sort);
      if (this.currentFilters.order)
        params.append("order", this.currentFilters.order);

      const data = await api.get(`${API_BASE}/audiobooks?${params}`, { toast: false });

      this.totalPages = data.pagination.total_pages;
      this.totalCount = data.pagination.total_count;

      this.browseBooks = data.audiobooks;
      this.renderBooks(data.audiobooks);
      this.renderPagination(data.pagination);
      this.updateResultsInfo(data.pagination);

      // Update download button visibility based on user permissions
      this.updateDownloadButtons();

      // Overlay translated metadata on book cards if locale is non-English
      this.applyBookTranslations();

      // Scroll to top
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      console.error("Error loading audiobooks:", error);
      document.getElementById("books-grid").innerHTML = `
                <p style="color: var(--parchment); text-align: center; grid-column: 1/-1;">
                    Error loading audiobooks. Please ensure the API server is running.
                    <br><br>
                    Run: <code style="background: var(--wood-dark); padding: 0.5rem; border-radius: 4px;">
                        ./launch.sh
                    </code>
                </p>
            `;
    } finally {
      this.showLoading(false);
    }
  }

  renderBooks(books) {
    const grid = document.getElementById("books-grid");
    grid.classList.remove("grouped-view");
    grid.classList.toggle("list-view", this.viewMode === "list");

    if (books.length === 0) {
      grid.innerHTML = `
                <p style="color: var(--parchment); text-align: center; grid-column: 1/-1;">
                    No audiobooks found matching your filters.
                </p>
            `;
      return;
    }

    grid.innerHTML = books.map((book) => this.createBookCard(book)).join("");
  }

  createBookCard(book) {
    const formatQuality = book.format ? book.format.toUpperCase() : "M4B";
    const quality = book.quality ? ` ${book.quality}` : "";
    const hasSupplement = book.supplement_count > 0;
    const hasEditions = book.edition_count && book.edition_count > 1;

    // Check for saved playback position (lightweight localStorage read)
    const savedPosition = getLocalPosition(book.id);
    const percentComplete = getLocalPercentComplete(book.id);
    const hasContinue = savedPosition !== null;

    return `
            <div class="book-card" data-id="${book.id}">
                <div class="book-cover">
                    ${
                      book.cover_path
                        ? `<img src="/covers/${book.cover_path}" alt="${this.escapeHtml(book.title)}" onerror="if(!this.dataset.retries){this.dataset.retries='0';}var r=parseInt(this.dataset.retries);if(r<2){this.dataset.retries=r+1;var s=this;setTimeout(function(){s.src=s.src.split('?')[0]+'?r='+Date.now()},500*(r+1));}else{this.parentElement.innerHTML='<span class=\\'book-cover-placeholder\\'>📖</span>';}">`
                        : '<span class="book-cover-placeholder">📖</span>'
                    }
                    ${hasSupplement ? `<span class="supplement-badge" title="${t("book.hasPdf")}" onclick="event.stopPropagation(); library.showSupplements(${book.id})">PDF</span>` : ""}
                    ${""/* Play button always resumes from saved position */}
                    ${hasEditions ? `<span class="editions-badge" title="${t("book.editions", { n: book.edition_count })}" onclick="event.stopPropagation(); library.toggleEditions(${book.id})">${t("book.editions", { n: book.edition_count })}</span>` : ""}
                </div>
                <div class="book-title">${this.escapeHtml(book.title)}</div>
                ${book.author ? `<div class="book-author">${t("book.byAuthor", { author: this.escapeHtml(book.author) })}</div>` : ""}
                ${book.narrator ? `<div class="book-narrator">${t("book.narratedByName", { narrator: this.escapeHtml(book.narrator === "Unknown Narrator" ? t("book.unknownNarrator") : book.narrator) })}</div>` : ""}
                ${book.series ? `<div class="book-series">(${this.escapeHtml(book.series)}${book.series_sequence ? `, ${t("book.seriesBook", { n: book.series_sequence })}` : ""})</div>` : ""}
                <div class="book-meta">
                    <span class="book-format">${formatQuality}${quality}</span>
                    <span class="book-duration">${book.duration_formatted || `${Math.round(book.duration_hours || 0)}h`}</span>
                </div>
                ${
                  hasContinue
                    ? `
                <div class="book-progress">
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: ${percentComplete}%"></div>
                    </div>
                    <span class="progress-text">${percentComplete}%</span>
                </div>
                `
                    : ""
                }
                <div class="book-actions">
                    <button class="btn-play" onclick="event.stopPropagation(); shellPlay(${JSON.stringify(book).replace(/"/g, "&quot;")}, true)" title="${hasContinue ? t("book.resumeFrom", { position: formatPlaybackTime(savedPosition.position) }) : t("book.playFromBeginning")}">${t("book.playFull")}</button>
                    <button class="btn-download download-button" style="display: none;" onclick="event.stopPropagation(); library.downloadAudiobook(${book.id})" title="${t("book.downloadTooltip")}">
                        ${t("book.downloadFull")}
                    </button>
                </div>
                ${hasEditions ? '<div class="book-editions" data-book-id="' + book.id + '" style="display: none;"></div>' : ""}
            </div>
        `;
  }

  // ============================================================
  // Grouped view — collapsible author/narrator groups
  // ============================================================

  async loadGroupedBooks(groupBy) {
    this.showLoading(true);

    try {
      const data = await api.get(`${API_BASE}/audiobooks/grouped?by=${encodeURIComponent(groupBy)}`, { toast: false });

      this.renderGroupedBooks(data, groupBy);
      this.applyBookTranslations();

      // Hide pagination — grouped view shows all books
      const paginationEl = document.getElementById("pagination");
      if (paginationEl) paginationEl.textContent = "";
      const resultsInfo = document.getElementById("results-info");
      if (resultsInfo) {
        resultsInfo.textContent = `${data.total_books} books in ${data.total_groups} ${groupBy} groups`;
      }

      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      console.error("Error loading grouped books:", error);
      const grid = document.getElementById("books-grid");
      grid.textContent = "";
      const errorMsg = document.createElement("p");
      errorMsg.style.cssText =
        "color: var(--parchment); text-align: center; grid-column: 1/-1;";
      errorMsg.textContent = t("library.errorGrouped");
      grid.appendChild(errorMsg);
    } finally {
      this.showLoading(false);
    }
  }

  renderGroupedBooks(data, groupBy) {
    const grid = document.getElementById("books-grid");

    if (!data.groups || data.groups.length === 0) {
      grid.textContent = "";
      const emptyMsg = document.createElement("p");
      emptyMsg.style.cssText =
        "color: var(--parchment); text-align: center; grid-column: 1/-1;";
      emptyMsg.textContent = t("book.noGrouped");
      grid.appendChild(emptyMsg);
      return;
    }

    // Build grouped view using DOM methods for safety
    grid.textContent = "";
    grid.classList.add("grouped-view");

    data.groups.forEach((group, idx) => {
      const groupId = `group-${groupBy}-${idx}`;
      const name = group.key.name;
      const bookCount = group.books.length;

      // Section wrapper
      const section = document.createElement("div");
      section.className = "grouped-section";
      section.dataset.groupId = groupId;

      // Collapsible header button
      const header = document.createElement("button");
      header.className = "grouped-header";
      header.title = `Click to expand/collapse ${name}`;
      header.addEventListener("click", () => this.toggleGroup(groupId));

      const nameSpan = document.createElement("span");
      nameSpan.className = "grouped-header-name";
      nameSpan.textContent = name;

      const countSpan = document.createElement("span");
      countSpan.className = "grouped-header-count";
      countSpan.textContent = bookCount === 1 ? t("book.bookCount", { n: bookCount }) : t("book.bookCountPlural", { n: bookCount });

      const arrow = document.createElement("span");
      arrow.className = "grouped-header-arrow";
      arrow.textContent = "\u25B8"; // ▸

      header.appendChild(nameSpan);
      header.appendChild(countSpan);
      header.appendChild(arrow);
      section.appendChild(header);

      // Books container (hidden by default)
      const booksContainer = document.createElement("div");
      booksContainer.className = "grouped-books";
      booksContainer.id = groupId;
      booksContainer.style.display = "none";

      const booksGrid = document.createElement("div");
      booksGrid.className = "grouped-books-grid";
      // Book cards use the existing createBookCard which returns safe HTML
      // (all user content passed through escapeHtml)
      booksGrid.innerHTML = group.books // nosec: createBookCard escapes all user data
        .map((book) => this.createBookCard(book))
        .join("");

      booksContainer.appendChild(booksGrid);
      section.appendChild(booksContainer);
      grid.appendChild(section);
    });
  }

  toggleGroup(groupId) {
    const booksContainer = document.getElementById(groupId);
    const section = booksContainer?.closest(".grouped-section");
    if (!booksContainer || !section) return;

    const isVisible = booksContainer.style.display !== "none";
    booksContainer.style.display = isVisible ? "none" : "block";
    section.classList.toggle("expanded", !isVisible);

    const arrow = section.querySelector(".grouped-header-arrow");
    if (arrow) arrow.textContent = isVisible ? "\u25B8" : "\u25BE"; // ▸ / ▾
  }

  async toggleEditions(bookId) {
    const editionsContainer = document.querySelector(
      `.book-editions[data-book-id="${bookId}"]`,
    );
    if (!editionsContainer) return;

    // Toggle visibility
    const isVisible = editionsContainer.style.display !== "none";

    if (isVisible) {
      // Hide editions
      editionsContainer.style.display = "none";
    } else {
      // Show editions - fetch if not already loaded
      if (!editionsContainer.dataset.loaded) {
        try {
          const data = await api.get(`${API_BASE}/audiobooks/${bookId}/editions`, { toast: false });

          if (data.editions && data.editions.length > 0) {
            editionsContainer.innerHTML = this.renderEditions(data.editions);
            editionsContainer.dataset.loaded = "true";
          } else {
            editionsContainer.innerHTML =
              '<p style="padding: 1rem; text-align: center;">No other editions found.</p>';
          }
        } catch (error) {
          console.error("Error loading editions:", error);
          editionsContainer.innerHTML =
            '<p style="padding: 1rem; color: #c0392b;">Error loading editions.</p>';
        }
      }
      editionsContainer.style.display = "block";
    }
  }

  renderEditions(editions) {
    return `
            <div class="editions-header">Available Editions</div>
            <div class="editions-list">
                ${editions.map((edition) => this.renderEditionItem(edition)).join("")}
            </div>
        `;
  }

  renderEditionItem(edition) {
    const formatQuality = edition.format ? edition.format.toUpperCase() : "M4B";
    const quality = edition.quality ? ` ${edition.quality}` : "";
    const savedPosition = getLocalPosition(edition.id);
    const percentComplete = getLocalPercentComplete(edition.id);
    const hasContinue = savedPosition !== null;

    return `
            <div class="edition-item">
                <div class="edition-info">
                    <div class="edition-narrator">🎙️ ${this.escapeHtml(edition.narrator || t("book.unknownNarrator"))}</div>
                    <div class="edition-details">
                        <span class="edition-format">${formatQuality}${quality}</span>
                        <span class="edition-duration">${edition.duration_formatted || `${Math.round(edition.duration_hours || 0)}h`}</span>
                        <span class="edition-size">${Math.round(edition.file_size_mb)}MB</span>
                        ${hasContinue ? `<span class="edition-progress">${percentComplete}% played</span>` : ""}
                    </div>
                </div>
                <div class="edition-actions">
                    <button class="btn-play-edition" onclick="event.stopPropagation(); shellPlay(${JSON.stringify(edition).replace(/"/g, "&quot;")}, true)">▶ Play</button>
                </div>
            </div>
        `;
  }

  async showSupplements(audiobookId) {
    try {
      const data = await api.get(`${API_BASE}/audiobooks/${audiobookId}/supplements`, { toast: false });

      if (data.supplements && data.supplements.length > 0) {
        // Open the first supplement (typically PDF)
        const supplement = data.supplements[0];
        window.open(
          `${API_BASE}/supplements/${supplement.id}/download`,
          "_blank",
        );
      } else {
        alert(t("book.noSupplements"));
      }
    } catch (error) {
      console.error("Error loading supplements:", error);
      alert(t("book.errorSupplements"));
    }
  }

  showBookDetail(bookId) {
    // Find book in cached data (browse or my-library tab)
    const book =
      this.browseBooks.find((b) => b.id === bookId) ||
      this.myLibraryBooks.find((b) => (b.bookId || b.id) === bookId);
    if (!book) return;

    const savedPosition = getLocalPosition(book.id);
    const percentComplete = getLocalPercentComplete(book.id);
    const hasContinue = savedPosition !== null;
    const formatQuality = book.format ? book.format.toUpperCase() : "M4B";
    const quality = book.quality ? ` ${book.quality}` : "";
    const hasSupplement = book.supplement_count > 0;

    // Remove existing detail modal if any
    document.getElementById("book-detail-modal")?.remove();

    // Build modal via DOM API for XSS safety on user-supplied fields
    const modal = document.createElement("div");
    modal.id = "book-detail-modal";
    modal.className = "modal book-detail-sheet show";

    const content = document.createElement("div");
    content.className = "modal-content book-detail-content";

    // Header
    const header = document.createElement("div");
    header.className = "modal-header";
    const h2 = document.createElement("h2");
    h2.textContent = t("book.detailsTitle");
    const closeBtn = document.createElement("button");
    closeBtn.className = "modal-close";
    closeBtn.title = t("book.closeDialog");
    closeBtn.textContent = "\u00D7";
    header.appendChild(h2);
    header.appendChild(closeBtn);

    // Body
    const body = document.createElement("div");
    body.className = "modal-body book-detail-body";

    // Cover
    const coverDiv = document.createElement("div");
    coverDiv.className = "detail-cover";
    if (book.cover_path) {
      const img = document.createElement("img");
      img.src = "/covers/" + book.cover_path;
      img.alt = book.title || "";
      coverDiv.appendChild(img);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "book-cover-placeholder";
      placeholder.style.fontSize = "3rem";
      placeholder.textContent = "\u{1F4D6}";
      coverDiv.appendChild(placeholder);
    }

    // Info section
    const info = document.createElement("div");
    info.className = "detail-info";

    const titleEl = document.createElement("div");
    titleEl.className = "detail-title";
    titleEl.textContent = book.title || t("book.unknownTitle");
    info.appendChild(titleEl);

    if (book.author) {
      const authorEl = document.createElement("div");
      authorEl.className = "detail-author";
      authorEl.textContent = t("book.byAuthor", { author: book.author });
      info.appendChild(authorEl);
    }

    if (book.narrator) {
      const narratorEl = document.createElement("div");
      narratorEl.className = "detail-narrator";
      narratorEl.textContent = t("book.narratedByName", { narrator: book.narrator });
      info.appendChild(narratorEl);
    }

    const meta = document.createElement("div");
    meta.className = "detail-meta";
    const fmtSpan = document.createElement("span");
    fmtSpan.textContent = formatQuality + quality;
    const durSpan = document.createElement("span");
    durSpan.textContent =
      book.duration_formatted || Math.round(book.duration_hours || 0) + "h";
    meta.appendChild(fmtSpan);
    meta.appendChild(durSpan);
    info.appendChild(meta);

    if (hasContinue) {
      const progressDiv = document.createElement("div");
      progressDiv.className = "detail-progress";
      const barBg = document.createElement("div");
      barBg.className = "progress-bar-bg";
      const barFill = document.createElement("div");
      barFill.className = "progress-bar-fill";
      barFill.style.width = percentComplete + "%";
      barBg.appendChild(barFill);
      const pctText = document.createElement("span");
      pctText.className = "progress-text";
      pctText.textContent = t("book.percentComplete", { n: percentComplete });
      progressDiv.appendChild(barBg);
      progressDiv.appendChild(pctText);
      info.appendChild(progressDiv);
    }

    if (hasSupplement) {
      const badge = document.createElement("div");
      badge.className = "detail-badge";
      badge.textContent = t("book.pdfSupplement");
      info.appendChild(badge);
    }

    // Actions
    const actions = document.createElement("div");
    actions.className = "detail-actions";

    const playBtn = document.createElement("button");
    playBtn.className = "btn-play";
    playBtn.textContent = t("book.playFull");
    playBtn.title = hasContinue
      ? t("book.resumeFrom", { position: formatPlaybackTime(savedPosition.position) })
      : t("book.playFromBeginning");
    playBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      shellPlay(book, true);
      modal.remove();
    });

    const downloadBtn = document.createElement("button");
    downloadBtn.className = "btn-download download-button";
    downloadBtn.style.display = "none";
    downloadBtn.textContent = t("book.downloadFull");
    downloadBtn.title = t("book.downloadTooltip");
    downloadBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      library.downloadAudiobook(book.id);
    });

    actions.appendChild(playBtn);
    actions.appendChild(downloadBtn);

    body.appendChild(coverDiv);
    body.appendChild(info);
    body.appendChild(actions);

    content.appendChild(header);
    content.appendChild(body);
    modal.appendChild(content);

    // Close on backdrop click
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.remove();
    });
    closeBtn.addEventListener("click", () => modal.remove());

    document.body.appendChild(modal);

    // Update download button visibility for current user permissions
    this.updateDownloadButtons();
  }

  setupCompactCardTap() {
    const grid = document.getElementById("books-grid");
    if (!grid) return;

    grid.addEventListener("click", (e) => {
      // Only active at compact viewports — desktop cards show all info inline
      const isCompact = window.matchMedia(
        "(max-width: 480px), " +
          "(orientation: landscape) and (max-height: 500px) and (max-width: 960px), " +
          "(orientation: landscape) and (max-height: 700px) and (max-width: 1024px)",
      ).matches;
      if (!isCompact) return;

      // Ignore clicks on the hide/unhide checkbox
      if (e.target.classList.contains("library-card-checkbox")) return;

      const card = e.target.closest(".book-card");
      if (!card) return;

      const bookId = parseInt(card.dataset.id, 10);
      if (bookId) this.showBookDetail(bookId);
    });
  }

  escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  updateResultsInfo(pagination) {
    const el = document.getElementById("showing-count");
    if (pagination.total_count === 0) {
      el.textContent = t("book.noBooks");
      return;
    }
    const start = (pagination.page - 1) * pagination.per_page + 1;
    const end = Math.min(
      pagination.page * pagination.per_page,
      pagination.total_count,
    );
    el.textContent = t("library.showing", { start: start, end: end, total: pagination.total_count.toLocaleString() });
  }

  renderPagination(pagination) {
    const html = this.createPaginationHTML(pagination);
    document.getElementById("top-pagination").innerHTML = html;
    document.getElementById("bottom-pagination").innerHTML = html;
  }

  createPaginationHTML(pagination) {
    const maxButtons = 7;
    let pages = [];

    if (pagination.total_pages <= maxButtons) {
      // Show all pages
      pages = Array.from({ length: pagination.total_pages }, (_, i) => i + 1);
    } else {
      // Show smart pagination
      if (pagination.page <= 4) {
        pages = [1, 2, 3, 4, 5, "...", pagination.total_pages];
      } else if (pagination.page >= pagination.total_pages - 3) {
        pages = [
          1,
          "...",
          ...Array.from(
            { length: 5 },
            (_, i) => pagination.total_pages - 4 + i,
          ),
        ];
      } else {
        pages = [
          1,
          "...",
          pagination.page - 1,
          pagination.page,
          pagination.page + 1,
          "...",
          pagination.total_pages,
        ];
      }
    }

    let html = `
            <button class="pagination-btn" onclick="library.goToPage(${pagination.page - 1})"
                    ${!pagination.has_prev ? "disabled" : ""}>
                ${t("library.prevPage")}
            </button>
        `;

    pages.forEach((page) => {
      if (page === "...") {
        html +=
          '<span style="padding: 0 0.5rem; color: var(--parchment);">...</span>';
      } else {
        html += `
                    <button class="pagination-btn ${page === pagination.page ? "active" : ""}"
                            onclick="library.goToPage(${page})">
                        ${page}
                    </button>
                `;
      }
    });

    html += `
            <button class="pagination-btn" onclick="library.goToPage(${pagination.page + 1})"
                    ${!pagination.has_next ? "disabled" : ""}>
                ${t("library.nextPage")}
            </button>
        `;

    return html;
  }

  goToPage(page) {
    if (page < 1 || page > this.totalPages) return;
    this.currentPage = page;
    this.loadAudiobooks();
  }

  // ============================================
  // Tab Management (Browse All / My Library)
  // ============================================

  /**
   * Initialize tab bar visibility and click handlers.
   * Tab bar is shown when logged in OR when auth is disabled (default user).
   */
  initTabs() {
    const tabContainer = document.getElementById("library-tabs");
    if (!tabContainer) return;

    // Show tabs when logged in or when auth is disabled (default user)
    if ((this.authEnabled && this.user) || !this.authEnabled) {
      tabContainer.style.display = "flex";
    }

    // Add click handlers to tab buttons
    tabContainer.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tabName = btn.dataset.tab;
        if (tabName !== this.currentTab) {
          this.switchTab(tabName);
        }
      });
    });
  }

  /**
   * Switch between "browse" and "my-library" tabs.
   * @param {string} tabName - "browse" or "my-library"
   */
  switchTab(tabName) {
    this.currentTab = tabName;

    // Update active tab button styling
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    // Toggle search/filter visibility (hide for My Library)
    const searchSection = document.querySelector(".search-section");
    const resultsInfo = document.querySelector(".results-info");
    const paginationSection = document.querySelector(".pagination-section");

    if (tabName === "my-library") {
      // Hide browse-specific UI
      if (searchSection) searchSection.style.display = "none";
      if (resultsInfo) resultsInfo.style.display = "none";
      if (paginationSection) paginationSection.style.display = "none";

      // Show hide/unhide controls
      const hideBtn = document.getElementById("hide-selected-btn");
      const hiddenPill = document.getElementById("hidden-books-btn");
      if (hideBtn) hideBtn.style.display = "";
      if (hiddenPill) hiddenPill.style.display = "";

      this.loadMyLibrary();
    } else {
      // Restore browse UI
      if (searchSection) searchSection.style.display = "";
      if (resultsInfo) resultsInfo.style.display = "";
      if (paginationSection) paginationSection.style.display = "";

      // Hide hide/unhide controls
      const hideBtn = document.getElementById("hide-selected-btn");
      const hiddenPill = document.getElementById("hidden-books-btn");
      if (hideBtn) hideBtn.style.display = "none";
      if (hiddenPill) hiddenPill.style.display = "none";
      this.viewingHidden = false;
      this.selectedBookIds.clear();

      this.loadAudiobooks();
    }
  }

  /**
   * Load the user's personal library (books they've interacted with).
   * Fetches from /api/user/library and position data, then renders cards.
   */
  async loadMyLibrary() {
    this.showLoading(true);
    const grid = document.getElementById("books-grid");

    try {
      // Fetch user's library (active or hidden books)
      const url = this.viewingHidden
        ? `${API_BASE}/user/library?hidden=true`
        : `${API_BASE}/user/library`;
      const data = await api.get(url, { toast: false });
      const books = data.books || [];

      if (books.length === 0) {
        // XSS safe: static content only, no user input
        const emptyMsg = document.createElement("p");
        emptyMsg.style.cssText =
          "color: var(--parchment); text-align: center; grid-column: 1/-1;";
        emptyMsg.textContent = this.viewingHidden
          ? t("library.emptyHidden")
          : t("library.emptyLibrary");
        while (grid.firstChild) grid.removeChild(grid.firstChild);
        grid.appendChild(emptyMsg);
        return;
      }

      // Fetch position data for each book that has a position
      const booksWithPositions = await this.enrichBooksWithPositions(books);

      // Sort by most recently interacted with (books with positions first, then by ID desc)
      booksWithPositions.sort((a, b) => {
        // Books with recent position updates come first
        if (a.positionData && b.positionData) {
          const aTime = a.positionData.local_position_updated || "";
          const bTime = b.positionData.local_position_updated || "";
          return bTime.localeCompare(aTime);
        }
        if (a.positionData) return -1;
        if (b.positionData) return 1;
        return b.id - a.id;
      });

      this.myLibraryBooks = booksWithPositions;

      // Build cards using safe DOM construction
      while (grid.firstChild) grid.removeChild(grid.firstChild);
      booksWithPositions.forEach((book) => {
        const card = this.buildMyLibraryCardElement(book);
        grid.appendChild(card);
      });

      // Update download button visibility
      this.updateDownloadButtons();

      // Fetch hidden count (when viewing active books)
      if (!this.viewingHidden) {
        this.updateHiddenCountPill();
      }
    } catch (error) {
      console.error("Error loading My Library:", error);
      const errMsg = document.createElement("p");
      errMsg.style.cssText =
        "color: var(--parchment); text-align: center; grid-column: 1/-1;";
      errMsg.textContent = t("library.errorLoading");
      while (grid.firstChild) grid.removeChild(grid.firstChild);
      grid.appendChild(errMsg);
    } finally {
      this.showLoading(false);
    }
  }

  /**
   * Enrich books with position data from the position API.
   * @param {Array} books - Books from /api/user/library
   * @returns {Array} Books with positionData attached
   */
  async enrichBooksWithPositions(books) {
    const enriched = books.map((book) => ({ ...book, positionData: null }));
    const positionBooks = enriched.filter((b) => b.has_position);

    if (positionBooks.length === 0) return enriched;

    const results = await Promise.all(
      positionBooks.map(async (book) => {
        try {
          return await api.get(`${API_BASE}/position/${book.id}`, { toast: false });
        } catch (e) {
          console.warn("Could not fetch position for book %d:", book.id, e);
          return null;
        }
      }),
    );

    positionBooks.forEach((book, i) => {
      book.positionData = results[i];
    });

    return enriched;
  }

  /**
   * Build a My Library card DOM element with progress bar and listening info.
   * Uses safe DOM construction (createElement/textContent) — no innerHTML.
   * @param {Object} book - Book object with positionData
   * @returns {HTMLElement} Card element
   */
  buildMyLibraryCardElement(book) {
    const pos = book.positionData;
    const percent = pos ? pos.percent_complete : 0;
    const durationHuman = pos
      ? pos.duration_human
      : this.formatDuration(book.duration_hours);
    const positionHuman = pos ? pos.local_position_human : "0h 0m";
    const progressText = pos
      ? `${positionHuman} / ${durationHuman} — ${percent}%`
      : t("library.notStarted", { duration: durationHuman });

    const card = document.createElement("div");
    card.className = "book-card";
    card.dataset.id = book.id;

    // Checkbox for hide/unhide selection
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "library-card-checkbox";
    checkbox.dataset.bookId = book.id;
    checkbox.title = t("book.selectBook");
    checkbox.checked = this.selectedBookIds.has(book.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        this.selectedBookIds.add(book.id);
      } else {
        this.selectedBookIds.delete(book.id);
      }
      this.updateHideButtonState();
    });
    card.appendChild(checkbox);

    // Cover section
    const coverDiv = document.createElement("div");
    coverDiv.className = "book-cover";
    if (book.cover_path) {
      const img = document.createElement("img");
      img.src = `/covers/${book.cover_path}`;
      img.alt = book.title;
      img.onerror = function () {
        const retries = parseInt(this.dataset.retries || "0", 10);
        if (retries < 2) {
          this.dataset.retries = retries + 1;
          const self = this;
          setTimeout(function () {
            self.src = self.src.split("?")[0] + "?r=" + Date.now();
          }, 500 * (retries + 1));
          return;
        }
        const parent = this.parentElement;
        if (!parent) return;
        parent.textContent = "";
        const placeholder = document.createElement("span");
        placeholder.className = "book-cover-placeholder";
        placeholder.textContent = "\u{1F4D6}";
        parent.appendChild(placeholder);
      };
      coverDiv.appendChild(img);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "book-cover-placeholder";
      placeholder.textContent = "\u{1F4D6}";
      coverDiv.appendChild(placeholder);
    }
    card.appendChild(coverDiv);

    // Title
    const titleDiv = document.createElement("div");
    titleDiv.className = "book-title";
    titleDiv.textContent = book.title;
    card.appendChild(titleDiv);

    // Author
    if (book.author) {
      const authorDiv = document.createElement("div");
      authorDiv.className = "book-author";
      authorDiv.textContent = t("book.byAuthor", { author: book.author });
      card.appendChild(authorDiv);
    }

    // Progress bar
    const progressDiv = document.createElement("div");
    progressDiv.className = "book-progress-bar";

    const progressBg = document.createElement("div");
    progressBg.className = "progress-bar-bg";
    const progressFill = document.createElement("div");
    progressFill.className = "book-progress-fill";
    progressFill.style.width = `${percent}%`;
    progressBg.appendChild(progressFill);
    progressDiv.appendChild(progressBg);

    const progressSpan = document.createElement("span");
    progressSpan.className = "book-progress-text";
    progressSpan.textContent = progressText;
    progressDiv.appendChild(progressSpan);
    card.appendChild(progressDiv);

    // My Library metadata (timestamps for last listened / downloaded)
    if (book.last_listened_at || book.downloaded_at) {
      const metaDiv = document.createElement("div");
      metaDiv.className = "my-library-meta";
      const dateOpts = { month: "short", day: "numeric", year: "numeric" };
      const dateLang = i18n.getLocale() || "en";
      if (book.last_listened_at) {
        const histSpan = document.createElement("span");
        const listenDate = new Date(book.last_listened_at).toLocaleDateString(
          dateLang,
          dateOpts,
        );
        histSpan.textContent = "\u{1F50A} " + t("book.lastListened", { date: listenDate });
        metaDiv.appendChild(histSpan);
      }
      if (book.downloaded_at) {
        const dlSpan = document.createElement("span");
        const dlDate = new Date(book.downloaded_at).toLocaleDateString(
          dateLang,
          dateOpts,
        );
        dlSpan.textContent = "\u{2B07} " + t("book.downloaded", { date: dlDate });
        metaDiv.appendChild(dlSpan);
      }
      card.appendChild(metaDiv);
    }

    // Action buttons
    const actionsDiv = document.createElement("div");
    actionsDiv.className = "book-actions";

    const bookData = {
      id: book.id,
      title: book.title,
      author: book.author,
      cover_path: book.cover_path,
      format: book.format,
      duration_hours: book.duration_hours,
    };

    const playBtn = document.createElement("button");
    playBtn.className = "btn-play";
    playBtn.textContent = t("book.playFull");
    playBtn.title =
      percent > 0 ? t("book.resumeFrom", { position: positionHuman }) : t("book.playFromBeginning");
    playBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      shellPlay(bookData, true);
    });
    actionsDiv.appendChild(playBtn);

    const downloadBtn = document.createElement("button");
    downloadBtn.className = "btn-download download-button";
    downloadBtn.style.display = "none";
    downloadBtn.title = t("book.downloadTooltip");
    downloadBtn.textContent = t("book.downloadFull");
    downloadBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      library.downloadAudiobook(book.id);
    });
    actionsDiv.appendChild(downloadBtn);

    card.appendChild(actionsDiv);
    return card;
  }

  /**
   * Format duration_hours (float) into human-readable string.
   * @param {number} hours - Duration in hours (e.g., 8.5)
   * @returns {string} Human readable (e.g., "8h 30m")
   */
  formatDuration(hours) {
    if (!hours || hours <= 0) return "0h 0m";
    const h = Math.floor(hours);
    const m = Math.round((hours - h) * 60);
    return `${h}h ${m}m`;
  }

  /** Update the hide/unhide button to appear raised (active) when books are selected */
  updateHideButtonState() {
    const btn = document.getElementById("hide-selected-btn");
    if (!btn) return;
    if (this.selectedBookIds.size > 0) {
      btn.classList.remove("depressed");
      btn.classList.add("raised");
      btn.textContent = this.viewingHidden
        ? t("library.unhideCount", { n: this.selectedBookIds.size })
        : t("library.hideCount", { n: this.selectedBookIds.size });
    } else {
      btn.classList.add("depressed");
      btn.classList.remove("raised");
      btn.textContent = this.viewingHidden ? t("library.unhideSelected") : t("library.hideSelected");
    }
  }

  /** Fetch hidden count and update the pill badge */
  async updateHiddenCountPill() {
    const pill = document.getElementById("hidden-books-btn");
    if (!pill) return;
    try {
      const data = await api.get(`${API_BASE}/user/library?hidden=true`, { toast: false });
      const count = (data.books || []).length;
      if (count > 0) {
        pill.textContent = this.viewingHidden
          ? t("library.myLibrary")
          : t("library.hiddenCount", { n: count });
        pill.style.display = "";
      } else {
        pill.style.display = "none";
      }
    } catch (e) {
      // Silently fail — pill just stays hidden
    }
  }

  /** Hide or unhide selected books */
  async hideUnhideSelected() {
    if (this.selectedBookIds.size === 0) return;

    const ids = Array.from(this.selectedBookIds);
    const endpoint = this.viewingHidden ? "unhide" : "hide";

    try {
      await api.post(`${API_BASE}/user/library/${endpoint}`, { audiobook_ids: ids }, { toast: false });

      // Animate removal: fade out selected cards, then reload
      const grid = document.getElementById("books-grid");
      ids.forEach((id) => {
        const card = grid.querySelector(`.book-card[data-id="${id}"]`);
        if (card) {
          card.style.transition = "opacity 0.25s ease, transform 0.25s ease";
          card.style.opacity = "0";
          card.style.transform = "scale(0.95)";
        }
      });

      // Wait for animation, then reload
      setTimeout(() => {
        this.selectedBookIds.clear();
        this.loadMyLibrary();
      }, 300);
    } catch (e) {
      console.error("Error during", endpoint, e);
    }
  }

  /** Toggle between active and hidden views */
  toggleHiddenView() {
    this.viewingHidden = !this.viewingHidden;
    this.selectedBookIds.clear();

    // Update button labels
    const hideBtn = document.getElementById("hide-selected-btn");
    if (hideBtn) {
      hideBtn.textContent = this.viewingHidden
        ? t("library.unhideSelected")
        : t("library.hideSelected");
      hideBtn.classList.add("depressed");
      hideBtn.classList.remove("raised");
    }

    // Update pill text
    const pill = document.getElementById("hidden-books-btn");
    if (pill) {
      pill.textContent = this.viewingHidden ? t("library.myLibrary") : t("library.hidden");
    }

    this.loadMyLibrary();
  }

  setupEventListeners() {
    // Back Office gate — intercept clicks from non-admins
    const boLink = document.getElementById("admin-backoffice-link");
    if (boLink) {
      boLink.addEventListener("click", (e) => {
        if (boLink.getAttribute("data-locked") === "true") {
          e.preventDefault();
          this.showGuestGate(boLink);
        }
      });
    }

    // Search input with debounce
    let searchTimeout;
    document.getElementById("search-input").addEventListener("input", (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        this.currentFilters.search = e.target.value.trim();
        this.currentPage = 1;
        this.loadAudiobooks();
      }, 500);
    });

    // Clear search
    document.getElementById("clear-search").addEventListener("click", () => {
      document.getElementById("search-input").value = "";
      // Clear author autocomplete
      this.selectAuthor("");
      // Clear narrator autocomplete
      this.selectNarrator("");
      // Clear collection filter
      this.currentCollection = "";
      this.renderCollectionButtons();
      this.currentFilters = {
        search: "",
        author: "",
        narrator: "",
        sort: "title",
        order: "asc",
      };
      localStorage.setItem("audiobook_sort_order", "title_asc");
      if (this.user) {
        api.patch("/api/user/preferences", { sort_order: "title_asc" },
          { toast: false, keepalive: true }).catch(() => {});
      }
      this.currentPage = 1;
      this.loadAudiobooks();
    });

    // Sort filter — detect grouped mode vs flat mode + persist preference
    document.getElementById("sort-filter").addEventListener("change", (e) => {
      const [sort, order] = e.target.value.split(":");
      this.currentFilters.sort = sort;
      this.currentFilters.order = order;
      this.currentPage = 1;

      // Persist sort preference (localStorage for instant restore + API for cross-device sync)
      const prefValue = sort + "_" + (order || "asc");
      localStorage.setItem("audiobook_sort_order", prefValue);
      if (this.user) {
        api.patch("/api/user/preferences", { sort_order: prefValue },
          { toast: false, keepalive: true }).catch(() => {});
      }

      if (sort === "grouped_author" || sort === "grouped_narrator") {
        const groupBy = sort === "grouped_author" ? "author" : "narrator";
        this.loadGroupedBooks(groupBy);
      } else {
        this.loadAudiobooks();
      }
    });

    // Per page
    document.getElementById("per-page").addEventListener("change", (e) => {
      this.perPage = parseInt(e.target.value);
      this.currentPage = 1;
      localStorage.setItem("audiobook_items_per_page", e.target.value);
      if (this.user) {
        api.patch("/api/user/preferences", { items_per_page: e.target.value },
          { toast: false, keepalive: true }).catch(() => {});
      }
      this.loadAudiobooks();
    });

    // Refresh button
    document
      .getElementById("refresh-btn")
      .addEventListener("click", async () => {
        await this.refreshLibrary();
      });

    // Setup sidebar events
    this.setupSidebarEvents();

    // Re-apply translations and re-render locale-sensitive UI on locale change.
    // Sidebar collections, filter chips, and book cards all contain t() output
    // captured at initial render — they must be rebuilt when the locale flips.
    document.addEventListener("localeChanged", () => {
      this.applyBookTranslations();
      this.loadCollections().catch((e) =>
        console.warn("loadCollections on localeChanged failed:", e)
      );
      // Re-render visible books so "Narrated by Unknown Narrator",
      // "Book N" sequence suffix, and other t() strings refresh.
      if (Array.isArray(this.browseBooks) && this.browseBooks.length > 0) {
        this.renderBooks(this.browseBooks);
        this.applyBookTranslations();
      }
    });
  }

  async refreshLibrary() {
    const refreshBtn = document.getElementById("refresh-btn");
    refreshBtn.disabled = true;
    refreshBtn.textContent = "↻ Refreshing...";

    try {
      // Purge browser caches (CSS/JS/image cache and service worker)
      if ("caches" in window) {
        const cacheNames = await caches.keys();
        await Promise.all(cacheNames.map((name) => caches.delete(name)));
      }

      // Purge Cloudflare CDN cache (non-fatal — best-effort)
      try {
        await api.post("/api/system/purge-cache", null, { toast: false });
      } catch {
        // CDN purge is non-fatal; log but don't block refresh
        console.warn("CDN cache purge unavailable");
      }

      // Reload stats, filters, and current page
      await this.loadStats();
      await this.loadFilters();
      await this.loadAudiobooks();

      // Silent on success — no notification needed
    } catch (error) {
      console.error("Error refreshing library:", error);
      this.showToast(
        "Failed to refresh library. Please try again.",
        "error",
      );
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "↻ Refresh";
    }
  }

  showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }
}

// Initialize library when DOM is loaded
let library;
document.addEventListener("DOMContentLoaded", () => {
  library = new AudiobookLibraryV2();
  // Expose to window for inline scripts (logout, etc.)
  window.library = library;

  // Wire up hide/unhide controls
  document
    .getElementById("hide-selected-btn")
    ?.addEventListener("click", () => library.hideUnhideSelected());
  document
    .getElementById("hidden-books-btn")
    ?.addEventListener("click", () => library.toggleHiddenView());
});

// AudioPlayer class removed — now in shell.js (ShellPlayer)
// PlaybackManager class removed — now in shell.js (ShellPlayer)
// Content pages delegate play/pause/seek to the shell via postMessage bridge (see bottom of file)

// ============================================
// DUPLICATE MANAGER
// ============================================

class DuplicateManager {
  constructor() {
    this.selectedIds = new Set();
    this.duplicateData = null;
    this.setupEventListeners();
  }

  setupEventListeners() {
    // Dropdown toggle
    const dropdownBtn = document.getElementById("duplicates-btn");
    const dropdownContent = document.getElementById("duplicates-menu");

    dropdownBtn?.addEventListener("click", (e) => {
      e.stopPropagation();
      dropdownContent.classList.toggle("show");
    });

    // Close dropdown when clicking outside
    document.addEventListener("click", () => {
      dropdownContent?.classList.remove("show");
    });

    // Dropdown menu items
    dropdownContent?.addEventListener("click", (e) => {
      const action = e.target.dataset.action;
      if (action) {
        e.preventDefault();
        dropdownContent.classList.remove("show");
        this.handleAction(action);
      }
    });

    // Modal close buttons
    document.querySelectorAll(".modal-close").forEach((btn) => {
      btn.addEventListener("click", () => {
        const modalId = btn.dataset.modal;
        this.closeModal(modalId);
      });
    });

    // Close modals when clicking backdrop
    document.querySelectorAll(".modal").forEach((modal) => {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) {
          modal.classList.remove("show");
        }
      });
    });

    // Toolbar buttons
    document
      .getElementById("select-all-duplicates")
      ?.addEventListener("click", () => {
        this.selectAllDuplicates();
      });

    document.getElementById("deselect-all")?.addEventListener("click", () => {
      this.deselectAll();
    });

    document
      .getElementById("delete-selected")
      ?.addEventListener("click", () => {
        this.confirmDelete();
      });

    // Duplicate mode tabs
    this.duplicateMode = "title"; // Default to title/author mode
    document.querySelectorAll(".mode-tab").forEach((tab) => {
      tab.addEventListener("click", (e) => {
        const mode = e.target.dataset.mode;
        if (mode !== this.duplicateMode) {
          this.duplicateMode = mode;
          document
            .querySelectorAll(".mode-tab")
            .forEach((t) => t.classList.remove("active"));
          e.target.classList.add("active");
          this.showDuplicates(mode);
        }
      });
    });

    // Confirmation modal
    document.getElementById("confirm-cancel")?.addEventListener("click", () => {
      this.closeModal("confirm-modal");
    });

    document.getElementById("confirm-delete")?.addEventListener("click", () => {
      this.executeDelete();
    });

    // Copy CLI command
    document
      .getElementById("copy-cli-command")
      ?.addEventListener("click", () => {
        const cmd = document.getElementById("cli-command").textContent;
        navigator.clipboard.writeText(cmd).then(() => {
          const btn = document.getElementById("copy-cli-command");
          btn.textContent = t("book.copied");
          setTimeout(() => (btn.textContent = t("book.copyClipboard")), 2000);
        });
      });
  }

  handleAction(action) {
    // Commands use relative paths from project root
    const cliCommands = {
      "hash-generate": {
        desc: "Generate SHA-256 hashes for all audiobooks. This may take several hours for large collections.",
        cmd: "cd library && python3 scripts/generate_hashes.py",
      },
      "hash-verify": {
        desc: "Verify a random sample of hashes to check for file corruption.",
        cmd: "cd library && python3 scripts/generate_hashes.py --verify 20",
      },
      "duplicates-report": {
        desc: "Generate a detailed duplicate report in the terminal.",
        cmd: "cd library && python3 scripts/find_duplicates.py",
      },
      "duplicates-json": {
        desc: "Export duplicate information to a JSON file.",
        cmd: "cd library && python3 scripts/find_duplicates.py --json -o duplicates.json",
      },
      "duplicates-dryrun": {
        desc: "Preview which files would be deleted without actually removing them.",
        cmd: "cd library && python3 scripts/find_duplicates.py --remove",
      },
      "duplicates-execute": {
        desc: "CAUTION: This will permanently delete duplicate files. The first copy of each audiobook is always protected.",
        cmd: "cd library && python3 scripts/find_duplicates.py --execute",
      },
    };

    switch (action) {
      case "hash-stats":
        this.showHashStats();
        break;
      case "show-duplicates":
        this.showDuplicates();
        break;
      default:
        if (cliCommands[action]) {
          this.showCLICommand(
            cliCommands[action].desc,
            cliCommands[action].cmd,
          );
        }
    }
  }

  openModal(modalId) {
    document.getElementById(modalId)?.classList.add("show");
  }

  closeModal(modalId) {
    document.getElementById(modalId)?.classList.remove("show");
  }

  showCLICommand(description, command) {
    document.getElementById("cli-description").textContent = description;
    document.getElementById("cli-command").textContent = command;
    this.openModal("cli-modal");
  }

  async showHashStats() {
    this.openModal("hash-stats-modal");
    const content = document.getElementById("hash-stats-content");
    content.innerHTML =
      '<div class="loading-spinner"></div><p>Loading statistics...</p>';

    try {
      const stats = await api.get(`${API_BASE}/hash-stats`, { toast: false });

      if (!stats.hash_column_exists) {
        content.innerHTML = `
                    <p>Hash column not found in database. Run hash generation first:</p>
                    <pre class="cli-command">cd library && python3 scripts/generate_hashes.py</pre>
                `;
        return;
      }

      content.innerHTML = `
                <div class="stats-grid">
                    <div class="stat-box">
                        <div class="stat-box-value">${stats.total_audiobooks.toLocaleString()}</div>
                        <div class="stat-box-label">Total Audiobooks</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-box-value">${stats.hashed_count.toLocaleString()}</div>
                        <div class="stat-box-label">With Hashes</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-box-value">${stats.unhashed_count.toLocaleString()}</div>
                        <div class="stat-box-label">Without Hashes</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-box-value">${stats.duplicate_groups}</div>
                        <div class="stat-box-label">Duplicate Groups</div>
                    </div>
                </div>
                <p><strong>Hash Progress:</strong></p>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" style="width: ${stats.hashed_percentage}%">
                        ${stats.hashed_percentage}%
                    </div>
                </div>
                ${
                  stats.unhashed_count > 0
                    ? `
                    <p>To generate remaining hashes, run:</p>
                    <pre class="cli-command">cd library && python3 scripts/generate_hashes.py</pre>
                `
                    : '<p style="color: #27ae60;">All audiobooks have been hashed!</p>'
                }
            `;
    } catch (error) {
      // Use safe DOM methods to avoid XSS - create element and set textContent
      const errorP = document.createElement("p");
      errorP.style.color = "#c0392b";
      errorP.textContent = `Error loading statistics: ${error.message}`;
      content.innerHTML = "";
      content.appendChild(errorP);
    }
  }

  async showDuplicates(mode = null) {
    this.openModal("duplicates-modal");
    this.selectedIds.clear();
    this.updateDeleteButton();

    // Use provided mode or current mode
    if (mode) {
      this.duplicateMode = mode;
    }
    const currentMode = this.duplicateMode || "title";

    const content = document.getElementById("duplicates-content");
    const summary = document.getElementById("duplicates-summary");
    // XSS safe: static content only
    content.textContent = "";
    const spinner = document.createElement("div");
    spinner.className = "loading-spinner";
    const loadingP = document.createElement("p");
    loadingP.textContent = t("common.loading");
    content.appendChild(spinner);
    content.appendChild(loadingP);
    summary.textContent = t("common.loading");

    try {
      // Choose endpoint based on mode
      const endpoint =
        currentMode === "hash" ? "duplicates" : "duplicates/by-title";
      const data = await api.get(`${API_BASE}/${endpoint}`, { toast: false });
      this.duplicateData = data;

      if (data.total_groups === 0) {
        summary.textContent = t("duplicates.noFound");
        const modeDesc =
          currentMode === "hash"
            ? t("duplicates.noHashDuplicates")
            : t("duplicates.noTitleDuplicates");
        // XSS safe: static translated content only
        content.textContent = "";
        const wrapper = document.createElement("div");
        wrapper.style.cssText = "text-align: center; padding: 3rem;";
        const mainP = document.createElement("p");
        mainP.style.cssText = "font-size: 1.2rem; color: #27ae60;";
        mainP.textContent = t("duplicates.noDuplicatesMsg");
        const descP = document.createElement("p");
        descP.textContent = modeDesc;
        wrapper.appendChild(mainP);
        wrapper.appendChild(descP);
        content.appendChild(wrapper);
        return;
      }

      // Format summary based on mode
      const savingsLabel =
        currentMode === "hash" ? t("duplicates.wasted") : t("duplicates.potentialSavings");
      const savingsValue =
        currentMode === "hash"
          ? data.total_wasted_mb
          : data.total_potential_savings_mb;
      summary.textContent = t("duplicates.summary", {
        groups: data.total_groups,
        files: data.total_duplicate_files,
        size: this.formatSize(savingsValue),
        label: savingsLabel,
      });

      content.innerHTML = data.duplicate_groups
        .map((group) => this.renderDuplicateGroup(group, currentMode))
        .join("");

      // Add checkbox event listeners
      content.querySelectorAll(".duplicate-checkbox").forEach((checkbox) => {
        checkbox.addEventListener("change", (e) => {
          const id = parseInt(e.target.dataset.id);
          const row = e.target.closest(".duplicate-file");

          if (e.target.checked) {
            this.selectedIds.add(id);
            row.classList.add("selected");
          } else {
            this.selectedIds.delete(id);
            row.classList.remove("selected");
          }
          this.updateDeleteButton();
        });
      });
    } catch (error) {
      summary.textContent = t("duplicates.error");
      // Use safe DOM methods to avoid XSS - error.message could contain malicious content
      content.innerHTML = "";
      const errorP = document.createElement("p");
      errorP.style.color = "#c0392b";
      errorP.textContent = t("library.errorDuplicates", { error: error.message });
      content.appendChild(errorP);

      // Add static help text (safe - no user input)
      if (currentMode === "hash") {
        const helpP = document.createElement("p");
        helpP.textContent = t("duplicates.hashHelp");
        content.appendChild(helpP);
        const pre = document.createElement("pre");
        pre.className = "cli-command";
        pre.textContent = "cd library && python3 scripts/generate_hashes.py";
        content.appendChild(pre);
      }
    }
  }

  renderDuplicateGroup(group, mode = "hash") {
    const filesHtml = group.files
      .map((file) => {
        const isKeeper = file.is_keeper;
        const badgeClass = isKeeper ? "badge-keep" : "badge-duplicate";
        const badgeText = isKeeper ? t("duplicates.keep") : t("duplicates.duplicate");
        const rowClass = isKeeper ? "keeper" : "deletable";

        return `
                <div class="duplicate-file ${rowClass}" data-id="${file.id}">
                    <input type="checkbox"
                           class="duplicate-checkbox"
                           data-id="${file.id}"
                           ${isKeeper ? 'disabled title="' + t("duplicates.fileProtected").replace(/"/g, "&quot;") + '"' : ""}>
                    <div class="duplicate-info">
                        <div class="duplicate-title">${this.escapeHtml(file.title)}</div>
                        <div class="duplicate-path">${this.escapeHtml(file.file_path)}</div>
                    </div>
                    <div class="duplicate-meta">
                        <span>${file.format?.toUpperCase() || "N/A"}</span>
                        <span>${file.duration_formatted || "N/A"}</span>
                        <span>${this.formatSize(file.file_size_mb)}</span>
                    </div>
                    <span class="duplicate-badge ${badgeClass}">${badgeText}</span>
                </div>
            `;
      })
      .join("");

    // Use appropriate label for mode
    const savingsLabel = mode === "hash" ? t("duplicates.wasted") : t("duplicates.potentialSavings");
    const savingsValue =
      mode === "hash" ? group.wasted_mb : group.potential_savings_mb;

    return `
            <div class="duplicate-group">
                <div class="duplicate-group-header">
                    <span class="duplicate-group-title">${this.escapeHtml(group.title || group.files[0]?.title || "Unknown")}</span>
                    <span class="duplicate-group-meta">
                        ${group.count} copies | ${savingsLabel}: ${this.formatSize(savingsValue)}
                    </span>
                </div>
                ${filesHtml}
            </div>
        `;
  }

  selectAllDuplicates() {
    document
      .querySelectorAll(".duplicate-checkbox:not(:disabled)")
      .forEach((checkbox) => {
        checkbox.checked = true;
        const id = parseInt(checkbox.dataset.id);
        this.selectedIds.add(id);
        checkbox.closest(".duplicate-file").classList.add("selected");
      });
    this.updateDeleteButton();
  }

  deselectAll() {
    document.querySelectorAll(".duplicate-checkbox").forEach((checkbox) => {
      checkbox.checked = false;
      checkbox.closest(".duplicate-file").classList.remove("selected");
    });
    this.selectedIds.clear();
    this.updateDeleteButton();
  }

  updateDeleteButton() {
    const btn = document.getElementById("delete-selected");
    if (btn) {
      btn.textContent = t("duplicates.deleteSelected", { n: this.selectedIds.size });
      btn.disabled = this.selectedIds.size === 0;
    }
  }

  confirmDelete() {
    if (this.selectedIds.size === 0) return;

    const content = document.getElementById("confirm-content");
    // XSS safe: DOM construction with translated static text
    content.textContent = "";
    const msgP = document.createElement("p");
    msgP.textContent = t("duplicates.confirmDeleteMsg", { n: this.selectedIds.size });
    const warnP = document.createElement("p");
    warnP.style.color = "#c0392b";
    const warnStrong = document.createElement("strong");
    warnStrong.textContent = t("duplicates.confirmCannotUndo");
    warnP.appendChild(warnStrong);
    const protectP = document.createElement("p");
    protectP.textContent = t("duplicates.confirmProtection");
    content.appendChild(msgP);
    content.appendChild(warnP);
    content.appendChild(protectP);

    this.openModal("confirm-modal");
  }

  async executeDelete() {
    this.closeModal("confirm-modal");

    const btn = document.getElementById("delete-selected");
    btn.disabled = true;
    btn.textContent = t("duplicates.deleting");

    try {
      const result = await api.post(`${API_BASE}/duplicates/delete`, {
          audiobook_ids: Array.from(this.selectedIds),
          mode: this.duplicateMode || "title",
        }, { toast: false });

      if (result.success) {
        let message = t("duplicates.deletedSuccess", { n: result.deleted_count });
        if (result.blocked_count > 0) {
          message += "\n\n" + t("duplicates.blockedProtected", { n: result.blocked_count });
        }
        if (result.errors.length > 0) {
          message += "\n\n" + t("duplicates.deleteErrors", { n: result.errors.length });
        }
        alert(message);

        // Refresh the duplicates view
        this.showDuplicates();

        // Refresh library stats
        if (library) {
          library.loadStats();
        }
      } else {
        alert(t("common.error") + ": " + (result.error || ""));
      }
    } catch (error) {
      alert(t("duplicates.deleteError", { error: error.message }));
    }

    this.updateDeleteButton();
  }

  formatSize(mb) {
    if (mb >= 1024) {
      return (mb / 1024).toFixed(1) + " GB";
    }
    return mb.toFixed(1) + " MB";
  }

  escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
}

// Initialize managers
let duplicateManager;
document.addEventListener("DOMContentLoaded", () => {
  duplicateManager = new DuplicateManager();
});

// ============================================
// LIGHTWEIGHT POSITION HELPERS
// ============================================
// Read-only localStorage helpers for book cards (progress bars, resume tooltips).
// Full position persistence (save, API sync) is handled by ShellPlayer in shell.js.

/**
 * Read saved position from localStorage (lightweight, no API call).
 * Used by book cards to show progress bars and resume tooltips.
 */
function getLocalPosition(fileId) {
  try {
    const key = `audiobook_position_${fileId}`;
    const saved = localStorage.getItem(key);
    if (!saved) return null;
    const parsed = JSON.parse(saved);
    // Return null if position is near end (>95%) or very beginning (<5s)
    const pct = (parsed.position / parsed.duration) * 100;
    if (pct > 95 || parsed.position < 5) return null;
    return parsed;
  } catch {
    return null;
  }
}

function getLocalPercentComplete(fileId) {
  const data = getLocalPosition(fileId);
  if (!data || !data.duration) return 0;
  return Math.round((data.position / data.duration) * 100);
}

function formatPlaybackTime(seconds) {
  if (!seconds || isNaN(seconds)) return "0:00";
  seconds = Math.floor(seconds);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  }
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

// ============================================
// IFRAME BRIDGE — Shell Communication
// ============================================
// When running inside shell.html's iframe, delegate play/pause/seek
// to the shell's ShellPlayer via postMessage.

const inIframe = window.self !== window.top;

/**
 * Wait for shellPlayer to initialize, then call the action.
 * Handles the race where iframe loads before parent's DOMContentLoaded.
 */
function whenShellReady(action) {
  if (window.parent.shellPlayer) {
    action(window.parent.shellPlayer);
    return;
  }
  const poll = setInterval(() => {
    if (window.parent.shellPlayer) {
      clearInterval(poll);
      action(window.parent.shellPlayer);
    }
  }, 50);
  setTimeout(() => clearInterval(poll), 3000);
}

function shellPlay(book, resume) {
  if (inIframe) {
    whenShellReady((sp) => sp.playBook(book, resume));
  } else {
    // Not in iframe — redirect to shell with play intent
    const bookId = book.bookId || book.id;
    sessionStorage.setItem("pendingPlay", JSON.stringify(book));
    sessionStorage.setItem("pendingPlayResume", resume ? "1" : "0");
    window.location.href = `/?autoplay=${encodeURIComponent(bookId)}`;
  }
}

function shellPause() {
  if (inIframe) {
    whenShellReady((sp) => sp.audio.pause());
  }
}

function shellResume() {
  if (inIframe) {
    whenShellReady((sp) => sp.audio.play());
  }
}

function shellSeek(seconds) {
  if (inIframe) {
    whenShellReady((sp) => {
      sp.audio.currentTime = seconds;
    });
  }
}

// Listen for playerState messages from the shell.
// Origin-validated handler extracted as a named function for static analysis.
function handleShellMessage(data) {
  if (data.type === "playerState") {
    // Update "Now Playing" indicators on book cards
    document.querySelectorAll(".book-card").forEach((card) => {
      card.classList.remove("now-playing");
    });
    if (data.bookId) {
      const playingCard = document.querySelector(
        `.book-card[data-id="${data.bookId}"]`,
      );
      if (playingCard) {
        playingCard.classList.add("now-playing");
      }
    }
  } else if (data.type === "playerClosed") {
    document.querySelectorAll(".book-card.now-playing").forEach((card) => {
      card.classList.remove("now-playing");
    });
  } else if (data.type === "playerVisible") {
    // Shell uses flex layout (iframe + player as siblings), so the iframe
    // auto-shrinks when the player appears. Toggle a class for any CSS
    // adjustments needed (e.g., scroll-to-bottom behavior).
    document.body.classList.toggle("shell-player-active", data.visible);
  } else if (data.type === "viewportBottom") {
    // Shell reports how much of the layout viewport is behind browser chrome.
    // Set a CSS variable so bottom-positioned elements can account for it.
    const offset = Math.max(data.offset || 0, 0);
    document.documentElement.style.setProperty(
      "--browser-chrome-bottom",
      offset + "px",
    );
  }
}

window.addEventListener("message", function (event) {
  if (event.origin !== window.location.origin) return;
  var data = event.data;
  if (data && data.type) handleShellMessage(data);
});
