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

  function escText(s) {
    return s == null ? "" : String(s);
  }

  function createCell(text) {
    var td = document.createElement("td");
    td.textContent = escText(text);
    return td;
  }

  // -- Task type population --
  function loadTaskTypes() {
    fetch("/api/admin/maintenance/tasks")
      .then(function (r) { return r.json(); })
      .then(function (tasks) {
        var sel = document.getElementById("maint-task-type");
        while (sel.firstChild) sel.removeChild(sel.firstChild);
        tasks.forEach(function (t) {
          var opt = document.createElement("option");
          opt.value = t.name;
          opt.textContent = t.display_name;
          opt.title = t.description || "";
          sel.appendChild(opt);
        });
      })
      .catch(function () {});
  }

  // -- Schedule type toggle --
  function onScheduleTypeChange() {
    var val = document.getElementById("maint-schedule-type").value;
    document.getElementById("maint-once-fields").style.display = val === "once" ? "" : "none";
    document.getElementById("maint-recurring-fields").style.display = val === "recurring" ? "" : "none";
  }

  // -- Preset to cron --
  function onPresetChange(e) {
    var val = e.target.value;
    var cronRow = document.getElementById("maint-cron-row");
    if (val === "custom") {
      cronRow.style.display = "";
      return;
    }
    cronRow.style.display = "none";
    var timeVal = document.getElementById("maint-time").value || "03:00";
    var parts = timeVal.split(":");
    var h = parseInt(parts[0], 10);
    var cron = PRESETS[val].replace("{H}", h);
    document.getElementById("maint-cron-input").value = cron;
  }

  // -- Create window --
  function createWindow() {
    var schedType = document.getElementById("maint-schedule-type").value;
    var body = {
      name: document.getElementById("maint-name").value.trim(),
      task_type: document.getElementById("maint-task-type").value,
      schedule_type: schedType,
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

    fetch("/api/admin/maintenance/windows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function () { loadWindows(); })
      .catch(function (e) { alert("Error: " + e.message); });
  }

  // -- Load windows --
  function loadWindows() {
    fetch("/api/admin/maintenance/windows")
      .then(function (r) { return r.json(); })
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
          tr.appendChild(createCell(
            w.next_run_at ? new Date(w.next_run_at).toLocaleString() : "N/A"
          ));
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
    fetch("/api/admin/maintenance/windows/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "cancelled" }),
    }).then(function () { loadWindows(); });
  }

  function deleteWindow(id) {
    fetch("/api/admin/maintenance/windows/" + id, { method: "DELETE" })
      .then(function () { loadWindows(); });
  }

  // -- Messages --
  function sendMessage() {
    var input = document.getElementById("maint-message-input");
    var text = input.value.trim();
    if (!text) return;

    fetch("/api/admin/maintenance/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        input.value = "";
        loadMessages();
      })
      .catch(function (e) { alert("Error: " + e.message); });
  }

  function loadMessages() {
    fetch("/api/admin/maintenance/messages")
      .then(function (r) { return r.json(); })
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
          meta.textContent = " -- " + m.created_by + " at " + new Date(m.created_at).toLocaleString();
          div.appendChild(meta);

          if (!m.dismissed_at) {
            var btn = document.createElement("button");
            btn.className = "office-btn office-btn-sm";
            btn.textContent = "Dismiss";
            btn.title = "Permanently dismiss this announcement for all users";
            btn.addEventListener("click", function () {
              dismissMessage(m.id);
            });
            div.appendChild(btn);
          }

          container.appendChild(div);
        });
      })
      .catch(function () {});
  }

  function dismissMessage(id) {
    fetch("/api/admin/maintenance/messages/" + id, { method: "DELETE" })
      .then(function () { loadMessages(); });
  }

  // -- History --
  function loadHistory() {
    fetch("/api/admin/maintenance/history")
      .then(function (r) { return r.json(); })
      .then(function (history) {
        var tbody = document.getElementById("maint-history-body");
        while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

        history.forEach(function (h) {
          var tr = document.createElement("tr");
          tr.appendChild(createCell(h.window_name || "Window #" + h.window_id));
          tr.appendChild(createCell(h.task_type));
          tr.appendChild(createCell(new Date(h.started_at).toLocaleString()));

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

    var presetRadios = document.querySelectorAll('input[name="maint-preset"]');
    for (var i = 0; i < presetRadios.length; i++) {
      presetRadios[i].addEventListener("change", onPresetChange);
    }

    loadTaskTypes();
    loadWindows();
    loadMessages();
    loadHistory();
  }

  // Auto-init when tab becomes visible
  document.addEventListener("DOMContentLoaded", function () {
    var observer = new MutationObserver(function () {
      var section = document.getElementById("maint-sched-section");
      if (section && section.style.display !== "none") {
        initMaintSched();
        observer.disconnect();
      }
    });
    var section = document.getElementById("maint-sched-section");
    if (section) {
      observer.observe(section, { attributes: true, attributeFilter: ["style"] });
    }
  });

  // Also init if directly navigated
  if (document.readyState === "complete") {
    var section = document.getElementById("maint-sched-section");
    if (section && section.style.display !== "none") {
      initMaintSched();
    }
  }
})();
