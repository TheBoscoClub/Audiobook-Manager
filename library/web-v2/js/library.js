// Modern Audiobook Library - API-backed with pagination
// Use relative URL for proxy support (works with both direct API and HTTPS proxy)
const API_BASE = "/api";

// SessionPersistence is loaded from js/session-persistence.js (shared with login/verify pages)

class AudiobookLibraryV2 {
  constructor() {
    this.currentPage = 1;
    this.perPage = 50;
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
      let response = await fetch("/auth/status", {
        credentials: "include",
      });

      if (response.ok) {
        let data = await response.json();
        this.authEnabled = data.auth_enabled;
        this.user = data.user;
        this.guestMode = data.guest;

        // If guest (no session cookie), try to recover from client storage
        if (this.guestMode && !this.user) {
          const recovered = await this._trySessionRecover();
          if (recovered) {
            // Re-check auth status after session restore
            const retry = await fetch("/auth/status", {
              credentials: "include",
            });
            if (retry.ok) {
              data = await retry.json();
              this.user = data.user;
              this.guestMode = data.guest;
            }
          }
        }

        if (this.user) {
          this.updateUserUI();
        } else if (this.guestMode) {
          this.updateGuestUI();
        }
        return true;
      }

      // /auth/status not available — auth not configured
      this.authEnabled = false;
      return true;
    } catch (error) {
      // Network error or auth not configured - allow access
      console.log("Auth check skipped:", error.message);
      this.authEnabled = false;
      return true;
    }
  }

  async _trySessionRecover() {
    try {
      const token = await SessionPersistence.recover();
      if (!token) return false;

      const response = await fetch("/auth/session/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token }),
      });

      if (response.ok) return true;

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
    const userMenu = document.getElementById("user-menu");
    const loginLink = document.getElementById("login-link");
    const backOfficeLink = document.getElementById("admin-backoffice-link");

    // Back Office is ONLY shown when we positively confirm user is admin
    // In all other cases (not logged in, not admin, error, unknown), keep it hidden
    if (backOfficeLink) {
      backOfficeLink.hidden = !(this.user && this.user.is_admin);
    }

    if (this.user) {
      // Show user menu, hide login link
      if (userMenu) {
        userMenu.hidden = false;
        const usernameEl = document.getElementById("username-display");
        if (usernameEl) {
          usernameEl.textContent = this.user.username;
        }
        const userInitial = document.getElementById("user-initial");
        if (userInitial) {
          userInitial.textContent = this.user.username.charAt(0).toUpperCase();
        }
      }
      if (loginLink) {
        loginLink.hidden = true;
      }

      // Show/hide download buttons based on permission
      this.updateDownloadButtons();
    } else if (this.authEnabled) {
      // Auth enabled but no user - show login link
      if (userMenu) userMenu.hidden = true;
      if (loginLink) loginLink.hidden = false;
    } else {
      // Auth not enabled or unknown state - hide both user elements
      if (userMenu) userMenu.hidden = true;
      if (loginLink) loginLink.hidden = true;
    }
  }

  /**
   * Update UI for guest mode — show sign in / request access, hide user elements.
   */
  updateGuestUI() {
    const userMenu = document.getElementById("user-menu");
    const loginLink = document.getElementById("login-link");
    const requestAccessLink = document.getElementById("request-access-link");
    const backOfficeLink = document.getElementById("admin-backoffice-link");
    const myLibraryTab = document.querySelector(
      '.tab-btn[data-tab="my-library"]',
    );

    if (userMenu) userMenu.hidden = true;
    if (loginLink) loginLink.hidden = false;
    if (requestAccessLink) requestAccessLink.hidden = false;
    if (backOfficeLink) backOfficeLink.hidden = true;
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
    heading.textContent = "Sign in to listen";
    tooltip.appendChild(heading);

    const desc = document.createElement("p");
    desc.textContent =
      "Playing and downloading audiobooks is available to members.";
    tooltip.appendChild(desc);

    const links = document.createElement("div");
    links.className = "guest-gate-links";

    const signInLink = document.createElement("a");
    signInLink.href = "login.html";
    signInLink.textContent = "Existing User Sign In";
    links.appendChild(signInLink);

    const sep = document.createElement("span");
    sep.className = "guest-gate-separator";
    sep.textContent = "\u00B7";
    links.appendChild(sep);

    const requestLink = document.createElement("a");
    requestLink.href = "register.html";
    requestLink.textContent = "Request a User Account";
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

  /**
   * Log out the current user.
   */
  async logout() {
    // Clear client-side session storage before server logout
    await SessionPersistence.clear();
    try {
      await fetch("/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } catch (error) {
      console.error("Logout error:", error);
    }
    window.location.href = "login.html";
  }

  /**
   * Load and display notifications for the current user.
   */
  async loadNotifications() {
    if (!this.authEnabled || !this.user) {
      return;
    }

    try {
      const response = await fetch("/auth/me", {
        credentials: "include",
      });

      if (!response.ok) {
        return;
      }

      const data = await response.json();
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

      // Create dismiss button if dismissable
      if (notif.dismissable) {
        const dismissBtn = document.createElement("button");
        dismissBtn.className = "notification-dismiss";
        dismissBtn.title = "Dismiss notification";
        dismissBtn.textContent = "Dismiss";
        dismissBtn.addEventListener("click", () =>
          this.dismissNotification(notif.id, banner),
        );
        banner.appendChild(dismissBtn);
      }

      container.appendChild(banner);
    }
  }

  /**
   * Dismiss a notification.
   */
  async dismissNotification(notificationId, bannerElement) {
    try {
      const response = await fetch(
        `/auth/notifications/dismiss/${notificationId}`,
        {
          method: "POST",
          credentials: "include",
        },
      );

      if (response.ok) {
        // Animate removal
        bannerElement.classList.add("dismissing");
        setTimeout(() => bannerElement.remove(), 300);
      }
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
    msg.textContent =
      "Your browser supports passkeys. Setting up a passkey means you can sign in instantly without waiting for an email link. You can switch in your profile settings.";
    content.appendChild(msg);
    const dismiss = document.createElement("button");
    dismiss.className = "notification-dismiss";
    dismiss.setAttribute("aria-label", "Dismiss");
    dismiss.title = "Dismiss this suggestion";
    dismiss.textContent = "\u00D7";
    dismiss.addEventListener("click", () => {
      try {
        localStorage.setItem("library_passkey_prompt_dismissed", "1");
      } catch (e) {}
      banner.classList.add("dismissing");
      setTimeout(() => banner.remove(), 300);
    });
    content.appendChild(dismiss);
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
      downloadBtn.textContent = "Downloading...";
    }

    try {
      const response = await fetch(`${API_BASE}/download/${bookId}`, {
        credentials: "include",
      });
      if (!response.ok) throw new Error(`Download failed: ${response.status}`);

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
      await fetch(`${API_BASE}/user/downloads/${bookId}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ file_format: "opus" }),
      });
    } catch (error) {
      console.error("Download error:", error);
      // Failed/cancelled downloads not recorded — by design
    } finally {
      if (downloadBtn) {
        downloadBtn.disabled = false;
        downloadBtn.textContent = "\u2B07 Download";
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
    await this.loadAudiobooks();
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
      const response = await fetch(`${API_BASE}/stats`);
      const stats = await response.json();

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
      const response = await fetch(`${API_BASE}/filters`);
      this.filters = await response.json();

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
      const response = await fetch(`${API_BASE}/collections`);
      this.collections = await response.json();
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
      const cat = c.category || "main";
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(c);
    });

    // Initialize expand state tracker
    if (!this._expandedCollections) {
      this._expandedCollections = new Set();
    }

    // Build tree-structured sidebar using DOM methods
    container.textContent = "";
    const categoryOrder = ["special", "main", "nonfiction", "subgenre"];
    const categoryLabels = {
      special: "Special Collections",
      main: "Fiction Genres",
      nonfiction: "Nonfiction",
      subgenre: "More Genres",
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
          toggleBtn.title = "Show subgenres";
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

    // Filter by letter group first
    let filtered = this.filterByLetterGroup(authors, this.authorLetterGroup);

    // Then filter by search query
    if (query) {
      filtered = filtered.filter((a) => a.toLowerCase().includes(query));
    }

    // Sort the results
    filtered = this.sortByLastName(filtered, this.authorSortAsc);

    // Count for this group before limiting
    const groupTotal = filtered.length;

    // Limit display to prevent performance issues
    const maxDisplay = 50;
    const hasMore = filtered.length > maxDisplay;
    filtered = filtered.slice(0, maxDisplay);

    // Build dropdown HTML
    let html = "";

    // "All Authors" option at top
    const allLabel =
      this.authorLetterGroup === "all"
        ? "All Authors"
        : `All ${this.authorLetterGroup.toUpperCase()}`;
    html += `<div class="author-option author-all-option" data-value="">
            <span>${allLabel}</span>
            <span class="count">${authors.length} total</span>
        </div>`;

    if (filtered.length === 0 && query) {
      html += `<div class="author-no-results">No authors matching "${query}"</div>`;
    } else if (filtered.length === 0) {
      html += `<div class="author-no-results">No authors in this range</div>`;
    } else {
      filtered.forEach((author) => {
        html += `<div class="author-option" data-value="${this.escapeHtml(author)}">
                    <span>${this.highlightMatch(author, query)}</span>
                </div>`;
      });

      if (hasMore) {
        html += `<div class="author-no-results">Showing ${maxDisplay} of ${groupTotal}. Type to filter...</div>`;
      }
    }

    // XSS safe: All dynamic content passes through escapeHtml() (lines 433, highlightMatch->escapeHtml)
    dropdown.innerHTML = html;

    // Add click handlers to options
    dropdown.querySelectorAll(".author-option").forEach((option) => {
      option.addEventListener("click", () => {
        this.selectAuthor(option.dataset.value);
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

  selectAuthor(author) {
    const input = document.getElementById("author-search");
    const clearBtn = document.getElementById("author-clear");

    this.currentFilters.author = author || "";
    input.value = author || "";

    // Show/hide clear button
    if (clearBtn) {
      clearBtn.style.display = author ? "block" : "none";
    }

    this.hideAuthorDropdown();
    this.currentPage = 1;
    this.loadAudiobooks();
  }

  async loadNarratorCounts() {
    try {
      // Get narrator counts from stats endpoint
      const response = await fetch(`${API_BASE}/narrator-counts`);
      if (response.ok) {
        this.narratorCounts = await response.json();
      } else {
        // Fallback: just use narrator list without counts
        this.narratorCounts = {};
        this.filters.narrators.forEach((n) => (this.narratorCounts[n] = null));
      }
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

    // Count for this group before limiting
    const groupTotal = filtered.length;

    // Limit display to prevent performance issues
    const maxDisplay = 50;
    const hasMore = filtered.length > maxDisplay;
    filtered = filtered.slice(0, maxDisplay);

    // Build dropdown HTML
    let html = "";

    // "All Narrators" option at top (shows total count for current group)
    const allLabel =
      this.narratorLetterGroup === "all"
        ? "All Narrators"
        : `All ${this.narratorLetterGroup.toUpperCase()}`;
    html += `<div class="narrator-option narrator-all-option" data-value="">
            <span>${allLabel}</span>
            <span class="count">${narrators.length} total</span>
        </div>`;

    if (filtered.length === 0 && query) {
      html += `<div class="narrator-no-results">No narrators matching "${query}"</div>`;
    } else if (filtered.length === 0) {
      html += `<div class="narrator-no-results">No narrators in this range</div>`;
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

      if (hasMore) {
        html += `<div class="narrator-no-results">Showing ${maxDisplay} of ${groupTotal}. Type to filter...</div>`;
      }
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
    const regex = new RegExp(`(${this.escapeRegex(query)})`, "gi");
    return escaped.replace(regex, "<strong>$1</strong>");
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

      const response = await fetch(`${API_BASE}/audiobooks?${params}`);
      const data = await response.json();

      this.totalPages = data.pagination.total_pages;
      this.totalCount = data.pagination.total_count;

      this.browseBooks = data.audiobooks;
      this.renderBooks(data.audiobooks);
      this.renderPagination(data.pagination);
      this.updateResultsInfo(data.pagination);

      // Update download button visibility based on user permissions
      this.updateDownloadButtons();

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
    const hasContinue = percentComplete > 0;

    return `
            <div class="book-card" data-id="${book.id}">
                <div class="book-cover">
                    ${
                      book.cover_path
                        ? `<img src="/covers/${book.cover_path}" alt="${this.escapeHtml(book.title)}" onerror="this.parentElement.innerHTML='<span class=\\'book-cover-placeholder\\'>📖</span>'">`
                        : '<span class="book-cover-placeholder">📖</span>'
                    }
                    ${hasSupplement ? `<span class="supplement-badge" title="Has PDF supplement" onclick="event.stopPropagation(); library.showSupplements(${book.id})">PDF</span>` : ""}
                    ${hasContinue ? `<span class="continue-badge" title="${percentComplete}% complete">Continue</span>` : ""}
                    ${hasEditions ? `<span class="editions-badge" title="${book.edition_count} editions" onclick="event.stopPropagation(); library.toggleEditions(${book.id})">${book.edition_count} editions</span>` : ""}
                </div>
                <div class="book-title">${this.escapeHtml(book.title)}</div>
                ${book.author ? `<div class="book-author">by ${this.escapeHtml(book.author)}</div>` : ""}
                ${book.narrator ? `<div class="book-narrator">Narrated by ${this.escapeHtml(book.narrator)}</div>` : ""}
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
                    <button class="btn-play" onclick="event.stopPropagation(); shellPlay(${JSON.stringify(book).replace(/"/g, "&quot;")}, false)">▶ Play</button>
                    <button class="btn-resume" ${!hasContinue ? "disabled" : ""} onclick="event.stopPropagation(); shellPlay(${JSON.stringify(book).replace(/"/g, "&quot;")}, true)" title="${hasContinue ? "Resume from " + formatPlaybackTime(savedPosition.position) : "No saved position"}">
                        ${hasContinue ? "⏯ Resume" : "⏯ Resume"}
                    </button>
                    <button class="btn-download download-button" style="display: none;" onclick="event.stopPropagation(); library.downloadAudiobook(${book.id})" title="Download this audiobook for offline listening in a local player. The Library streams from its own server storage and cannot access files on your device.">
                        ⬇ Download
                    </button>
                </div>
                ${hasEditions ? '<div class="book-editions" data-book-id="' + book.id + '" style="display: none;"></div>' : ""}
            </div>
        `;
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
          const response = await fetch(
            `${API_BASE}/audiobooks/${bookId}/editions`,
          );
          const data = await response.json();

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
    const hasContinue = percentComplete > 0;

    return `
            <div class="edition-item">
                <div class="edition-info">
                    <div class="edition-narrator">🎙️ ${this.escapeHtml(edition.narrator || "Unknown Narrator")}</div>
                    <div class="edition-details">
                        <span class="edition-format">${formatQuality}${quality}</span>
                        <span class="edition-duration">${edition.duration_formatted || `${Math.round(edition.duration_hours || 0)}h`}</span>
                        <span class="edition-size">${Math.round(edition.file_size_mb)}MB</span>
                        ${hasContinue ? `<span class="edition-progress">${percentComplete}% played</span>` : ""}
                    </div>
                </div>
                <div class="edition-actions">
                    <button class="btn-play-edition" onclick="event.stopPropagation(); shellPlay(${JSON.stringify(edition).replace(/"/g, "&quot;")}, false)">▶ Play</button>
                    ${hasContinue ? `<button class="btn-resume-edition" onclick="event.stopPropagation(); shellPlay(${JSON.stringify(edition).replace(/"/g, "&quot;")}, true)">⏯ Resume</button>` : ""}
                </div>
            </div>
        `;
  }

  async showSupplements(audiobookId) {
    try {
      const response = await fetch(
        `${API_BASE}/audiobooks/${audiobookId}/supplements`,
      );
      const data = await response.json();

      if (data.supplements && data.supplements.length > 0) {
        // Open the first supplement (typically PDF)
        const supplement = data.supplements[0];
        window.open(
          `${API_BASE}/supplements/${supplement.id}/download`,
          "_blank",
        );
      } else {
        alert("No supplements available for this audiobook.");
      }
    } catch (error) {
      console.error("Error loading supplements:", error);
      alert("Error loading supplements.");
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
    const hasContinue = percentComplete > 0;
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
    h2.textContent = "Book Details";
    const closeBtn = document.createElement("button");
    closeBtn.className = "modal-close";
    closeBtn.title = "Close dialog";
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
    titleEl.textContent = book.title || "Unknown Title";
    info.appendChild(titleEl);

    if (book.author) {
      const authorEl = document.createElement("div");
      authorEl.className = "detail-author";
      authorEl.textContent = "by " + book.author;
      info.appendChild(authorEl);
    }

    if (book.narrator) {
      const narratorEl = document.createElement("div");
      narratorEl.className = "detail-narrator";
      narratorEl.textContent = "Narrated by " + book.narrator;
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
      pctText.textContent = percentComplete + "% complete";
      progressDiv.appendChild(barBg);
      progressDiv.appendChild(pctText);
      info.appendChild(progressDiv);
    }

    if (hasSupplement) {
      const badge = document.createElement("div");
      badge.className = "detail-badge";
      badge.textContent = "PDF Supplement Available";
      info.appendChild(badge);
    }

    // Actions
    const actions = document.createElement("div");
    actions.className = "detail-actions";

    const playBtn = document.createElement("button");
    playBtn.className = "btn-play";
    playBtn.textContent = "\u25B6 Play";
    playBtn.title = "Play from beginning";
    playBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      shellPlay(book, false);
      modal.remove();
    });

    const resumeBtn = document.createElement("button");
    resumeBtn.className = "btn-resume";
    resumeBtn.textContent = "\u23EF Resume";
    resumeBtn.disabled = !hasContinue;
    if (hasContinue && savedPosition) {
      resumeBtn.title =
        "Resume from " + formatPlaybackTime(savedPosition.position);
    }
    resumeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      shellPlay(book, true);
      modal.remove();
    });

    const downloadBtn = document.createElement("button");
    downloadBtn.className = "btn-download download-button";
    downloadBtn.style.display = "none";
    downloadBtn.textContent = "\u2B07 Download";
    downloadBtn.title = "Download this audiobook";
    downloadBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      library.downloadAudiobook(book.id);
    });

    actions.appendChild(playBtn);
    actions.appendChild(resumeBtn);
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
      el.textContent = "No audiobooks found";
      return;
    }
    const start = (pagination.page - 1) * pagination.per_page + 1;
    const end = Math.min(
      pagination.page * pagination.per_page,
      pagination.total_count,
    );
    el.textContent = `Showing ${start}-${end} of ${pagination.total_count.toLocaleString()} audiobooks`;
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
                ← Prev
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
                Next →
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
      this.loadMyLibrary();
    } else {
      // Restore browse UI
      if (searchSection) searchSection.style.display = "";
      if (resultsInfo) resultsInfo.style.display = "";
      if (paginationSection) paginationSection.style.display = "";
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
      // Fetch user's library
      const response = await fetch(`${API_BASE}/user/library`, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`Failed to load library: ${response.status}`);
      }

      const data = await response.json();
      const books = data.books || [];

      if (books.length === 0) {
        // XSS safe: static content only, no user input
        const emptyMsg = document.createElement("p");
        emptyMsg.style.cssText =
          "color: var(--parchment); text-align: center; grid-column: 1/-1;";
        emptyMsg.textContent =
          "Your library is empty. Start listening to build your collection!";
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
    } catch (error) {
      console.error("Error loading My Library:", error);
      const errMsg = document.createElement("p");
      errMsg.style.cssText =
        "color: var(--parchment); text-align: center; grid-column: 1/-1;";
      errMsg.textContent = "Error loading your library. Please try again.";
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
          const res = await fetch(`${API_BASE}/position/${book.id}`, {
            credentials: "include",
          });
          return res.ok ? await res.json() : null;
        } catch (e) {
          console.warn(`Could not fetch position for book ${book.id}:`, e);
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
      : `Not started — ${durationHuman}`;

    const card = document.createElement("div");
    card.className = "book-card";
    card.dataset.id = book.id;

    // Cover section
    const coverDiv = document.createElement("div");
    coverDiv.className = "book-cover";
    if (book.cover_path) {
      const img = document.createElement("img");
      img.src = `/covers/${book.cover_path}`;
      img.alt = book.title;
      img.onerror = function () {
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
    if (percent > 0) {
      const badge = document.createElement("span");
      badge.className = "continue-badge";
      badge.title = `${percent}% complete`;
      badge.textContent = "Continue";
      coverDiv.appendChild(badge);
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
      authorDiv.textContent = `by ${book.author}`;
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
      if (book.last_listened_at) {
        const histSpan = document.createElement("span");
        const listenDate = new Date(book.last_listened_at).toLocaleDateString(
          "en-US",
          dateOpts,
        );
        histSpan.textContent = `\u{1F50A} Last listened: ${listenDate}`;
        metaDiv.appendChild(histSpan);
      }
      if (book.downloaded_at) {
        const dlSpan = document.createElement("span");
        const dlDate = new Date(book.downloaded_at).toLocaleDateString(
          "en-US",
          dateOpts,
        );
        dlSpan.textContent = `\u{2B07} Downloaded: ${dlDate}`;
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
    playBtn.textContent = "\u25B6 Play";
    playBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      shellPlay(bookData, false);
    });
    actionsDiv.appendChild(playBtn);

    const resumeBtn = document.createElement("button");
    resumeBtn.className = "btn-resume";
    resumeBtn.textContent = "\u23EF Resume";
    resumeBtn.disabled = percent <= 0;
    resumeBtn.title =
      percent > 0 ? `Resume from ${positionHuman}` : "No saved position";
    resumeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      shellPlay(bookData, true);
    });
    actionsDiv.appendChild(resumeBtn);

    const downloadBtn = document.createElement("button");
    downloadBtn.className = "btn-download download-button";
    downloadBtn.style.display = "none";
    downloadBtn.title =
      "Download this audiobook for offline listening in a local player. The Library streams from its own server storage and cannot access files on your device.";
    downloadBtn.textContent = "\u2B07 Download";
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

  setupEventListeners() {
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
      this.currentPage = 1;
      this.loadAudiobooks();
    });

    // Sort filter
    document.getElementById("sort-filter").addEventListener("change", (e) => {
      const [sort, order] = e.target.value.split(":");
      this.currentFilters.sort = sort;
      this.currentFilters.order = order;
      this.currentPage = 1;
      this.loadAudiobooks();
    });

    // Per page
    document.getElementById("per-page").addEventListener("change", (e) => {
      this.perPage = parseInt(e.target.value);
      this.currentPage = 1;
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
  }

  async refreshLibrary() {
    const refreshBtn = document.getElementById("refresh-btn");
    refreshBtn.disabled = true;
    refreshBtn.textContent = "↻ Refreshing...";

    try {
      // Reload stats and filters
      await this.loadStats();
      await this.loadFilters();

      // Reload current page
      await this.loadAudiobooks();

      alert("Library refreshed successfully!");
    } catch (error) {
      console.error("Error refreshing library:", error);
      alert("Failed to refresh library. Please check the console for details.");
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "↻ Refresh";
    }
  }
}

// Initialize library when DOM is loaded
let library;
document.addEventListener("DOMContentLoaded", () => {
  library = new AudiobookLibraryV2();
  // Expose to window for inline scripts (logout, etc.)
  window.library = library;
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
          btn.textContent = "Copied!";
          setTimeout(() => (btn.textContent = "Copy to Clipboard"), 2000);
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
      const response = await fetch(`${API_BASE}/hash-stats`);
      const stats = await response.json();

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
    content.innerHTML =
      '<div class="loading-spinner"></div><p>Loading duplicates...</p>';
    summary.textContent = "Loading...";

    try {
      // Choose endpoint based on mode
      const endpoint =
        currentMode === "hash" ? "duplicates" : "duplicates/by-title";
      const response = await fetch(`${API_BASE}/${endpoint}`);

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || "Failed to load duplicates");
      }

      const data = await response.json();
      this.duplicateData = data;

      if (data.total_groups === 0) {
        summary.textContent = "No duplicates found";
        const modeDesc =
          currentMode === "hash"
            ? "No byte-for-byte identical files found."
            : "No audiobooks with matching title and author found.";
        content.innerHTML = `
                    <div style="text-align: center; padding: 3rem;">
                        <p style="font-size: 1.2rem; color: #27ae60;">No duplicate audiobooks found!</p>
                        <p>${modeDesc}</p>
                    </div>
                `;
        return;
      }

      // Format summary based on mode
      const savingsLabel =
        currentMode === "hash" ? "wasted" : "potential savings";
      const savingsValue =
        currentMode === "hash"
          ? data.total_wasted_mb
          : data.total_potential_savings_mb;
      summary.textContent = `${data.total_groups} groups | ${data.total_duplicate_files} duplicates | ${this.formatSize(savingsValue)} ${savingsLabel}`;

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
      summary.textContent = "Error";
      // Use safe DOM methods to avoid XSS - error.message could contain malicious content
      content.innerHTML = "";
      const errorP = document.createElement("p");
      errorP.style.color = "#c0392b";
      errorP.textContent = `Error loading duplicates: ${error.message}`;
      content.appendChild(errorP);

      // Add static help text (safe - no user input)
      if (currentMode === "hash") {
        const helpP = document.createElement("p");
        helpP.textContent = "Make sure hashes have been generated first:";
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
        const badgeText = isKeeper ? "KEEP" : "DUPLICATE";
        const rowClass = isKeeper ? "keeper" : "deletable";

        return `
                <div class="duplicate-file ${rowClass}" data-id="${file.id}">
                    <input type="checkbox"
                           class="duplicate-checkbox"
                           data-id="${file.id}"
                           ${isKeeper ? 'disabled title="This file is protected - it is the preferred copy"' : ""}>
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
    const savingsLabel = mode === "hash" ? "Wasted" : "Savings";
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
      btn.textContent = `Delete Selected (${this.selectedIds.size})`;
      btn.disabled = this.selectedIds.size === 0;
    }
  }

  confirmDelete() {
    if (this.selectedIds.size === 0) return;

    const content = document.getElementById("confirm-content");
    content.innerHTML = `
            <p>You are about to permanently delete <strong>${this.selectedIds.size}</strong> audiobook file(s).</p>
            <p style="color: #c0392b;"><strong>This action cannot be undone!</strong></p>
            <p>The system will automatically protect the last copy of each audiobook to prevent data loss.</p>
        `;

    this.openModal("confirm-modal");
  }

  async executeDelete() {
    this.closeModal("confirm-modal");

    const btn = document.getElementById("delete-selected");
    btn.disabled = true;
    btn.textContent = "Deleting...";

    try {
      const response = await fetch(`${API_BASE}/duplicates/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audiobook_ids: Array.from(this.selectedIds),
          mode: this.duplicateMode || "title",
        }),
      });

      const result = await response.json();

      if (result.success) {
        let message = `Successfully deleted ${result.deleted_count} file(s).`;
        if (result.blocked_count > 0) {
          message += `\n\n${result.blocked_count} file(s) were protected (last copies).`;
        }
        if (result.errors.length > 0) {
          message += `\n\n${result.errors.length} error(s) occurred.`;
        }
        alert(message);

        // Refresh the duplicates view
        this.showDuplicates();

        // Refresh library stats
        if (library) {
          library.loadStats();
        }
      } else {
        alert("Error: " + (result.error || "Unknown error"));
      }
    } catch (error) {
      alert("Error deleting files: " + error.message);
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
// Read-only localStorage helpers for book cards (progress bars, "Continue" badges).
// Full position persistence (save, API sync) is handled by ShellPlayer in shell.js.

/**
 * Read saved position from localStorage (lightweight, no API call).
 * Used by book cards to show progress bars and "Continue" badges.
 */
function getLocalPosition(fileId) {
  try {
    const key = `audiobook_position_${fileId}`;
    const saved = localStorage.getItem(key);
    if (!saved) return null;
    const parsed = JSON.parse(saved);
    // Return null if position is near end (>95%) or very beginning (<30s)
    const pct = (parsed.position / parsed.duration) * 100;
    if (pct > 95 || parsed.position < 30) return null;
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
    // Not in iframe — redirect to shell.html with play intent
    const bookId = book.bookId || book.id;
    sessionStorage.setItem("pendingPlay", JSON.stringify(book));
    sessionStorage.setItem("pendingPlayResume", resume ? "1" : "0");
    window.location.href = `shell.html?autoplay=${encodeURIComponent(bookId)}`;
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

// Listen for playerState messages from the shell
window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;
  const data = event.data;
  if (!data || !data.type) return;

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
    // Add/remove bottom padding so content isn't hidden behind overlay player bar
    // 100px covers both desktop (80px) and mobile (100px) player heights
    document.body.style.paddingBottom = data.visible ? "100px" : "0";
  }
});
