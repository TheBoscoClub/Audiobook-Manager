/**
 * My Account modal — self-service account management.
 * Loaded in shell.html, operates via /auth/account/* endpoints.
 *
 * INVARIANT: The account button (#my-account-btn) is ALWAYS visible.
 * Authenticated → shows username + avatar initial, opens modal.
 * Unauthenticated → shows "Sign In", navigates to login page.
 * The button is NEVER hidden regardless of API state.
 */
(function () {
  "use strict";

  // ── State ──
  var accountData = null;
  var authenticated = false;

  // ── Safety net: prevent anything from hiding the button ──
  // A MutationObserver watches for hidden attribute changes and reverts them.
  var btn = document.getElementById("my-account-btn");
  if (btn) {
    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.attributeName === "hidden" && btn.hidden) {
          console.warn("[account] Something tried to hide the account button — blocked");
          btn.hidden = false;
        }
      });
    }).observe(btn, { attributes: true });
  }

  // ── Modal open/close ──
  function openAccountModal() {
    if (!authenticated) {
      window.location.href = "/auth/login";
      return;
    }
    refreshAccountData();
    document.getElementById("account-modal").classList.add("show");
  }

  function closeAccountModal() {
    document.getElementById("account-modal").classList.remove("show");
    // Reset any open edit states
    hideUsernameEdit();
    hideEmailEdit();
    document.getElementById("auth-switch-panel").hidden = true;
    document.getElementById("auth-setup-result").hidden = true;
  }

  // ── Update button to show authenticated state ──
  function showAuthenticatedState(data) {
    authenticated = true;
    var btn = document.getElementById("my-account-btn");
    btn.onclick = null; // clear any sign-in handler; uses addEventListener
    document.getElementById("account-username").textContent = data.username;
    var initialEl = document.getElementById("account-initial");
    if (initialEl && data.username) {
      initialEl.textContent = data.username.charAt(0).toUpperCase();
    }
    updateBackOfficeButton(data.is_admin);
  }

  // ── Update button to show unauthenticated state ──
  function showSignInState() {
    authenticated = false;
    document.getElementById("account-initial").textContent = "\u2192";
    document.getElementById("account-username").textContent = "Sign In";
    updateBackOfficeButton(false);
  }

  // ── Back Office button visibility ──
  function updateBackOfficeButton(isAdmin) {
    var boLink = document.getElementById("admin-backoffice-link");
    if (!boLink) return;
    boLink.hidden = false;
    if (isAdmin) {
      boLink.classList.remove("backoffice-locked");
      boLink.removeAttribute("data-locked");
    } else {
      boLink.classList.add("backoffice-locked");
      boLink.setAttribute("data-locked", "true");
    }
  }

  // ── Initial auth probe on page load ──
  async function initAccountButton() {
    try {
      accountData = await api.get("/auth/account", { toast: false });
      showAuthenticatedState(accountData);
      populateModal(accountData);
      return;
    } catch (_e) {
      // Network error or not authenticated — fall through to status check
    }

    // /auth/account failed — check if auth is even enabled
    try {
      var statusData = await checkAuthStatus();
      if (!statusData.auth_enabled) {
        // Auth disabled — show generic "Account" (button stays visible)
        return;
      }
      if (statusData.user) {
        // Authenticated but /auth/account failed — show username from status
        showAuthenticatedState(statusData.user);
        return;
      }
    } catch (_e2) {
      // Both endpoints failed — API is down, keep default button state
    }

    // Auth enabled but not logged in (or API down) — show "Sign In"
    showSignInState();
  }

  // ── Refresh account data (for modal open, not initial load) ──
  async function refreshAccountData() {
    try {
      accountData = await api.get("/auth/account", { toast: false });
      populateModal(accountData);
    } catch (_e) {
      // keep existing modal data
    }
  }

  // ── Populate modal fields from account data ──
  function populateModal(data) {
    if (!data) return;
    document.getElementById("acct-username").textContent = data.username;
    document.getElementById("acct-email").textContent = data.email || "(none)";
    document.getElementById("acct-created").textContent =
      data.created_at ? formatDate(data.created_at, "short") : "Unknown";
    document.getElementById("acct-auth-badge").textContent =
      (data.auth_type || "").toUpperCase();
  }

  // ── Username inline edit ──
  function showUsernameEdit() {
    var display = document.getElementById("acct-username");
    var input = document.getElementById("acct-username-input");
    var saveBtn = document.getElementById("acct-username-save");
    var cancelBtn = document.getElementById("acct-username-cancel");

    input.value = display.textContent;
    display.hidden = true;
    input.hidden = false;
    saveBtn.hidden = false;
    cancelBtn.hidden = false;
    input.focus();
  }

  function hideUsernameEdit() {
    document.getElementById("acct-username").hidden = false;
    document.getElementById("acct-username-input").hidden = true;
    document.getElementById("acct-username-save").hidden = true;
    document.getElementById("acct-username-cancel").hidden = true;
  }

  async function saveUsername() {
    var input = document.getElementById("acct-username-input");
    var newName = input.value.trim();
    if (!newName || newName.length < 3) {
      alert("Username must be at least 3 characters");
      return;
    }
    if (newName.length > 24) {
      alert("Username must be at most 24 characters");
      return;
    }
    if (/[<>\\]/.test(newName)) {
      alert("Username contains invalid characters");
      return;
    }

    try {
      await api.put("/auth/account/username", { username: newName }, { toast: false });
      hideUsernameEdit();
      refreshAccountData();
      // Update header button with new username
      showAuthenticatedState({ username: newName });
    } catch (err) {
      alert("Error: " + err.message);
    }
  }

  // ── Email inline edit ──
  function showEmailEdit() {
    var display = document.getElementById("acct-email");
    var input = document.getElementById("acct-email-input");
    var saveBtn = document.getElementById("acct-email-save");
    var cancelBtn = document.getElementById("acct-email-cancel");

    input.value = display.textContent === "(none)" ? "" : display.textContent;
    display.hidden = true;
    input.hidden = false;
    saveBtn.hidden = false;
    cancelBtn.hidden = false;
    input.focus();
  }

  function hideEmailEdit() {
    document.getElementById("acct-email").hidden = false;
    document.getElementById("acct-email-input").hidden = true;
    document.getElementById("acct-email-save").hidden = true;
    document.getElementById("acct-email-cancel").hidden = true;
  }

  async function saveEmail() {
    var input = document.getElementById("acct-email-input");
    var newEmail = input.value.trim();

    try {
      await api.put("/auth/account/email", { email: newEmail }, { toast: false });
      hideEmailEdit();
      refreshAccountData();
    } catch (err) {
      alert("Error: " + err.message);
    }
  }

  // ── Auth method switch ──
  function initAuthSwitch() {
    var panel = document.getElementById("auth-switch-panel");
    panel.hidden = !panel.hidden;
    document.getElementById("auth-setup-result").hidden = true;
  }

  async function confirmAuthSwitch() {
    var selected = document.querySelector('input[name="switch_auth"]:checked');
    if (!selected) {
      alert("Select an authentication method");
      return;
    }

    try {
      var data = await api.put("/auth/account/auth-method", { auth_method: selected.value }, { toast: false });
      document.getElementById("auth-switch-panel").hidden = true;
      showSetupResult(data.setup_data, selected.value);
      refreshAccountData();
    } catch (err) {
      alert("Error: " + err.message);
    }
  }

  // ── Reset credentials ──
  async function resetCredentials() {
    if (!confirm("Reset your authentication credentials? You will need to reconfigure your authenticator.")) return;

    try {
      var data = await api.post("/auth/account/reset-credentials", null, { toast: false });
      showSetupResult(data.setup_data, accountData ? accountData.auth_type : "");
    } catch (err) {
      alert("Error: " + err.message);
    }
  }

  function showSetupResult(setupData, authMethod) {
    var result = document.getElementById("auth-setup-result");
    result.hidden = false;
    result.textContent = "";

    if (!setupData || Object.keys(setupData).length === 0) {
      result.textContent = "Auth method updated successfully.";
      return;
    }

    if (setupData.manual_key) {
      if (setupData.qr_base64) {
        var img = document.createElement("img");
        img.src = "data:image/png;base64," + setupData.qr_base64;
        img.alt = "TOTP QR Code";
        img.style.cssText = "display:block;margin:0.75rem auto;max-width:200px;";
        result.appendChild(img);
      }
      var keyText = document.createElement("p");
      keyText.style.cssText = "margin-top:0.5rem;word-break:break-all;";
      keyText.textContent = "Manual key: " + setupData.manual_key;
      result.appendChild(keyText);
      var hint = document.createElement("p");
      hint.style.cssText = "font-size:0.85em;opacity:0.8;";
      hint.textContent = "Scan the QR code in your authenticator app or enter the key manually.";
      result.appendChild(hint);
    } else if (setupData.claim_url) {
      result.textContent = "Claim URL: " + window.location.origin + setupData.claim_url +
        "\nVisit this URL on your device to register your passkey.";
    } else if (setupData.email) {
      result.textContent = "Magic Link configured for: " + setupData.email;
    }
  }

  // ── Delete account ──
  async function deleteOwnAccount() {
    var msg = "This will permanently delete your account and all of your listening history. " +
      "You will likely experience intermittent swattings and harassment. " +
      "Can\u2019t be helped \u2014 this is normal and should be expected, because you " +
      "already knew who was behind this bullshit webapp when you signed up in the first place.";
    if (!confirm(msg)) return;

    try {
      await api.delete("/auth/account", { toast: false });
      window.location.href = "/auth/login";
    } catch (err) {
      alert("Error: " + err.message);
    }
  }

  // ── Event Listeners ──
  document.addEventListener("DOMContentLoaded", function () {
    // Open/close modal
    document.getElementById("my-account-btn").addEventListener("click", openAccountModal);
    document.getElementById("account-modal-close").addEventListener("click", closeAccountModal);

    // Close on backdrop click
    document.getElementById("account-modal").addEventListener("click", function (e) {
      if (e.target === this) closeAccountModal();
    });

    // Username edit
    document.getElementById("acct-username").addEventListener("click", showUsernameEdit);
    document.getElementById("acct-username-save").addEventListener("click", saveUsername);
    document.getElementById("acct-username-cancel").addEventListener("click", hideUsernameEdit);
    document.getElementById("acct-username-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") saveUsername();
      if (e.key === "Escape") hideUsernameEdit();
    });

    // Email edit
    document.getElementById("acct-email").addEventListener("click", showEmailEdit);
    document.getElementById("acct-email-save").addEventListener("click", saveEmail);
    document.getElementById("acct-email-cancel").addEventListener("click", hideEmailEdit);
    document.getElementById("acct-email-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") saveEmail();
      if (e.key === "Escape") hideEmailEdit();
    });

    // Auth switch
    document.getElementById("auth-switch-btn").addEventListener("click", initAuthSwitch);
    document.getElementById("auth-switch-confirm").addEventListener("click", confirmAuthSwitch);

    // Reset credentials
    document.getElementById("auth-reset-btn").addEventListener("click", resetCredentials);

    // Delete account
    document.getElementById("delete-account-btn").addEventListener("click", deleteOwnAccount);

    // Sign out
    document.getElementById("sign-out-btn").addEventListener("click", function () {
      closeAccountModal();
      // Use the iframe's library logout if available, otherwise direct API call
      var frame = document.getElementById("content-frame");
      if (frame && frame.contentWindow && frame.contentWindow.library) {
        frame.contentWindow.library.logout();
      } else {
        api.post("/auth/logout", null, { toast: false })
          .then(function () { window.location.href = "/auth/login"; })
          .catch(function () { window.location.href = "/auth/login"; });
      }
    });

    // Back Office gate — intercept clicks from non-admins
    var boLink = document.getElementById("admin-backoffice-link");
    if (boLink) {
      boLink.addEventListener("click", function (e) {
        if (boLink.getAttribute("data-locked") === "true") {
          e.preventDefault();
          alert("The Back Office is restricted to admin users.");
        }
      });
    }

    // Contact admin — navigate the iframe
    var contactBtn = document.getElementById("contact-admin-btn");
    if (contactBtn) {
      contactBtn.addEventListener("click", function (e) {
        e.preventDefault();
        closeAccountModal();
        var frame = document.getElementById("content-frame");
        if (frame) frame.src = "contact.html";
      });
    }

    // ── Preferences controls ──
    initPreferencesControls();

    // Populate account button — never hides it
    initAccountButton();
  });

  // ── Preferences: load, bind, save ──

  var PREF_DEFAULTS = {
    sort_order: 'title_asc', view_mode: 'grid', items_per_page: '24',
    content_filter: 'all', playback_speed: '1', sleep_timer: '0',
    auto_play_series: 'false'
  };

  function initPreferencesControls() {
    // Hide prefs section for unauthenticated users (loaded after auth check)
    var prefsSection = document.getElementById('prefs-section');
    if (!prefsSection) return;

    // Auto-save on select change
    prefsSection.querySelectorAll('.pref-select').forEach(function (sel) {
      sel.addEventListener('change', function () {
        saveBrowsingPref(sel.dataset.key, sel.value);
      });
    });

    // Toggle groups
    prefsSection.querySelectorAll('.pref-toggle-group').forEach(function (group) {
      group.addEventListener('click', function (e) {
        var target = e.target.closest('button');
        if (!target) return;
        group.querySelectorAll('button').forEach(function (b) {
          b.classList.toggle('active', b === target);
        });
        saveBrowsingPref(group.dataset.key, target.dataset.value);
      });
    });

    // Checkbox
    var autoPlay = document.getElementById('pref-auto-play');
    if (autoPlay) {
      autoPlay.addEventListener('change', function () {
        saveBrowsingPref('auto_play_series', this.checked ? 'true' : 'false');
      });
    }
  }

  function saveBrowsingPref(key, value) {
    var body = {};
    body[key] = value;
    api.patch('/api/user/preferences', body, { toast: false }).catch(function () {});
  }

  function loadPreferencesIntoModal() {
    api.get('/api/user/preferences', { toast: false })
      .then(function (data) {
        // Select dropdowns
        var selects = document.querySelectorAll('#prefs-section .pref-select');
        selects.forEach(function (sel) {
          var key = sel.dataset.key;
          if (data[key] !== undefined) sel.value = data[key];
        });
        // Toggle groups
        document.querySelectorAll('#prefs-section .pref-toggle-group').forEach(function (group) {
          var key = group.dataset.key;
          var val = data[key] || PREF_DEFAULTS[key];
          group.querySelectorAll('button').forEach(function (b) {
            b.classList.toggle('active', b.dataset.value === val);
          });
        });
        // Checkbox
        var autoPlay = document.getElementById('pref-auto-play');
        if (autoPlay) autoPlay.checked = data.auto_play_series === 'true';

        // Show section
        document.getElementById('prefs-section').style.display = '';
      })
      .catch(function () {
        // Hide preferences section for unauthenticated users
        var s = document.getElementById('prefs-section');
        if (s) s.style.display = 'none';
      });
  }

  // Override openAccountModal to also load preferences
  var _origOpenModal = openAccountModal;
  openAccountModal = function () {
    _origOpenModal();
    if (authenticated) loadPreferencesIntoModal();
  };
})();
