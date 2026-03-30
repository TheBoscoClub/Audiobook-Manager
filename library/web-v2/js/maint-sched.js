/**
 * Maint Sched tab -- CRUD for maintenance windows, messages, and history.
 *
 * Uses safe DOM methods (createElement/textContent) throughout.
 * No innerHTML with dynamic content.
 */
(function () {
  "use strict";

  var PRESETS = {
    daily: "0 {H} * * *",
    weekly: "0 {H} * * 1",
    biweekly: "0 {H} 1,15 * *",
    monthly: "0 {H} 1 * *",
  };

  // fetchAuth replaced by shared api.* client (api.js)

  function escText(s) {
    return s == null ? "" : String(s);
  }

  function createCell(text) {
    var td = document.createElement("td");
    td.textContent = escText(text);
    return td;
  }

  // formatLocal is now provided by utils.js

  // -- Task type population --
  function loadTaskTypes() {
    api.get("/api/admin/maintenance/tasks", { toast: false })
      .then(function (tasks) {
        var sel = document.getElementById("maint-task-type");
        while (sel.firstChild) sel.removeChild(sel.firstChild);
        if (tasks.length === 0) {
          var empty = document.createElement("option");
          empty.value = "";
          empty.textContent = "No tasks registered";
          empty.disabled = true;
          sel.appendChild(empty);
          return;
        }
        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Select a task\u2026";
        placeholder.disabled = true;
        placeholder.selected = true;
        sel.appendChild(placeholder);
        tasks.forEach(function (t) {
          var opt = document.createElement("option");
          opt.value = t.name;
          opt.textContent = t.display_name;
          opt.title = t.description || "";
          sel.appendChild(opt);
        });
      })
      .catch(function (e) {
        console.error("Failed to load task types:", e);
        var sel = document.getElementById("maint-task-type");
        while (sel.firstChild) sel.removeChild(sel.firstChild);
        var err = document.createElement("option");
        err.value = "";
        err.textContent = "Error loading tasks";
        err.disabled = true;
        sel.appendChild(err);
      });
  }

  // -- Schedule type toggle --
  function showHide(id, visible) {
    var el = document.getElementById(id);
    if (el) el.style.display = visible ? "" : "none";
  }

  function onScheduleTypeChange() {
    var val = document.getElementById("maint-schedule-type").value;
    showHide("maint-once-fields", val === "once");
    showHide("maint-recurring-fields", val === "recurring");
    showHide("maint-cron-fields", val === "cron");

    // Auto-generate cron from preset when switching to recurring
    if (val === "recurring") {
      var checked = document.querySelector('input[name="maint-preset"]:checked');
      if (checked) onPresetChange({ target: checked });
    }
  }

  // -- Preset to cron (generates cron expression from friendly presets) --
  function onPresetChange(e) {
    var val = e.target.value;
    var timeVal = document.getElementById("maint-time").value || "03:00";
    var parts = timeVal.split(":");
    var h = parseInt(parts[0], 10);
    var cron = PRESETS[val].replace("{H}", h);
    document.getElementById("maint-cron-input").value = cron;
  }

  // -- Create window --
  function createWindow() {
    var schedType = document.getElementById("maint-schedule-type").value;
    var taskType = document.getElementById("maint-task-type").value;
    // Map the 3-option UI to the 2-value API (once/recurring)
    var apiScheduleType = schedType === "once" ? "once" : "recurring";
    var body = {
      name: document.getElementById("maint-name").value.trim(),
      task_type: taskType,
      schedule_type: apiScheduleType,
      lead_time_hours: parseInt(document.getElementById("maint-lead-time").value, 10) || 48,
      description: document.getElementById("maint-description").value.trim(),
    };

    if (schedType === "once") {
      var dt = document.getElementById("maint-scheduled-at").value;
      if (dt) body.scheduled_at = new Date(dt).toISOString();
    } else {
      body.cron_expression = document.getElementById("maint-cron-input").value.trim();
    }

    if (!body.name) { alert("Name is required"); return; }
    if (!body.task_type) { alert("Please select a task type"); return; }

    api.post("/api/admin/maintenance/windows", body, { toast: false })
      .then(function () { loadWindows(); })
      .catch(function (e) { alert("Error: " + e.message); });
  }

  // -- Load windows --
  function loadWindows() {
    api.get("/api/admin/maintenance/windows", { toast: false })
      .then(function (windows) {
        var tbody = document.getElementById("maint-windows-body");
        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        windows.forEach(function (w) {
          var tr = document.createElement("tr");
          tr.appendChild(createCell(w.name));
          tr.appendChild(createCell(w.task_type));
          tr.appendChild(createCell(
            w.schedule_type === "recurring" ? w.cron_expression : "One-time"
          ));
          tr.appendChild(createCell(formatLocal(w.next_run_at)));
          tr.appendChild(createCell(w.status));

          var actionTd = document.createElement("td");
          if (w.status === "active") {
            var cancelBtn = document.createElement("button");
            cancelBtn.className = "office-btn office-btn-sm";
            cancelBtn.textContent = "Cancel";
            cancelBtn.title = "Cancel this maintenance window";
            cancelBtn.addEventListener("click", function () {
              cancelWindow(w.id);
            });
            actionTd.appendChild(cancelBtn);
          }
          var delBtn = document.createElement("button");
          delBtn.className = "office-btn office-btn-sm office-btn-danger";
          delBtn.textContent = "Delete";
          delBtn.title = "Delete this maintenance window";
          delBtn.addEventListener("click", function () {
            deleteWindow(w.id);
          });
          actionTd.appendChild(delBtn);
          tr.appendChild(actionTd);

          tbody.appendChild(tr);
        });
      })
      .catch(function () {});
  }

  function cancelWindow(id) {
    api.put("/api/admin/maintenance/windows/" + id, { status: "cancelled" }, { toast: false })
      .then(function () { loadWindows(); })
      .catch(function () {});
  }

  function deleteWindow(id) {
    api.delete("/api/admin/maintenance/windows/" + id, { toast: false })
      .then(function () { loadWindows(); })
      .catch(function () {});
  }

  // -- Messages --
  function sendMessage() {
    var input = document.getElementById("maint-message-input");
    var text = input.value.trim();
    if (!text) return;

    api.post("/api/admin/maintenance/messages", { message: text }, { toast: false })
      .then(function (created) {
        input.value = "";
        loadMessages();
        // Trigger banner update on the main page via custom event so the
        // pulsing indicator activates immediately without waiting for
        // WebSocket or polling.  The parent frame (shell.html) listens
        // for a postMessage; if we are IN shell.html, dispatch directly.
        try {
          var announceEvt = new CustomEvent("maintenance-announce", {
            detail: {
              type: "maintenance_announce",
              messages: [created],
            },
          });
          // Dispatch on own document (Back Office is inside shell iframe)
          document.dispatchEvent(announceEvt);
          // Also notify parent frame if running inside an iframe
          if (window.parent && window.parent !== window) {
            window.parent.document.dispatchEvent(announceEvt);
          }
        } catch (e) {
          console.warn("Could not dispatch announcement event:", e);
        }
      })
      .catch(function (e) { alert("Error: " + e.message); });
  }

  function loadMessages() {
    api.get("/api/admin/maintenance/messages", { toast: false })
      .then(function (messages) {
        var container = document.getElementById("maint-messages-list");
        while (container.firstChild) container.removeChild(container.firstChild);

        messages.forEach(function (m) {
          var div = document.createElement("div");
          div.className = "maint-message-item" + (m.dismissed_at ? " dismissed" : "");

          var text = document.createElement("span");
          text.textContent = m.message;
          div.appendChild(text);

          var meta = document.createElement("small");
          meta.textContent = " -- " + m.created_by + " at " + formatLocal(m.created_at);
          div.appendChild(meta);

          if (!m.dismissed_at) {
            var ks = createKnifeSwitch({
              size: "compact",
              title: "Permanently dismiss this announcement for all users",
              label: "Dismiss",
              onDismiss: function () { dismissMessage(m.id); }
            });
            div.appendChild(ks);
          }

          container.appendChild(div);
        });
      })
      .catch(function () {});
  }

  function dismissMessage(id) {
    api.delete("/api/admin/maintenance/messages/" + id, { toast: false })
      .then(function () { loadMessages(); })
      .catch(function () {});
  }

  // -- History --
  function loadHistory() {
    api.get("/api/admin/maintenance/history", { toast: false })
      .then(function (history) {
        var tbody = document.getElementById("maint-history-body");
        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        history.forEach(function (h) {
          var tr = document.createElement("tr");
          tr.appendChild(createCell(h.window_name || "Window #" + h.window_id));
          tr.appendChild(createCell(h.task_type));
          tr.appendChild(createCell(formatLocal(h.started_at)));

          var statusTd = document.createElement("td");
          var badge = document.createElement("span");
          badge.className = "maint-status-badge maint-status-" + h.status;
          badge.textContent = h.status;
          statusTd.appendChild(badge);
          tr.appendChild(statusTd);

          tr.appendChild(createCell(h.result_message || ""));
          tbody.appendChild(tr);
        });
      })
      .catch(function () {});
  }

  // -- Init --
  function initMaintSched() {
    document.getElementById("maint-schedule-type").addEventListener("change", onScheduleTypeChange);
    document.getElementById("maint-create-btn").addEventListener("click", createWindow);
    document.getElementById("maint-send-msg-btn").addEventListener("click", sendMessage);

    // Wire up the "Pick" button to open the native datetime picker
    var pickBtn = document.getElementById("maint-pick-date-btn");
    var dateInput = document.getElementById("maint-scheduled-at");
    if (pickBtn && dateInput) {
      pickBtn.addEventListener("click", function () {
        if (typeof dateInput.showPicker === "function") {
          dateInput.showPicker();
        } else {
          // Fallback for older browsers: focus triggers the picker
          dateInput.focus();
          dateInput.click();
        }
      });
    }

    var presetRadios = document.querySelectorAll('input[name="maint-preset"]');
    for (var i = 0; i < presetRadios.length; i++) {
      presetRadios[i].addEventListener("change", onPresetChange);
    }

    loadTaskTypes();
    loadWindows();
    loadMessages();
    loadHistory();
  }

  // Auto-init when the Maint Sched tab becomes visible.
  // The tab system uses classList.toggle("active"), not style.display,
  // so we observe class attribute changes on the section element.
  var _initialized = false;

  function tryInit() {
    if (_initialized) return;
    var section = document.getElementById("maint-sched-section");
    if (section && section.classList.contains("active")) {
      _initialized = true;
      initMaintSched();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var section = document.getElementById("maint-sched-section");
    if (!section) return;

    // Watch for class changes (tab activation adds/removes "active")
    var observer = new MutationObserver(function () {
      tryInit();
    });
    observer.observe(section, { attributes: true, attributeFilter: ["class"] });

    // Also check immediately in case the tab is already active
    tryInit();
  });
})();
