/**
 * Admin suggestion notification badge and drawer.
 *
 * - Polls for unread count every 30s (admins only)
 * - Throbs badge when unread > 0
 * - Drawer shows suggestions with read/unread/delete controls
 * - Listens for WebSocket "suggestion_new" events for instant updates
 */
(function () {
  "use strict";

  var POLL_INTERVAL = 30000;
  var alertBtn = document.getElementById("suggestion-alert");
  var badgeCount = document.getElementById("suggestion-badge-count");
  var drawer = document.getElementById("suggestion-drawer");
  var drawerList = document.getElementById("suggestion-drawer-list");
  var closeBtn = document.getElementById("suggestion-drawer-close");
  var isAdmin = false;
  var currentFilter = "unread";
  var pollTimer = null;

  // ── Check if current user is admin ──
  function checkAdmin() {
    return fetch("/auth/status", { credentials: "include" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.user && data.user.is_admin) {
          isAdmin = true;
          alertBtn.hidden = false;
          pollUnreadCount();
          pollTimer = setInterval(pollUnreadCount, POLL_INTERVAL);
        }
      })
      .catch(function () {});
  }

  // ── Poll unread count ──
  function pollUnreadCount() {
    fetch("/api/admin/suggestions/unread-count", { credentials: "include" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var count = data.count || 0;
        badgeCount.textContent = count;
        if (count > 0) {
          alertBtn.classList.add("has-unread");
        } else {
          alertBtn.classList.remove("has-unread");
        }
      })
      .catch(function () {});
  }

  // ── Load suggestions into drawer ──
  function loadSuggestions(filter) {
    currentFilter = filter || currentFilter;
    drawerList.textContent = "Loading...";

    fetch("/api/admin/suggestions?filter=" + currentFilter, {
      credentials: "include",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (items) {
        drawerList.textContent = "";

        if (!items || items.length === 0) {
          var empty = document.createElement("p");
          empty.className = "suggestion-empty";
          empty.textContent =
            currentFilter === "unread"
              ? "No unread suggestions."
              : "No suggestions found.";
          drawerList.appendChild(empty);
          return;
        }

        items.forEach(function (item) {
          var card = document.createElement("div");
          card.className =
            "suggestion-card" + (item.is_read ? " suggestion-read" : "");
          card.dataset.id = item.id;

          var meta = document.createElement("div");
          meta.className = "suggestion-meta";

          var user = document.createElement("strong");
          user.textContent = item.username;
          meta.appendChild(user);

          var time = document.createElement("span");
          time.className = "suggestion-time";
          var d = new Date(item.created_at);
          time.textContent =
            " \u2014 " +
            d.toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
              year: "numeric",
            }) +
            " " +
            d.toLocaleTimeString(undefined, {
              hour: "2-digit",
              minute: "2-digit",
            });
          meta.appendChild(time);

          card.appendChild(meta);

          var body = document.createElement("div");
          body.className = "suggestion-body";
          body.textContent = item.message;
          card.appendChild(body);

          var actions = document.createElement("div");
          actions.className = "suggestion-actions";

          // Toggle read/unread
          var toggleBtn = document.createElement("button");
          toggleBtn.className = "sug-btn";
          toggleBtn.textContent = item.is_read
            ? "Mark Unread"
            : "Mark Read";
          toggleBtn.title = item.is_read
            ? "Mark as unread"
            : "Mark as read";
          toggleBtn.addEventListener("click", function () {
            toggleRead(item.id, !item.is_read);
          });
          actions.appendChild(toggleBtn);

          // Delete
          var delBtn = document.createElement("button");
          delBtn.className = "sug-btn sug-btn-danger";
          delBtn.textContent = "Delete";
          delBtn.title = "Permanently delete this suggestion";
          delBtn.addEventListener("click", function () {
            if (confirm("Delete this suggestion?")) {
              deleteSuggestion(item.id);
            }
          });
          actions.appendChild(delBtn);

          card.appendChild(actions);
          drawerList.appendChild(card);
        });
      })
      .catch(function () {
        drawerList.textContent = "Error loading suggestions.";
      });
  }

  function toggleRead(id, markRead) {
    fetch("/api/admin/suggestions/" + id, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_read: markRead }),
    }).then(function (r) {
      if (r.ok) {
        loadSuggestions();
        pollUnreadCount();
      }
    });
  }

  function deleteSuggestion(id) {
    fetch("/api/admin/suggestions/" + id, {
      method: "DELETE",
      credentials: "include",
    }).then(function (r) {
      if (r.ok) {
        loadSuggestions();
        pollUnreadCount();
      }
    });
  }

  // ── Toggle drawer ──
  alertBtn.addEventListener("click", function () {
    if (drawer.hidden) {
      drawer.hidden = false;
      loadSuggestions("unread");
    } else {
      drawer.hidden = true;
    }
  });

  closeBtn.addEventListener("click", function () {
    drawer.hidden = true;
  });

  // ── Tab switching ──
  document.querySelectorAll(".sdtab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".sdtab").forEach(function (t) {
        t.classList.remove("active");
      });
      tab.classList.add("active");
      loadSuggestions(tab.dataset.filter);
    });
  });

  // ── WebSocket live notification ──
  document.addEventListener("ws-connected", function () {
    // Already connected — set up listener for suggestion events
  });

  // Listen for suggestion_new events from WebSocket
  // The websocket.js dispatches custom DOM events for known message types.
  // We need to add "suggestion_new" to its dispatch list, or listen on raw ws.
  // Simpler: just poll more aggressively is fine, but let's also hook the
  // existing dispatch system by listening for a generic event.
  // We'll patch: on each heartbeat cycle, if ws message contains suggestion_new,
  // the websocket.js dispatches it. Let's add that handler.
  document.addEventListener("suggestion-new", function () {
    if (isAdmin) {
      pollUnreadCount();
      if (!drawer.hidden) {
        loadSuggestions();
      }
    }
  });

  // ── Init ──
  checkAdmin();
})();
