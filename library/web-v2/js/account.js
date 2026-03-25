/**
 * My Account modal — self-service account management.
 * Loaded in shell.html, operates via /auth/account/* endpoints.
 */
(function () {
  "use strict";

  // ── State ──
  var accountData = null;

  // ── Modal open/close ──
  function openAccountModal() {
    loadAccountData();
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

  // ── Load profile data ──
  async function loadAccountData() {
    try {
      var resp = await fetch("/auth/account", { credentials: "same-origin" });
      if (!resp.ok) throw new Error("Not authenticated");
      accountData = await resp.json();

      document.getElementById("acct-username").textContent = accountData.username;
      document.getElementById("acct-email").textContent = accountData.email || "(none)";
      document.getElementById("acct-created").textContent =
        accountData.created_at ? new Date(accountData.created_at).toLocaleDateString() : "Unknown";
      document.getElementById("acct-auth-badge").textContent =
        (accountData.auth_type || "").toUpperCase();
      document.getElementById("account-username").textContent = accountData.username;
      // Update avatar initial
      var initialEl = document.getElementById("account-initial");
      if (initialEl && accountData.username) {
        initialEl.textContent = accountData.username.charAt(0).toUpperCase();
      }
    } catch (e) {
      // Not logged in — hide the account button
      document.getElementById("my-account-btn").hidden = true;
    }
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
      var resp = await fetch("/auth/account/username", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ username: newName }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        alert("Error: " + (data.error || "Failed to change username"));
        return;
      }
      hideUsernameEdit();
      loadAccountData();
    } catch (err) {
      alert("Network error: " + err.message);
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
      var resp = await fetch("/auth/account/email", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email: newEmail }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        alert("Error: " + (data.error || "Failed to change email"));
        return;
      }
      hideEmailEdit();
      loadAccountData();
    } catch (err) {
      alert("Network error: " + err.message);
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
      var resp = await fetch("/auth/account/auth-method", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ auth_method: selected.value }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        alert("Error: " + (data.error || "Failed to switch auth method"));
        return;
      }

      document.getElementById("auth-switch-panel").hidden = true;
      showSetupResult(data.setup_data, selected.value);
      loadAccountData();
    } catch (err) {
      alert("Network error: " + err.message);
    }
  }

  // ── Reset credentials ──
  async function resetCredentials() {
    if (!confirm("Reset your authentication credentials? You will need to reconfigure your authenticator.")) return;

    try {
      var resp = await fetch("/auth/account/reset-credentials", {
        method: "POST",
        credentials: "same-origin",
      });
      var data = await resp.json();
      if (!resp.ok) {
        alert("Error: " + (data.error || "Failed to reset credentials"));
        return;
      }

      showSetupResult(data.setup_data, accountData ? accountData.auth_type : "");
    } catch (err) {
      alert("Network error: " + err.message);
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
      var resp = await fetch("/auth/account", {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (resp.ok) {
        window.location.href = "/auth/login";
      } else {
        var data = await resp.json();
        alert("Error: " + (data.error || "Failed to delete account"));
      }
    } catch (err) {
      alert("Network error: " + err.message);
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
        fetch("/auth/logout", { method: "POST", credentials: "same-origin" })
          .then(function () { window.location.href = "/auth/login"; })
          .catch(function () { window.location.href = "/auth/login"; });
      }
    });

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

    // Load account data to populate header username
    loadAccountData();
  });
})();
